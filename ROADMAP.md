# NomaData — Development Roadmap

> **Know My Data.**

NomaData is an AI-native, model-agnostic client for conversational Business Intelligence.

This roadmap describes the planned development path from the initial technical foundation to a production-ready AI data workspace.

> **Progress (2026-08-15):**
> - Phase 0 — Foundation ✅ **complete**. Skeleton boots (web → API health), five
>   core interfaces, `pnpm` dev scripts, boundaries enforced by import-linter.
> - Phase 1 — Data Connectivity 🚧. **MySQL + SQL Server** connectors done —
>   connect, schema introspection (tables/columns/PK/FK), column profiling — both
>   behind one `DataSource` interface, configured in `data_sources.json`, with a
>   `/schema` explorer + source switcher. **Target adapted from PostgreSQL →
>   MySQL + SQL Server** (the real data in `test/infra`). Verified live: MySQL
>   124 tables / 1687 cols / 187 FKs; SQL Server 196 tables / 3040 cols / 12 FKs.
>
> **Current focus → Phase 2 (Semantic Intelligence): turn the schema into a
> business semantic model.** Plan: [docs/M2-SEMANTIC.md](docs/M2-SEMANTIC.md)
> (decisions: Postgres storage · AI-first · full Cube).

---

## 1. Roadmap Principles

NomaData will be developed around several principles:

### 1.1 Semantic First

The AI should reason about business concepts rather than directly interpreting raw database schemas.

### 1.2 Model Agnostic

The core system must not depend on a specific LLM provider.

### 1.3 Data Source Agnostic

The architecture should support multiple databases without coupling the agent to a specific database engine.

### 1.4 Trust Before Automation

AI-generated metrics, queries, and insights must be explainable and traceable.

### 1.5 Vertical Slices

Each milestone should produce an end-to-end working capability instead of building isolated infrastructure for a long period.

### 1.6 Start Small

The initial release will focus on PostgreSQL and a small number of visualization types before expanding to more databases and enterprise capabilities.

---

# 2. Development Phases

```text
Phase 0
Foundation
   ↓
Phase 1
Data Connectivity
   ↓
Phase 2
Semantic Intelligence
   ↓
Phase 3
Conversational Query Engine
   ↓
Phase 4
Visualization & Analysis
   ↓
Phase 5
Multi-Provider / Multi-Database
   ↓
Phase 6
Trust, Security & Governance
   ↓
Phase 7
Production Readiness
   ↓
Phase 8
AI-Native Data Workspace
```

---

# Phase 0 — Foundation ✅

## Objective

Establish the repository structure, development environment, core interfaces, and architectural boundaries.

## Deliverables

### Repository

* [x] Define monorepo structure (`apps/api`, `apps/web`, `cube/`, pnpm workspace)
* [x] Add development documentation (M0-FOUNDATION, ARCHITECTURE, GETTING-STARTED)
* [ ] Add contribution guidelines
* [x] Add environment configuration (typed settings + configurable infra creds)
* [x] Add `.env.example`
* [x] Add linting and formatting (ruff, eslint, prettier)
* [x] Add type checking (mypy strict on core, tsc)
* [ ] ~~Add basic CI pipeline~~ — deferred (GitHub Actions skipped; boundaries enforced locally via import-linter)

### Backend

* [x] Initialize FastAPI application
* [x] Define API versioning strategy (`/api/v1`)
* [x] Define configuration management (pydantic-settings)
* [x] Define dependency injection strategy (registry + composition root)
* [x] Define error handling (`core/errors.py`)
* [x] Define logging structure (structlog)
* [x] Define health-check endpoint

### Frontend

* [x] Initialize NomaData client (Next.js 16 + React 19)
* [x] Define application shell
* [x] Define routing (App Router)
* [x] Define basic design system (shadcn `radix-lyra`)
* [x] Define API client layer (`lib/api-client.ts`)
* [ ] Define state management strategy — deferred until real client state exists

### Core Interfaces

Define provider-independent interfaces:

