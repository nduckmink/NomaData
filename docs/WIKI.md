# NomaData — Concepts and Settled Decisions

> What the pieces are, how a question becomes a number, and which choices are
> already made — with the reason, so nobody has to rediscover it.
>
> Companion to [ARCHITECTURE.md](./ARCHITECTURE.md), which covers layering and
> dependency rules. This one covers meaning.

---

## 1. The four things a semantic model is made of

| | What it is | What it answers |
| --- | --- | --- |
| **Entity** | a table, given a business name | *what kind of thing is this* |
| **Dimension** | a column you can group or filter by | *cut it by what* |
| **Metric** | a number worth tracking | *measure what* |
| **Relationship** | how two entities join | *what can be cut by what* |

These multiply. Sixteen real metrics, a few dozen dimensions and fourteen time
ranges answer thousands of questions without anybody defining one of them:

```
metrics × dimensions × time ranges × filters
```

So a question like "revenue by bank" needs **one** metric (revenue) and **one**
relationship (transactions → banks). It does not need a "revenue by bank"
metric, and building one per question is the mistake this design exists to
avoid.

The corollary is where the real risk lives. Metrics are the vocabulary: a
question about something nobody measured cannot be answered, on purpose — the
agent may not invent a measure. A missing **relationship** is the same failure
wearing a quieter mask; it shows up as Cube's "can't find join path" rather than
as anything a user would recognise.

---

## 2. Three names for one metric — and never a fourth

Every metric has three identities, and each exists because the others cannot do
its job:

| Name | Example | Who uses it |
| --- | --- | --- |
| Business name | `Tổng doanh thu` | people, and the AI |
| `MetricDefinition.id` | a uuid | storage — survives renames |
| Cube member | `transactions.Tong_doanh_thu` | execution |

**Do not add a fourth.** Mixing two of these three once caused every metric in
a published model to lose its entity, and the compiled model shipped with no
measures at all — an AI rename rewrote the display name that metrics were
pointing at. The lesson is not "be careful"; it is that each extra name is
another thing to keep in step, and they drift silently.

This is why the formula editor inserts metrics as chips instead of asking for a
slug like `metric.tong_doanh_thu`: the reference must be unambiguous, and the
way to get that is to stop people typing it, not to invent a syntax for
checking what they typed.

Translation between the three lives in one place: `query/cube_schema.py`,
beside the code that invents the Cube identifiers.

---

## 3. Why a calculated metric must stay on one table

A derived metric — `Revenue / Order count` — compiles to a Cube *calculated
measure*, which is an expression inside one cube: one `SELECT`, one `FROM`.

Two metrics on two tables means two `FROM` clauses, so they must be joined
first — and a join at the wrong grain silently multiplies one side:

```
Enterprise A: limit 1000, 3 transactions
Enterprise B: limit  500, 1 transaction

Joined:  A·1000·txn1
         A·1000·txn2      ← A's limit repeated per transaction
         A·1000·txn3
         B· 500·txn4
         SUM(limit) = 3500          the answer is 1500
```

3500 looks entirely normal. Nothing errors. So Cube refuses, and the compiler
drops what it cannot build — `_derived_by_entity` in `cube_schema.py` groups a
formula by the entities its parts belong to and keeps it only when there is
exactly one.

**This is a current limit, not a law.** The general method is to run each side
as its own query at its own grain and divide afterwards — two queries, in our
own engine, by code. The day cross-table ratios matter, that is the path; it
belongs in the query layer, not in the model.

---

## 4. How a question becomes a number

```
question
   │
   ├─ model card — what this source publishes, trimmed to what the question needs
   │
   ├─ the agent picks tools:
   │     list_metrics      find a metric the card did not show
   │     describe_metric   what it is actually calculated from
   │     values_of         what a column really contains
   │     run_query         the numbers
   │     reply / ask_back / decline   end the turn deliberately
   │
   ├─ resolver — business names → Cube members, or a sentence saying why not
   │
   └─ Cube — joins, aggregates, returns rows
```

Three guarantees hold this together, and each one is there because its absence
produced a specific wrong answer:

**The headline number is computed, not narrated.** No second LLM call describes
the result, so it cannot misstate it. (It must also be read *by column name* —
Cube returns more keys in a row than it lists in `columns`, and reading the
first value printed a date where the money belonged.)

**The "read from" line is built from the query, not by the model.** A model
explaining its own query is how it rationalises a wrong one convincingly. The
line names the metric, its formula, the date axis, the slice **and the filters
the question added** — a filtered count and an unfiltered one produced the same
sentence and different numbers until it did.

