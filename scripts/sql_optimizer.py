"""
SQL Optimizer Agent
-------------------
Pipeline LangGraph que:
  1) Detecta archivos .sql cambiados en una Pull Request (carpeta sql/).
  2) Valida la sintaxis con sqlglot (dialecto ANSI / multi-dialecto).
  3) Pide a un modelo de OpenAI que optimice las querys SIN alterar la lógica.
  4) Re-valida la salida optimizada para asegurar que sigue siendo SQL correcto.
  5) Construye un log de cambios (diff + explicación) y lo publica como
     comentario en la Pull Request a través de la GitHub API.

Variables de entorno requeridas (Están en las GitHub Actions):
  OPENAI_API_KEY   - token de OpenAI (Se tiene que configurar el secreto)
  GITHUB_TOKEN     - token automático del workflow
  GITHUB_REPOSITORY - "owner/repo"
  PR_NUMBER        - número de la PR (lo pasa el workflow)
  BASE_SHA         - SHA base de la PR
  HEAD_SHA         - SHA head de la PR
  OPENAI_MODEL     - opcional, default "gpt-4o-mini"
  SQL_DIALECT      - opcional, default "ansi"
"""

from __future__ import annotations

import difflib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, TypedDict

import requests
import sqlglot
from sqlglot.errors import ParseError
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

"""
Variables de entorno ACTIONS
"""

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
SQL_DIALECT = os.getenv("SQL_DIALECT", "ansi")
TARGET_FOLDER = os.getenv("TARGET_FOLDER", "sql")
_SQLGLOT_DIALECT = None if SQL_DIALECT.lower() == "ansi" else SQL_DIALECT
GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY")
PR_NUMBER = os.getenv("PR_NUMBER")
BASE_SHA = os.getenv("BASE_SHA")
HEAD_SHA = os.getenv("HEAD_SHA")



"""
SYSTEM_MESSAGE
"""

SYSTEM_MESSAGE = """Eres un experto en optimización de SQL.
Tu tarea es reescribir una consulta SQL para que sea más eficiente
SIN alterar la lógica ni el conjunto de resultados que produce.

Reglas estrictas:
- NO cambies los nombres de columnas seleccionadas ni su orden.
- NO cambies los filtros lógicos (WHERE, HAVING) salvo reescritura equivalente.
- Mantén el mismo dialecto SQL del input.
- Prefiere JOINs explícitos sobre subqueries correlacionadas cuando aporten claridad.
- Elimina SELECT * solo si puedes inferir las columnas exactas; si no, déjalo.
- Sugiere índices solo en la sección "explicacion", nunca dentro del SQL.

Devuelve EXCLUSIVAMENTE un JSON válido con esta forma:
{
  "sql_optimizado": "<string con el SQL reescrito>",
  "cambios": ["<bullet 1>", "<bullet 2>", ...],
  "explicacion": "<párrafo breve explicando por qué es más eficiente>"
}
No agregues texto fuera del JSON. No uses bloques de código markdown.
"""


"""
GRAFO
"""


@dataclass
class FileResult:
    path: str
    original_sql: str
    optimized_sql: str = ""
    is_valid_input: bool = True
    is_valid_output: bool = True
    input_error: str = ""
    output_error: str = ""
    changes: List[str] = field(default_factory=list)
    explanation: str = ""
    diff: str = ""
    skipped_reason: Optional[str] = None


class GraphState(TypedDict):
    files: List[FileResult]
    summary_markdown: str


"""
GITHUB UTILS
"""

def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_changed_sql_files() -> List[Path]:
    """Devuelve los .sql modificados en la PR dentro de TARGET_FOLDER."""
    if not (GITHUB_REPO and PR_NUMBER):
        print("[warn] No GITHUB_REPOSITORY/PR_NUMBER, escaneando carpeta completa.")
        return list(Path(TARGET_FOLDER).rglob("*.sql"))

    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/pulls/{PR_NUMBER}/files?per_page=100"
    resp = requests.get(url, headers=_gh_headers(), timeout=30)
    resp.raise_for_status()
    files = resp.json()

    target_prefix = f"{TARGET_FOLDER.rstrip('/')}/"
    changed = [
        Path(f["filename"])
        for f in files
        if f["filename"].startswith(target_prefix)
        and f["filename"].endswith(".sql")
        and f["status"] != "removed"
    ]
    return changed