```text
AIProvider           ✓
DataSource           ✓
SemanticModel        ✓
QueryEngine          ✓
Visualization        ✓
```

## Acceptance Criteria

* [x] Client can start locally
* [x] Backend can start locally
* [x] Frontend can communicate with backend
* [ ] ~~CI passes on every pull request~~ — deferred (CI skipped)
* [x] No application component directly depends on a specific LLM provider (enforced by import-linter)

---

# Phase 1 — Data Connectivity 🚧

## Objective

Connect NomaData to a real database and build the foundation for schema discovery.

Database targets (adapted to the data on hand — see `test/infra`):

> **MySQL ✅ · SQL Server ✅ · PostgreSQL (later)**

Full connector priority list (SQL + warehouses, by effort × popularity):
[docs/CONNECTORS.md](docs/CONNECTORS.md).

## 1.1 Database Connection

* [x] Create data source configuration (data_sources.json; encrypted storage in Phase 6)
* [ ] Store connection metadata securely — deferred to Phase 6
* [x] Implement MySQL + SQL Server connectors (PostgreSQL later)
* [x] Implement connection testing
* [x] Implement connection lifecycle (pooling + close)
* [x] Implement connection error handling

Example:

```text
Data Source
├── Name
├── Type
├── Host
├── Port
├── Database
├── Username
└── Credentials
```

## 1.2 Schema Introspection

Discover:

* [x] Tables
* [x] Columns
* [x] Data types
* [x] Primary keys
* [x] Foreign keys
* [ ] Unique constraints
* [ ] Indexes
* [ ] Views

## 1.3 Database Catalog

Create an internal representation:

```text
DataSource
    ↓
Schema
    ↓
Table
    ↓
Column
    ↓
Relationship
```

## 1.4 Data Profiling

Profile selected columns:

* [x] Null percentage
* [x] Distinct values
* [x] Min / max
* [ ] Numeric distribution
* [x] Sample values
* [ ] Date ranges
* [ ] Potential categorical fields

## Acceptance Criteria

Given a supported database (MySQL today), NomaData must be able to:

1. Connect to the database. ✅
2. Discover its schema. ✅
3. Display tables and columns. ✅
4. Display relationships. ✅
5. Provide basic profiling information. ✅

---

# Phase 2 — Semantic Intelligence

## Objective

Transform raw database metadata into a business-oriented semantic model.

This phase is the foundation of reliable AI analytics.

---

## 2.1 Semantic Model

Define:

```text
Entity
Dimension
Measure
Metric
Relationship
Segment
Time Dimension
Business Definition
```

Example:

```text
Entity: Order

Dimensions:
- Status
- Created Date
- Customer

Measures:
- Order Count
- Revenue
- Average Order Value
```

---

## 2.2 AI Schema Analysis

Use an LLM to analyze database metadata and suggest:

* [ ] Entities
* [ ] Dimensions
* [ ] Measures
* [ ] Relationships
* [ ] Time dimensions
* [ ] Potential business metrics
* [ ] Semantic descriptions

The AI must produce structured output.

---

## 2.3 Semantic Review

The AI must not automatically publish business definitions.

Implement:

```text
Generated
    ↓
Review
    ↓
Accept / Modify / Reject
    ↓
Published Semantic Model
```

---

## 2.4 Semantic Model Storage

Persist semantic models independently from the database schema.

Possible representation:

```text
semantic/
├── entities/
├── metrics/
├── dimensions/
└── relationships/
```

The final storage implementation may use PostgreSQL, files, or another persistent representation.

---

## 2.5 Cube Integration

Integrate Cube as the initial semantic/query layer.

Responsibilities:

```text
NomaData Semantic Model
          ↓
        Cube
          ↓
      Database
```

NomaData should treat Cube as an implementation layer rather than exposing Cube-specific concepts throughout the application.

---

## Acceptance Criteria

Given an existing PostgreSQL database:

* [ ] NomaData identifies potential entities.
* [ ] NomaData suggests dimensions and measures.
* [ ] NomaData suggests relationships.
* [ ] NomaData suggests potential metrics.
* [ ] A human can review and modify the suggestions.
* [ ] A semantic model can be published.
* [ ] Cube can execute queries against the published semantic model.

