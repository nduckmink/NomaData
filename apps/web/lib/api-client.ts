// Single entry point for talking to the NomaData API.
// Every network call to the backend goes through here — no scattered fetch().

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"

export interface HealthResponse {
  status: string
  version: string
  env: string
  checks: Record<string, string>
  providers: string[]
  data_sources: string[]
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return getJSON<HealthResponse>("/api/v1/health", signal)
}

// ---------- Data sources / schema ----------

export interface ColumnInfo {
  name: string
  data_type: string
  nullable: boolean
  is_primary_key: boolean
}

export interface ForeignKey {
  column: string
  references_table: string
  references_column: string
}

export interface TableInfo {
  schema_name: string
  name: string
  columns: ColumnInfo[]
  primary_key: string[]
  foreign_keys: ForeignKey[]
}

export interface DatabaseCatalog {
  source_id: string
  tables: TableInfo[]
}

export interface TableSummary {
  schema_name: string
  name: string
  column_count: number
  foreign_key_count: number
}

export interface TablePage {
  items: TableSummary[]
  total: number
  total_tables: number
  total_columns: number
  total_relationships: number
}

export type DataSourceKind = "mysql" | "sqlserver"

export interface DataSourceInput {
  name: string
  kind: DataSourceKind
  host: string
  port: number
  database: string
  user: string
  password: string
}

export interface DataSourceInfo {
  name: string
  kind: string
  host: string
  port: number
  database: string
  user: string
  uses_password_env: boolean
}

export async function getDataSources(
  signal?: AbortSignal
): Promise<DataSourceInfo[]> {
  return getJSON<DataSourceInfo[]>("/api/v1/datasources", signal)
}

export interface ConnectionStatus {
  state: "ok" | "error"
  latency_ms?: number | null
  message?: string | null
}

export async function verifyDataSource(
  input: DataSourceInput
): Promise<ConnectionStatus> {
  const res = await fetch(`${API_BASE_URL}/api/v1/datasources/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as ConnectionStatus
}

export async function getDataSource(name: string): Promise<DataSourceInfo> {
  return getJSON<DataSourceInfo>(
    `/api/v1/datasources/${encodeURIComponent(name)}`
  )
}

export async function createDataSource(
  input: DataSourceInput
): Promise<DataSourceInfo> {
  return sendDataSource("POST", "/api/v1/datasources", input)
}

export async function updateDataSource(
  name: string,
  input: DataSourceInput
): Promise<DataSourceInfo> {
  return sendDataSource(
    "PUT",
    `/api/v1/datasources/${encodeURIComponent(name)}`,
    input
  )
}

export async function deleteDataSource(name: string): Promise<void> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}`,
    { method: "DELETE" }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
}

async function sendDataSource(
  method: "POST" | "PUT",
  path: string,
  input: DataSourceInput
): Promise<DataSourceInfo> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as DataSourceInfo
}

/** The server's own message, whatever shape `detail` took.
 *
 *  FastAPI lets `detail` be a string or any JSON value, and the publish gate
 *  uses an object so it can name every metric that blocks the publish. Reading
 *  that as a string produced "[object Object]" at the one moment the message
 *  mattered most, so both shapes are handled here — the single place every
 *  request funnels its errors through. */
async function errorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    const detail = body?.detail
    if (typeof detail === "string") return detail
    if (detail && typeof detail === "object") {
      const { message, issues } = detail as {
        message?: string
        issues?: { message?: string }[]
      }
      const lines = (issues ?? [])
        .map((i) => i?.message)
        .filter((m): m is string => !!m)
        .map((m) => `• ${m}`)
      const text = [message, ...lines].filter(Boolean).join("\n")
      if (text) return text
    }
  } catch {
    // fall through to status-based message
  }
  return `API returned ${res.status}`
}

export async function getSchema(
  name: string,
  signal?: AbortSignal
): Promise<DatabaseCatalog> {
  return getJSON<DatabaseCatalog>(
    `/api/v1/datasources/${encodeURIComponent(name)}/schema`,
    signal
  )
}

export async function listTables(
  name: string,
  {
    offset = 0,
    limit = 40,
    q = "",
  }: { offset?: number; limit?: number; q?: string },
  signal?: AbortSignal
): Promise<TablePage> {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  })
  if (q) params.set("q", q)
  return getJSON<TablePage>(
    `/api/v1/datasources/${encodeURIComponent(name)}/tables?${params}`,
    signal
  )
}

export async function getTable(
  name: string,
  table: string,
  signal?: AbortSignal
): Promise<TableInfo> {
  return getJSON<TableInfo>(
    `/api/v1/datasources/${encodeURIComponent(name)}/tables/${encodeURIComponent(table)}`,
    signal
  )
}