def post_pr_comment(body: str) -> None:
    if not (GITHUB_REPO and PR_NUMBER):
        print("[info] Modo local: comentario no publicado.\n", body)
        return
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/issues/{PR_NUMBER}/comments"
    resp = requests.post(url, headers=_gh_headers(), json={"body": body}, timeout=30)
    if resp.status_code >= 300:
        print(f"[error] No se pudo publicar comentario: {resp.status_code} {resp.text}")
    else:
        print(f"[ok] Comentario publicado en PR #{PR_NUMBER}")


"""
NODOS LANG_GRAPH
"""

def node_load_files(state: GraphState) -> GraphState:
    """Lee los archivos SQL que vienen de la PR. Solo los de la carpeta SQL"""
    paths = get_changed_sql_files()
    print(f"[load] {len(paths)} archivo(s) SQL detectado(s)")
    files: List[FileResult] = []
    for p in paths:
        try:
            content = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(f"[skip] No existe en el checkout: {p}")
            continue
        files.append(FileResult(path=str(p), original_sql=content))
    state["files"] = files
    return state


def node_validate_input(state: GraphState) -> GraphState:
    """Valida sintaxis del SQL original."""
    for f in state["files"]:
        try:
            sqlglot.parse(f.original_sql, dialect=_SQLGLOT_DIALECT)
            f.is_valid_input = True
        except ParseError as e:
            f.is_valid_input = False
            f.input_error = str(e)
            f.skipped_reason = "El SQL original no es parseable; no se optimiza."
    return state


def node_optimize(state: GraphState) -> GraphState:
    """Llama a OpenAI vía LangChain para reescribir cada query."""
    llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0)
    for f in state["files"]:
        if not f.is_valid_input:
            continue
        user_prompt = (
            f"Dialecto: {SQL_DIALECT}\n"
            f"Archivo: {f.path}\n\n"
            f"SQL original:\n```\n{f.original_sql}\n```"
        )
        try:
            resp = llm.invoke(
                [SystemMessage(content=SYSTEM_MESSAGE), HumanMessage(content=user_prompt)]
            )
            raw = (resp.content or "").strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.lower().startswith("json"):
                    raw = raw[4:].strip()
            data = json.loads(raw)
            f.optimized_sql = data.get("sql_optimizado", "").strip()
            f.changes = list(data.get("cambios", []))
            f.explanation = data.get("explicacion", "").strip()
        except Exception as e:  # noqa: BLE001
            f.skipped_reason = f"Error consultando al LLM: {e}"
            f.optimized_sql = ""
    return state


def node_validate_output(state: GraphState) -> GraphState:
    """Valida que el SQL optimizado siga siendo parseable."""
    for f in state["files"]:
        if not f.optimized_sql:
            continue
        try:
            sqlglot.parse(f.optimized_sql, dialect=_SQLGLOT_DIALECT)
            f.is_valid_output = True
        except ParseError as e:
            f.is_valid_output = False
            f.output_error = str(e)
            f.skipped_reason = (
                "La salida del LLM no era SQL válido; se descarta para no romper la lógica."
            )
            f.optimized_sql = ""
    return state


def node_build_diff(state: GraphState) -> GraphState:
    for f in state["files"]:
        if not f.optimized_sql:
            continue
        diff = difflib.unified_diff(
            f.original_sql.splitlines(keepends=False),
            f.optimized_sql.splitlines(keepends=False),
            fromfile=f"a/{f.path}",
            tofile=f"b/{f.path}",
            lineterm="",
        )
        f.diff = "\n".join(diff)
    return state


