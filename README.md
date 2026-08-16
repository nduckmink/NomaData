# NomaData

**Know My Data.**

NomaData is a model-agnostic AI client for conversational Business Intelligence.

Connect your own AI model, connect your databases, define your business semantics, and simply ask questions about your data.

NomaData turns natural language into real-time data analysis, interactive visualizations, and raw data — without requiring users to learn SQL or build traditional BI reports.

## Vision

Traditional BI tools require users to understand dashboards, metrics, filters, and data models before they can get answers.

NomaData takes a different approach:

> **Connect your data. Tell NomaData what it means. Ask anything.**

The goal is to create an AI-native interface between people and their business data.

```text
        Any AI Provider
              │
              ▼
      ┌───────────────┐
      │   NomaData    │
      │               │
      │ AI Agent      │
      │ Semantic Layer│
      │ BI Engine     │
      └───────┬───────┘
              │
              ▼
        Your Data
```

## Core Concepts

### Model Agnostic

Use the AI provider you prefer.

* OpenAI
* Anthropic
* Google
* OpenAI-compatible APIs
* Local models
* Other providers

Your data and semantic models should not be locked to a specific AI provider.

### Connect Your Data

Connect one or multiple data sources and let NomaData discover the existing schema.

Initial targets may include:

* PostgreSQL
* MySQL
* SQL Server
* ClickHouse
* BigQuery
* Snowflake

### Semantic Layer

Raw database schemas are not enough for reliable business analysis.

NomaData uses a semantic layer to describe:

* Entities
* Dimensions
* Measures
* Metrics
* Relationships
* Business definitions
* Time dimensions

Cube is currently explored as the foundation for the semantic layer.

### Conversational BI

Instead of manually building reports:

```text
"Show me monthly revenue for the last 6 months."
```

NomaData can understand the intent, query the appropriate data, and present the result as:

* Tables
* Charts
* Metrics
* Insights
* Raw data
* CSV exports

Follow-up questions should work naturally:

```text
"Why did revenue drop in June?"

"Compare it with last year."

"Show me the top 10 products."

"Break that down by region."
```

## Example

```text
User:
    Show me revenue by month for 2026.

NomaData:
    → Understand intent
    → Select semantic metrics
    → Generate analytical query
    → Execute against the database
    → Generate visualization
    → Explain the result

Result:
    📈 Revenue chart
    📊 Data table
    💡 AI-generated insights
    ↓
    Export CSV
```

## Architecture

The initial architecture is centered around four layers:

```text
┌─────────────────────────────────────────┐
│              NomaData Client            │
│                                         │
│   Chat · Charts · Tables · Insights     │
└────────────────────┬────────────────────┘
                     │
┌────────────────────▼────────────────────┐
│              AI Orchestrator            │
│                                         │
│ Intent · Planning · Agent · Tools       │
└────────────────────┬────────────────────┘
                     │
┌────────────────────▼────────────────────┐
│             Semantic Layer              │
│                                         │
│ Metrics · Dimensions · Entities         │
│ Relationships · Business Context        │
└────────────────────┬────────────────────┘
                     │
┌────────────────────▼────────────────────┐
│               Data Sources              │
│                                         │
│ PostgreSQL · MySQL · BigQuery · ...     │
└─────────────────────────────────────────┘
```

## Development

Everything runs through `pnpm` scripts — no `make` required.

```bash
cp .env.example .env
pnpm setup        # install web + api deps (pnpm + uv)
pnpm infra        # postgres + cube in Docker (background)
pnpm api:dev      # terminal A — API with hot reload   →  :8000
pnpm web:dev      # terminal B — web with HMR           →  :3000
```

Or boot the whole stack in Docker: `pnpm up`.

> Requires **pnpm** and **uv** on your PATH (see the guide for install steps).

**Full setup, commands, and troubleshooting → [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md).**

See also: [Vision](VISION.md) · [Roadmap](ROADMAP.md) · [Architecture](docs/ARCHITECTURE.md).

## Status

🚧 **Early development**

NomaData is currently an experimental project.

The architecture, APIs, semantic modeling approach, and supported data sources are subject to change.

Current focus:

* [ ] AI provider configuration
* [ ] Database connection management
* [ ] Schema introspection
* [ ] Semantic model generation
* [ ] Metric definition
* [ ] Natural language data queries
* [ ] Query execution
* [ ] Interactive charts
* [ ] CSV export
* [ ] Conversation history
* [ ] Multi-database support

## Philosophy

NomaData is built around a few principles:

**Bring Your Own AI**

Your AI provider should be a configuration, not a platform lock-in.

**Semantic First**

AI should reason about business concepts, not blindly generate SQL from raw database schemas.

**Data Stays Yours**

NomaData should operate on your existing data rather than requiring you to migrate everything into another analytics platform.

**Ask, Don't Build**

Business users should be able to explore data through conversation instead of manually constructing dashboards for every question.

## Roadmap

The long-term vision is to evolve NomaData from a conversational query interface into a complete AI-native data workspace.

```text
Database
    ↓
Semantic Model
    ↓
AI Analysis
    ↓
Conversation
    ↓
Charts / Tables
    ↓
Saved Analysis
    ↓
Dashboards
    ↓
Alerts / Reports / Automation
```

## License

[PolyForm Noncommercial License 1.0.0](LICENSE) — SPDX `PolyForm-Noncommercial-1.0.0`.

> Required Notice: Copyright Nguyễn Đức Minh (https://github.com/nduckmink/NomaData)

You may use, modify and redistribute NomaData freely for **any noncommercial
purpose**, including personal projects, study and research. Use by charities,
schools, public research bodies, and government institutions counts as
noncommercial regardless of how they are funded.

**Commercial use is not granted by this license.** That includes running
NomaData as part of a paid product or service, or using it internally to
operate a for-profit business. If you need that, ask the copyright holder for a
separate commercial license.

This is a source-available license, not an OSI-approved open source one — tools
that check for OSI licenses will flag it, and that is expected.

---

**NomaData**

> **Know My Data.**
