"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  RiAddLine,
  RiArrowDownLine,
  RiArrowUpLine,
  RiCloseLine,
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
  type Aggregation,
  type ColumnInfo,
  deleteSemantic,
  type EnrichmentHints,
  enrichSemantic,
  getAIConfig,
  getSchema,
  getSemanticDraft,
  type MetricDefinition,
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

/** Entities/metrics per AI enrichment call. Small enough that the model returns
 * valid JSON quickly, instead of overflowing on the whole schema at once. */
const ENRICH_BATCH = 12
/** AI calls in flight at once during enrichment. Kept low: rate-limited
 * providers serialize anyway, so more just piles up held connections on the
 * dev server for no speed gain. */
const ENRICH_CONCURRENCY = 2
/** Per-batch ceiling. A good batch takes a few seconds; this only trips on a
 * stuck/retrying call so one straggler can't stall the whole run. */
const BATCH_TIMEOUT_MS = 40_000

const isBlank = (v: string | null | undefined) => !v || v.trim() === ""

function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = []
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size))
  return out
}

// Mirrors the backend heuristic's default text so Enhance can tell an
// untouched (still-default) field from one a human has edited, and only
// replace the former. Keep in sync with nomadata/semantic/suggester.py.
const humanize = (id: string) =>
  id
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w))
    .join(" ")

const isDefaultEntityName = (e: SemanticGraph["entities"][number]) =>
  isBlank(e.name) || e.name === humanize(e.table)
const isDefaultEntityDesc = (e: SemanticGraph["entities"][number]) =>
  isBlank(e.description) || e.description === `Business entity backed by table ${e.table}.`
const isDefaultMetricName = (m: MetricDefinition) =>
  isBlank(m.name) || / Count$/.test(m.name) || /^Total /.test(m.name)
const isDefaultMetricDesc = (m: MetricDefinition) =>
  isBlank(m.description) ||
  /^Number of .+ records\.$/.test(m.description ?? "") ||
  /^Sum of .+/.test(m.description ?? "")

/** Stable id for a metric — must match nomadata/semantic/suggester.py. */
const metricKey = (m: MetricDefinition) =>
  m.kind === "derived"
    ? `=${m.expression ?? ""}`
    : `${m.aggregation}(${m.entity}.${m.column ?? "*"})`

const AGGREGATIONS: Aggregation[] = [
  "count",
  "count_distinct",
  "sum",
  "avg",
  "min",
  "max",
]
const FORMATS = ["number", "currency", "percent"] as const
/** count needs no column; the others aggregate one. */
const needsColumn = (a: Aggregation | null | undefined) => a === "count_distinct" || a === "sum" || a === "avg" || a === "min" || a === "max"
const isNumericType = (t: string) =>
  /int|dec|numeric|float|double|real|money|number/i.test(t)
const isTemporalType = (t: string) =>
  /date|time|timestamp|datetime|year/i.test(t)

/** Per-source semantic model: generate, review, edit business names, publish,
 * delete. Scoped to one data source — the global overview lives at /semantic. */