---

# Phase 3 — Conversational Query Engine

## Objective

Enable users to ask business questions using natural language.

This phase introduces the NomaData core loop:

```text
Ask
 ↓
Understand
 ↓
Plan
 ↓
Query
 ↓
Verify
 ↓
Return Result
```

---

## 3.1 AI Provider Abstraction

Implement:

```text
AIProvider
├── OpenAI
├── Anthropic
├── Gemini
└── OpenAI-Compatible
```

Initial implementation:

* [ ] OpenAI-compatible provider
* [ ] Structured output
* [ ] Tool calling
* [ ] Model configuration
* [ ] Provider configuration
* [ ] API key management

---

## 3.2 Agent Runtime

Implement an agent capable of:

* [ ] Understanding user intent
* [ ] Discovering relevant semantic objects
* [ ] Building analytical plans
* [ ] Calling query tools
* [ ] Interpreting query results
* [ ] Producing structured responses

---

## 3.3 Analytical Query Representation

Do not make the LLM directly responsible for arbitrary SQL.

Define an intermediate query representation:

```json
{
  "measures": ["revenue"],
  "dimensions": ["region"],
  "filters": [],
  "time": {
    "dimension": "order_date",
    "range": "this_year"
  }
}
```

The query engine translates this representation into the underlying semantic/query layer.

---

## 3.4 Query Tools

Initial tools:

```text
inspect_schema
inspect_entity
inspect_metric
query_data
```

Later:

```text
compare_period
drill_down
aggregate
export_data
```

---

## 3.5 Conversation

Implement:

* [ ] Conversation creation
* [ ] Message history
* [ ] Context management
* [ ] Follow-up questions
* [ ] Query/result persistence
* [ ] Conversation state

Example:

```text
User:
Show revenue this year.

AI:
[Chart]

User:
Why did it drop in June?

AI:
[Analysis]

User:
Break that down by region.

AI:
[Chart]
```

---

## Acceptance Criteria

A user must be able to:

1. Connect a PostgreSQL database.
2. Publish a semantic model.
3. Ask a natural-language business question.
4. Have NomaData generate an analytical query.
5. Execute the query through the semantic layer.
6. Return real data.
7. Ask a follow-up question using the previous context.

---

# Phase 4 — Visualization & Analysis

## Objective

Transform query results into useful analytical artifacts.

---

## 4.1 Visualization Engine

Initial visualization types:

* [ ] Number
* [ ] Table
* [ ] Line chart
* [ ] Bar chart
* [ ] Pie chart

---

## 4.2 Visualization Selection

The AI should select a visualization type based on query structure.

Example:

```text
Time series
→ Line chart

Category comparison
→ Bar chart

Single metric
→ Number

Raw records
→ Table
```

The AI should return a visualization specification rather than executable frontend code.

---

## 4.3 Insight Generation

Generate analytical summaries from verified query results.

Example:

```text
Revenue increased 18.2% compared with the previous month.

The largest contribution came from the North region,
which accounted for 41% of total revenue.
```

All numerical claims must be derived from the actual query result.

---

## 4.4 Query Explanation

Add:

> **Explain this result**

Display:

```text
Metric
Revenue

Definition
SUM(payments.amount)

Filters
payment.status = SUCCESS

Time Range
2026-08-01 → 2026-08-15

Data Source
production_db
```

---

## 4.5 CSV Export

* [ ] Export current result
* [ ] Preserve column names
* [ ] Preserve raw values
* [ ] Handle large result sets
* [ ] Add download action

---

## Acceptance Criteria

A user asking:

> "Show me monthly revenue for this year."

should receive:

* [ ] Correct data
* [ ] Appropriate chart
* [ ] Supporting table
* [ ] AI explanation
* [ ] Query explanation
* [ ] CSV export

---

# Phase 5 — Multi-Provider & Multi-Database

## Objective

Expand NomaData beyond the initial PostgreSQL + single-provider MVP.

---

