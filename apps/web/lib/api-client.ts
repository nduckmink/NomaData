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