**The agent never sees raw database columns.** There is no `inspect_schema` in
the answering flow. An agent that can see raw columns starts inventing metrics
from them, which is what the semantic layer exists to prevent, and its answer
reaches the asker with nobody in between.

### Controlled vocabularies

Filter operators (11) and relative time ranges (14) are fixed lists, rejected at
the edge with a suggestion rather than silently degraded. A name like
`this_month` has to be compiled into real dates in a specific timezone with a
specific week start; letting a model write its own range string moves that
decision into a library nobody is watching. Absolute `since`/`until` covers
anything the list does not.

---

## 5. How a model gets built

```
① inspect schema      tables, columns, keys, foreign keys
② profile columns     one query per candidate column: distinct count, samples
③ heuristic           tables → entities, columns → dimensions, FKs → relationships
④ AI naming           business names, and: is this table worth measuring at all
⑤ AI metrics          for the tables ④ said are worth measuring
⑤b AI ratios          over the base metrics ⑤ just created
⑥ save as draft       a human reviews, edits, publishes
```

Settled decisions in that pipeline:

**No cap on profiling.** It was 400 columns, taken in catalogue order — which is
alphabetical by table, so a 122-table source spent the whole budget on
`category_*` lookups and left `transactions` and `enterprises` with nothing. A
time budget replaced it and was the same mistake in a different unit. A build
runs once, deliberately, with a progress bar; what it produces is used until
somebody rebuilds. Stopping early buys minutes once and costs a model that never
learns what its own columns hold. The per-column timeout is the guard that
belongs here — one pathological column is skipped, the rest are not.

**The heuristic does not give every table a row count.** Counting rows is
mechanical; whether a count of `department_roles` is a number anyone asks for is
a judgement. 122 of them buried the 16 metrics somebody designed, and each was a
name the agent read past on every question. The naming pass already visits every
entity, so it answers that question there at no extra cost, and a table nobody
measures loses its count and keeps its columns. Silence is not a no: a failed
batch, or a model that omits the field, leaves the count alone.

**Which tables get metric proposals is that same judgement, uncapped.** It came
from a decision about the business rather than a number we chose, so capping it
again would put the arbitrary limit straight back.

**Values are read when needed, not profiled in advance — for the agent.** The
model says a column is called `Status`; it does not say whether its rows read
`COMPLETED`, `completed` or `3`. Filtering on the wrong one returns nothing,
which reads exactly like a real answer of zero. `values_of` asks at question
time, cached for an hour, and scales with the question rather than with the
database. Build-time profiling still serves the editor's filter picker and the
naming pass.

---

## 6. Conversations

Every turn is stored, including the ones that failed — the questions the agent
could not answer are the list of what the model is missing, and nothing else
records them. Each turn keeps the `model_version` that produced it, because an
answer from v3 cannot be reproduced once v4 is live and the reader has to be
told rather than left to assume.

A follow-up is given the previous turns as three lines each — what was asked,
the query that ran, the shape of the result — never a transcript. A transcript
grows with the conversation and hands back an old `QueryResult` the model may
read as current. "Còn năm ngoái thì sao?" is then a small edit to a query that
is already on screen.

A thread belongs to the source it was started on. Carrying one across sources
would put a question beside history whose metric names mean something else.

---

## 7. Known limits

**Relative time is anchored to today, and the data may not be.** `this_month`
means the current calendar month, so a source whose data ends three months ago
answers "no matching rows" to a perfectly good question. Absolute `since`/`until`
expresses what is wanted; nothing yet tells the agent — or the user — what
period the data actually covers.

**Cross-table ratios cannot be published.** See §3. The validator flags them
rather than letting them ship as metrics that quietly compile to nothing.

**Prose quality is the model's, not the prompt's.** One provider produced four
languages in a paragraph and repeated a word until it ran out, on the same
prompt that another answered cleanly. When output degrades, check which model is
configured before rewriting instructions.

**A missing relationship is invisible until it isn't.** "Find missing links" in
the Relationships tab is worth running after every build.

---

## 8. Where things live

| | |
| --- | --- |
| `core/models.py` | every shape; depends on nothing |
| `semantic/` | building and validating a model |
| `query/cube_schema.py` | model → Cube YAML, and the name translation |
| `query/cube.py` | running a query, reading the result |
| `agent/` | the answering loop: card, tools, resolver, runtime |
| `storage/` | Postgres repositories |
| `apps/web/app/semantic/` | reviewing and editing a model |
| `apps/web/app/chat/` | asking |
