"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  RiAddLine,
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
  type GenerationJob,
  getAIConfig,
  getJob,
  getSchema,
  getSemanticDraft,
  type MetricDefinition,
  publishSemantic,
  saveSemanticDraft,
  type SemanticGraph,
  startEnhance,
  startGenerate,
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
import { Textarea } from "@/components/ui/textarea"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

type Status = "loading" | "error" | "empty" | "generating" | "ready"

const isBlank = (v: string | null | undefined) => !v || v.trim() === ""

const humanize = (id: string) =>
  id
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w))
    .join(" ")

/** Stable id for a metric (used for React keys). Matches the backend. */
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
  const [action, setAction] = useState<"save" | "publish" | "delete" | null>(null)
  // Build-job progress (server-side generate/enhance); drives the loading readout.
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  // Columns per table, for the metric column pickers — loaded once from the schema.
  const [columnsByTable, setColumnsByTable] = useState<Map<string, ColumnInfo[]>>(new Map())
  // Whether an AI provider is configured — gates auto-enhance on generate.
  const [aiConfigured, setAiConfigured] = useState(false)
  // Master–detail selection: which entity / metric row is open in the editor.
  const [selEntity, setSelEntity] = useState(0)
  const [selMetric, setSelMetric] = useState(0)
  // Cancel polling of an in-flight build job when the panel unmounts (source
  // switch). The job itself keeps running server-side.
  const jobAbort = useRef<AbortController | null>(null)
  useEffect(() => () => jobAbort.current?.abort(), [])

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

  // Generate = one server-side job (heuristic + AI enrichment when configured).
  // The panel shows a loading state while it runs and reveals the finished model
  // in one go — no heuristic-then-AI flicker.
  const generate = () => void runJob(() => startGenerate(source, aiConfigured))

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
  // Re-run AI enrichment on the current draft as a background job (keeps edits).
  const enhance = () => void runJob(() => startEnhance(source))

  const sleep = (ms: number, signal: AbortSignal) =>
    new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, ms)
      signal.addEventListener("abort", () => {
        clearTimeout(timer)
        resolve()
      })
    })

  // Submit a build job, then poll it. The panel shows a loading state with
  // progress; when the job is done the saved draft is reloaded — one clean
  // reveal instead of heuristic-then-AI. The job runs on the server regardless
  // of whether the client is watching.
  async function runJob(start: () => Promise<GenerationJob>) {
    jobAbort.current?.abort()
    const controller = new AbortController()
    jobAbort.current = controller
    const signal = controller.signal
    setStatus("generating")
    setProgress({ done: 0, total: 0 })
    try {
      const job = await start()
      if (signal.aborted) return
      setProgress({ done: job.done, total: job.total })
      while (!signal.aborted) {
        await sleep(1000, signal)
        if (signal.aborted) return
        const j = await getJob(source, job.id, signal)
        if (signal.aborted) return
        setProgress({ done: j.done, total: j.total })
        if (j.status === "done") {
          await load()
          return
        }
        if (j.status === "error") {
          toast.error(j.error ?? "Generation failed")
          await load()
          return
        }
      }
    } catch (e) {
      if (!signal.aborted) {
        toast.error(e instanceof Error ? e.message : "Generation failed")
        await load()
      }
    } finally {
      setProgress(null)
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
    setSelMetric(0) // the new blank metric sits at the top; open it
    setDirty(true)
  }
  const removeMetric = (idx: number) => {
    setGraph((g) => (g ? { ...g, metrics: g.metrics.filter((_, i) => i !== idx) } : g))
    setSelMetric((s) => (idx < s ? s - 1 : s))
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

  if (status === "generating") {
    const pct =
      progress && progress.total > 0
        ? Math.round((progress.done / progress.total) * 100)
        : null
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 border bg-wash px-6 py-14 text-center">
        <RiLoader4Line className="size-6 animate-spin text-accent-brand" />
        <div className="flex flex-col items-center gap-2">
          <span className="text-sm font-medium">
            {progress && progress.total > 0
              ? `Building model — ${progress.done}/${progress.total}`
              : "Building model…"}
          </span>
          {pct !== null && (
            <div className="h-1.5 w-56 overflow-hidden rounded-full bg-border">
              <div
                className="h-full bg-accent-brand transition-[width] duration-300"
                style={{ width: `${pct}%` }}
              />
            </div>
          )}
          <p className="max-w-sm text-xs text-balance text-muted-foreground">
            Heuristic draft + AI naming. This runs on the server and keeps going
            if you leave — come back to see the result.
          </p>
        </div>
      </div>
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
          <RiSparkling2Line data-icon="inline-start" />
          Generate model
        </Button>
      </div>
    )
  }

  const published = graph.published
  // Selection clamped to the current list length (a regenerate can shrink it).
  const eIdx = graph.entities.length
    ? Math.min(selEntity, graph.entities.length - 1)
    : 0
  const mIdx = graph.metrics.length
    ? Math.min(selMetric, graph.metrics.length - 1)
    : 0

  // Per-row "has a blank field" flags — drive the dot in the master list and
  // the total counts shown above each list.
  const entityEmpty = (e: SemanticGraph["entities"][number]) =>
    isBlank(e.name) || isBlank(e.description)
  const metricEmpty = (m: MetricDefinition) =>
    isBlank(m.name) ||
    isBlank(m.description) ||
    (m.kind === "base" && needsColumn(m.aggregation) && isBlank(m.column)) ||
    (m.kind === "derived" && isBlank(m.expression))
  const entityEmpties = graph.entities.filter(entityEmpty).length
  const metricEmpties = graph.metrics.filter(metricEmpty).length

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
            <RiMagicLine data-icon="inline-start" />
            Enhance with AI
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
            <RiSparkling2Line data-icon="inline-start" />
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
          <MasterDetail
            header={<EmptyCount n={entityEmpties} />}
            list={
              <MasterList
                items={graph.entities.map((e, i) => ({
                  key: `${e.table}-${i}`,
                  title: e.name || humanize(e.table),
                  subtitle: e.table,
                  empty: entityEmpty(e),
                }))}
                selected={eIdx}
                onSelect={setSelEntity}
              />
            }
          >
            {(() => {
              const e = graph.entities[eIdx]
              if (!e) return null
              return (
                <>
                  <FormField label="Name">
                    <EditCell
                      value={e.name}
                      onChange={(v) => editEntity(eIdx, { name: v })}
                      label="Entity name"
                      className="font-medium"
                    />
                  </FormField>
                  <FormField label="Table" hint="Source table — read-only.">
                    <ReadOnlyValue>{e.table}</ReadOnlyValue>
                  </FormField>
                  <FormField label="Description">
                    <EditArea
                      value={e.description ?? ""}
                      onChange={(v) => editEntity(eIdx, { description: v })}
                      label="Entity description"
                      placeholder="What this entity means to the business…"
                    />
                  </FormField>
                  <p className="text-xs text-muted-foreground">
                    {e.dimensions.length} dimension
                    {e.dimensions.length === 1 ? "" : "s"}
                  </p>
                </>
              )
            })()}
          </MasterDetail>
        </TabsContent>

        <TabsContent value="metrics" className="flex min-h-0 flex-1 flex-col">
          {/* Metric names, for referencing inside a derived metric's expression. */}
          <datalist id="semantic-metric-names">
            {metricNames.map((n) => (
              <option key={n} value={n} />
            ))}
          </datalist>
          <MasterDetail
            header={
              <>
                <EmptyCount n={metricEmpties} />
                <Button
                  variant="outline"
                  size="sm"
                  className="ml-auto"
                  onClick={addMetric}
                >
                  <RiAddLine data-icon="inline-start" />
                  Add metric
                </Button>
              </>
            }
            list={
              <MasterList
                items={graph.metrics.map((m, i) => ({
                  key: `${metricKey(m)}-${i}`,
                  title: m.name || "(unnamed metric)",
                  subtitle: recipeSummary(m),
                  empty: metricEmpty(m),
                }))}
                selected={mIdx}
                onSelect={setSelMetric}
              />
            }
          >
            {(() => {
              const m = graph.metrics[mIdx]
              if (!m) return null
              const cols =
                columnsByTable.get(entityTable.get(m.entity ?? "") ?? "") ?? []
              const aggCols = cols.filter((c) =>
                m.aggregation && m.aggregation !== "count_distinct"
                  ? isNumericType(c.data_type)
                  : true
              )
              const timeCols = cols.filter((c) => isTemporalType(c.data_type))
              return (
                <>
                  <div className="flex items-center justify-between gap-2">
                    <span className="tnum text-xs text-muted-foreground">
                      Metric {mIdx + 1} of {graph.metrics.length}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-muted-foreground hover:text-destructive"
                      onClick={() => removeMetric(mIdx)}
                    >
                      <RiDeleteBinLine data-icon="inline-start" />
                      Delete
                    </Button>
                  </div>

                  <FormField label="Name">
                    <EditCell
                      value={m.name}
                      onChange={(v) => editMetric(mIdx, { name: v })}
                      label="Metric name"
                      className="font-medium"
                    />
                  </FormField>

                  <FormField label="Kind">
                    <MiniSelect
                      value={m.kind}
                      onChange={(v) =>
                        editMetric(mIdx, { kind: v as MetricDefinition["kind"] })
                      }
                      label="Metric kind"
                      options={[
                        { value: "base", label: "base — aggregate a column" },
                        { value: "derived", label: "derived — formula" },
                      ]}
                      className="w-full"
                    />
                  </FormField>

                  {m.kind === "derived" ? (
                    <FormField
                      label="Expression"
                      hint="Combine other metrics by name, e.g. Revenue / Order Count."
                    >
                      <EditCell
                        value={m.expression ?? ""}
                        onChange={(v) => editMetric(mIdx, { expression: v })}
                        label="Metric expression"
                        placeholder="Revenue / Order Count"
                        mono
                        list="semantic-metric-names"
                      />
                    </FormField>
                  ) : (
                    <>
                      <FormField label="Entity">
                        <MiniSelect
                          value={m.entity ?? ""}
                          onChange={(v) => editMetric(mIdx, { entity: v })}
                          label="Metric entity"
                          placeholder="Select an entity…"
                          empty={isBlank(m.entity)}
                          options={entityNames.map((n) => ({
                            value: n,
                            label: n,
                          }))}
                          className="w-full"
                        />
                      </FormField>
                      <FormField label="Aggregation">
                        <MiniSelect
                          value={m.aggregation ?? ""}
                          onChange={(v) =>
                            editMetric(mIdx, { aggregation: v as Aggregation })
                          }
                          label="Metric aggregation"
                          placeholder="Select…"
                          options={AGGREGATIONS.map((a) => ({
                            value: a,
                            label: a,
                          }))}
                          className="w-full"
                        />
                      </FormField>
                      {needsColumn(m.aggregation) && (
                        <FormField label="Column">
                          <MiniSelect
                            value={m.column ?? ""}
                            onChange={(v) => editMetric(mIdx, { column: v })}
                            label="Metric column"
                            placeholder="Select a column…"
                            empty={isBlank(m.column)}
                            options={aggCols.map((c) => ({
                              value: c.name,
                              label: c.name,
                            }))}
                            className="w-full"
                          />
                        </FormField>
                      )}
                      <FormField label="Time dimension" hint="Optional.">
                        <MiniSelect
                          value={m.time_dimension ?? ""}
                          onChange={(v) =>
                            editMetric(mIdx, { time_dimension: v || null })
                          }
                          label="Metric time dimension"
                          placeholder="None"
                          options={timeCols.map((c) => ({
                            value: c.name,
                            label: c.name,
                          }))}
                          className="w-full"
                        />
                      </FormField>
                    </>
                  )}

                  <FormField label="Format" hint="Optional.">
                    <MiniSelect
                      value={m.format ?? ""}
                      onChange={(v) => editMetric(mIdx, { format: v || null })}
                      label="Metric format"
                      placeholder="None"
                      options={FORMATS.map((f) => ({ value: f, label: f }))}
                      className="w-full"
                    />
                  </FormField>

                  <FormField label="Definition">
                    <EditArea
                      value={m.description ?? ""}
                      onChange={(v) => editMetric(mIdx, { description: v })}
                      label="Metric definition"
                      placeholder="What this metric means to the business…"
                    />
                  </FormField>
                </>
              )
            })()}
          </MasterDetail>
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

/** One-line summary of a metric's recipe, shown under its name in the list. */
function recipeSummary(m: MetricDefinition): string {
  if (m.kind === "derived") return m.expression || "= …"
  const col = m.column ? `.${m.column}` : ""
  return `${m.aggregation ?? "?"}(${m.entity ?? "?"}${col})`
}

/** Master–detail layout: a scrollable list on the left, the selected item's
 * editor on the right. Replaces the old wide table whose columns truncated. */
function MasterDetail({
  header,
  list,
  children,
}: {
  header: React.ReactNode
  list: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {header}
      </div>
      <div className="grid min-h-0 flex-1 gap-4 md:grid-cols-[minmax(0,15rem)_1fr]">
        {list}
        <div className="min-h-0 overflow-y-auto">
          <div className="flex max-w-2xl flex-col gap-4 pr-1 pb-4">
            {children}
          </div>
        </div>
      </div>
    </div>
  )
}

function MasterList({
  items,
  selected,
  onSelect,
}: {
  items: { key: string; title: string; subtitle?: string; empty: boolean }[]
  selected: number
  onSelect: (i: number) => void
}) {
  return (
    <div className="flex min-h-0 flex-col overflow-y-auto border">
      <ul className="flex flex-col">
        {items.map((it, i) => (
          <li key={it.key}>
            <button
              type="button"
              onClick={() => onSelect(i)}
              aria-current={selected === i ? "true" : undefined}
              className={cn(
                "flex w-full flex-col gap-0.5 border-b border-l-2 border-l-transparent px-3 py-2 text-left transition-colors",
                selected === i
                  ? "border-l-accent-brand bg-accent-brand/15"
                  : "hover:bg-accent-brand/8"
              )}
            >
              <span className="flex min-w-0 items-center gap-2">
                {it.empty && (
                  <span
                    className="size-1.5 shrink-0 rounded-full bg-accent-brand"
                    title="has empty fields"
                  />
                )}
                <span
                  className={cn(
                    "truncate text-sm",
                    selected === i && "font-medium"
                  )}
                >
                  {it.title}
                </span>
              </span>
              {it.subtitle && (
                <span className="truncate font-mono text-xs text-muted-foreground">
                  {it.subtitle}
                </span>
              )}
            </button>
          </li>
        ))}
        {items.length === 0 && (
          <li className="p-3 text-sm text-muted-foreground">Nothing here.</li>
        )}
      </ul>
    </div>
  )
}

/** One labelled field in the editor form. */
function FormField({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

function ReadOnlyValue({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-9 items-center rounded-md border border-border/50 bg-muted/40 px-3 font-mono text-sm text-muted-foreground">
      {children}
    </div>
  )
}

function EmptyCount({ n }: { n: number }) {
  return (
    <span className={cn(n > 0 && "text-accent-brand")}>
      {n} empty field{n === 1 ? "" : "s"}
    </span>
  )
}

/** A multi-line editable field. Blank tints + tags for parity with EditCell. */
function EditArea({
  value,
  onChange,
  label,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  label: string
  placeholder?: string
}) {
  const empty = isBlank(value)
  return (
    <Textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={label}
      placeholder={placeholder}
      rows={3}
      data-empty={empty ? "true" : undefined}
      className={cn(
        "resize-y border-border/50 bg-transparent shadow-none hover:border-border focus-visible:border-border",
        empty && "border-accent-brand/50 bg-accent-brand/5"
      )}
    />
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
