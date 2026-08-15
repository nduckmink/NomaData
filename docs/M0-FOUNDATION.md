# M0 — Foundation (Implementation Plan)

> **Know My Data.**
>
> Phase 0 of the [ROADMAP](../ROADMAP.md). This document is the concrete build plan for the foundation milestone.

---

## 1. Objective

Establish the repository structure, development environment, core interfaces, and architectural boundaries so that a **skeleton runs end-to-end**.

M0 does **not** deliver any real BI capability. It delivers the *shape* of NomaData: a monorepo where the client talks to the API, the API exposes provider-independent interfaces, and everything boots with a single command.

The one user-visible outcome of M0:

```text
Browser (Next.js)
      │  GET /health
      ▼
FastAPI  ──►  returns { status: "ok", version, providers: [], data_sources: [] }
      │
      ▼
Web renders a "System OK" status page
```

If that loop works and the architectural seams are in place, M0 is done.

---

## 2. Guiding Constraints (from VISION / ROADMAP)

These are locked decisions that shape every file created in M0:

1. **Semantic First** — the codebase must have a place for the semantic model as a *persistent artifact*, separate from schema. M0 creates the seam (`semantic/`), not the logic.
2. **Model Agnostic** — no file outside `providers/` may import an LLM SDK. The agent depends on the `AIProvider` interface only.
3. **Data Source Agnostic** — no file outside `connectors/` may import a database driver. Everything else depends on `DataSource`.
4. **Trust > Automation** — the LLM never receives credentials or raw DB access. M0 enforces this structurally: credentials live in config/secrets, never in provider or agent modules.
5. **Vertical Slice** — M0 is itself the first (trivial) vertical slice: web → api → response.

> **Acceptance rule of thumb:** at the end of M0, swapping the LLM provider or the database engine must be a change confined to a single directory.

---

## 3. Tech Stack (M0)

| Component      | Choice                        | Notes                                        |
| -------------- | ----------------------------- | -------------------------------------------- |
| Client         | Next.js (App Router) + React  | TypeScript, Tailwind                         |
| Backend        | Python 3.11+ · FastAPI        | `uv` for dependency + venv management        |
| Agent runtime  | Python (same process as API)  | Split into its own service only if needed    |
| Semantic layer | Cube                          | Container only in M0; no models yet          |
| Database       | PostgreSQL 16                 | Two roles: app metadata DB + target data DB  |
| LLM            | OpenAI-compatible interface   | Interface only in M0; no live calls          |
| Visualization  | ECharts                       | Dependency wired; no charts rendered yet     |
| Orchestration  | Docker Compose                | One command boots the whole stack            |
| Config         | `pydantic-settings` + `.env`  | Single typed settings object                 |

Deferred to later phases: Auth, cache/Redis, RBAC, additional providers/connectors.

---

## 4. Monorepo Structure

```text
NomaData/
├── apps/
│   ├── web/                      # Next.js client
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   └── page.tsx          # System status page (calls /health)
│   │   ├── lib/
│   │   │   └── api-client.ts     # typed fetch wrapper — single API entry point
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   └── next.config.ts
│   │
│   └── api/                      # FastAPI backend + agent
│       ├── nomadata/
│       │   ├── main.py           # app factory, router mount, health
│       │   ├── config.py         # typed settings (pydantic-settings)
│       │   ├── logging.py        # structured logging setup
│       │   ├── api/
│       │   │   └── v1/
│       │   │       ├── router.py
│       │   │       └── health.py
│       │   ├── core/
│       │   │   ├── interfaces/   # provider-independent contracts
│       │   │   │   ├── ai_provider.py
│       │   │   │   ├── data_source.py
│       │   │   │   ├── semantic_model.py
│       │   │   │   ├── query_engine.py
│       │   │   │   └── visualization.py
│       │   │   ├── errors.py     # error hierarchy
│       │   │   └── registry.py   # provider/connector registration
│       │   ├── providers/        # AIProvider implementations (none live in M0)
│       │   │   └── __init__.py
│       │   ├── connectors/       # DataSource implementations (stub in M0)
│       │   │   └── __init__.py
│       │   ├── semantic/         # semantic artifact home (empty seam in M0)
│       │   │   └── __init__.py
│       │   ├── query/            # query engine (Cube adapter later)
│       │   │   └── __init__.py
│       │   └── agent/            # agent runtime (empty seam in M0)
│       │       └── __init__.py
│       ├── tests/
│       │   └── test_health.py
│       ├── pyproject.toml
│       └── Dockerfile
│
├── cube/                         # Cube semantic/query layer
│   ├── cube.js
│   ├── model/                    # generated schema goes here later
│   └── .env.example
│
├── docs/
│   ├── M0-FOUNDATION.md          # this file
│   └── ARCHITECTURE.md           # boundary rules (added in M0)
│
├── docker-compose.yml            # postgres · api · web · cube
├── .env.example
├── .editorconfig
├── .gitignore
├── Makefile                      # dev entrypoints (up / down / fmt / lint / test)
├── README.md
├── VISION.md
└── ROADMAP.md
```

