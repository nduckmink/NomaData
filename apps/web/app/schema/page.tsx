"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import {
  RiAddLine,
  RiArrowRightLine,
  RiDatabase2Line,
  RiErrorWarningLine,
  RiKey2Line,
  RiLinksLine,
  RiLoaderLine,
  RiSearchLine,
  RiTableLine,
} from "@remixicon/react"

import {
  type DatabaseCatalog,
  type DataSourceInfo,
  getSchema,
  getDataSources,
  getTable,
  listTables,
  type TableInfo,
  type TableSummary,
} from "@/lib/api-client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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
import { PageContainer, PageHeader } from "@/components/page-header"
import { cn } from "@/lib/utils"

import { DataSourceDialog } from "./data-source-dialogs"
import { DataSourceSidebar } from "./data-source-sidebar"
import { SchemaDiagram } from "./schema-diagram"
import { SemanticPanel } from "./semantic-panel"

/** One page of the table list. Matches the backend's default/cap. */
const PAGE_SIZE = 40
/** Wait for typing to settle before hitting the network — every keystroke
 * would otherwise fire its own request. */
const SEARCH_DEBOUNCE_MS = 250
/** Fetch the next page once the list is scrolled within this many px of the
 * bottom — the request lands before the user hits empty space. */
const LOAD_MORE_THRESHOLD_PX = 160

type ListStatus = "idle" | "loading" | "error" | "ready"

