"use client"

import Link from "next/link"
import { useCallback, useEffect, useMemo, useState } from "react"
import {
  RiAddLine,
  RiArrowRightLine,
  RiDatabase2Line,
  RiDeleteBinLine,
  RiEditLine,
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
import { Button } from "@/components/ui/button"
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { ThemeToggle } from "@/components/theme-toggle"
import { cn } from "@/lib/utils"

import { DataSourceDialog, DeleteDataSourceDialog } from "./data-source-dialogs"
import { TableErd } from "./erd"

type Status = "loading" | "error" | "empty" | "ready"

export default function SchemaPage() {
  const [status, setStatus] = useState<Status>("loading")
  const [sources, setSources] = useState<string[]>([])
  const [source, setSource] = useState<string | null>(null)
  const [catalog, setCatalog] = useState<DatabaseCatalog | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [query, setQuery] = useState("")

  // Load the list of sources once, and select the first.
  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const names = await getDataSources(controller.signal)
        if (controller.signal.aborted) return
        if (names.length === 0) {
          setStatus("empty")
          return
        }
        setSources(names)
        setSource(names[0])
      } catch {
        if (!controller.signal.aborted) setStatus("error")
      }
    })()
    return () => controller.abort()
  }, [])

  // Load the schema whenever the selected source changes.
  useEffect(() => {
    if (!source) return
    const controller = new AbortController()
    void (async () => {
      try {
        const cat = await getSchema(source, controller.signal)
        if (controller.signal.aborted) return
        setCatalog(cat)
        setSelected(cat.tables[0]?.name ?? null)
        setQuery("")
        setStatus("ready")
      } catch {
        if (!controller.signal.aborted) setStatus("error")
      }
    })()
    return () => controller.abort()
  }, [source])

  // Called from the "Add source" dialog (an event handler — setState is fine).
  const refreshSources = useCallback(async (preferred?: string) => {
    setStatus("loading")
    try {
      const names = await getDataSources()
      if (names.length === 0) {
        setSources([])
        setSource(null)
        setStatus("empty")
        return
      }
      setSources(names)
      setSource(preferred && names.includes(preferred) ? preferred : names[0])
    } catch {
      setStatus("error")
    }
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
      <header className="flex flex-col gap-3">
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← System status
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <RiDatabase2Line className="size-6 text-muted-foreground" />
            Schema
          </span>
          <div className="flex items-center gap-2">
            {sources.length > 0 && (
              <ToggleGroup
                type="single"
                value={source ?? ""}
                onValueChange={(v) => {
                  if (v && v !== source) {
                    setStatus("loading")
                    setSource(v)
                  }
                }}
              >
                {sources.map((s) => (
                  <ToggleGroupItem key={s} value={s} className="font-mono">
                    {s}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            )}
            {source && (
              <>
                <DataSourceDialog
                  mode="edit"
                  name={source}
                  onSaved={(name) => void refreshSources(name)}
                  trigger={
                    <Button
                      variant="outline"
                      size="icon-sm"
                      aria-label={`Edit ${source}`}
                      title="Edit source"
                    >
                      <RiEditLine />
                    </Button>
                  }
                />
                <DeleteDataSourceDialog
                  name={source}
                  onDeleted={() => void refreshSources()}
                  trigger={
                    <Button
                      variant="outline"
                      size="icon-sm"
                      aria-label={`Remove ${source}`}
                      title="Remove source"
                    >
                      <RiDeleteBinLine />
                    </Button>
                  }
                />
              </>
            )}
            <DataSourceDialog
              mode="create"
              onSaved={(name) => void refreshSources(name)}
              trigger={
                <Button variant="outline" size="sm">
                  <RiAddLine data-icon="inline-start" />
                  Add source
                </Button>
              }
            />
            <ThemeToggle />
          </div>
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
        <div className="flex flex-col items-start gap-4">
          <Alert>
            <RiDatabase2Line />
            <AlertTitle>No data source connected</AlertTitle>
            <AlertDescription>
              Add a database connection to introspect its schema.
            </AlertDescription>
          </Alert>
          <DataSourceDialog
            mode="create"
            onSaved={(name) => void refreshSources(name)}
            trigger={
              <Button variant="outline" size="sm">
                <RiAddLine data-icon="inline-start" />
                Add source
              </Button>
            }
          />
        </div>
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
                  <li key={`${t.schema_name}.${t.name}`}>
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
            {selectedTable && catalog ? (
              <Tabs defaultValue="columns" className="gap-3">
                <TabsList>
                  <TabsTrigger value="columns">Columns</TabsTrigger>
                  <TabsTrigger value="erd">Diagram</TabsTrigger>
                </TabsList>
                <TabsContent value="columns">
                  <ColumnTable table={selectedTable} />
                </TabsContent>
                <TabsContent value="erd">
                  <TableErd catalog={catalog} table={selectedTable} />
                </TabsContent>
              </Tabs>
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
