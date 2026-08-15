# NomaData API

FastAPI backend + agent runtime for NomaData.

See [../../docs/M0-FOUNDATION.md](../../docs/M0-FOUNDATION.md) and
[../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) for the design and the
dependency-boundary rules this package must obey.

## Layout

```text
nomadata/
├── main.py            # app factory, router mount
├── config.py          # typed settings (pydantic-settings)
├── logging.py         # structured logging
├── api/v1/            # HTTP layer (health)
├── core/              # interfaces + shared models + errors + registry
│   └── interfaces/    # AIProvider, DataSource, SemanticModel, QueryEngine, Visualization
├── providers/         # AIProvider implementations (empty seam in M0)
├── connectors/        # DataSource implementations (empty seam in M0)
├── semantic/          # semantic artifact home (empty seam in M0)
├── query/             # QueryEngine / Cube adapter (empty seam in M0)
└── agent/             # agent runtime (empty seam in M0)
```

## Develop

```bash
uv sync                                                  # install deps
uv run uvicorn nomadata.main:app --reload --port 8000    # run
uv run pytest                                            # test
uv run ruff check . && uv run lint-imports               # lint + boundaries
uv run mypy nomadata                                     # types
```

Health check: `GET http://localhost:8000/api/v1/health`
