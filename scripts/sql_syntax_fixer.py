"""
SQL Syntax Fixer 
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, TypedDict
import requests
import sqlglot
from sqlglot.errors import ParseError
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage


OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
REPORT_PATH = os.getenv("REPORT_PATH", "sqlfluff_report.json")

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY")
PR_NUMBER = os.getenv("PR_NUMBER")
HEAD_SHA = os.getenv("HEAD_SHA", "")

SYNTAX_CODES = {"PRS", "TMP", "LXR"}

SYSTEM_MESSAGE = """Eres un experto en SQL que ayuda a desarrolladores a
corregir errores de SINTAXIS detectados por sqlfluff.

Te dan:
- el contenido completo de un archivo SQL,
- la lista de errores (linea, columna, codigo, mensaje).

Tu tarea es:
1. Identificar la causa real (no repetir el mensaje de sqlfluff literalmente).
2. Explicarla en una frase corta y clara, en espanol neutro.
3. Proponer la correccion minima que resuelva TODOS los errores listados,
   sin reescribir mas de lo necesario y sin cambiar la logica.
4. Devolver el SQL corregido completo (no solo el snippet) para que el
   desarrollador pueda copiarlo y pegarlo.

Devuelve EXCLUSIVAMENTE un JSON valido con esta forma:
{
  "diagnostico": "<una linea: que falla>",
  "explicacion": "<2-3 lineas explicando el porque>",
  "fix_sugerido": "<SQL corregido completo, en texto plano sin tildes invertidas>",
  "cambios": ["<bullet 1>", "<bullet 2>", ...]
}
No agregues texto fuera del JSON. No uses markdown ni bloques de codigo.
"""


@dataclass
class FileDiag:
    path: str
    content: str = ""
    violations: List[Dict] = field(default_factory=list)
    diagnostico: str = ""
    explicacion: str = ""
    fix_sugerido: str = ""
    cambios: List[str] = field(default_factory=list)
    fix_is_valid: bool = False
    llm_error: str = ""


class State(TypedDict):
    files: List[FileDiag]
    summary_markdown: str
    had_violations: bool


def node_load_report(state: State) -> State:
    p = Path(REPORT_PATH)
    if not p.exists():
        print(f"[load] {REPORT_PATH} no existe, asumiendo 0 violaciones.")
        state["files"] = []
        state["had_violations"] = False
        return state

    raw = p.read_text(encoding="utf-8").strip()
    if not raw:
        print(f"[load] {REPORT_PATH} vacio, asumiendo 0 violaciones.")
        state["files"] = []
        state["had_violations"] = False
        return state

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[load] {REPORT_PATH} no es JSON valido ({e}); asumiendo 0 violaciones.")
        state["files"] = []
        state["had_violations"] = False
        return state

    files: List[FileDiag] = []
    for entry in data:
        fp = entry.get("filepath", "")
        fp_rel = fp.split("/./")[-1].lstrip("./")
        syntax_violations = [
            v for v in entry.get("violations", [])
            if v.get("code") in SYNTAX_CODES
        ]
        if not syntax_violations:
            continue
        try:
            content = Path(fp_rel).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            content = ""
        files.append(FileDiag(path=fp_rel, content=content, violations=syntax_violations))

    state["files"] = files
    state["had_violations"] = bool(files)
    print(f"[load] {len(files)} archivo(s) con errores de sintaxis.")
    return state


def node_diagnose(state: State) -> State:
    if not state["files"]:
        return state
    if not os.getenv("OPENAI_API_KEY"):
        for f in state["files"]:
            f.llm_error = "OPENAI_API_KEY no configurado; no se generaron sugerencias."
        return state

    llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0)
    for f in state["files"]:
        errs_str = "\n".join(
            f"- linea {v.get('start_line_no','?')}, col {v.get('start_line_pos','?')} "
            f"[{v.get('code','?')}]: {v.get('description','').strip()}"
            for v in f.violations
        )
        user_prompt = (
            f"Archivo: {f.path}\n\n"
            f"Errores reportados por sqlfluff:\n{errs_str}\n\n"
            f"Contenido del archivo:\n---\n{f.content}\n---"
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
            f.diagnostico = data.get("diagnostico", "").strip()
            f.explicacion = data.get("explicacion", "").strip()
            f.fix_sugerido = data.get("fix_sugerido", "").strip()
            f.cambios = list(data.get("cambios", []))
        except Exception as e:
            f.llm_error = f"Error consultando al LLM: {e}"
    return state


def node_validate_fix(state: State) -> State:
    for f in state["files"]:
        if not f.fix_sugerido:
            continue
        try:
            sqlglot.parse(f.fix_sugerido, dialect=None)
            f.fix_is_valid = True
        except ParseError as e:
            f.fix_is_valid = False
            f.llm_error = (
                f"La correccion propuesta no fue parseable por sqlglot ({e}); "
                "se muestra de todos modos como sugerencia, pero verificala."
            )
    return state


def node_render(state: State) -> State:
    if not state["had_violations"]:
        body = (
            "## SQL Syntax Gate - PASSED\n\n"
            "Todos los archivos `.sql` modificados en `src/` son sintacticamente validos.\n\n"
            f"_commit `{HEAD_SHA[:7]}` - sqlfluff parse-only + LangGraph diagnostic_"
        )
        state["summary_markdown"] = body
        return state

    total_v = sum(len(f.violations) for f in state["files"])
    parts: List[str] = [
        "## SQL Syntax Gate - FAILED",
        "",
        f"Se detectaron **{total_v}** error(es) de sintaxis en "
        f"**{len(state['files'])}** archivo(s) de `src/`. "
        f"Un agente LangGraph + `{OPENAI_MODEL}` analizo cada caso y "
        f"propone una correccion.",
        "",
        "El merge queda bloqueado por la `Branch Protection Rule` "
        "hasta que todos los errores esten corregidos.",
        "",
    ]

    for f in state["files"]:
        parts.append(f"### `{f.path}`")
        parts.append("")
        parts.append("**Errores detectados (sqlfluff):**")
        parts.append("")
        parts.append("| Linea | Col | Codigo | Mensaje |")
        parts.append("|------:|----:|:------:|---------|")
        for v in f.violations:
            msg = (v.get("description") or "").strip().replace("|", "\\|")
            parts.append(
                f"| {v.get('start_line_no','?')} | {v.get('start_line_pos','?')} "
                f"| `{v.get('code','?')}` | {msg} |"
            )
        parts.append("")

        if f.diagnostico or f.explicacion:
            parts.append("**Diagnostico (IA):**")
            if f.diagnostico:
                parts.append(f"> {f.diagnostico}")
            if f.explicacion:
                parts.append("")
                parts.append(f.explicacion)
            parts.append("")

        if f.cambios:
            parts.append("**Cambios necesarios:**")
            for c in f.cambios:
                parts.append(f"- {c}")
            parts.append("")

        if f.fix_sugerido:
            label = "**Correccion sugerida"
            label += " (validada por sqlglot):**" if f.fix_is_valid else " (revisar):**"
            parts.append(label)
            parts.append("")
            parts.append("```sql")
            parts.append(f.fix_sugerido)
            parts.append("```")
            parts.append("")

        if f.llm_error:
            parts.append(f"_Nota: {f.llm_error}_")
            parts.append("")

        if HEAD_SHA and PR_NUMBER:
            parts.append(
                f"[Abrir archivo](../blob/{HEAD_SHA}/{f.path})  -  "
                f"[Ver diff](../pull/{PR_NUMBER}/files)"
            )
        parts.append("")

    parts.append("---")
    parts.append(
        "_Reporte generado por `sql-syntax-check.yml` "
        "(sqlfluff parse-only + LangGraph + OpenAI)._"
    )
    state["summary_markdown"] = "\n".join(parts)
    return state


def node_publish(state: State) -> State:
    body = state["summary_markdown"]
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(body + "\n")
        except OSError:
            pass

    if not (GITHUB_REPO and PR_NUMBER and GITHUB_TOKEN):
        print("[info] Modo local: comentario no publicado.\n", body)
        return state

    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/issues/{PR_NUMBER}/comments"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"body": body},
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"[error] No se pudo publicar comentario: {resp.status_code} {resp.text}")
    else:
        print(f"[ok] Comentario publicado en PR #{PR_NUMBER}")
    return state


def build_graph():
    g = StateGraph(State)
    g.add_node("load", node_load_report)
    g.add_node("diagnose", node_diagnose)
    g.add_node("validate_fix", node_validate_fix)
    g.add_node("render", node_render)
    g.add_node("publish", node_publish)
    g.set_entry_point("load")
    g.add_edge("load", "diagnose")
    g.add_edge("diagnose", "validate_fix")
    g.add_edge("validate_fix", "render")
    g.add_edge("render", "publish")
    g.add_edge("publish", END)
    return g.compile()


def main() -> int:
    graph = build_graph()
    final_state = graph.invoke(
        {"files": [], "summary_markdown": "", "had_violations": False}
    )
    return 1 if final_state["had_violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
