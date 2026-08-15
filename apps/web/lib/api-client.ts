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

export async function createDataSource(
  input: DataSourceInput
): Promise<DataSourceInfo> {
  const res = await fetch(`${API_BASE_URL}/api/v1/datasources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
  if (!res.ok) {
    let detail = `API returned ${res.status}`
    try {
      const body = (await res.json()) as { detail?: string }
      if (body?.detail) detail = body.detail
    } catch {
      // keep the status-based message
    }
    throw new Error(detail)
  }
  return (await res.json()) as DataSourceInfo
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
