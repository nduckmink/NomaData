# NomaData — Architecture & Boundary Rules

> Companion to [M0-FOUNDATION.md](./M0-FOUNDATION.md). This document defines the layering
> and the dependency rules that keep NomaData model-agnostic and data-source-agnostic.

---

## 1. The One Rule

> **All dependency arrows point toward `core/`. `core/` depends on nothing inside `nomadata/`.**

Everything else in this document is a consequence of that rule.

```text
            ┌───────────────────────────────┐
            │              api/             │  composition root — wires everything
            └───────────────┬───────────────┘
                            │
     ┌──────────┬───────────┼───────────┬──────────┐
     ▼          ▼           ▼           ▼          ▼
  agent/    providers/  connectors/   query/   semantic/
     │          │           │           │          │
     └──────────┴───────────┴─────┬─────┴──────────┘
                                  ▼
                               core/            ← depends on NOTHING above
```

---

## 2. Layer Responsibilities

| Layer          | Owns                                             | May import                                  |
| -------------- | ------------------------------------------------ | ------------------------------------------- |
| `core/`        | Interfaces + shared Pydantic models + errors     | only `core/`                                |
| `providers/`   | `AIProvider` implementations                     | `core/` + its own vendor SDK                |
| `connectors/`  | `DataSource` implementations                     | `core/` + its own DB driver                 |
| `query/`       | `QueryEngine` (e.g. Cube adapter)                | `core/` + `connectors/` (via registry)      |
| `semantic/`    | Semantic artifact load/publish/resolve           | `core/`                                     |
| `agent/`       | Agent runtime, tools, planning                   | `core/`, and providers/query/semantic via registry |
| `api/`         | HTTP layer, DI, composition                       | everything                                  |

---

## 3. Why These Boundaries Exist

### Model Agnosticism
No module outside `providers/` may import an LLM SDK (`openai`, `anthropic`, …).
The agent depends on the `AIProvider` interface only. Swapping GPT for Claude is a
change confined to `providers/` + configuration.

**Enforced** by an `import-linter` contract in CI, not by convention.

### Data Source Agnosticism
No module outside `connectors/` may import a database driver (`psycopg`, …).
The rest of the system depends on the `DataSource` interface. Adding Snowflake is a
change confined to `connectors/`.

### Trust & Security
The LLM never receives credentials or raw database access. Credentials live in typed
config / secret storage and are injected into connectors and providers at the
composition root (`api/`). Neither `agent/` nor `providers/` ever sees a DB password.

### Semantic First
The semantic model is a **persistent artifact** with a fixed shape in `core/`
(`SemanticGraph`, `MetricDefinition`, …). It is never an ad-hoc prompt string. The LLM
proposes; a human publishes; the artifact is the contract between AI and database.

### No Text-to-SQL
The LLM produces an `AnalyticalQuery` (measures / dimensions / filters / time), never
SQL. `query/` translates that intermediate representation into the underlying engine.

---

## 4. The Registry Pattern

`core/registry.py` is how the composition root wires pluggable implementations without
the core knowing them:

```text
api/ startup
   │
   ├── register AIProvider implementations   (from providers/, chosen by config)
   ├── register DataSource implementations   (from connectors/, chosen by config)
   └── register QueryEngine implementation   (from query/)
        │
        ▼
   agent/ resolves capabilities by interface, never by concrete class
```

This keeps `agent/` free of any `import providers.openai` — it asks the registry for an
`AIProvider` and receives whatever config selected.

---

## 5. Contract Ownership

Shared data models are the contracts between layers and live in `core/`:

```text
Message, ChatResponse, ToolSpec, ProviderCapabilities   # AI boundary
DatabaseCatalog, ColumnProfile, ConnectionStatus        # data boundary
SemanticGraph, MetricDefinition, Entity, Dimension      # semantic boundary
AnalyticalQuery, ExecutionPlan, QueryResult             # query boundary
VisualizationSpec, QueryIntent                          # visualization boundary
```

Changing a contract is a deliberate, reviewed act because it ripples across layers by
design — that is the point of putting them in one place.

---

## 6. What CI Enforces

```text
import-linter contracts:
  - providers/*  must not import  connectors/, query/, semantic/, agent/
  - connectors/* must not import  providers/, query/, semantic/, agent/
  - agent/*      must not import  any vendor SDK
  - core/*       must not import  anything outside core/
  - nothing (except providers/) may import openai/anthropic/google-genai
mypy:
  - strict mode on core/
```

A violation of the boundary rules fails the build. These rules are the architecture;
the code is just their current expression.
