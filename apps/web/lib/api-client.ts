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

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string }
    if (body?.detail) return body.detail
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
  if (!res.ok) {
    throw new Error(`API returned ${res.status}`)
  }
  return (await res.json()) as T
}

// ---------- Semantic model ----------

export interface Dimension {
  name: string
  column: string
  description?: string | null
}

export interface MetricFilter {
  field: string
  operator: string
  value: unknown
}

export type Aggregation =
  | "count"
  | "count_distinct"
  | "sum"
  | "avg"
  | "min"
  | "max"

export type MetricKind = "base" | "derived"

export interface MetricDefinition {
  name: string
  description?: string | null
  kind: MetricKind
  // base
  entity?: string | null
  aggregation?: Aggregation | null
  column?: string | null
  filters?: MetricFilter[]
  time_dimension?: string | null
  // derived
  expression?: string | null
  // display
  format?: string | null
}

export interface Relationship {
  from_entity: string
  to_entity: string
  from_column: string
  to_column: string
  kind: string
}

export interface Entity {
  name: string
  table: string
  primary_key: string
  dimensions: Dimension[]
  description?: string | null
}

export interface SemanticGraph {
  source_id: string
  entities: Entity[]
  metrics: MetricDefinition[]
  relationships: Relationship[]
  version: number
  published: boolean
  provenance: "ai" | "heuristic"
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
}

/** Start a background build (heuristic + optional AI enrichment). Poll with
 *  getJob; when done the draft is saved, reload it with getSemanticDraft. */
export async function startGenerate(
  name: string,
  useAi = true
): Promise<GenerationJob> {
  const params = new URLSearchParams({ use_ai: String(useAi) })
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/generate?${params}`,
    { method: "POST" }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as GenerationJob
}

/** Start a background AI re-enrichment of the current draft. */
export async function startEnhance(name: string): Promise<GenerationJob> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/enhance`,
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

export async function saveSemanticDraft(
  name: string,
  graph: SemanticGraph
): Promise<SemanticGraph> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic`,
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

/** Introspect the source schema and propose a draft semantic model. Not saved
 *  — the caller reviews it, then persists/publishes separately. */
export async function suggestSemantic(
  name: string,
  { useAi = true }: { useAi?: boolean } = {}
): Promise<SemanticGraph> {
  const params = new URLSearchParams({ use_ai: String(useAi) })
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/suggest?${params}`,
    { method: "POST" }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as SemanticGraph
}

export interface EnrichmentHints {
  entities: { table: string; name: string; description: string }[]
  metrics: { key: string; name: string; definition: string }[]
}

/** Compact AI business text (name + description/definition) for the entities and
 *  metrics in `graph`, matched back by table/formula. Send a manageable slice;
 *  the caller batches. Throws 409 when no AI provider is configured. */
export async function enrichSemantic(
  name: string,
  graph: SemanticGraph,
  signal?: AbortSignal
): Promise<EnrichmentHints> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/datasources/${encodeURIComponent(name)}/semantic/enrich`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(graph),
      signal,
    }
  )
  if (!res.ok) throw new Error(await errorDetail(res))
  return (await res.json()) as EnrichmentHints
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