def node_render_comment(state: GraphState) -> GraphState:
    parts: List[str] = []
    parts.append("## SQL Optimizer Report")
    parts.append("")
    parts.append(
        f"Se analizaron **{len(state['files'])}** archivo(s) SQL de la carpeta "
        f"`{TARGET_FOLDER}/` con dialecto **{SQL_DIALECT}** usando el modelo "
        f"`{OPENAI_MODEL}` orquestado con LangGraph."
    )
    parts.append("")

    if not state["files"]:
        parts.append("_No se detectaron archivos SQL modificados en esta PR._")
        state["summary_markdown"] = "\n".join(parts)
        return state

    for f in state["files"]:
        parts.append(f"### `{f.path}`")
        if not f.is_valid_input:
            parts.append("Sintaxis del SQL original: **INVALIDA**")
            parts.append("```text")
            parts.append(f.input_error)
            parts.append("```")
            parts.append("")
            continue

        parts.append("Sintaxis del SQL original: **VALIDA**")

        if f.skipped_reason and not f.optimized_sql:
            parts.append(f"_No se aplicó optimización: {f.skipped_reason}_")
            parts.append("")
            continue

        if f.optimized_sql.strip() == f.original_sql.strip():
            parts.append("_El modelo no propuso cambios; la consulta ya estaba optimizada._")
            parts.append("")
            continue

        parts.append("")
        parts.append("**Cambios sugeridos:**")
        for c in f.changes or ["(sin detalle)"]:
            parts.append(f"- {c}")
        if f.explanation:
            parts.append("")
            parts.append(f"**Por qué:** {f.explanation}")

        parts.append("")
        parts.append("**SQL optimizado:**")
        parts.append("```sql")
        parts.append(f.optimized_sql)
        parts.append("```")

        if f.diff:
            parts.append("")
            parts.append("<details><summary>Diff</summary>")
            parts.append("")
            parts.append("```diff")
            parts.append(f.diff)
            parts.append("```")
            parts.append("</details>")
        parts.append("")

    parts.append("---")
    parts.append(
        "_Reporte generado automáticamente por `sql-optimizer.yml` "
        "(LangGraph + OpenAI). Revisa los cambios antes de aplicarlos._"
    )
    state["summary_markdown"] = "\n".join(parts)
    return state


def node_publish(state: GraphState) -> GraphState:
    post_pr_comment(state["summary_markdown"])
    # Además guardamos el reporte como artifact local para depuración
    Path("sql_optimizer_report.md").write_text(state["summary_markdown"], encoding="utf-8")
    return state


# --------------------------------------------------------------------------- #
# Construcción del grafo
# --------------------------------------------------------------------------- #


def build_graph():
    g = StateGraph(GraphState)
    g.add_node("load", node_load_files)
    g.add_node("validate_input", node_validate_input)
    g.add_node("optimize", node_optimize)
    g.add_node("validate_output", node_validate_output)
    g.add_node("build_diff", node_build_diff)
    g.add_node("render", node_render_comment)
    g.add_node("publish", node_publish)

    g.set_entry_point("load")
    g.add_edge("load", "validate_input")
    g.add_edge("validate_input", "optimize")
    g.add_edge("optimize", "validate_output")
    g.add_edge("validate_output", "build_diff")
    g.add_edge("build_diff", "render")
    g.add_edge("render", "publish")
    g.add_edge("publish", END)
    return g.compile()


def main() -> int:
    if not os.getenv("OPENAI_API_KEY"):
        print("[fatal] OPENAI_API_KEY no está definido.", file=sys.stderr)
        return 1
    graph = build_graph()
    final_state = graph.invoke({"files": [], "summary_markdown": ""})
    # Nunca fallamos el job aquí: la herramienta 1 es informativa.
    # La herramienta 2 (sql-syntax-check.yml) es la que bloquea merges.
    invalid = [f for f in final_state["files"] if not f.is_valid_input]
    if invalid:
        print(f"[warn] {len(invalid)} archivo(s) con SQL inválido (no se bloquea PR aquí).")
        # Si se quiere bloquear colocar 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