export default function SchemaPage() {
  const [tab, setTab] = useState<"schema" | "diagram" | "semantic">("schema")

  const [sourcesLoading, setSourcesLoading] = useState(true)
  const [sources, setSources] = useState<DataSourceInfo[]>([])
  const [source, setSource] = useState<string | null>(null)

  // The table list is paginated and searched server-side — only the visible
  // window (plus whatever's been scrolled through) ever reaches the client.
  const [query, setQuery] = useState("")
  const [debouncedQuery, setDebouncedQuery] = useState("")
  const [tableItems, setTableItems] = useState<TableSummary[]>([])
  const [tableMatchTotal, setTableMatchTotal] = useState(0)
  const [tableTotals, setTableTotals] = useState({
    tables: 0,
    columns: 0,
    relationships: 0,
  })
  const [listStatus, setListStatus] = useState<ListStatus>("idle")
  const [loadingMore, setLoadingMore] = useState(false)

  // The selected table's full columns — fetched on selection, not carried in
  // the list (which only has counts).
  const [selected, setSelected] = useState<string | null>(null)
  const [selectedTable, setSelectedTable] = useState<TableInfo | null>(null)
  const [selectedStatus, setSelectedStatus] = useState<ListStatus>("idle")

  // Only the diagram needs every table at once — fetched the first time that
  // tab opens, not on page load.
  const [diagramCatalog, setDiagramCatalog] = useState<DatabaseCatalog | null>(
    null
  )
  const [diagramStatus, setDiagramStatus] = useState<ListStatus>("idle")

  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  /** The one place `source` changes — resets everything that belongs to the
   * previous source in the same batch, so no effect ever runs on a stale
   * (new source, old query/selection) combination. */
  const switchSource = useCallback(
    (name: string | null) => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
      setSource(name)
      setQuery("")
      setDebouncedQuery("")
      setTableItems([])
      setTableMatchTotal(0)
      setListStatus(name ? "loading" : "idle")
      setSelected(null)
      setSelectedTable(null)
      setSelectedStatus("idle")
      setDiagramCatalog(null)
      // Still on the diagram tab? It needs the new source's full catalog
      // right away, not on the next tab click.
      setDiagramStatus(name && tab === "diagram" ? "loading" : "idle")
    },
    [tab]
  )

  // Load the list of sources once, and select the first.
  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const list = await getDataSources(controller.signal)
        if (controller.signal.aborted) return
        setSources(list)
        setSourcesLoading(false)
        if (list[0]) switchSource(list[0].name)
      } catch {
        if (!controller.signal.aborted) setSourcesLoading(false)
      }
    })()
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Debounce the search box into `debouncedQuery`, which is what actually
  // drives the fetch below.
  useEffect(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current)
    debounceTimer.current = setTimeout(() => {
      setListStatus("loading")
      setDebouncedQuery(query)
    }, SEARCH_DEBOUNCE_MS)
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
    }
  }, [query])

  // Fetch page 1 whenever the source or the settled search text changes.
  useEffect(() => {
    if (!source) return
    const controller = new AbortController()
    void (async () => {
      try {
        const page = await listTables(
          source,
          { offset: 0, limit: PAGE_SIZE, q: debouncedQuery },
          controller.signal
        )
        if (controller.signal.aborted) return
        setTableItems(page.items)
        setTableMatchTotal(page.total)
        setTableTotals({
          tables: page.total_tables,
          columns: page.total_columns,
          relationships: page.total_relationships,
        })
        setListStatus("ready")
        // Mirrors the old "select the first table" behaviour, but only on a
        // fresh source (not on every search keystroke).
        // Updater must stay side-effect-free — capture "did we auto-pick"
        // in a local instead of calling setState from inside it.
        let autoSelected = false
        setSelected((prev) => {
          if (prev) return prev
          const first = page.items[0]?.name ?? null
          autoSelected = first !== null
          return first
        })
        if (autoSelected) setSelectedStatus("loading")
      } catch {
        if (!controller.signal.aborted) setListStatus("error")
      }
    })()
    return () => controller.abort()
  }, [source, debouncedQuery])

  // Fetch the selected table's full columns.
  useEffect(() => {
    if (!source || !selected) return
    const controller = new AbortController()
    void (async () => {
      try {
        const info = await getTable(source, selected, controller.signal)
        if (controller.signal.aborted) return
        setSelectedTable(info)
        setSelectedStatus("ready")
      } catch {
        if (!controller.signal.aborted) setSelectedStatus("error")
      }
    })()
    return () => controller.abort()
  }, [source, selected])

  // Fetch the whole catalog once the Diagram tab opens. The trigger (tab
  // switch / source switch) sets status to "loading"; this effect performs the
  // fetch for that loading state. Guarding on "idle" here would deadlock —
  // the status is already "loading" by the time the effect runs.
  useEffect(() => {
    if (tab !== "diagram" || !source || diagramStatus !== "loading") return
    const controller = new AbortController()
    void (async () => {
      try {
        const cat = await getSchema(source, controller.signal)
        if (controller.signal.aborted) return
        setDiagramCatalog(cat)
        setDiagramStatus("ready")
      } catch {
        if (!controller.signal.aborted) setDiagramStatus("error")
      }
    })()
    return () => controller.abort()
  }, [tab, source, diagramStatus])

  const loadMore = useCallback(async () => {
    if (!source || loadingMore || tableItems.length >= tableMatchTotal) return
    setLoadingMore(true)
    try {
      const page = await listTables(source, {
        offset: tableItems.length,
        limit: PAGE_SIZE,
        q: debouncedQuery,
      })
      setTableItems((prev) => [...prev, ...page.items])
      setTableMatchTotal(page.total)
    } catch {
      // A failed "load more" just leaves the list where it was — scrolling
      // again retries.
    } finally {
      setLoadingMore(false)
    }
  }, [source, debouncedQuery, loadingMore, tableItems.length, tableMatchTotal])

  const onListScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => {
      const el = e.currentTarget
      if (
        el.scrollHeight - el.scrollTop - el.clientHeight <
        LOAD_MORE_THRESHOLD_PX
      ) {
        void loadMore()
      }
    },
    [loadMore]
  )

  // Following a foreign key / diagram node re-runs the search as the exact
  // table name — the fetch that reveals is the same one the search box
  // already uses, so the destination lands in the (now one-item) list too.
  const goToTable = useCallback(
    (name: string) => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
      setQuery(name)
      if (name !== debouncedQuery) {
        setDebouncedQuery(name)
        setListStatus("loading")
      }
      if (name !== selected) {
        setSelected(name)
        setSelectedStatus("loading")
      }
    },
    [debouncedQuery, selected]
  )

  // Called after add/edit (an event handler — setState is fine here).
  const handleSaved = useCallback(
    (preferred: string) => {
      void (async () => {
        try {
          const list = await getDataSources()
          setSources(list)
          switchSource(preferred)
        } catch {
          // Keep the previous list rather than clearing it on a transient
          // refresh failure — the sidebar stays usable.
        }
      })()
    },
    [switchSource]
  )

  const handleDeleted = useCallback(() => {
    void (async () => {
      try {
        const list = await getDataSources()
        setSources(list)
        switchSource(list[0]?.name ?? null)
      } catch {
        // As above — leave the current list in place.
      }
    })()
  }, [switchSource])

  return (
    <PageContainer variant="fill" className="max-w-none">
      <PageHeader title="Schema" />

      <div className="grid min-h-0 gap-5 md:flex-1 md:grid-cols-[12rem_1fr]">
        <DataSourceSidebar
          loading={sourcesLoading}
          sources={sources}
          selected={source}
          onSelect={(name) => {
            if (name !== source) switchSource(name)
          }}
          onSaved={handleSaved}
          onDeleted={handleDeleted}
        />

        <div className="flex min-h-0 flex-col gap-3 md:min-h-0">
          {sourcesLoading && <LoadingState />}

          {!sourcesLoading && sources.length === 0 && (
            <div className="flex flex-col items-center gap-3 border bg-wash px-6 py-14 text-center">
              <RiDatabase2Line className="size-8 text-muted-foreground" />
              <div className="flex flex-col gap-1">
                <h2 className="text-sm font-medium">
                  No data source connected
                </h2>
                <p className="max-w-sm text-sm text-balance text-muted-foreground">
                  Add a MySQL or SQL Server connection and NomaData will
                  discover its tables, columns, keys and relationships.
                </p>
              </div>
              <DataSourceDialog
                mode="create"
                onSaved={handleSaved}
                trigger={
                  <Button variant="outline" size="sm">
                    <RiAddLine data-icon="inline-start" />
                    Add source
                  </Button>
                }
              />
            </div>
          )}

          {!sourcesLoading && source && (
            <Tabs
              value={tab}
              onValueChange={(v) => {
                const next = v as "schema" | "diagram" | "semantic"
                setTab(next)
                if (next === "diagram" && diagramStatus === "idle") {
                  setDiagramStatus("loading")
                }
              }}
              className="flex min-h-0 flex-1 flex-col gap-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-2">
                <TabsList>
                  <TabsTrigger value="schema">Schema</TabsTrigger>
                  <TabsTrigger value="diagram">Diagram</TabsTrigger>
                  <TabsTrigger value="semantic">Semantic</TabsTrigger>
                </TabsList>
                {/* Always rendered (skeleton while empty) — swapping this in
                    and out of the tree was the whole page's height jump. */}
                {listStatus === "idle" ? (
                  <Skeleton className="h-5 w-56" />
                ) : (
                  <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-muted-foreground">
                    <span className="font-mono text-foreground">{source}</span>
                    <Stat label="tables" value={tableTotals.tables} />
                    <Stat label="columns" value={tableTotals.columns} />
                    <Stat
                      label="relationships"
                      value={tableTotals.relationships}
                    />
                  </div>
                )}
              </div>

              <TabsContent
                value="schema"
                className="grid min-h-0 gap-5 md:flex-1 md:grid-cols-[minmax(0,17rem)_1fr]"
              >
                <aside className="flex min-h-0 flex-col gap-2">
                  <div className="relative">
                    <RiSearchLine
                      aria-hidden
                      className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
                    />
                    <Input
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder={`Search ${tableTotals.tables} tables…`}
                      aria-label="Search tables"
                      className="pl-8 font-mono"
                    />
                  </div>

                  {listStatus === "error" && (
                    <p className="p-2 text-sm text-destructive">
                      Could not load tables.
                    </p>
                  )}

                  <div
                    onScroll={onListScroll}
                    className="h-[38svh] overflow-y-auto border md:h-auto md:min-h-0 md:flex-1"
                  >
                    {listStatus === "loading" && tableItems.length === 0 ? (
                      <TableListSkeleton />
                    ) : (
                      <ul className="flex flex-col">
                        {tableItems.map((t) => (
                          <li key={`${t.schema_name}.${t.name}`}>
                            <TableListItem
                              table={t}
                              active={t.name === selected}
                              onSelect={() => {
                                if (t.name === selected) return
                                setSelected(t.name)
                                setSelectedStatus("loading")
                              }}
                            />
                          </li>
                        ))}
                        {listStatus === "ready" && tableItems.length === 0 && (
                          <li className="p-4 text-sm text-muted-foreground">
                            No tables match “{debouncedQuery}”.
                          </li>
                        )}
                        {loadingMore && (
                          <li className="flex items-center justify-center gap-2 py-3 text-xs text-muted-foreground">
                            <RiLoaderLine className="size-3.5 animate-spin" />
                            Loading more…
                          </li>
                        )}
                      </ul>
                    )}
                  </div>
                </aside>

                <section className="flex min-h-0 min-w-0 flex-col gap-3 md:min-h-0">
                  {selectedStatus === "loading" && !selectedTable && (
                    <TableDetailSkeleton />
                  )}
                  {selectedStatus === "error" && (
                    <p className="text-sm text-destructive">
                      Could not load this table.
                    </p>
                  )}
                  {selectedTable ? (
                    <>
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <h2 className="truncate font-mono text-base font-semibold">
                          {selectedTable.name}
                        </h2>
                        <Badge variant="secondary" className="tnum">
                          {selectedTable.columns.length} columns
                        </Badge>
                        {selectedTable.foreign_keys.length > 0 && (
                          <Badge variant="secondary" className="tnum">
                            {selectedTable.foreign_keys.length} relationships
                          </Badge>
                        )}
                      </div>
                      <ColumnTable table={selectedTable} onFollow={goToTable} />
                    </>
                  ) : (
                    selectedStatus === "idle" && (
                      <p className="text-sm text-muted-foreground">
                        Select a table to inspect its columns.
                      </p>
                    )
                  )}
                </section>
              </TabsContent>

              <TabsContent
                value="diagram"
                className="flex h-[70svh] flex-col md:h-auto md:min-h-0 md:flex-1"
              >
                {diagramStatus === "loading" && (
                  <Skeleton className="min-h-64 w-full flex-1" />
                )}
                {diagramStatus === "error" && (
                  <Alert variant="destructive">
                    <RiErrorWarningLine />
                    <AlertTitle>Could not load the diagram</AlertTitle>
                    <AlertDescription>
                      Is the API running? Start it with{" "}
                      <code>pnpm api:dev</code>.
                    </AlertDescription>
                  </Alert>
                )}
                {diagramStatus === "ready" && diagramCatalog && (
                  <SchemaDiagram
                    catalog={diagramCatalog}
                    onOpenTable={(name) => {
                      goToTable(name)
                      setTab("schema")
                    }}
                  />
                )}
              </TabsContent>

              <TabsContent
                value="semantic"
                className="flex min-h-0 flex-col md:min-h-0 md:flex-1"
              >
                {/* Keyed by source so switching sources remounts with a clean
                    load instead of showing the previous source's model. */}
                <SemanticPanel key={source} source={source} />
              </TabsContent>
            </Tabs>
          )}
        </div>
      </div>
    </PageContainer>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="font-mono text-sm font-semibold text-foreground tnum">
        {value.toLocaleString()}
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
  table: TableSummary
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      className={cn(
        "flex w-full items-center justify-between gap-2 border-b border-l-2 border-l-transparent px-3 py-2 text-left text-sm transition-colors",
        active
          ? "border-l-accent-brand bg-accent-brand/15"
          : "hover:bg-accent-brand/8"
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        <RiTableLine
          aria-hidden
          className="size-4 shrink-0 text-muted-foreground"
        />
        <span className="truncate font-mono">{table.name}</span>
      </span>
      <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
        {table.foreign_key_count > 0 && (
          <RiLinksLine
            role="img"
            aria-label="has relationships"
            className="size-3.5"
          />
        )}
        <span className="tnum">{table.column_count}</span>
      </span>
    </button>
  )
}

function ColumnTable({
  table,
  onFollow,
}: {
  table: TableInfo
  onFollow: (name: string) => void
}) {
  const fkByColumn = new Map(table.foreign_keys.map((fk) => [fk.column, fk]))
  return (
    // The scroll must live on shadcn's own table-container, otherwise the
    // sticky header sticks to a container that never scrolls.
    <div className="h-[55svh] overflow-hidden border md:h-auto md:min-h-0 md:flex-1 [&>[data-slot=table-container]]:h-full">
      <Table>
        <TableHeader className="sticky top-0 z-10 bg-background">
          <TableRow>
            <TableHead>Column</TableHead>
            <TableHead>Type</TableHead>
            <TableHead className="w-20">Null</TableHead>
            <TableHead>Key / Reference</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {table.columns.map((c) => {
            const fk = fkByColumn.get(c.name)
            return (
              <TableRow key={c.name}>
                <TableCell className="font-mono">{c.name}</TableCell>
                <TableCell className="font-mono text-muted-foreground">
                  {c.data_type}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {c.nullable ? (
                    "null"
                  ) : (
                    <span title="NOT NULL" aria-label="not null">
                      —
                    </span>
                  )}
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
                      <button
                        type="button"
                        onClick={() => onFollow(fk.references_table)}
                        title={`Go to ${fk.references_table}`}
                        className="cursor-pointer"
                      >
                        <Badge
                          variant="outline"
                          className="font-mono transition-colors hover:bg-accent"
                        >
                          <RiArrowRightLine data-icon="inline-start" />
                          {fk.references_table}.{fk.references_column}
                        </Badge>
                      </button>
                    )}
                  </span>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

function TableListSkeleton() {
  return (
    <div className="flex flex-col gap-2 p-2">
      {Array.from({ length: 12 }).map((_, i) => (
        <div key={i} className="flex items-center justify-between gap-2 px-1">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-3 w-6" />
        </div>
      ))}
    </div>
  )
}

function TableDetailSkeleton() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex items-center gap-2">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-5 w-20" />
      </div>
      <Skeleton className="min-h-0 flex-1" />
    </div>
  )
}

/** Mirrors the real layout so nothing jumps when data arrives. This lives in
 * the right-hand pane only — the data-source sidebar (left column) renders
 * its own skeleton via its `loading` prop while sources are still loading. */
function LoadingState() {
  return (
    <div className="grid min-h-0 flex-1 gap-5 md:grid-cols-[minmax(0,17rem)_1fr]">
      <div className="flex min-h-0 flex-col gap-2">
        <Skeleton className="h-8 w-full" />
        <div className="flex min-h-0 flex-1 flex-col border">
          {Array.from({ length: 12 }).map((_, i) => (
            <div
              key={i}
              className="flex items-center justify-between gap-2 border-b px-3 py-2"
            >
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-6" />
            </div>
          ))}
        </div>
      </div>
      <div className="flex min-h-0 flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-8 w-44" />
        </div>
        <Skeleton className="min-h-0 flex-1" />
      </div>
    </div>
  )
}