async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    signal,
    cache: "no-store",
  })
  // Reuse the shared reader rather than inventing a status-only message: the
  // server explains *why* ("app database not connected"), and throwing
  // "API returned 503" threw that explanation away on every GET.
  if (!res.ok) {
    throw new Error(await errorDetail(res))
  }
  return (await res.json()) as T
}

// ---------- Semantic model ----------

export type DimensionKind = "time" | "number" | "string" | "boolean"

/** Where a value came from, and whether AI may overwrite it. `user` or
 *  `locked` means hands off — that is how an edit survives a re-enrichment. */
export interface Provenance {
  origin: "heuristic" | "ai" | "user"
  locked: boolean
}

export const USER_OWNED: Provenance = { origin: "user", locked: false }

export interface Dimension {
  name: string
  column: string
  kind: DimensionKind
  data_type: string
  hidden: boolean
  description?: string | null
  distinct_count?: number | null
  /** Real values from the database — used to populate filter value pickers. */
  sample_values: unknown[]
  provenance: Provenance
}

/** Operators the whole stack can execute. Anything else is rejected server-side
 *  rather than degraded to `eq`, which would return a wrong number silently. */
export const FILTER_OPERATORS = [
  "eq",
  "neq",
  "gt",
  "gte",
  "lt",
  "lte",
  "in",
  "not_in",
  "contains",
  "set",
  "not_set",
] as const

export type FilterOperator = (typeof FILTER_OPERATORS)[number]

export interface MetricFilter {
  field: string
  operator: FilterOperator
  value: unknown
}

export type Aggregation =
  "count" | "count_distinct" | "sum" | "avg" | "min" | "max"

export type MetricKind = "base" | "derived"

export interface MetricDefinition {
  /** Stable handle. Names are labels and may change; nothing references them. */
  id: string
  name: string
  description?: string | null
  kind: MetricKind
  // base
  entity_key?: string | null
  aggregation?: Aggregation | null
  column?: string | null
  filters: MetricFilter[]
  time_dimension?: string | null
  // derived
  expression?: string | null
  // display
  format?: string | null
  provenance: Provenance
}

export interface Relationship {
  from_entity_key: string
  to_entity_key: string
  from_column: string
  to_column: string
  kind: string
}

export interface Entity {
  /** Immutable identity. `name` is the label and is free to change. */
  key: string
  name: string
  table: string
  schema_name: string
  primary_key: string
  dimensions: Dimension[]
  description?: string | null
  hidden: boolean
  provenance: Provenance
}

export interface SemanticGraph {
  source_id: string
  entities: Entity[]
  metrics: MetricDefinition[]
  relationships: Relationship[]
  version: number
  published: boolean
  /** Bumped on every draft save; send it back to detect a concurrent edit. */
  revision: number
  provenance: "ai" | "heuristic"
  /** Tables left out of the model, with the reason (e.g. no primary key). */
  skipped_tables: { table: string; reason: string }[]
  /** Tables the model was built from. Empty = the whole catalog. */
  scope_tables: string[]
}

export interface ValidationIssue {
  level: "error" | "warning"
  code: string
  message: string
  target?: string | null
  target_kind?: string | null
}

export interface ValidationReport {
  ok: boolean
  issues: ValidationIssue[]
}

/** What the AI needs to know about this business before it can name anything. */
export interface BusinessContext {
  source_id: string
  domain: string
  glossary: string
  conventions: string
  language: string
  instructions: string
  /** IANA zone the data's timestamps are read in — decides what "this month"
   *  means. Left at UTC, a UTC+7 database reports the first seven hours of
   *  every day against the previous day. */
  timezone: string
}

export interface EntityDraftResponse {
  name: string
  description: string
  /** Fields the AI changed — highlight these so the user knows what to check. */
  changed_fields: string[]
  reasoning: string
  warnings: string[]
}

export interface MetricDraftResponse {
  metric: MetricDefinition
  /** Fields the AI filled or changed — highlight these in the form. */
  changed_fields: string[]
  reasoning: string
  warnings: string[]
}

export interface MetricSuggestResponse {
  metrics: MetricDefinition[]
  /** Why each metric was proposed, in the same order. */
  reasons: string[]
  warnings: string[]
}

export interface MetricPreview {
  metric_id: string
  value: unknown
  row_count?: number | null
  /** The span of the metric's time column across the matched rows — this is
   *  what reveals a metric measured by the wrong date. */
  period_start?: unknown
  period_end?: unknown
  time_column?: string | null
  sql: string
  error?: string | null
}

export interface SemanticModelVersion {
  version: number
  status: string
  created_at: string
}

export interface GenerationJob {
  id: string
  source_id: string
  kind: string
  status: "running" | "done" | "error"
  done: number
  total: number
  error?: string | null
  /** Naming batches that failed. The build still succeeded — those entities
   *  kept their heuristic names — but "partly named" must not read as "named". */
  failed_batches: number
  last_batch_error?: string | null
}

