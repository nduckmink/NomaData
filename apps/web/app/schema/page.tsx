"use client"

import Link from "next/link"
import { useEffect, useMemo, useState } from "react"
import {
  RiArrowRightLine,
  RiDatabase2Line,
  RiErrorWarningLine,
  RiKey2Line,
  RiLinksLine,
  RiSearchLine,
  RiTableLine,
} from "@remixicon/react"

import {
  type DatabaseCatalog,
  getDataSources,
  getSchema,
  type TableInfo,
} from "@/lib/api-client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"

type Status = "loading" | "error" | "empty" | "ready"

export default function SchemaPage() {
  const [status, setStatus] = useState<Status>("loading")
  const [source, setSource] = useState<string | null>(null)
  const [catalog, setCatalog] = useState<DatabaseCatalog | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [query, setQuery] = useState("")

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const sources = await getDataSources(controller.signal)
        if (sources.length === 0) {
          if (!controller.signal.aborted) setStatus("empty")
          return
        }
        const cat = await getSchema(sources[0], controller.signal)
        if (controller.signal.aborted) return
        setSource(sources[0])
        setCatalog(cat)
        setSelected(cat.tables[0]?.name ?? null)
        setStatus("ready")
      } catch {
        if (!controller.signal.aborted) setStatus("error")
      }
    })()
    return () => controller.abort()
  }, [])

  const tables = useMemo(() => catalog?.tables ?? [], [catalog])
  const filtered = useMemo(
    () =>
      tables.filter((t) => t.name.toLowerCase().includes(query.toLowerCase())),
    [tables, query]
  )
  const selectedTable = useMemo(
    () => tables.find((t) => t.name === selected) ?? null,
    [tables, selected]
  )

  const totals = useMemo(() => {
    const columns = tables.reduce((n, t) => n + t.columns.length, 0)
    const fks = tables.reduce((n, t) => n + t.foreign_keys.length, 0)
    return { tables: tables.length, columns, fks }
  }, [tables])

  return (
    <main className="mx-auto flex min-h-svh w-full max-w-6xl flex-col gap-6 p-6">
      <header className="flex flex-col gap-2">
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← System status
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <span className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <RiDatabase2Line className="size-6 text-muted-foreground" />
            Schema
          </span>
          {source && <Badge variant="outline">{source}</Badge>}
        </div>
        {status === "ready" && (
          <div className="flex gap-6 text-sm text-muted-foreground">
            <Stat label="tables" value={totals.tables} />
            <Stat label="columns" value={totals.columns} />
            <Stat label="relationships" value={totals.fks} />
          </div>
        )}
      </header>

      {status === "loading" && <LoadingState />}

      {status === "error" && (
        <Alert variant="destructive">
          <RiErrorWarningLine />
          <AlertTitle>Could not load the schema</AlertTitle>
          <AlertDescription>
            Is the API running? Start it with{" "}
            <code className="font-mono">pnpm api:dev</code>.
          </AlertDescription>
        </Alert>
      )}

      {status === "empty" && (
        <Alert>
          <RiDatabase2Line />
          <AlertTitle>No data source configured</AlertTitle>
          <AlertDescription>
            Set <code className="font-mono">NOMADATA_DS_*</code> in your{" "}
            <code className="font-mono">.env</code> (see{" "}
            <code className="font-mono">.env.example</code>) and restart the
            API.
          </AlertDescription>
        </Alert>
      )}

      {status === "ready" && (
        <div className="grid flex-1 gap-6 md:grid-cols-[minmax(0,18rem)_1fr]">
          <aside className="flex min-h-0 flex-col gap-3">
            <div className="relative">
              <RiSearchLine className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={`Search ${totals.tables} tables…`}
                className="pl-8"
              />
            </div>
            <ScrollArea className="h-[60svh] rounded-none border">
              <ul className="flex flex-col">
                {filtered.map((t) => (
                  <li key={t.name}>
                    <TableListItem
                      table={t}
                      active={t.name === selected}
                      onSelect={() => setSelected(t.name)}
                    />
                  </li>
                ))}
                {filtered.length === 0 && (
                  <li className="p-4 text-sm text-muted-foreground">
                    No tables match “{query}”.
                  </li>
                )}
              </ul>
            </ScrollArea>
          </aside>

          <section className="min-w-0">
            {selectedTable ? (
              <ColumnTable table={selectedTable} />
            ) : (
              <p className="text-sm text-muted-foreground">Select a table.</p>
            )}
          </section>
        </div>
      )}
    </main>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="font-mono text-base font-semibold text-foreground">
        {value}
      </span>
      {label}
    </span>
  )
}

function TableListItem({
  table,
  active,
  onSelect,
}: {
  table: TableInfo
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "flex w-full items-center justify-between gap-2 border-b px-3 py-2 text-left text-sm transition-colors hover:bg-accent",
        active && "bg-accent"
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        <RiTableLine className="size-4 shrink-0 text-muted-foreground" />
        <span className="truncate font-mono">{table.name}</span>
      </span>
      <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
        {table.foreign_keys.length > 0 && (
          <RiLinksLine className="size-3.5" aria-label="has relationships" />
        )}
        {table.columns.length}
      </span>
    </button>
  )
}

function ColumnTable({ table }: { table: TableInfo }) {
  const fkByColumn = new Map(table.foreign_keys.map((fk) => [fk.column, fk]))
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-mono text-lg font-semibold">{table.name}</h2>
        <Badge variant="secondary">{table.columns.length} columns</Badge>
        {table.foreign_keys.length > 0 && (
          <Badge variant="secondary">
            {table.foreign_keys.length} relationships
          </Badge>
        )}
      </div>
      <div className="overflow-x-auto rounded-none border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Column</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Null</TableHead>
              <TableHead>Key / Reference</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {table.columns.map((c) => {
              const fk = fkByColumn.get(c.name)
              return (
                <TableRow key={c.name}>
                  <TableCell className="font-mono">{c.name}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {c.data_type}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {c.nullable ? "yes" : "no"}
                  </TableCell>
                  <TableCell>
                    <span className="flex flex-wrap items-center gap-1.5">
                      {c.is_primary_key && (
                        <Badge>
                          <RiKey2Line data-icon="inline-start" />
                          PK
                        </Badge>
                      )}
                      {fk && (
                        <Badge variant="outline" className="font-mono">
                          <RiArrowRightLine data-icon="inline-start" />
                          {fk.references_table}.{fk.references_column}
                        </Badge>
                      )}
                    </span>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

function LoadingState() {
  return (
    <div className="grid flex-1 gap-6 md:grid-cols-[minmax(0,18rem)_1fr]">
      <div className="flex flex-col gap-2">
        {Array.from({ length: 10 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-full" />
        ))}
      </div>
      <Skeleton className="h-[60svh] w-full" />
    </div>
  )
}