export function SemanticPanel({ source }: { source: string }) {
  const [status, setStatus] = useState<Status>("loading")
  const [graph, setGraph] = useState<SemanticGraph | null>(null)
  const [dirty, setDirty] = useState(false)
  const [action, setAction] = useState<
    "generate" | "enrich" | "save" | "publish" | "delete" | null
  >(null)
  // AI enrichment runs in batches; this drives the progress readout.
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  // Columns per table, for the metric column pickers — loaded once from the schema.
  const [columnsByTable, setColumnsByTable] = useState<Map<string, ColumnInfo[]>>(new Map())
  // Whether an AI provider is configured — gates auto-enhance on generate.
  const [aiConfigured, setAiConfigured] = useState(false)
  // Cancel an in-flight batched enrichment when the panel unmounts (source switch).
  const enrichAbort = useRef<AbortController | null>(null)
  useEffect(() => () => enrichAbort.current?.abort(), [])

  // Load the table→columns map once so base metrics can pick a column.
  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const catalog = await getSchema(source, controller.signal)
        if (controller.signal.aborted) return
        setColumnsByTable(new Map(catalog.tables.map((t) => [t.name, t.columns])))
      } catch {
        // Column pickers just fall back to free text.
      }
    })()
    return () => controller.abort()
  }, [source])

  // Is AI configured? Gates whether Generate auto-enhances.
  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const cfg = await getAIConfig(controller.signal)
        if (!controller.signal.aborted) setAiConfigured(!!cfg?.configured)
      } catch {
        // Leave false — Generate stays heuristic-only, Enhance is disabled.
      }
    })()
    return () => controller.abort()
  }, [])

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

  // Generate the structural draft instantly (heuristic, deterministic), then —
  // if AI is configured — improve names/descriptions in the background. The
  // draft is usable immediately; AI text streams in and never blocks.
  const generate = () =>
    void (async () => {
      setAction("generate")
      try {
        const g = await suggestSemantic(source, { useAi: false })
        const saved = await saveSemanticDraft(source, g)
        setGraph(saved)
        setDirty(false)
        setStatus("ready")
        toast.success(`Draft generated — ${saved.entities.length} entities`)
        if (aiConfigured) {
          setAction("enrich")
          await runEnhance(saved)
        }
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Generate failed")
      } finally {
        setAction(null)
        setProgress(null)
      }
    })()

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

  // AI helper: improve business names/descriptions/definitions. Runs in small
  // batches (a whole-model call is slow and overflows the model's output on a
  // large schema), reporting progress and surviving a failed batch. Only blank
  // or still-default (heuristic-generated) fields are replaced — anything you
  // typed is kept.
  // Manual re-run of AI enrichment (also invoked automatically by generate).
  const enhance = () =>
    void run("enrich", () => (graph ? runEnhance(graph) : Promise.resolve()))

  // Snapshot `base`: batches partition entities/metrics, so each field's
  // "is-default" test stays valid against it even as results stream in.
  async function runEnhance(base: SemanticGraph) {
      enrichAbort.current = new AbortController()
      const signal = enrichAbort.current.signal

      const entityBatches = chunk(base.entities, ENRICH_BATCH)
      const metricBatches = chunk(base.metrics, ENRICH_BATCH)
      const total = entityBatches.length + metricBatches.length
      let failed = 0
      let changed = 0
      setProgress({ done: 0, total })
      const bump = () => setProgress((p) => (p ? { done: p.done + 1, total } : p))

      // One timeout per AI call so a stuck/retrying batch can't stall the run.
      const callWithTimeout = async (mini: SemanticGraph): Promise<EnrichmentHints> => {
        const ac = new AbortController()
        const onAbort = () => ac.abort()
        signal.addEventListener("abort", onAbort)
        const timer = setTimeout(() => ac.abort(), BATCH_TIMEOUT_MS)
        try {
          return await enrichSemantic(source, mini, ac.signal)
        } finally {
          clearTimeout(timer)
          signal.removeEventListener("abort", onAbort)
        }
      }

      // Apply each batch's hints as it lands (incremental) — names appear
      // progressively, and a slow batch only delays its own rows. Only blank /
      // still-default fields are replaced; anything you typed is kept.
      const applyEntities = (hints: EnrichmentHints["entities"]) => {
        const byTable = new Map(hints.map((h) => [h.table, h]))
        const patches = new Map<string, Partial<SemanticGraph["entities"][number]>>()
        for (const e of base.entities) {
          const a = byTable.get(e.table)
          if (!a) continue
          const patch: Partial<typeof e> = {}
          if (isDefaultEntityName(e) && !isBlank(a.name) && a.name !== e.name) patch.name = a.name
          if (isDefaultEntityDesc(e) && !isBlank(a.description) && a.description !== e.description)
            patch.description = a.description
          if (Object.keys(patch).length) patches.set(e.table, patch)
        }
        if (!patches.size) return
        changed += [...patches.values()].reduce((n, p) => n + Object.keys(p).length, 0)
        setGraph((g) =>
          g
            ? {
                ...g,
                entities: g.entities.map((e) =>
                  patches.has(e.table) ? { ...e, ...patches.get(e.table) } : e
                ),
              }
            : g
        )
        setDirty(true)
      }
      const applyMetrics = (hints: EnrichmentHints["metrics"]) => {
        const byKey = new Map(hints.map((h) => [h.key, h]))
        const patches = new Map<number, Partial<MetricDefinition>>()
        base.metrics.forEach((m, i) => {
          const a = byKey.get(metricKey(m))
          if (!a) return
          const patch: Partial<MetricDefinition> = {}
          if (isDefaultMetricName(m) && !isBlank(a.name) && a.name !== m.name) patch.name = a.name
          if (isDefaultMetricDesc(m) && !isBlank(a.definition) && a.definition !== m.description)
            patch.description = a.definition
          if (Object.keys(patch).length) patches.set(i, patch)
        })
        if (!patches.size) return
        changed += [...patches.values()].reduce((n, p) => n + Object.keys(p).length, 0)
        setGraph((g) =>
          g
            ? { ...g, metrics: g.metrics.map((m, i) => (patches.has(i) ? { ...m, ...patches.get(i) } : m)) }
            : g
        )
        setDirty(true)
      }

      const tasks: Array<() => Promise<void>> = [
        ...entityBatches.map((batch) => async () => {
          try {
            const res = await callWithTimeout({
              ...base,
              entities: batch,
              metrics: [],
              relationships: [],
            })
            if (!signal.aborted) applyEntities(res.entities)
          } catch (e) {
            if (signal.aborted) return
            failed++
            // A missing/invalid key fails every batch the same way — abort the rest.
            if (e instanceof Error && /configured|401|403/.test(e.message)) {
              enrichAbort.current?.abort()
              throw e
            }
          } finally {
            bump()
          }
        }),
        ...metricBatches.map((batch) => async () => {
          try {
            const res = await callWithTimeout({
              ...base,
              entities: [],
              metrics: batch,
              relationships: [],
            })
            if (!signal.aborted) applyMetrics(res.metrics)
          } catch {
            if (signal.aborted) return
            failed++
          } finally {
            bump()
          }
        }),
      ]

      // Bounded concurrency — a few AI calls in flight at once, not all N (fast)
      // and not one-at-a-time (slow).
      let next = 0
      await Promise.all(
        Array.from({ length: Math.min(ENRICH_CONCURRENCY, tasks.length) }, async () => {
          while (next < tasks.length && !signal.aborted) await tasks[next++]()
        })
      )
      if (signal.aborted) return
      setProgress(null)

      const failNote = failed > 0 ? `, ${failed} batch${failed === 1 ? "" : "es"} failed` : ""
      if (changed > 0) {
        toast.success(`Enhanced ${changed} field${changed === 1 ? "" : "s"} with AI${failNote}`)
      } else {
        toast(`Nothing to enhance${failNote}`)
      }
  }

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
      setProgress(null)
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
  // Add a blank base metric at the top (visible immediately) for the user to fill.
  const addMetric = () => {
    setGraph((g) => {
      if (!g) return g
      const fresh: MetricDefinition = {
        name: "",
        description: "",
        kind: "base",
        entity: g.entities[0]?.name ?? null,
        aggregation: "count",
        column: null,
        filters: [],
        time_dimension: null,
        expression: null,
        format: null,
      }
      return { ...g, metrics: [fresh, ...g.metrics] }
    })
    setDirty(true)
  }
  const removeMetric = (idx: number) => {
    setGraph((g) => (g ? { ...g, metrics: g.metrics.filter((_, i) => i !== idx) } : g))
    setDirty(true)
  }

  // entity name → table, so a metric's `entity` resolves to its columns.
  const entityTable = useMemo(
    () => new Map((graph?.entities ?? []).map((e) => [e.name, e.table])),
    [graph]
  )
  const entityNames = useMemo(
    () => (graph?.entities ?? []).map((e) => e.name).sort(),
    [graph]
  )
  // Metric names, for referencing in a derived metric's expression.
  const metricNames = useMemo(
    () => (graph?.metrics ?? []).map((m) => m.name).filter(Boolean).sort(),
    [graph]
  )

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
            entities, dimensions and metrics for you to review.
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
      (isBlank(m.description) ? 1 : 0) +
      // a base metric that aggregates a column but has none set is incomplete
      (m.kind === "base" && needsColumn(m.aggregation) && isBlank(m.column) ? 1 : 0) +
      (m.kind === "derived" && isBlank(m.expression) ? 1 : 0),
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
          <Button
            variant="ghost"
            size="sm"
            onClick={enhance}
            disabled={action !== null || !aiConfigured}
            title={
              aiConfigured
                ? "Improve names & descriptions with AI (only untouched fields)"
                : "Configure an AI provider in Settings to enable this"
            }
          >
            {action === "enrich" ? (
              <RiLoader4Line data-icon="inline-start" className="animate-spin" />
            ) : (
              <RiMagicLine data-icon="inline-start" />
            )}
            {progress
              ? `Enhancing ${progress.done}/${progress.total}`
              : "Enhance with AI"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={generate}
            disabled={action !== null}
            title={
              aiConfigured
                ? "Rebuild from schema, then AI-name it in the background"
                : "Rebuild the structural draft from the schema (instant)"
            }
          >
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
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </EmptyNavScroll>
        </TabsContent>

        <TabsContent value="metrics" className="flex min-h-0 flex-1 flex-col">
          {/* Metric names, for referencing inside a derived metric's expression. */}
          <datalist id="semantic-metric-names">
            {metricNames.map((n) => (
              <option key={n} value={n} />
            ))}
          </datalist>
          <EmptyNavScroll
            emptyCount={metricEmpties}
            action={
              <Button variant="outline" size="sm" onClick={addMetric}>
                <RiAddLine data-icon="inline-start" />
                Add metric
              </Button>
            }
          >
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-background">
                <TableRow>
                  <TableHead className="w-10 text-right">#</TableHead>
                  <TableHead className="w-48">Name</TableHead>
                  <TableHead className="w-24">Kind</TableHead>
                  <TableHead className="w-[22rem]">Recipe</TableHead>
                  <TableHead className="w-36">Time</TableHead>
                  <TableHead className="w-28">Format</TableHead>
                  <TableHead>Definition</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {graph.metrics.map((m, i) => {
                  const cols = columnsByTable.get(entityTable.get(m.entity ?? "") ?? "") ?? []
                  const aggCols = cols.filter((c) =>
                    m.aggregation && m.aggregation !== "count_distinct"
                      ? isNumericType(c.data_type)
                      : true
                  )
                  const timeCols = cols.filter((c) => isTemporalType(c.data_type))
                  return (
                    <TableRow key={`${metricKey(m)}-${i}`}>
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
                        <MiniSelect
                          value={m.kind}
                          onChange={(v) => editMetric(i, { kind: v as MetricDefinition["kind"] })}
                          label={`Metric kind ${i + 1}`}
                          options={[
                            { value: "base", label: "base" },
                            { value: "derived", label: "derived" },
                          ]}
                        />
                      </TableCell>
                      <TableCell>
                        {m.kind === "derived" ? (
                          <EditCell
                            value={m.expression ?? ""}
                            onChange={(v) => editMetric(i, { expression: v })}
                            label={`Metric expression ${i + 1}`}
                            placeholder="Revenue / Order Count"
                            mono
                            list="semantic-metric-names"
                          />
                        ) : (
                          <div className="flex items-center gap-1.5">
                            <MiniSelect
                              value={m.entity ?? ""}
                              onChange={(v) => editMetric(i, { entity: v })}
                              label={`Metric entity ${i + 1}`}
                              placeholder="entity…"
                              options={entityNames.map((n) => ({ value: n, label: n }))}
                              className="w-32"
                            />
                            <MiniSelect
                              value={m.aggregation ?? ""}
                              onChange={(v) =>
                                editMetric(i, { aggregation: v as Aggregation })
                              }
                              label={`Metric aggregation ${i + 1}`}
                              placeholder="agg…"
                              options={AGGREGATIONS.map((a) => ({ value: a, label: a }))}
                              className="w-28"
                            />
                            {needsColumn(m.aggregation) ? (
                              <MiniSelect
                                value={m.column ?? ""}
                                onChange={(v) => editMetric(i, { column: v })}
                                label={`Metric column ${i + 1}`}
                                placeholder="column…"
                                empty={isBlank(m.column)}
                                options={aggCols.map((c) => ({
                                  value: c.name,
                                  label: c.name,
                                }))}
                                className="min-w-24 flex-1"
                              />
                            ) : (
                              <span className="text-xs text-muted-foreground">—</span>
                            )}
                          </div>
                        )}
                      </TableCell>
                      <TableCell>
                        {m.kind === "base" ? (
                          <MiniSelect
                            value={m.time_dimension ?? ""}
                            onChange={(v) => editMetric(i, { time_dimension: v || null })}
                            label={`Metric time ${i + 1}`}
                            placeholder="—"
                            options={timeCols.map((c) => ({ value: c.name, label: c.name }))}
                          />
                        ) : (
                          <Dash />
                        )}
                      </TableCell>
                      <TableCell>
                        <MiniSelect
                          value={m.format ?? ""}
                          onChange={(v) => editMetric(i, { format: v || null })}
                          label={`Metric format ${i + 1}`}
                          placeholder="—"
                          options={FORMATS.map((f) => ({ value: f, label: f }))}
                        />
                      </TableCell>
                      <TableCell>
                        <EditCell
                          value={m.description ?? ""}
                          onChange={(v) => editMetric(i, { description: v })}
                          label={`Metric definition ${i + 1}`}
                          placeholder="Business definition…"
                        />
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="size-7 p-0 text-muted-foreground hover:text-destructive"
                          onClick={() => removeMetric(i)}
                          aria-label={`Delete metric ${i + 1}`}
                        >
                          <RiCloseLine />
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </EmptyNavScroll>
        </TabsContent>

        <TabsContent value="relationships" className="flex min-h-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1 overflow-auto border [&_[data-slot=table-container]]:overflow-visible">
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

/** Lightweight native <select> styled like EditCell — cheap enough for hundreds
 * of rows (Radix Select would not be). `empty` tints + tags it for the nav. */
function MiniSelect({
  value,
  onChange,
  options,
  label,
  placeholder,
  className,
  empty,
}: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  label: string
  placeholder?: string
  className?: string
  empty?: boolean
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={label}
      data-empty={empty ? "true" : undefined}
      className={cn(
        "h-8 rounded-md border border-border/50 bg-transparent px-2 text-sm outline-none transition-colors hover:border-border focus-visible:border-border",
        empty && "border-accent-brand/50 bg-accent-brand/5",
        className
      )}
    >
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
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
  action,
}: {
  emptyCount: number
  children: React.ReactNode
  action?: React.ReactNode
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
        {action && <div className="ml-auto">{action}</div>}
      </div>
      {/* This div is the sole scroller (both axes): neutralise shadcn's inner
          table-container overflow so the sticky <thead> sticks to THIS box. */}
      <div
        ref={ref}
        className="relative min-h-0 flex-1 overflow-auto border [&_[data-slot=table-container]]:overflow-visible"
      >
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
