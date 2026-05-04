# SQL Optimizer y analisis de errores SQL

Repositorio plantilla con dos GitHub Actions para análisis de código SQL en Pull Requests:
En este repositorio se encuentran 2 propuestas de herramientas para ingeniería de datos con el fin de reducir imprevistos en ambiente de producción.

1. **`sql-optimizer.yml`** — Github Action que cuando se abre/actualiza un PR contra `develop` y hay actualizaciones en la carpeta `sql/`, un agente LangGraph utilizando el API de OpenAI valida la sintaxis, propone una versión optimizada (sin alterar la lógica) y publica un comentario en la PR con el código optimizado y un log de cambios.
2. **`sql-syntax-check.yml`** — Github Action que al realizar un PR revisa los `.sql` de `src/` con `sqlfluff` (Librería de validacion SQL). Si hay errores de sintaxis el job falla, lo cual bloquea automaticamente el merge, impidiendo así que se realice la carga de codigos erroneos hacia main. Ademas se brinda la solución del error utilizando el API de OpenAI.

NOTA: Se está utilizando agentes en el optimizador para que se puedan brindar detalles de como mejorar el codigo o solucionar problemas. Mientras que se utiliza sqlfluff para detectar errores, ya que no es necesario el uso de tokkens para detectar errores de sintaxis.

## Estructura

```
.
├── .github/
│   └── workflows/
│       ├── sql-optimizer.yml       # Herramienta 1
│       └── sql-syntax-check.yml    # Herramienta 2
├── scripts/
│   ├── sql_optimizer.py            # Pipeline LangGraph
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

Se está utilizando el secreto OPENAI_API_KEY para almacenar el token y reglas para evitar el merge si no pasa la validación de codigo SQL.

A nivel de costos, cada llamada al optimizador consume aproximadamente 2 centavos de dolar, se están utilizando los tokens de OPEN AI que son mas costosos, se podría realizar la misma implementación con tokens mas baratos como los de deep seek.

Precios por cada millon de tokens.
- `OPEN AI` Input: 0,15 - Output: 0,6
- `Anthropic` Input: 0,25 - Output: 1,25
- `DeepSeek` Input: 0,14 - Output: 0,28
