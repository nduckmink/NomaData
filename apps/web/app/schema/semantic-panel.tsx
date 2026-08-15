"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  RiArrowDownLine,
  RiArrowUpLine,
  RiDeleteBinLine,
  RiLoader4Line,
  RiMagicLine,
  RiNodeTree,
  RiSaveLine,
  RiSparkling2Line,
  RiUploadLine,
} from "@remixicon/react"
import { toast } from "sonner"

import {
  deleteSemantic,
  enrichSemantic,
  getSemanticDraft,
  publishSemantic,
  saveSemanticDraft,
  type SemanticGraph,
  suggestSemantic,
} from "@/lib/api-client"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
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
import { cn } from "@/lib/utils"

type Status = "loading" | "error" | "empty" | "ready"

const isBlank = (v: string | null | undefined) => !v || v.trim() === ""

/** Per-source semantic model: generate, review, edit business names, publish,
 * delete. Scoped to one data source — the global overview lives at /semantic. */
export function SemanticPanel({ source }: { source: string }) {
  const [status, setStatus] = useState<Status>("loading")
  const [graph, setGraph] = useState<SemanticGraph | null>(null)
  const [dirty, setDirty] = useState(false)
  const [action, setAction] = useState<
    "generate" | "enrich" | "save" | "publish" | "delete" | null
  >(null)

  // No synchronous setState here — the panel is keyed by source, so it remounts
  // with the initial "loading" state on every source change.
  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const g = await getSemanticDraft(source, signal)
        if (signal?.aborted) return
        setGraph(g)
        setDirty(false)
        setStatus(g ? "ready" : "empty")
      } catch {
        if (!signal?.aborted) setStatus("error")
      }
    },
    [source]
  )

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      await load(controller.signal)
    })()
    return () => controller.abort()
  }, [load])

  const generate = () =>
    void run("generate", async () => {
      const g = await suggestSemantic(source) // AI when configured, else heuristic
      const saved = await saveSemanticDraft(source, g)
      setGraph(saved)
      setDirty(false)
      setStatus("ready")
      toast.success(`Draft generated (${saved.provenance})`)
    })

  const save = () =>
    void run("save", async () => {
      if (!graph) return
      const saved = await saveSemanticDraft(source, graph)
      setGraph(saved)
      setDirty(false)
      toast.success(`Draft saved (v${saved.version})`)
    })

  const publish = () =>
    void run("publish", async () => {
      if (!graph) return
      const res = await publishSemantic(source, graph)
      toast.success(`Published v${res.version}`)
      await load()
    })

  // AI helper: fill only the blank business fields, matching AI output back by
  // table (entities) and formula (metrics). Never overwrites what you've typed.
  const fillBlanks = () =>
    void run("enrich", async () => {
      if (!graph) return
      const ai = await enrichSemantic(source, graph)
      const aiEnt = new Map(ai.entities.map((e) => [e.table, e]))
      const aiMet = new Map(ai.metrics.map((m) => [m.formula, m]))
      let filled = 0
      const entities = graph.entities.map((e) => {
        const a = aiEnt.get(e.table)
        if (!a) return e
        const patch: Partial<typeof e> = {}
        if (isBlank(e.name) && !isBlank(a.name)) {
          patch.name = a.name
          filled++
        }
        if (isBlank(e.description) && !isBlank(a.description)) {
          patch.description = a.description
          filled++
        }
        return Object.keys(patch).length ? { ...e, ...patch } : e
      })
      const metrics = graph.metrics.map((m) => {
        const a = aiMet.get(m.formula)
        if (!a) return m
        const patch: Partial<typeof m> = {}
        if (isBlank(m.name) && !isBlank(a.name)) {
          patch.name = a.name
          filled++
        }
        if (isBlank(m.definition) && !isBlank(a.definition)) {
          patch.definition = a.definition
          filled++
        }
        return Object.keys(patch).length ? { ...m, ...patch } : m
      })
      if (filled === 0) {
        toast("No blank fields to fill")
        return
      }
      setGraph({ ...graph, entities, metrics })
      setDirty(true)
      toast.success(`Filled ${filled} blank field${filled === 1 ? "" : "s"} with AI`)
    })

  const remove = () =>
    void run("delete", async () => {
      await deleteSemantic(source)
      setGraph(null)
      setStatus("empty")
      toast.success("Semantic model deleted")
    })

  async function run(kind: NonNullable<typeof action>, fn: () => Promise<void>) {
    setAction(kind)
    try {
      await fn()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Action failed")
    } finally {
      setAction(null)
    }
  }

  // Local edits to business meaning — persisted only when the user saves.
  const editEntity = (idx: number, patch: Partial<SemanticGraph["entities"][number]>) => {
    setGraph((g) =>
      g
        ? { ...g, entities: g.entities.map((e, i) => (i === idx ? { ...e, ...patch } : e)) }
        : g
    )
    setDirty(true)
  }
  const editMetric = (idx: number, patch: Partial<SemanticGraph["metrics"][number]>) => {
    setGraph((g) =>
      g
        ? { ...g, metrics: g.metrics.map((m, i) => (i === idx ? { ...m, ...patch } : m)) }
        : g
    )
    setDirty(true)
  }

  // Autocomplete pool for formulas: every table.column the model exposes
  // (primary keys, dimension columns, and the numeric columns inside measure
  // expressions), plus aggregation-function skeletons and ready-made
  // FUNC(table.column) formulas over the numeric columns. One shared <datalist>.
  const formulaOptions = useMemo(() => {
    const cols = new Set<string>() // every table.column
    const numeric = new Set<string>() // columns that appear inside a measure
    for (const e of graph?.entities ?? []) {
      if (e.primary_key) cols.add(`${e.table}.${e.primary_key}`)
      for (const d of e.dimensions) cols.add(`${e.table}.${d.column}`)
      for (const m of e.measures) {
        const ref = /([A-Za-z_]\w*\.[A-Za-z_]\w*)/.exec(m.expression)?.[1]
        if (ref) {
          cols.add(ref)
          numeric.add(ref)
        }
      }
    }

    const opts = new Set<string>()
    // Bare function skeletons — for discoverability / custom formulas.
    for (const s of ["COUNT(*)", "SUM()", "AVG()", "MIN()", "MAX()", "COUNT()", "COUNT(DISTINCT )"]) {
      opts.add(s)
    }
    // Ready aggregations over numeric columns.
    for (const c of numeric) {
      for (const fn of ["SUM", "AVG", "MIN", "MAX"]) opts.add(`${fn}(${c})`)
    }
    // COUNT works on any column; DISTINCT COUNT on primary keys.
    for (const c of cols) opts.add(`COUNT(${c})`)
    for (const e of graph?.entities ?? []) {
      if (e.primary_key) opts.add(`COUNT(DISTINCT ${e.table}.${e.primary_key})`)
    }
    // Bare column references.
    for (const c of cols) opts.add(c)

    return Array.from(opts).sort()
  }, [graph])

  if (status === "loading") {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="min-h-48 flex-1" />
      </div>
    )
  }

  if (status === "error") {
    return (
      <p className="text-sm text-destructive">
        Could not load the semantic model. Is the API running?
      </p>
    )
  }

  if (status === "empty" || !graph) {
    return (
      <div className="flex flex-col items-center gap-3 border bg-wash px-6 py-14 text-center">
        <RiNodeTree className="size-8 text-muted-foreground" />
        <div className="flex flex-col gap-1">
          <h3 className="text-sm font-medium">No semantic model yet</h3>
          <p className="max-w-sm text-sm text-balance text-muted-foreground">
            Generate a draft from this source&apos;s schema — NomaData proposes
            entities, dimensions, measures and metrics for you to review.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={generate} disabled={action !== null}>
          {action === "generate" ? (
            <RiLoader4Line data-icon="inline-start" className="animate-spin" />
          ) : (
            <RiSparkling2Line data-icon="inline-start" />
          )}
          Generate model
        </Button>
      </div>
    )
  }

  const published = graph.published
  // Every editable field left blank — what the empty-field navigator jumps to.
  const entityEmpties = graph.entities.reduce(
    (n, e) => n + (isBlank(e.name) ? 1 : 0) + (isBlank(e.description) ? 1 : 0),
    0
  )
  const metricEmpties = graph.metrics.reduce(
    (n, m) =>
      n +
      (isBlank(m.name) ? 1 : 0) +
      (isBlank(m.formula) ? 1 : 0) +
      (isBlank(m.definition) ? 1 : 0),
    0
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <Badge variant={published ? "default" : "secondary"}>
            {published ? "published" : "draft"}
          </Badge>
          <span className="tnum">v{graph.version}</span>
          <span>{graph.provenance}</span>
          {dirty && <span className="text-accent-brand">unsaved changes</span>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="ghost" size="sm" onClick={fillBlanks} disabled={action !== null}>
            {action === "enrich" ? (
              <RiLoader4Line data-icon="inline-start" className="animate-spin" />
            ) : (
              <RiMagicLine data-icon="inline-start" />
            )}
            Fill blanks (AI)
          </Button>
          <Button variant="ghost" size="sm" onClick={generate} disabled={action !== null}>
            {action === "generate" ? (
              <RiLoader4Line data-icon="inline-start" className="animate-spin" />
            ) : (
              <RiSparkling2Line data-icon="inline-start" />
            )}
            Regenerate
          </Button>
          <Button variant="outline" size="sm" onClick={save} disabled={action !== null || !dirty}>
            {action === "save" ? (
              <RiLoader4Line data-icon="inline-start" className="animate-spin" />
            ) : (
              <RiSaveLine data-icon="inline-start" />
            )}
            Save draft
          </Button>
          <Button size="sm" onClick={publish} disabled={action !== null}>
            {action === "publish" ? (
              <RiLoader4Line data-icon="inline-start" className="animate-spin" />
            ) : (
              <RiUploadLine data-icon="inline-start" />
            )}
            Publish
          </Button>
          <DeleteButton onConfirm={remove} busy={action === "delete"} source={source} />
        </div>
      </div>

      {/* Body — one table per concern so each column is labelled and empty cells
          are obvious. */}
      <Tabs defaultValue="entities" className="flex min-h-0 flex-1 flex-col gap-3">
        <TabsList>
          <TabsTrigger value="entities">Entities ({graph.entities.length})</TabsTrigger>
          <TabsTrigger value="metrics">Metrics ({graph.metrics.length})</TabsTrigger>
          <TabsTrigger value="relationships">
            Relationships ({graph.relationships.length})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="entities" className="flex min-h-0 flex-1 flex-col">
          <EmptyNavScroll emptyCount={entityEmpties}>
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-background">
                <TableRow>
                  <TableHead className="w-10 text-right">#</TableHead>
                  <TableHead className="w-56">Name</TableHead>
                  <TableHead>Table</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead className="w-16 text-right">Dims</TableHead>
                  <TableHead className="w-20 text-right">Measures</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {graph.entities.map((e, i) => (
                  <TableRow key={`${e.table}-${i}`}>
                    <TableCell className="text-right text-xs text-muted-foreground tnum">
                      {i + 1}
                    </TableCell>
                    <TableCell>
                      <EditCell
                        value={e.name}
                        onChange={(v) => editEntity(i, { name: v })}
                        label={`Entity name ${i + 1}`}
                        className="font-medium"
                      />
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {e.table}
                    </TableCell>
                    <TableCell>
                      <EditCell
                        value={e.description ?? ""}
                        onChange={(v) => editEntity(i, { description: v })}
                        label={`Entity description ${i + 1}`}
                        placeholder="Business description…"
                      />
                    </TableCell>
                    <TableCell className="text-right tnum">{e.dimensions.length}</TableCell>
                    <TableCell className="text-right tnum">{e.measures.length}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </EmptyNavScroll>
        </TabsContent>

        <TabsContent value="metrics" className="flex min-h-0 flex-1 flex-col">
          {/* Columns + aggregation functions for the formula autocomplete. */}
          <datalist id="semantic-column-refs">
            {formulaOptions.map((opt) => (
              <option key={opt} value={opt} />
            ))}
          </datalist>
          <EmptyNavScroll emptyCount={metricEmpties}>
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-background">
                <TableRow>
                  <TableHead className="w-10 text-right">#</TableHead>
                  <TableHead className="w-56">Name</TableHead>
                  <TableHead>Formula</TableHead>
                  <TableHead>Definition</TableHead>
                  <TableHead className="w-16 text-right">Filters</TableHead>
                  <TableHead className="w-40">Time</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {graph.metrics.map((m, i) => (
                  <TableRow key={`${m.name}-${i}`}>
                    <TableCell className="text-right text-xs text-muted-foreground tnum">
                      {i + 1}
                    </TableCell>
                    <TableCell>
                      <EditCell
                        value={m.name}
                        onChange={(v) => editMetric(i, { name: v })}
                        label={`Metric name ${i + 1}`}
                        className="font-medium"
                      />
                    </TableCell>
                    <TableCell>
                      <EditCell
                        value={m.formula}
                        onChange={(v) => editMetric(i, { formula: v })}
                        label={`Metric formula ${i + 1}`}
                        placeholder="SUM(table.column)"
                        mono
                        list="semantic-column-refs"
                      />
                    </TableCell>
                    <TableCell>
                      <EditCell
                        value={m.definition}
                        onChange={(v) => editMetric(i, { definition: v })}
                        label={`Metric definition ${i + 1}`}
                        placeholder="Business definition…"
                      />
                    </TableCell>
                    <TableCell className="text-right tnum">
                      {m.filters?.length ? m.filters.length : <Dash />}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {m.time_dimension ?? <Dash />}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </EmptyNavScroll>
        </TabsContent>

        <TabsContent value="relationships" className="flex min-h-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1 overflow-auto border">
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-background">
                <TableRow>
                  <TableHead className="w-10 text-right">#</TableHead>
                  <TableHead>From</TableHead>
                  <TableHead>Column</TableHead>
                  <TableHead>To</TableHead>
                  <TableHead>Column</TableHead>
                  <TableHead className="w-32">Kind</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {graph.relationships.map((r, i) => (
                  <TableRow key={i}>
                    <TableCell className="text-right text-xs text-muted-foreground tnum">
                      {i + 1}
                    </TableCell>
                    <TableCell className="font-mono text-sm">{r.from_entity}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {r.from_column}
                    </TableCell>
                    <TableCell className="font-mono text-sm">{r.to_entity}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {r.to_column}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{r.kind}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
                {graph.relationships.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="text-sm text-muted-foreground">
                      No relationships detected.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function Dash() {
  return (
    <span className="text-muted-foreground" aria-label="empty" title="empty">
      —
    </span>
  )
}

/** An editable table cell. Blank cells are tinted and tagged `data-empty` so the
 * empty-field navigator can jump between them. */
function EditCell({
  value,
  onChange,
  label,
  placeholder,
  className,
  mono,
  list,
}: {
  value: string
  onChange: (v: string) => void
  label: string
  placeholder?: string
  className?: string
  mono?: boolean
  /** id of a <datalist> to offer autocomplete (e.g. column references). */
  list?: string
}) {
  const empty = isBlank(value)
  return (
    <Input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={label}
      placeholder={placeholder}
      list={list}
      data-empty={empty ? "true" : undefined}
      className={cn(
        // A faint border always shows so the cell reads as editable; it darkens
        // on hover/focus.
        "h-8 border-border/50 bg-transparent shadow-none hover:border-border focus-visible:border-border",
        mono && "font-mono text-xs",
        empty && "border-accent-brand/50 bg-accent-brand/5",
        className
      )}
    />
  )
}

/** Scroll container with a "jump to next / previous empty field" toolbar. Finds
 * empties by their `data-empty` marker relative to the current scroll position,
 * wrapping around at the ends. */
function EmptyNavScroll({
  emptyCount,
  children,
}: {
  emptyCount: number
  children: React.ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)

  const jump = useCallback((dir: 1 | -1) => {
    const root = ref.current
    if (!root) return
    const nodes = Array.from(
      root.querySelectorAll<HTMLElement>('[data-empty="true"]')
    )
    if (nodes.length === 0) return

    // If the focus is already on an empty field, step to the adjacent one and
    // wrap — so repeated clicks cycle through them in order. Otherwise pick the
    // first empty relative to the current scroll position.
    const curIdx = nodes.indexOf(document.activeElement as HTMLElement)
    let target: HTMLElement
    if (curIdx !== -1) {
      target = nodes[(curIdx + dir + nodes.length) % nodes.length]
    } else {
      const rootTop = root.getBoundingClientRect().top
      const tops = nodes.map((n) => n.getBoundingClientRect().top - rootTop + root.scrollTop)
      const cur = root.scrollTop
      if (dir === 1) {
        const idx = tops.findIndex((t) => t > cur + 4)
        target = nodes[idx === -1 ? 0 : idx]
      } else {
        let idx = -1
        for (let i = tops.length - 1; i >= 0; i--) {
          if (tops[i] < cur - 4) {
            idx = i
            break
          }
        }
        target = nodes[idx === -1 ? nodes.length - 1 : idx]
      }
    }

    const top = target.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop
    // Instant, not smooth: this container doesn't animate scrollTo, and a jump
    // reads better landing immediately anyway.
    root.scrollTo({ top: Math.max(0, top - 16) })
    target.focus({ preventScroll: true })
  }, [])

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className={cn(emptyCount > 0 && "text-accent-brand")}>
          {emptyCount} empty field{emptyCount === 1 ? "" : "s"}
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="size-7 p-0"
          disabled={emptyCount === 0}
          onClick={() => jump(-1)}
          aria-label="Previous empty field"
        >
          <RiArrowUpLine />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="size-7 p-0"
          disabled={emptyCount === 0}
          onClick={() => jump(1)}
          aria-label="Next empty field"
        >
          <RiArrowDownLine />
        </Button>
      </div>
      <div ref={ref} className="relative min-h-0 flex-1 overflow-auto border">
        {children}
      </div>
    </div>
  )
}

function DeleteButton({
  onConfirm,
  busy,
  source,
}: {
  onConfirm: () => void
  busy: boolean
  source: string
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="ghost" size="sm" disabled={busy} aria-label="Delete semantic model">
          {busy ? <RiLoader4Line className="animate-spin" /> : <RiDeleteBinLine />}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete semantic model?</AlertDialogTitle>
          <AlertDialogDescription>
            This removes every version of the semantic model for{" "}
            <span className="font-mono">{source}</span>. This cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>Delete</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