**Rationale for the split:**

- `apps/` = deployable units. `web` and `api` are the only two things that "run".
- `core/interfaces/` = the model-agnostic and data-source-agnostic contracts. This is the most important directory in the whole project — everything else depends on it, and it depends on nothing.
- `providers/`, `connectors/`, `query/`, `semantic/`, `agent/` = pluggable implementation zones. Each is a seam that later phases fill in without touching the core.

---

## 5. Core Interfaces (the heart of M0)

M0 defines these as **abstract contracts with typed models** but ships no live implementation (except stubs used to prove boot). The signatures below are the spec; async throughout.

### 5.1 `AIProvider`

```python
class AIProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[Message], **opts) -> ChatResponse: ...

    @abstractmethod
    async def generate_structured(
        self, messages: list[Message], schema: type[T], **opts
    ) -> T: ...

    @abstractmethod
    async def tool_call(
        self, messages: list[Message], tools: list[ToolSpec], **opts
    ) -> ToolCallResponse: ...

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...
```

The agent depends on this and **never** on a vendor SDK. Credentials are injected at construction from config — the interface exposes capabilities, not keys.

### 5.2 `DataSource`

```python
class DataSource(ABC):
    @abstractmethod
    async def test_connection(self) -> ConnectionStatus: ...

    @abstractmethod
    async def inspect_schema(self) -> DatabaseCatalog: ...

    @abstractmethod
    async def profile(self, target: ProfileTarget) -> ColumnProfile: ...

    @abstractmethod
    async def execute(self, plan: ExecutionPlan) -> QueryResult: ...
```

Only `connectors/` may import a DB driver. The agent sees `DataSource`, never `psycopg`.

### 5.3 `SemanticModel`

```python
class SemanticModel(ABC):
    @abstractmethod
    async def load(self, source_id: str) -> SemanticGraph: ...

    @abstractmethod
    async def publish(self, graph: SemanticGraph) -> PublishResult: ...

    @abstractmethod
    async def resolve_metric(self, name: str) -> MetricDefinition: ...
```

The semantic model is a **persistent artifact**, not a prompt. M0 fixes the shape of `SemanticGraph`, `MetricDefinition`, etc. so later phases have a target.

### 5.4 `QueryEngine`

```python
class QueryEngine(ABC):
    @abstractmethod
    async def plan(self, query: AnalyticalQuery) -> ExecutionPlan: ...

    @abstractmethod
    async def run(self, query: AnalyticalQuery) -> QueryResult: ...
```

`AnalyticalQuery` is the intermediate representation (measures / dimensions / filters / time) — **the LLM produces this, never SQL**. Cube is one implementation behind this interface.

### 5.5 `Visualization`

```python
class VisualizationSelector(ABC):
    @abstractmethod
    def select(self, result: QueryResult, intent: QueryIntent) -> VisualizationSpec: ...
```

Returns a **spec** (`{ type, x, y, series }`), never frontend code. The client renders it with ECharts.

> All shared data models (`Message`, `AnalyticalQuery`, `SemanticGraph`, `QueryResult`, `VisualizationSpec`, …) live as Pydantic models under `core/` and are the contract between layers.

---

## 6. Architectural Boundary Rules

Documented in `docs/ARCHITECTURE.md` and enforced by review (and, ideally, an import-linter rule in CI):

```text
agent/       → may import: core/, providers (via registry), query, semantic
providers/   → may import: core/ + its own vendor SDK          (nothing else in nomadata/)
connectors/  → may import: core/ + its own DB driver           (nothing else in nomadata/)
query/       → may import: core/ + connectors (via registry)
semantic/    → may import: core/
core/        → imports NOTHING from nomadata/ except within core/
api/         → may import: everything (composition root)
```

The dependency arrow always points **toward `core/`**. Violations are the one thing M0 review blocks hard.

---

## 7. Developer Environment & Tooling

| Concern        | Tool                                   |
| -------------- | -------------------------------------- |
| Python deps    | `uv` (lockfile committed)              |
| Python lint    | `ruff`                                 |
| Python format  | `ruff format`                          |
| Python types   | `mypy` (strict on `core/`)             |
| Python tests   | `pytest` + `pytest-asyncio`            |
| Import rules   | `import-linter` contracts              |
| JS/TS deps     | `pnpm`                                 |
| TS lint/format | `eslint` + `prettier`                  |
| TS types       | `tsc --noEmit`                         |
| Pre-commit     | `pre-commit` (ruff, prettier, eof)     |
| CI             | GitHub Actions                         |

