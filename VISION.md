# NomaData — Product & Technical Vision

> **Know My Data.**

NomaData is an AI-native client for understanding and interacting with business data.

It connects the AI model of your choice to the databases you already have, uses a semantic layer to understand what that data means, and provides a conversational interface for exploring, analyzing, and visualizing it.

NomaData is not intended to replace databases, data warehouses, or semantic layers.

It is intended to become the **interface between humans, AI, and business data**.

---

# 1. The Problem

Modern organizations already have large amounts of structured data.

The problem is not necessarily access to data.

The problem is **understanding and using it**.

Business data is distributed across:

* ERP systems
* CRM systems
* E-commerce platforms
* Finance systems
* Marketing platforms
* Operational databases
* Data warehouses

Traditional BI tools solve part of this problem, but they often require users to understand:

* Data models
* Metrics
* Dimensions
* Filters
* SQL
* Dashboard configuration
* BI-specific workflows

This creates a gap between:

```text
Business Question
        ↓
        ?
        ↓
Database
```

NomaData aims to remove that gap.

---

# 2. The Vision

NomaData aims to make business data accessible through natural language while maintaining the semantic correctness and traceability required for serious analytics.

The desired experience is simple:

```text
Connect your data.

Tell NomaData what it means.

Ask questions.

Understand the answer.
```

A user should not need to know how the underlying database is structured to ask:

> "How much revenue did we generate this month?"

or:

> "Why did revenue decrease in June?"

or:

> "Which products caused the decline?"

NomaData should translate these questions into reliable analytical operations against the user's actual data.

---

# 3. The Core Idea

NomaData is built around three independent components:

```text
        AI
         │
         │
         ▼
   ┌─────────────┐
   │  NomaData   │
   │             │
   │ Intelligence│
   │ + Semantic  │
   │ + Analytics │
   └──────┬──────┘
          │
          ▼
        Data
```

### AI

The user chooses the model.

NomaData should not require a single AI provider.

The user may bring:

* OpenAI
* Anthropic
* Google
* OpenAI-compatible providers
* Local models
* Future providers

AI is an interchangeable reasoning engine.

---

### Data

The user chooses where their data lives.

NomaData should work with existing data sources rather than requiring users to migrate their data into a proprietary storage system.

Potential data sources include:

* PostgreSQL
* MySQL
* SQL Server
* ClickHouse
* BigQuery
* Snowflake
* Other analytical databases

---

### NomaData

NomaData sits between AI and data.

Its responsibility is to provide the missing context:

> **What does this data actually mean?**

---

# 4. Semantic Layer Is the Foundation

A database schema describes how data is stored.

A semantic model describes what that data means.

Consider:

```text
orders.total_amount
```

A database understands:

```text
DECIMAL
```

But a business user might understand:

> Revenue

These are not the same thing.

Revenue may actually mean:

```text
SUM(payments.amount)
WHERE payment.status = 'SUCCESS'
```

using:

```text
payments.paid_at
```

rather than:

```text
orders.created_at
```

NomaData therefore treats semantic modeling as a first-class concept.

---

# 5. Semantic Model

The semantic model should represent business concepts such as:

```text
Entities
Dimensions
Measures
Metrics
Relationships
Segments
Time Dimensions
Business Definitions
```

For example:

```text
Entity
└── Order

Dimensions
├── Status
├── Order Date
├── Customer
└── Region

Measures
├── Order Count
├── Revenue
└── Average Order Value
```

A metric should contain enough information for the AI to understand its business meaning.

```text
Revenue

Definition:
Total successfully paid transaction value.

Formula:
SUM(payments.amount)

Filter:
payment.status = SUCCESS

Time:
payments.paid_at
```

This semantic information becomes the contract between the AI and the database.

---

# 6. AI Should Reason About Meaning, Not Tables

A fundamental principle of NomaData is:

> **The AI should reason about business concepts instead of blindly generating SQL from database schemas.**

The naive architecture looks like:

```text
User
 ↓
LLM
 ↓
SQL
 ↓
Database
```

NomaData aims for:

```text
User
 ↓
AI Agent
 ↓
Semantic Model
 ↓
Analytical Query
 ↓
Semantic Query Engine
 ↓
Database
 ↓
Verified Result
```

