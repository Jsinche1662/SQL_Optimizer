# SQL Optimizer & Syntax Gate

Repositorio plantilla con dos GitHub Actions para análisis de código SQL en Pull Requests:

1. **`sql-optimizer.yml`** — Cuando se abre/actualiza una PR contra `develop` y hay cambios en `sql/**.sql`, un agente LangGraph + OpenAI valida la sintaxis, propone una versión optimizada (sin alterar la lógica) y publica un comentario en la PR con el código optimizado y un log de cambios.
2. **`sql-syntax-check.yml`** — En toda PR (a cualquier rama) revisa los `.sql` de `src/` con `sqlfluff`. Si hay errores de sintaxis el job falla, lo cual permite usarlo como **Required Status Check** en una **Branch Protection Rule** para bloquear el merge.

## Estructura

```
.
├── .github/
│   └── workflows/
│       ├── sql-optimizer.yml       # Herramienta 1
│       └── sql-syntax-check.yml    # Herramienta 2
├── scripts/
│   ├── sql_optimizer.py            # Pipeline LangGraph + OpenAI
│   └── requirements.txt
├── sql/                            # Queries que pasan por el optimizador
│   ├── reporte_ventas.sql
│   └── top_productos.sql
├── src/                            # SQL "de producción" — gate de sintaxis
│   ├── schema_clientes.sql
│   └── seed_productos.sql
├── tests/
│   └── test_sql_optimizer.py
├── .sqlfluff                       # Config del linter
├── .gitignore
└── README.md
```

## Configuración inicial (una sola vez)

### 1. Crear el repo y subir estos archivos

```bash
cd SQL_optimizer
git init
git add .
git commit -m "chore: bootstrap SQL optimizer + syntax gate"
git branch -M main
git remote add origin https://github.com/<tu-usuario>/<tu-repo>.git
git push -u origin main
git checkout -b develop
git push -u origin develop
```

### 2. Configurar el secret de OpenAI

En tu repo en GitHub: **Settings → Secrets and variables → Actions → New repository secret**

| Nombre | Valor |
|---|---|
| `OPENAI_API_KEY` | tu token `sk-...` de OpenAI |

Opcionalmente puedes definir variables (no secretos) en la misma pantalla, pestaña **Variables**:

| Nombre | Default | Descripción |
|---|---|---|
| `OPENAI_MODEL` | `gpt-4o-mini` | Modelo a usar (ej. `gpt-4o`, `gpt-4-turbo`) |
| `SQL_DIALECT`  | `ansi`        | Dialecto que usa sqlglot/sqlfluff |

`GITHUB_TOKEN` es automático, no hay que crearlo.

### 3. Activar la Branch Protection Rule (bloqueo de merge)

En **Settings → Branches → Add branch ruleset** (o "Branch protection rule" clásico):

1. Branch name pattern: `develop` (y/o `main`).
2. Marcar **Require a pull request before merging**.
3. Marcar **Require status checks to pass before merging**.
4. En "Status checks that are required", buscar y agregar:
   - `sqlfluff syntax check` (es el `name` del job de `sql-syntax-check.yml`).
5. Guardar.

A partir de ahí, cualquier PR con un error de sintaxis en `src/**.sql` quedará bloqueada hasta que se corrija.

## Cómo se comporta cada herramienta

### Herramienta 1 — `sql-optimizer.yml`

- **Trigger:** PR `opened` / `synchronize` contra `develop` con cambios en `sql/**.sql`.
- **Pipeline LangGraph** (`scripts/sql_optimizer.py`):
  1. `load_files` → trae los `.sql` cambiados vía GitHub API.
  2. `validate_input` → `sqlglot.parse(dialect=ansi)`.
  3. `optimize` → llama a OpenAI con un prompt estricto que devuelve JSON `{sql_optimizado, cambios, explicacion}`.
  4. `validate_output` → re-parsea la salida; si no es válida, descarta el cambio (no rompe tu lógica).
  5. `build_diff` → unified diff entre original y optimizado.
  6. `render_comment` → arma Markdown con cambios + diff colapsable.
  7. `publish` → `POST /repos/:owner/:repo/issues/:n/comments`.
- **Salida:** un comentario en la PR como este:

  ```
  ## SQL Optimizer Report
  ### `sql/reporte_ventas.sql`
  Sintaxis del SQL original: VALIDA

  Cambios sugeridos:
  - Reemplaza la subquery por un INNER JOIN
  - Especifica las columnas en lugar de SELECT *

  SQL optimizado:
  ```sql
  SELECT v.id, v.fecha, v.monto, c.nombre
  FROM ventas v
  INNER JOIN clientes c ON c.id = v.cliente_id
  WHERE v.fecha >= '2025-01-01'
    AND c.pais = 'PE'
  ORDER BY v.fecha DESC;
  ```
  ```
- **Importante:** este job **no** falla la PR. Es informativo; tú decides si aplicas los cambios.

### Herramienta 2 — `sql-syntax-check.yml`

- **Trigger:** cualquier PR con cambios en `src/**.sql`.
- **Lógica:**
  1. Detecta los `.sql` modificados con `git diff` entre `base` y `head`.
  2. Corre `sqlfluff lint --format github-annotation-native` solo sobre esos archivos.
  3. `.sqlfluff` está configurado con `exclude_rules = all` → solo reportará errores de **parseo / sintaxis** (no estilo).
  4. Si `sqlfluff` encuentra un error → exit code ≠ 0 → job falla → PR bloqueada por la Branch Protection Rule.
- **Bonus:** si falla, deja un comentario en la PR explicando el bloqueo.

## Probar localmente

```bash
# 1) Test del optimizer (no llama a OpenAI)
python -m pip install -r scripts/requirements.txt
python tests/test_sql_optimizer.py

# 2) Linter sobre src/ como lo hace la action
pip install "sqlfluff>=3.0.0"
sqlfluff lint src/ --dialect ansi
```

## Personalizaciones frecuentes

| Quiero... | Dónde tocar |
|---|---|
| Cambiar el dialecto a Postgres | `vars.SQL_DIALECT = postgres` y `dialect = postgres` en `.sqlfluff` |
| Disparar el optimizador en otra rama | `branches: [main]` en `sql-optimizer.yml` |
| Cambiar la carpeta analizada | env `TARGET_FOLDER` y `paths:` del workflow |
| Forzar `gpt-4o` | `vars.OPENAI_MODEL = gpt-4o` |
| Activar reglas de estilo en el gate | quitar `exclude_rules = all` en `.sqlfluff` |

## Costos y latencia

- El optimizador hace **1 llamada por archivo SQL cambiado**. Con `gpt-4o-mini` cada llamada ronda los 2–4 ¢ USD.
- El gate de sintaxis no usa OpenAI (es 100 % `sqlfluff`).

## Seguridad

- `OPENAI_API_KEY` se inyecta solo en el step que la necesita.
- El workflow usa `permissions:` mínimas (`pull-requests: write`).
- El SQL nunca se modifica en el repo automáticamente: el optimizador solo comenta.
- `concurrency:` cancela ejecuciones obsoletas para evitar race conditions de comentarios.