## 5.1 AI Providers

Add:

* [ ] Anthropic
* [ ] Google Gemini
* [ ] Additional OpenAI-compatible providers
* [ ] Local models
* [ ] Custom provider configuration

The agent runtime must remain provider-independent.

---

## 5.2 Data Sources

Add connectors progressively:

* [ ] MySQL
* [ ] SQL Server
* [ ] ClickHouse
* [ ] BigQuery
* [ ] Snowflake

Each connector must implement the common `DataSource` interface.

---

## 5.3 Multiple Data Sources

Support:

```text
Workspace
├── Production DB
├── CRM DB
├── Analytics DB
└── Finance DB
```

Implement:

* [ ] Data source selection
* [ ] Semantic model association
* [ ] Data source metadata
* [ ] Query routing

---

## Acceptance Criteria

Users can:

* [ ] Configure multiple AI providers.
* [ ] Switch models without changing application logic.
* [ ] Connect multiple databases.
* [ ] Maintain independent semantic models.
* [ ] Query supported data sources through the same conversational interface.

---

# Phase 6 — Trust, Security & Governance

## Objective

Make NomaData safe enough for enterprise data environments.

---

## 6.1 Credentials

* [ ] Encrypt credentials at rest
* [ ] Avoid exposing database passwords to the LLM
* [ ] Avoid exposing API keys to the LLM
* [ ] Secure secret storage
* [ ] Credential rotation

---

## 6.2 Query Security

Implement:

* [ ] Read-only database users
* [ ] Query validation
* [ ] Query timeout
* [ ] Result size limits
* [ ] Resource limits
* [ ] Dangerous query prevention

---

## 6.3 Permission Model

Introduce:

```text
User
 ↓
Workspace
 ↓
Data Source
 ↓
Semantic Model
 ↓
Entity
 ↓
Query
```

Support:

* [ ] Workspace permissions
* [ ] Data source permissions
* [ ] Semantic model permissions
* [ ] Entity permissions
* [ ] Row-level security where supported

---

## 6.4 Auditability

Record:

* [ ] User question
* [ ] AI provider
* [ ] Model
* [ ] Semantic objects used
* [ ] Query generated
* [ ] Query execution
* [ ] Result metadata
* [ ] Timestamp

---

## 6.5 Explainability

Every analytical result should be traceable to:

```text
Question
 ↓
Intent
 ↓
Semantic Model
 ↓
Query
 ↓
Database
 ↓
Result
 ↓
Insight
```

---

## Acceptance Criteria

A user must be able to understand:

> **Why did NomaData produce this answer?**

Sensitive credentials must never be available to the model.

---

# Phase 7 — Production Readiness

## Objective

Prepare NomaData for real-world deployment.

---

## 7.1 Reliability

* [ ] Retry policies
* [ ] Timeout handling
* [ ] Graceful degradation
* [ ] Database connection pooling
* [ ] Query cancellation
* [ ] Background jobs

---

## 7.2 Performance

* [ ] Query caching
* [ ] Semantic metadata caching
* [ ] Result caching
* [ ] LLM response caching where appropriate
* [ ] Query optimization
* [ ] Pagination

---

## 7.3 Observability

Implement:

* [ ] Structured logs
* [ ] Metrics
* [ ] Tracing
* [ ] LLM latency tracking
* [ ] Query latency tracking
* [ ] Token usage tracking
* [ ] Error monitoring

---

## 7.4 Evaluation

Create an evaluation framework for:

### Semantic Accuracy

```text
Does the AI select the correct metric?
```

### Query Accuracy

```text
Does the generated analytical query represent the question?
```

### Data Accuracy

```text
Does the result match the expected result?
```

### Insight Accuracy

```text
Are generated explanations supported by the data?
```

### Visualization Quality

```text
Is the selected chart appropriate?
```

---

## 7.5 Benchmark Dataset

Create representative business datasets:

```text
E-commerce
Sales
Finance
CRM
Marketing
Inventory
```

Each dataset should contain known questions and expected answers.

---

## Acceptance Criteria