/** Start a background build (profile + heuristic + optional AI enrichment).
 *  Poll with getJob; when done the draft is saved, reload it with
 *  getSemanticDraft.
 *
 *  `tables` limits the model to a chosen scope — a 124-table database produces
 *  a model nobody can review. `keepEdits` folds the rebuild onto the existing
 *  draft so reviewed work is not discarded. */
export async function startGenerate(
  name: string,
  useAi = true,
  { tables, keepEdits = true }: { tables?: string[]; keepEdits?: boolean } = {}
): Promise<GenerationJob> {
  const params = new URLSearchParams({
    use_ai: String(useAi),
    keep_edits: String(keepEdits),
  })
  if (tables?.length) params.set("tables", tables.join(","))
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/generate?${params}`,
    { method: "POST" }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as GenerationJob
}

export async function getJob(
  name: string,
  jobId: string,
  signal?: AbortSignal
): Promise<GenerationJob> {
  return getJSON<GenerationJob>(
    `/api/v1/datasources/${encodeURIComponent(name)}/semantic/jobs/${encodeURIComponent(jobId)}`,
    signal
  )
}

/** The build job currently running for a source, or null — used to resume
 *  watching after the client navigated away. */
export async function getActiveJob(
  name: string,
  signal?: AbortSignal
): Promise<GenerationJob | null> {
  return getJSON<GenerationJob | null>(
    `/api/v1/datasources/${encodeURIComponent(name)}/semantic/job`,
    signal
  )
}

export interface SemanticModelSummary {
  source_id: string
  kind?: string | null
  has_model: boolean
  status: "none" | "draft" | "published"
  latest_version?: number | null
  published_version?: number | null
  provenance?: "ai" | "heuristic" | null
  entity_count: number
  metric_count: number
  relationship_count: number
  /** Structural validation counts (no DB hit) — errors block a publish. */
  error_count: number
  warning_count: number
  /** A draft exists that is newer than the published model. */
  has_unpublished_changes: boolean
}

/** Cross-source overview: one row per data source with its model status. */
export async function getSemanticOverview(
  signal?: AbortSignal
): Promise<SemanticModelSummary[]> {
  return getJSON<SemanticModelSummary[]>("/api/v1/semantic", signal)
}

/** The latest saved graph (draft or published) for a source. Returns null when
 *  the source has no model yet (200 + null body). A 404 means the data source
 *  itself is unknown — a real error — so it throws rather than reading as empty. */
export async function getSemanticDraft(
  name: string,
  signal?: AbortSignal
): Promise<SemanticGraph | null> {
  return getJSON<SemanticGraph | null>(
    `/api/v1/datasources/${encodeURIComponent(name)}/semantic`,
    signal
  )
}

/** Save the working draft. Sends the revision the client loaded, so a second
 *  tab editing the same model gets a 409 instead of silently winning. */
export async function saveSemanticDraft(
  name: string,
  graph: SemanticGraph
): Promise<SemanticGraph> {
  const params = new URLSearchParams({
    expected_revision: String(graph.revision),
  })
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic?${params}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(graph),
    }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as SemanticGraph
}

export async function publishSemantic(
  name: string,
  graph: SemanticGraph
): Promise<{ source_id: string; version: number; published: boolean }> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/publish`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(graph),
    }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as {
    source_id: string
    version: number
    published: boolean
  }
}

export async function listSemanticVersions(
  name: string,
  signal?: AbortSignal
): Promise<SemanticModelVersion[]> {
  return getJSON<SemanticModelVersion[]>(
    `/api/v1/datasources/${encodeURIComponent(name)}/semantic/versions`,
    signal
  )
}

/** Delete a source's semantic model (all versions). */
export async function deleteSemantic(name: string): Promise<void> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic`,
    { method: "DELETE" }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
}

/** Check a graph for anything that would break at query time. Errors block a
 *  publish; warnings are advisory. */
export async function validateSemantic(
  name: string,
  graph: SemanticGraph,
  signal?: AbortSignal
): Promise<ValidationReport> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/validate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(graph),
      signal,
    }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as ValidationReport
}

// ---------- Business context ----------

export async function getBusinessContext(
  name: string,
  signal?: AbortSignal
): Promise<BusinessContext> {
  return getJSON<BusinessContext>(
    `/api/v1/datasources/${encodeURIComponent(name)}/semantic/context`,
    signal
  )
}

export async function saveBusinessContext(
  name: string,
  context: BusinessContext
): Promise<BusinessContext> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/context`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(context),
    }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as BusinessContext
}

// ---------- Metric authoring ----------