This separation improves:

* Accuracy
* Consistency
* Explainability
* Security
* Maintainability

Cube is currently considered as a foundation for this semantic/query layer.

---

# 7. The Core Interaction Loop

The fundamental NomaData experience is:

```text
ASK
 ↓
UNDERSTAND
 ↓
PLAN
 ↓
QUERY
 ↓
VERIFY
 ↓
VISUALIZE
 ↓
EXPLAIN
 ↓
FOLLOW UP
```

For example:

### User

> Show me monthly revenue for 2026.

### NomaData

1. Identify `Revenue` metric.
2. Identify the appropriate time dimension.
3. Construct an analytical query.
4. Execute it through the semantic layer.
5. Validate the result.
6. Select an appropriate visualization.
7. Generate an explanation.

The user sees:

```text
Revenue — 2026

        ╭────────────╮
       ╱              ╲
  ────╯                ╰────

Jan Feb Mar Apr May Jun Jul Aug
```

along with the underlying data and a concise explanation.

The user can immediately continue:

> Why did revenue drop in June?

The conversation becomes an analytical workflow rather than a sequence of isolated queries.

---

# 8. Conversation Is the New BI Interface

Traditional BI generally works like:

```text
Database
 ↓
Data Model
 ↓
Dashboard
 ↓
User explores dashboard
```

NomaData aims for:

```text
Database
 ↓
Semantic Model
 ↓
Conversation
 ↓
Analysis
 ↓
Visualization
```

Instead of building a dashboard before asking a question, users can start with the question.

The resulting analysis can later become a persistent artifact.

```text
Conversation
    │
    ├── Question
    ├── Query
    ├── Result
    ├── Visualization
    └── Insight
```

This means dashboards and reports become **outputs of analysis**, rather than prerequisites for analysis.

---

# 9. Analytical Artifacts

NomaData should eventually treat analytical results as reusable objects.

An analysis may contain:

```text
Analysis
├── Question
├── Semantic Query
├── Data Result
├── Visualization
├── Explanation
└── Source Metadata
```

Users should eventually be able to:

* Save an analysis
* Share an analysis
* Convert it into a dashboard
* Schedule it
* Export it
* Use it as a source for another analysis

---

# 10. Trust & Traceability

AI-generated analytics cannot simply be "probably correct".

Every important analytical result should be traceable.

A result should conceptually have the following chain:

```text
User Question
      ↓
Intent
      ↓
Semantic Objects
      ↓
Analytical Query
      ↓
Database
      ↓
Raw Result
      ↓
Visualization
      ↓
AI Explanation
```

A user should be able to ask:

> **Why did NomaData give me this number?**

and receive an understandable explanation.

For example:

```text
Metric:
Revenue

Definition:
Successfully paid transaction value

Data Source:
production_db

Time Range:
2026-08-01 → 2026-08-15

Filter:
payment.status = SUCCESS

Query:
[View Query]
```

This is a fundamental requirement for enterprise adoption.

---

# 11. Security Philosophy

NomaData should never assume that the AI can be trusted with unrestricted access to data.

The AI should operate through controlled capabilities.

Instead of:

```text
LLM
 ↓
Direct Database Access
```

the architecture should be:

```text
LLM
 ↓
Agent Tools
 ↓
Permission Engine
 ↓
Semantic Layer
 ↓
Database
```

The model should not receive:

* Database credentials
* API secrets
* Unrestricted database access

The application controls what the agent is allowed to inspect and execute.

---

# 12. Model Agnosticism

NomaData should treat the LLM as an interchangeable component.

Conceptually:

```text
                  ┌── OpenAI
                  │
                  ├── Anthropic
                  │
NomaData Agent ───┼── Gemini
                  │
                  ├── Local Model
                  │
                  └── Custom Provider
```

The agent should depend on capabilities rather than vendor-specific implementations.

Core capabilities include:

```text
Chat
Tool Calling
Structured Output
Context
```

Provider-specific functionality should remain behind an abstraction layer.

This prevents NomaData from becoming another AI application locked to a single model provider.

---

# 13. Data Source Agnosticism

The same principle applies to databases.

The agent should not know whether the data comes from PostgreSQL or Snowflake.