NomaData should have automated evaluations covering:

* [ ] Semantic selection
* [ ] Query planning
* [ ] Query correctness
* [ ] Result correctness
* [ ] Insight correctness

---

# Phase 8 — AI-Native Data Workspace

## Objective

Move beyond individual questions toward a persistent AI-native analytics workspace.

---

## 8.1 Saved Analysis

Allow users to save:

* [ ] Questions
* [ ] Queries
* [ ] Charts
* [ ] Insights
* [ ] Tables

---

## 8.2 Dashboards

Introduce dashboards as collections of analytical artifacts.

```text
Dashboard
├── Revenue
├── Orders
├── Customers
└── Conversion
```

Dashboards should be generated from saved analytical artifacts rather than requiring users to manually configure every visualization.

---

## 8.3 Smart Follow-ups

Allow users to continue exploration naturally:

```text
Revenue dropped.

→ Why?

→ By region?

→ Which products caused it?

→ Compare with last year.

→ Show me the underlying orders.
```

---

## 8.4 Scheduled Analysis

Future capabilities:

* [ ] Scheduled queries
* [ ] Scheduled reports
* [ ] Alerts
* [ ] Anomaly detection
* [ ] Threshold notifications

---

## 8.5 Automated Insights

NomaData should eventually identify meaningful changes without requiring users to ask first.

Examples:

```text
Revenue decreased 14% this week.

Customer churn increased 8%.

Product A sales are 31% below the expected range.
```

---

# 3. Release Strategy

## v0.1 — Proof of Concept

Target:

```text
PostgreSQL
+
One LLM Provider
+
Cube
+
Semantic Model
+
Natural Language Query
+
Table
+
Basic Charts
```

The goal is to prove the core loop.

---

## v0.2 — Conversational BI

Add:

```text
Conversation
+
Follow-up Questions
+
Visualization
+
Insight Generation
+
CSV
+
Query Explanation
```

The goal is to make NomaData genuinely useful for exploration.

---

## v0.3 — Universal Client

Add:

```text
Multiple LLM Providers
+
Multiple Data Sources
+
Provider Switching
+
Data Source Management
```

The goal is to establish the model-agnostic and data-source-agnostic architecture.

---

## v0.4 — Trust & Governance

Add:

```text
Permissions
+
Audit Logs
+
Query Security
+
Credential Security
+
Evaluation Framework
```

The goal is to make NomaData suitable for controlled enterprise environments.

---

## v0.5 — AI Data Workspace

Add:

```text
Saved Analysis
+
Dashboards
+
Advanced Exploration
+
Alerts
+
Automated Insights
```

---

# 4. Definition of Done

A feature is considered complete only when:

* [ ] Backend implementation is complete.
* [ ] Frontend integration is complete where applicable.
* [ ] Error handling exists.
* [ ] Logging exists where appropriate.
* [ ] Tests cover the core behavior.
* [ ] Documentation is updated.
* [ ] Security implications have been reviewed.
* [ ] The feature works through a real end-to-end flow.

For AI-related features, additionally:

* [ ] Structured output is validated.
* [ ] AI failures are handled.
* [ ] Model output cannot bypass application permissions.
* [ ] Results can be traced back to source data.
* [ ] Evaluation cases exist.

---

# 5. Current Priority

The immediate development priority is:

```text
1. Foundation                    ✅ done
       ↓
2. MySQL + SQL Server Connectors  ✅ done
       ↓
3. Schema Introspection          ✅ done
       ↓
4. Semantic Model
       ↓
5. Cube Integration
       ↓
6. AI Provider Abstraction
       ↓
7. Agent Runtime
       ↓
8. Natural Language Query
       ↓
9. Table + Chart
       ↓
10. Follow-up Conversation
```

The first major milestone is:

> **Connect a real PostgreSQL database, understand its semantic model, ask a natural-language business question, execute the query through the semantic layer, and return a trustworthy result with visualization.**

Everything before this milestone exists to enable that loop.

Everything after it exists to make the loop **more accurate, more powerful, more trustworthy, and more scalable.**