**One-command dev loop** via `Makefile` / Compose:

```text
make up      # docker compose up: postgres + api + web + cube
make test    # pytest + web unit tests
make lint    # ruff + eslint + import-linter
make fmt     # format everything
```

---

## 8. CI Pipeline (GitHub Actions)

On every pull request:

```text
lint      → ruff check · eslint · import-linter contracts
typecheck → mypy (core strict) · tsc --noEmit
test      → pytest · web unit tests
build     → docker build api · next build
```

CI must be green before merge. Acceptance criterion: **no application component imports a vendor LLM SDK outside `providers/`** — enforced by an import-linter contract, not just convention.

---

## 9. End-to-End Skeleton Flow (the M0 demo)

```text
1. `make up`
2. Postgres, API, Web, Cube containers start.
3. API exposes GET /api/v1/health:
     {
       "status": "ok",
       "version": "0.0.1",
       "checks": { "database": "ok", "cube": "reachable" },
       "providers": [],        # registry is empty but wired
       "data_sources": []      # registry is empty but wired
     }
4. Web loads at localhost:3000, calls /health through lib/api-client.ts,
   renders a "System OK" status card showing version + component health.
```

No LLM call, no schema read, no query — just proof the seams connect and the stack boots as one system.

---

## 10. Deliverables Checklist

### Repository
- [ ] Monorepo structure created (`apps/`, `cube/`, `docs/`)
- [ ] `.gitignore`, `.editorconfig`, `.env.example`
- [ ] `Makefile` with `up / down / fmt / lint / test`
- [ ] `docs/ARCHITECTURE.md` with boundary rules
- [ ] `docker-compose.yml` (postgres · api · web · cube)

### Backend (`apps/api`)
- [ ] FastAPI app factory + `main.py`
- [ ] Typed settings via `pydantic-settings`
- [ ] Structured logging
- [ ] API versioning (`/api/v1`) + `router.py`
- [ ] `GET /api/v1/health` with component checks
- [ ] Error hierarchy (`core/errors.py`)
- [ ] Provider/connector `registry.py`
- [ ] All five core interfaces defined with Pydantic models
- [ ] `Dockerfile`
- [ ] `test_health.py` passing

### Frontend (`apps/web`)
- [ ] Next.js app shell + routing
- [ ] Tailwind design-system baseline
- [ ] `lib/api-client.ts` typed API layer
- [ ] System status page consuming `/health`
- [ ] ECharts dependency wired (unused placeholder)

### Core Interfaces
- [ ] `AIProvider`, `DataSource`, `SemanticModel`, `QueryEngine`, `VisualizationSelector`

### Tooling / CI
- [ ] `uv` + `ruff` + `mypy` + `pytest` configured
- [ ] `pnpm` + `eslint` + `prettier` + `tsc` configured
- [ ] `import-linter` contracts for boundary rules
- [ ] `pre-commit` hooks
- [ ] GitHub Actions CI (lint · typecheck · test · build)

---

## 11. Acceptance Criteria (Phase 0)

- [ ] Client can start locally (`localhost:3000`)
- [ ] Backend can start locally (`localhost:8000`)
- [ ] Frontend successfully calls the backend `/health` endpoint end-to-end
- [ ] `make up` boots the full stack with one command
- [ ] CI passes on every pull request
- [ ] **No application component directly depends on a specific LLM provider** (enforced by import-linter)
- [ ] Swapping the target database engine would be confined to `connectors/`
- [ ] All five core interfaces exist and are imported only through the registry / `core`

---

## 12. Explicitly Out of Scope for M0

```text
✗ Real database connection / introspection   → M1
✗ Data profiling                             → M1
✗ Semantic suggestions / editor              → M2
✗ Cube models / real queries                 → M2/M3
✗ Live LLM calls / agent reasoning           → M3
✗ Charts rendered from real data             → M4
✗ Auth, RBAC, caching                        → M6/M7
```

M0 builds the stage. M1 puts the first real actor on it: **connect a PostgreSQL database and introspect its schema.**

---

## 13. Exit → Next Milestone

When every box in §10 and §11 is checked, NomaData has a booting, model-agnostic, data-source-agnostic skeleton with clean seams. The next vertical slice begins:

> **M1 — Data Connectivity:** connect a real PostgreSQL database, introspect its schema, and display tables, columns, and relationships.