Instead:

```text
Agent
 ↓
Data Abstraction
 ↓
Semantic Layer
 ↓
Data Source
```

Every connector should implement a consistent interface.

This allows NomaData to expand its database support without changing the core agent architecture.

---

# 14. AI-Native, Not AI-Added

NomaData should not be a traditional BI tool with a chatbot attached to it.

The product should be designed around AI from the beginning.

Traditional approach:

```text
BI Platform
 ├── Dashboard
 ├── Reports
 ├── Filters
 └── + AI Chat
```

NomaData approach:

```text
AI Agent
 ├── Semantic Understanding
 ├── Data Exploration
 ├── Query Planning
 ├── Analysis
 ├── Visualization
 └── Conversation

       ↓

Analytical Workspace
```

The AI is the primary interaction layer.

Charts, tables, dashboards, and reports are outputs of that interaction.

---

# 15. What NomaData Is Not

NomaData is not intended to initially become:

### A Data Warehouse

NomaData should query existing data rather than replace the user's storage infrastructure.

### An ETL Platform

Data transformation may be supported eventually, but it is not the initial focus.

### A Traditional Dashboard Builder

Dashboards are a future output of analysis, not the primary interaction model.

### A Single-Provider AI Wrapper

NomaData should remain model agnostic.

### A Text-to-SQL Toy

Generating SQL is an implementation detail.

The actual product is:

> **Understanding business questions and reliably interacting with business data.**

---

# 16. Long-Term Architecture

The long-term architecture is envisioned as:

```text
                         ┌─────────────────────────┐
                         │       NomaData Client   │
                         │                         │
                         │ Chat                    │
                         │ Charts                  │
                         │ Tables                  │
                         │ Semantic Model          │
                         │ Analytical Workspace    │
                         └────────────┬────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │      AI Orchestrator    │
                         │                         │
                         │ Intent                  │
                         │ Planning                │
                         │ Agent                   │
                         │ Tool Execution          │
                         └────────────┬────────────┘
                                      │
                 ┌────────────────────┼────────────────────┐
                 │                    │                    │
                 ▼                    ▼                    ▼
          AI Provider Layer    Semantic Layer       Permission Layer
                 │                    │                    │
        ┌────────┼────────┐           │                    │
        │        │        │           ▼                    │
      OpenAI  Claude   Gemini       Cube                   │
                                      │                    │
                                      ▼                    ▼
                              ┌─────────────────────────────┐
                              │        Data Sources         │
                              │                             │
                              │ PostgreSQL / MySQL / ...   │
                              └─────────────────────────────┘
```

---

# 17. Long-Term Product Direction

The long-term vision is to evolve NomaData through several stages:

```text
Stage 1
Natural Language Query
        ↓
Stage 2
Conversational BI
        ↓
Stage 3
AI Data Exploration
        ↓
Stage 4
Persistent Analytical Workspace
        ↓
Stage 5
Automated Insights
        ↓
Stage 6
AI-Native Business Intelligence
```

Eventually, a user should not need to ask every question manually.

NomaData could proactively identify:

```text
Revenue decreased 14% this week.

The primary cause appears to be a 31% decline
in Product A sales in the North region.

[Investigate]
```

The system moves from:

> **Answering questions**

to:

> **Helping users understand what is happening in their business.**

---

# 18. The Ultimate Product

The ultimate goal of NomaData is not to create another BI dashboard.

It is to create a universal interface for business data.

```text
              HUMAN
                │
                │ Natural Language
                ▼
          ┌─────────────┐
          │  NomaData   │
          └──────┬──────┘
                 │
        ┌────────┴────────┐
        │                 │
       AI              Semantics
        │                 │
        └────────┬────────┘
                 │
                 ▼
              DATA
```

Users should be able to bring:

```text
Any AI
+
Any Data
+
Their Business Context
```

and get:

```text
Understanding
+
Analysis
+
Visualization
+
Insights
```

without needing to become SQL experts or BI specialists.

---

# 19. The NomaData Principle

> **AI knows how to reason.**
>
> **The semantic layer knows what the data means.**
>
> **The database knows the facts.**
>
> **NomaData connects them.**

That is the foundation of NomaData.

**Know My Data.**
