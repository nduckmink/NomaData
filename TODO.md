# Data Source Connectors — TODO / Tracker

> Which database engines NomaData can connect to. Each connector is one file in
> `apps/api/nomadata/connectors/` implementing the `DataSource` interface, plus
> one branch in `build_data_source()` — nothing else changes. See
> [ARCHITECTURE.md](./docs/ARCHITECTURE.md).

**Focus:** relational SQL databases + analytical/warehouse engines first (that's
where business data and BI live). NoSQL / search / graph are out of scope for
now — listed at the bottom for completeness.

**Legend:** ✅ done · 🔜 next · ⬜ planned · 💤 later

Effort is the *driver* difficulty: **Low** = pure pip, cross-platform; **Med** =
pip but needs cloud auth or a sync-in-thread wrapper; **High** = needs a
system/native client library installed.

---

## Priority checklist (build in this order)

Ordered by value = popularity ÷ effort (cheap + popular first).

- [x] **1. MySQL** — ✅ done (`aiomysql`, Low)
- [x] **2. SQL Server** — ✅ done (`pymssql`, Med — sync-in-thread)
- [ ] **3. PostgreSQL** — 🔜 Low effort, very popular. `asyncpg` or `psycopg` 3.
- [ ] **4. MariaDB** — ⬜ near-zero effort: same MySQL wire protocol → reuse the
      MySQL connector (`aiomysql`). Mostly a factory alias + testing.
- [ ] **5. ClickHouse** — ⬜ Low–Med. Popular OSS warehouse; `clickhouse-connect`
      (HTTP) or `asynch`. Very BI-relevant.
- [ ] **6. BigQuery** — ⬜ Med (cloud auth). `google-cloud-bigquery` SDK, no ODBC.
      Service-account credentials.
- [ ] **7. Snowflake** — ⬜ Med (cloud auth, sync). `snowflake-connector-python`.
- [ ] **8. Redshift** — ⬜ Low *if* PostgreSQL is done — Postgres-wire compatible,
      often reuses the Postgres driver. `redshift_connector` otherwise.
- [ ] **9. DuckDB** — ⬜ Low. Embedded analytical engine (`duckdb`); great for
      local files / Parquet / quick demos. Not a server, but a cheap win.
- [ ] **10. Oracle** — 💤 Med. Enterprise-popular; `python-oracledb` **thin mode**
      needs no Oracle client (much easier than it used to be).
- [ ] **11. Databricks SQL** — 💤 Med. `databricks-sql-connector`.

---

## Reference — Relational (SQL / OLTP)

| Engine      | Status | Driver (pip)              | Effort | Notes |
| ----------- | ------ | ------------------------- | ------ | ----- |
| MySQL       | ✅     | `aiomysql`                | Low    | Done. |
| SQL Server  | ✅     | `pymssql`                 | Med    | Done. Sync→thread; wheel bundles FreeTDS (no ODBC). |
| PostgreSQL  | 🔜     | `asyncpg` / `psycopg`     | Low    | Most-requested; async-native. |
| MariaDB     | ⬜     | `aiomysql` (reuse MySQL)  | Trivial| Same protocol as MySQL. |
| Oracle      | 💤     | `python-oracledb` (thin)  | Med    | Enterprise; thin mode = no native client. |
| SQLite      | 💤     | `aiosqlite`               | Low    | File DB, low BI relevance; handy for tests. |

## Reference — Analytical / Warehouse (OLAP)

| Engine        | Status | Driver (pip)                    | Effort | Notes |
| ------------- | ------ | ------------------------------- | ------ | ----- |
| ClickHouse    | ⬜     | `clickhouse-connect` / `asynch` | Low–Med| Popular OSS columnar warehouse. |
| BigQuery      | ⬜     | `google-cloud-bigquery`         | Med    | Cloud auth (service account). |
| Snowflake     | ⬜     | `snowflake-connector-python`    | Med    | Cloud auth; sync→thread. |
| Redshift      | ⬜     | reuse Postgres / `redshift_connector` | Low* | *Low if Postgres is done (wire-compatible). |
| DuckDB        | ⬜     | `duckdb`                        | Low    | Embedded; files/Parquet. |
| Databricks    | 💤     | `databricks-sql-connector`      | Med    | SQL warehouse endpoint. |

---

## Reuse opportunities (why the order is cheap)

- **MariaDB ← MySQL:** identical wire protocol; the existing `aiomysql` connector
  works with a factory alias.
- **Redshift ← PostgreSQL:** Redshift speaks the Postgres wire protocol; the
  Postgres connector largely covers it.
- **Cloud warehouses (BigQuery/Snowflake/Databricks):** no ODBC/native pain —
  official pip SDKs — but they add an auth dimension (keys/tokens) the on-prem
  engines don't have. Plan a small credential story for these.

## Per-connector definition of done

For each engine, the connector must implement `DataSource`:

- [ ] `test_connection` — connectivity + latency
- [ ] `inspect_schema` — tables, columns, data types, primary keys, foreign keys
- [ ] `profile` — null %, distinct, min/max, samples
- [ ] `execute` — read-only query execution
- [ ] identifier quoting is injection-safe
- [ ] driver added to the import-linter "drivers only in connectors" contract
- [ ] verified against a real database

---

## Out of scope (for now)

Not part of the SQL + warehouse focus; revisit only if a concrete need appears.

- **NoSQL document:** MongoDB, CouchDB
- **Key-value:** Redis, DynamoDB
- **Wide-column:** Cassandra, HBase, Bigtable
- **Graph:** Neo4j
- **Search:** Elasticsearch, OpenSearch
- **Time-series:** InfluxDB, TimescaleDB, Prometheus
- **Vector (AI/embeddings):** pgvector, Pinecone, Qdrant, Milvus, Weaviate