/** Describe a metric in words and get a filled-in definition back.
 *
 *  Nothing is saved: the caller fills its form with the result, the user checks
 *  it (ideally with previewMetric) and presses Save. Pass `base` to edit an
 *  existing metric instead of creating one. */
export async function draftMetric(
  name: string,
  body: {
    prompt: string
    base?: MetricDefinition | null
    entity_key?: string | null
  },
  signal?: AbortSignal
): Promise<MetricDraftResponse> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/metrics/draft`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as MetricDraftResponse
}

/** Describe one entity in words and get its business name and description.
 *
 *  Text only — the table, its columns and its key come from the database and
 *  are not negotiable. Nothing is saved; the editor fills its fields. */
export async function draftEntity(
  name: string,
  body: { prompt: string; entity_key: string },
  signal?: AbortSignal
): Promise<EntityDraftResponse> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/entities/draft`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as EntityDraftResponse
}

/** Propose the metrics worth tracking on one entity.
 *
 *  Scoped to one entity because most tables in a large schema are lookups
 *  nobody measures. Nothing is saved — the user picks what to keep. */
export async function suggestMetrics(
  name: string,
  body: { entity_key: string; limit?: number },
  signal?: AbortSignal
): Promise<MetricSuggestResponse> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/metrics/suggest`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as MetricSuggestResponse
}

/** Joins implied by column names that the model does not have yet.
 *
 *  Rule-based, not an AI call: a wrong join pairs unrelated rows silently, so
 *  ambiguous names are skipped rather than guessed. Nothing is saved. */
export async function suggestRelationships(
  name: string,
  signal?: AbortSignal
): Promise<Relationship[]> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/relationships/suggest`,
    { method: "POST", signal }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as Relationship[]
}

/** Run one metric against the real database and return the number. Works on an
 *  unsaved draft — which is exactly when "is this right?" needs answering. */
export async function previewMetric(
  name: string,
  metric: MetricDefinition,
  signal?: AbortSignal
): Promise<MetricPreview> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/metrics/preview`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(metric),
      signal,
    }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as MetricPreview
}

// ---------- AI provider config ----------

export interface AIProviderInput {
  provider: string
  base_url: string
  api_key: string
  api_key_env?: string | null
  model: string
}

export interface AIProviderInfo {
  provider: string
  base_url: string
  model: string
  configured: boolean
  uses_api_key_env: boolean
  /** Masked preview of the stored key (first 5 + dots + last 3), or null. */
  key_hint?: string | null
}

/** Current AI config (safe view, no key), or null if unconfigured. */
export async function getAIConfig(
  signal?: AbortSignal
): Promise<AIProviderInfo | null> {
  return getJSON<AIProviderInfo | null>("/api/v1/ai/config", signal)
}

export async function saveAIConfig(
  input: AIProviderInput
): Promise<AIProviderInfo> {
  const res = await fetch(`${API_BASE_URL}/api/v1/ai/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as AIProviderInfo
}

/** Test an AI config WITHOUT saving it. A blank api_key reuses the stored key. */
export async function testAIConfig(
  input: AIProviderInput
): Promise<ConnectionStatus> {
  const res = await fetch(`${API_BASE_URL}/api/v1/ai/config/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as ConnectionStatus
}

export async function deleteAIConfig(): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/ai/config`, {
    method: "DELETE",
  })
  if (!res.ok) throw new Error(await errorDetail(res))
}

// ---------- Ask (conversational query) ----------

export interface QueryTimeSpec {
  dimension: string
  range?: string | null
  since?: string | null
  until?: string | null
  grain?: string | null
  timezone?: string | null
}

/** The intermediate query behind an answer — business names, never SQL. */
export interface AnalyticalQuery {
  measures: string[]
  dimensions: string[]
  filters: { field: string; operator: string; value: unknown }[]
  time?: QueryTimeSpec | null
  limit?: number | null
  order_by: string[]
}

export interface ResultColumn {
  name: string
  data_type: string
}

export interface QueryResult {
  columns: ResultColumn[]
  rows: Record<string, unknown>[]
  row_count: number
  truncated: boolean
}

/** One answered (or declined) question. `kind` decides which fields matter. */
export interface AgentTurn {
  kind: "answer" | "clarify" | "refuse" | "error"
  question: string
  query?: AnalyticalQuery | null
  result?: QueryResult | null
  answer: string
  explanation: string
  notes: string[]
  clarification: string
  reason: string
}

export async function ask(
  name: string,
  question: string,
  signal?: AbortSignal
): Promise<AgentTurn> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/ask`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      signal,
    }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as AgentTurn
}

/** A few real questions to try, from the source's published model. */
export async function askExamples(
  name: string,
  signal?: AbortSignal
): Promise<string[]> {
  return getJSON<string[]>(
    `/api/v1/datasources/${encodeURIComponent(name)}/ask/examples`,
    signal
  )
}
