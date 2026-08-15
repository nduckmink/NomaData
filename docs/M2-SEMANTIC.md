# M2 — Semantic Intelligence (Implementation Plan)

> Phase 2 of the [ROADMAP](../ROADMAP.md). Turn a discovered schema into a
> reviewed, **published semantic model** — the business-meaning layer the AI
> reasons over instead of raw tables.

**Core principle (from [VISION](../VISION.md)):** the AI *proposes*, a human
*publishes*. The semantic model is a **persistent artifact**, not a prompt.
Semantic Model > Prompt Engineering.

---

## 1. Locked decisions

| Decision | Choice |
| -------- | ------ |
| Where the semantic model lives | **App PostgreSQL** (NomaData's own metadata DB, versioned rows) |
| When AI suggestions arrive | **AI-first** — build a minimal `AIProvider` now; heuristic stays as a no-key fallback |
| Cube scope in Phase 2 | **Full** — generate Cube schema *and* execute queries through Cube against the data source |

The app metadata Postgres already exists in `docker-compose` (`pnpm infra`).
The AI provider uses the OpenAI-compatible config already in `.env`
(OpenRouter/DeepSeek) — a real `NOMADATA_AI_API_KEY` is required for the AI
slice (M2.2).

---

## 2. The semantic model (recap)

Already modeled in `core/models.py`; extend as needed:

```text
SemanticGraph
├── Entity          (business object ← a table with a PK)
│   ├── Dimension   (attribute to slice by ← categorical / date column)
│   └── Measure     (aggregatable value ← numeric column + SUM/AVG/COUNT)
├── MetricDefinition(named business number: definition + formula + filters + time)
├── Relationship    (← foreign key)
└── (add) TimeDimension, Segment, business descriptions
```

A metric carries enough meaning for the AI to trust it:

```text
Revenue
  definition: Total successfully paid order amount
  formula:    SUM(payments.amount)
  filters:    payment.status = SUCCESS
  time:       payments.paid_at
```

---

## 3. Architecture additions

```text
apps/api/nomadata/
├── storage/                 # NEW — NomaData's own persistence (app Postgres)
│   ├── database.py          #   asyncpg pool + schema init (CREATE TABLE IF NOT EXISTS)
│   └── semantic_repo.py     #   save draft / publish / load semantic graphs (JSONB, versioned)
├── providers/
│   └── openai_compatible.py # NEW — AIProvider via httpx (OpenRouter/DeepSeek)
├── semantic/
│   ├── service.py           # NEW — SemanticModel impl (load/publish/resolve_metric) over storage
│   └── suggester.py         # NEW — build a draft SemanticGraph from a DatabaseCatalog
│       #                        (heuristic baseline + AI enrichment)
├── query/
│   └── cube.py              # NEW — QueryEngine: generate Cube schema + run queries
└── api/v1/
    └── semantic.py          # NEW — draft / review / publish / query endpoints
cube/model/                  # generated Cube schema written here
```

**Boundary update:** `storage/` may import the app DB driver (`asyncpg`). The
"drivers only in connectors" rule is really "*data-source* drivers only in
connectors" — `storage/` is app persistence, a separate axis. `semantic/`
stays driver-free by depending on `storage/`, not the driver. import-linter
contracts updated accordingly.

---

## 4. Vertical slices

### M2.1 — App storage + semantic persistence  *(no AI, no Cube)*
- `storage/database.py`: asyncpg pool to `NOMADATA_DATABASE_URL`; init a
  `semantic_models` table (`id, source_id, version, status, graph jsonb, created_at`).
- `storage/semantic_repo.py`: `save_draft`, `publish`, `get_published`,
  `get_latest`, `list_versions`.
- `semantic/service.py`: implement `SemanticModel` (load/publish/resolve_metric).
- Wire lifespan: open/close the app DB pool; register the semantic service.
- API: `GET/PUT /api/v1/datasources/{name}/semantic` (draft), `POST …/publish`.
- **Acceptance:** a semantic graph can be saved, published, reloaded, versioned.

### M2.2 — AI provider + suggester  *(AI-first)*
- `providers/openai_compatible.py`: implement `AIProvider.chat` /
  `generate_structured` / `tool_call` via httpx against the OpenAI-compatible
  endpoint; register from config.
- `semantic/suggester.py`: from a `DatabaseCatalog` (+ profiling), produce a
  draft `SemanticGraph`. Heuristic baseline (entities=tables, rels=FKs,
  measures=numeric, dims=categorical/date, metric candidates); **AI enrichment**
  via `generate_structured` (business names, descriptions, fact/dimension calls,
  metric definitions). AI output is a *suggestion* — never auto-published.
- API: `POST …/semantic/suggest` → draft graph with provenance ("ai" | "heuristic").
- **Acceptance:** given a connected DB, NomaData suggests a reviewable model;
  works (heuristic only) even with no AI key.

### M2.3 — Semantic editor UI + review/publish
- Web `/semantic`: view draft/published per source; edit entity/dimension/
  measure/metric names & definitions; Accept / Modify / Reject AI suggestions;
  Publish. Show suggestion provenance and diffs vs published.
- **Acceptance:** a human can review, modify, and publish a model in the UI.

### M2.4 — Cube integration (full)
- `query/cube.py`: generate Cube model files (cubes: measures, dimensions,
  joins) from the published `SemanticGraph`; write to `cube/model/`.
- Configure Cube to reach the **data source** (MySQL/SQL Server), not just the
  app Postgres — the main config wrinkle. Point Cube at the published source.
- `QueryEngine.run(AnalyticalQuery)` → Cube REST API → results.
- API: `POST …/query` with an `AnalyticalQuery` → `QueryResult`.
- **Acceptance:** Cube executes a query against the published semantic model and
  returns real data — proving the model is queryable end-to-end.

---

## 5. Risks & mitigations

- **Cube ↔ MySQL/SQL Server config** (hardest): Cube must connect to the scp
  source with its own creds. Mitigation: drive Cube's DB config from
  `data_sources.json` (the published source); start with one source.
- **Structured output from DeepSeek/OpenRouter**: not all models honor JSON
  schema strictly. Mitigation: use JSON mode + schema-in-prompt, validate with
  Pydantic, retry on invalid; heuristic fallback if AI unavailable.
- **App DB migrations**: M2 uses `CREATE TABLE IF NOT EXISTS` (no Alembic yet);
  add real migrations before multi-env prod.
- **AI key**: required for M2.2 AI path — the heuristic baseline keeps the loop
  working without one.

---

## 6. Acceptance criteria (Phase 2)

- [x] NomaData identifies potential entities, dimensions, measures, relationships, metrics
      — M2.2 `POST …/semantic/suggest` (heuristic baseline + optional AI enrichment)
- [ ] A human can review, modify, accept/reject suggestions
- [ ] A semantic model can be published and reloaded (persisted in Postgres, versioned)
- [ ] Cube executes queries against the published semantic model
- [ ] AI output can never bypass the human publish step

---

## 7. Order of work

```text
M2.1 storage + persistence
      ↓
M2.2 AI provider + suggester
      ↓
M2.3 semantic editor UI
      ↓
M2.4 Cube integration (full)
```

Each slice ships independently green (ruff · mypy · import-linter · pytest ·
web build) and is verified against the real SCP databases.
