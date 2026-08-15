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

export async function getDataSources(signal?: AbortSignal): Promise<string[]> {
  return getJSON<string[]>("/api/v1/datasources", signal)
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
