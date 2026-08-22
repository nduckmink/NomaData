"use client"

/**
 * Per-source semantic model: build, review, edit, validate, publish.
 *
 * Scoped to one data source — the cross-source overview lives at /semantic.
 */

import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  RiAddLine,
  RiAlertLine,
  RiInformationLine,
  RiCheckLine,
  RiDeleteBinLine,
  RiEyeOffLine,
  RiLoader4Line,
  RiLockLine,
  RiLockUnlockLine,
  RiNodeTree,
  RiSaveLine,
  RiSparkling2Line,
  RiTranslate2,
  RiUploadLine,
} from "@remixicon/react"
import { toast } from "sonner"

import {
  deleteSemantic,
  type Dimension,
  type Entity,
  buildPhase,
  type GenerationJob,
  getActiveJob,
  getAIConfig,
  getJob,
  getSemanticDraft,
  type MetricDefinition,
  publishSemantic,
  saveSemanticDraft,
  type SemanticGraph,
  draftEntity,
  startGenerate,
  type ValidationIssue,
  validateSemantic,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

import { BuildModelDialog } from "./build-model-dialog"
import { MetricEditor, metricIncomplete, recipeSummary } from "./metric-editor"
import { RelationshipEditor } from "./relationship-editor"
import { SuggestMetricsButton } from "./suggest-metrics-dialog"
import {
  EditArea,
  EditCell,
  FormField,
  isBlank,
  DraftNotes,
  MasterDetail,
  MasterList,
  PromptBox,
  relSignature,
  TabLabel,
  ReadOnlyValue,
  USER,
} from "./semantic-fields"

type Status = "loading" | "error" | "empty" | "generating" | "ready"

const humanize = (id: string) =>
  id
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w))
    .join(" ")

const ACTION_FAILED = {
  save: "Could not save the draft",
  publish: "Cannot publish this model",
  delete: "Could not delete the model",
} as const

/** Show a server message that may be several lines.
 *
 *  A failed publish lists every metric blocking it; a toast title is one line,
 *  so the detail goes in `description` where it stays readable instead of being
 *  clipped into uselessness. */
function showError(title: string, error: unknown) {
  const text = error instanceof Error ? error.message : ""
  const [first, ...rest] = text.split("\n")
  const description = rest.length
    ? rest.join("\n")
    : first && first !== title
      ? first
      : undefined
  toast.error(rest.length ? first || title : title, {
    description,
    duration: rest.length ? 10_000 : undefined,
  })
}

const emptyMetric = (entityKey: string | null): MetricDefinition => ({
  id: crypto.randomUUID().replace(/-/g, ""),
  name: "",
  description: "",
  kind: "base",
  entity_key: entityKey,
  aggregation: "count",
  column: null,
  filters: [],
  time_dimension: null,
  expression: null,
  format: null,
  provenance: USER,
})

export function SemanticPanel({ source }: { source: string }) {
  const [status, setStatus] = useState<Status>("loading")
  const [graph, setGraph] = useState<SemanticGraph | null>(null)
  // The last state the server has. Diffing against it is what lets the list say
  // *which* rows are unsaved, instead of one flag saying that something is.
  const [saved, setSaved] = useState<SemanticGraph | null>(null)
  const [dirty, setDirty] = useState(false)
  const [tab, setTab] = useState("entities")
  // What is actually rendered. React lets this fall behind while a heavy tab
  // builds, which is what makes the switch itself feel instant.
  const shownTab = useDeferredValue(tab)
  const switching = shownTab !== tab

  const [action, setAction] = useState<"save" | "publish" | "delete" | null>(
    null
  )
  // The whole job, not two numbers: a build runs two stages with separate
  // counters, and which stage it is in is the thing worth showing.
  const [running, setRunning] = useState<GenerationJob | null>(null)
  const [aiConfigured, setAiConfigured] = useState(false)
  const [issues, setIssues] = useState<ValidationIssue[]>([])
  const [selEntity, setSelEntity] = useState(0)
  const [selMetric, setSelMetric] = useState(0)
  const [entityQuery, setEntityQuery] = useState("")
  const [metricQuery, setMetricQuery] = useState("")

  const jobAbort = useRef<AbortController | null>(null)
  useEffect(() => () => jobAbort.current?.abort(), [])

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const cfg = await getAIConfig(controller.signal)
        if (!controller.signal.aborted) setAiConfigured(!!cfg?.configured)
      } catch {
        // Leave false — the heuristic path still works without AI.
      }
    })()
    return () => controller.abort()
  }, [])

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const g = await getSemanticDraft(source, signal)
        if (signal?.aborted) return
        setGraph(g)
        setSaved(g)
        setDirty(false)
        setStatus(g ? "ready" : "empty")
      } catch {
        if (!signal?.aborted) setStatus("error")
      }
    },
    [source]
  )

  const sleep = (ms: number, signal: AbortSignal) =>
    new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, ms)
      signal.addEventListener("abort", () => {
        clearTimeout(timer)
        resolve()
      })
    })

  const watchJob = useCallback(
    async (initial: GenerationJob) => {
      jobAbort.current?.abort()
      const controller = new AbortController()
      jobAbort.current = controller
      const signal = controller.signal
      setStatus("generating")
      setRunning(initial)
      let job = initial
      try {
        while (!signal.aborted) {
          if (job.status === "done") {
            // A build can succeed with some naming batches skipped. Saying so
            // is the difference between "the AI named it badly" and "the AI
            // never saw it" — the fix for each is different.
            if (job.failed_batches > 0) {
              toast.warning(
                `Built, but ${job.failed_batches} naming batch(es) failed — ` +
                  "those entities kept their default names. Ask for a name on " +
                  "each one, or rebuild.",
                { description: job.last_batch_error ?? undefined }
              )
            }
            // A column that could not be sampled is a column whose values the
            // model never learns. It still works; it just knows less, and the
            // filter picker for that column will come up empty.
            if (job.unprofiled_columns > 0) {
              toast.warning(
                `${job.unprofiled_columns} of ${job.profile_total} columns could not be ` +
                  "sampled, so their values are unknown."
              )
            }
            await load()
            return
          }
          if (job.status === "error") {
            toast.error(job.error ?? "Generation failed")
            await load()
            return
          }
          await sleep(1000, signal)
          if (signal.aborted) return
          job = await getJob(source, job.id, signal)
          if (signal.aborted) return
          setRunning(job)
        }
      } catch (e) {
        if (!signal.aborted) {
          toast.error(e instanceof Error ? e.message : "Generation failed")
          await load()
        }
      } finally {
        setRunning(null)
      }
    },
    [source, load]
  )

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const active = await getActiveJob(source, controller.signal)
        if (controller.signal.aborted) return
        if (active && active.status === "running") {
          await watchJob(active)
          return
        }
      } catch {
        // Fall through to a normal load.
      }
      await load(controller.signal)
    })()
    return () => controller.abort()
  }, [source, load, watchJob])

  // Re-validate whenever the model changes, so problems are visible while
  // editing rather than only at the publish attempt.
  useEffect(() => {
    if (!graph) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const report = await validateSemantic(
            source,
            graph,
            controller.signal
          )
          if (!controller.signal.aborted) setIssues(report.issues)
        } catch {
          // Validation is advisory here; the publish call re-checks anyway.
        }
      })()
    }, 400)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [graph, source])

  const generate = (tables: string[], keepEdits: boolean) =>
    void (async () => {
      try {
        await watchJob(
          await startGenerate(source, aiConfigured, { tables, keepEdits })
        )
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Generation failed")
        await load()
      }
    })()

  const save = () =>
    void run("save", async () => {
      if (!graph) return
      const stored = await saveSemanticDraft(source, graph)
      setGraph(stored)
      setSaved(stored)
      setDirty(false)
      toast.success("Draft saved")
    })

  const publish = () =>
    void run("publish", async () => {
      if (!graph) return
      const res = await publishSemantic(source, graph)
      toast.success(`Published v${res.version}`)
      await load()
    })

  const remove = () =>
    void run("delete", async () => {
      await deleteSemantic(source)
      setGraph(null)
      setStatus("empty")
      toast.success("Semantic model deleted")
    })

  async function run(
    kind: NonNullable<typeof action>,
    fn: () => Promise<void>
  ) {
    setAction(kind)
    try {
      await fn()
    } catch (e) {
      showError(ACTION_FAILED[kind], e)
    } finally {
      setAction(null)
      setRunning(null)
    }
  }

  const editEntity = (key: string, patch: Partial<Entity>) => {
    setGraph((g) =>
      g
        ? {
            ...g,
            entities: g.entities.map((e) =>
              e.key === key ? { ...e, ...patch } : e
            ),
          }
        : g
    )
    setDirty(true)
  }

  const editDimension = (
    entityKey: string,
    column: string,
    patch: Partial<Dimension>
  ) => {
    setGraph((g) =>
      g
        ? {
            ...g,
            entities: g.entities.map((e) =>
              e.key === entityKey
                ? {
                    ...e,
                    dimensions: e.dimensions.map((d) =>
                      d.column === column ? { ...d, ...patch } : d
                    ),
                  }
                : e
            ),
          }
        : g
    )
    setDirty(true)
  }

  const editMetric = (id: string, patch: Partial<MetricDefinition>) => {
    setGraph((g) =>
      g
        ? {
            ...g,
            metrics: g.metrics.map((m) =>
              m.id === id ? { ...m, ...patch } : m
            ),
          }
        : g
    )
    setDirty(true)
  }

  const addMetric = () => {
    setGraph((g) => {
      if (!g) return g
      return {
        ...g,
        metrics: [emptyMetric(g.entities[0]?.key ?? null), ...g.metrics],
      }
    })
    setSelMetric(0)
    setMetricQuery("")
    setDirty(true)
  }

  const setRelationships = (relationships: SemanticGraph["relationships"]) => {
    setGraph((g) => (g ? { ...g, relationships } : g))
    setDirty(true)
  }

  const addMetrics = (metrics: MetricDefinition[]) => {
    if (!metrics.length) return
    setGraph((g) => (g ? { ...g, metrics: [...metrics, ...g.metrics] } : g))
    setSelMetric(0)
    setMetricQuery("")
    setDirty(true)
  }

  const removeMetric = (id: string) => {
    setGraph((g) =>
      g ? { ...g, metrics: g.metrics.filter((m) => m.id !== id) } : g
    )
    setSelMetric((s) => Math.max(0, s - 1))
    setDirty(true)
  }

  // Compared by value against the saved copy. Any edit also stamps
  // `provenance.origin = "user"`, so an object that looks identical really is.
  const changes = useMemo(() => {
    const before = new Map(
      (saved?.entities ?? []).map((e) => [e.key, JSON.stringify(e)])
    )
    const beforeMetrics = new Map(
      (saved?.metrics ?? []).map((m) => [m.id, JSON.stringify(m)])
    )
    const entities = new Map<string, "new" | "edited">()
    for (const e of graph?.entities ?? []) {
      const prior = before.get(e.key)
      if (prior === undefined) entities.set(e.key, "new")
      else if (prior !== JSON.stringify(e)) entities.set(e.key, "edited")
    }
    const metrics = new Map<string, "new" | "edited">()
    for (const m of graph?.metrics ?? []) {
      const prior = beforeMetrics.get(m.id)
      if (prior === undefined) metrics.set(m.id, "new")
      else if (prior !== JSON.stringify(m)) metrics.set(m.id, "edited")
    }
    // Relationships have no stable id, so they are diffed by signature. A row
    // whose signature is not in the saved set is unsaved (added, found, or
    // edited — an edit changes the signature); the count also picks up removals.
    const savedRelSigs = new Set((saved?.relationships ?? []).map(relSignature))
    const liveRelSigs = new Set((graph?.relationships ?? []).map(relSignature))
    let relationships = 0
    for (const s of liveRelSigs) if (!savedRelSigs.has(s)) relationships++
    for (const s of savedRelSigs) if (!liveRelSigs.has(s)) relationships++
    return { entities, metrics, relationships, savedRelSigs }
  }, [graph, saved])

  const entityNames = useMemo(
    () => new Map((graph?.entities ?? []).map((e) => [e.key, e.name])),
    [graph]
  )
  const metricNames = useMemo(
    () =>
      [
        ...new Set((graph?.metrics ?? []).map((m) => m.name).filter(Boolean)),
      ].sort(),
    [graph]
  )

  const errors = issues.filter((i) => i.level === "error")
  const warnings = issues.filter((i) => i.level === "warning")

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
    const phase = running
      ? buildPhase(running)
      : { label: "Building your model…", percent: null }
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 border bg-wash px-6 py-14 text-center">
        <RiLoader4Line className="size-6 animate-spin text-accent-brand" />
        <div className="flex flex-col items-center gap-2">
          <span className="text-sm font-medium">{phase.label}</span>
          {phase.percent !== null && (
            <div className="h-1.5 w-56 overflow-hidden rounded-full bg-border">
              <div
                className="h-full bg-accent-brand transition-[width] duration-300"
                style={{ width: `${phase.percent}%` }}
              />
            </div>
          )}
          <p className="max-w-sm text-xs text-balance text-muted-foreground">
            Reading the schema, sampling column values, then naming everything.
            You can leave and come back — we&apos;ll keep working on it.
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
        <BuildModelDialog
          source={source}
          mode="generate"
          aiConfigured={aiConfigured}
          disabled={action !== null}
          onBuild={(tables) => generate(tables, true)}
        >
          <Button variant="outline" size="sm">
            <RiSparkling2Line data-icon="inline-start" />
            Generate model
          </Button>
        </BuildModelDialog>
      </div>
    )
  }

  const visibleEntities = graph.entities.filter((e) =>
    matches(entityQuery, e.name, e.table)
  )
  const visibleMetrics = graph.metrics.filter((m) =>
    matches(
      metricQuery,
      m.name,
      m.description ?? "",
      entityNames.get(m.entity_key ?? "") ?? ""
    )
  )
  const entity =
    visibleEntities[Math.min(selEntity, visibleEntities.length - 1)]
  const metric = visibleMetrics[Math.min(selMetric, visibleMetrics.length - 1)]

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <Badge variant={graph.published ? "default" : "secondary"}>
            {graph.published ? "published" : "draft"}
          </Badge>
          <span className="tnum">v{graph.version}</span>
          <ModelNotes
            errors={errors}
            warnings={warnings}
            skipped={graph.skipped_tables}
          />
          {dirty &&
            (() => {
              const n =
                changes.entities.size +
                changes.metrics.size +
                changes.relationships
              return (
                <span className="text-accent-brand">
                  {n || ""} unsaved change{n === 1 ? "" : "s"}
                </span>
              )
            })()}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <BuildModelDialog
            source={source}
            mode="edit"
            aiConfigured={aiConfigured}
          >
            <Button
              variant="ghost"
              size="sm"
              title="Tell the AI about this business"
            >
              <RiTranslate2 data-icon="inline-start" />
              Business context
            </Button>
          </BuildModelDialog>
          <BuildModelDialog
            source={source}
            mode="rebuild"
            aiConfigured={aiConfigured}
            disabled={action !== null}
            scope={graph.scope_tables}
            onBuild={(tables) => generate(tables, true)}
          >
            <Button variant="ghost" size="sm">
              <RiSparkling2Line data-icon="inline-start" />
              Rebuild
            </Button>
          </BuildModelDialog>
          <Button
            variant="outline"
            size="sm"
            onClick={save}
            disabled={action !== null || !dirty}
          >
            {action === "save" ? (
              <RiLoader4Line
                data-icon="inline-start"
                className="animate-spin"
              />
            ) : (
              <RiSaveLine data-icon="inline-start" />
            )}
            Save draft
          </Button>
          <PublishButton
            errors={errors.length}
            warnings={warnings.length}
            graph={graph}
            busy={action === "publish"}
            // Nothing to publish when the live model already matches what is on
            // screen — re-publishing an unchanged model would only bump the
            // version for no reason.
            nothingToPublish={graph.published && !dirty}
            disabled={
              action !== null ||
              errors.length > 0 ||
              (graph.published && !dirty)
            }
            onConfirm={publish}
          />
          <DeleteButton
            onConfirm={remove}
            busy={action === "delete"}
            source={source}
          />
        </div>
      </div>

      {/* The tab is controlled, and what it renders lags behind on purpose.
          Relationships is 183 rows of five dropdowns each; switching to it
          spent several seconds on the old tab before the new one appeared, so
          the click read as a page that had failed. React paints the new tab
          first and builds its contents after, and the gap is a spinner. */}
      <Tabs
        value={tab}
        onValueChange={setTab}
        className="flex min-h-0 flex-1 flex-col gap-3"
      >
        <TabsList>
          <TabsTrigger value="entities">
            <TabLabel
              label="Entities"
              count={graph.entities.length}
              unsaved={changes.entities.size}
            />
          </TabsTrigger>
          <TabsTrigger value="metrics">
            <TabLabel
              label="Metrics"
              count={graph.metrics.length}
              unsaved={changes.metrics.size}
            />
          </TabsTrigger>
          <TabsTrigger value="relationships">
            <TabLabel
              label="Relationships"
              count={graph.relationships.length}
              unsaved={changes.relationships}
            />
          </TabsTrigger>
        </TabsList>

        <TabsContent value="entities" className="flex min-h-0 flex-1 flex-col">
          <MasterDetail
            header={
              <SearchBar
                value={entityQuery}
                onChange={(v) => {
                  setEntityQuery(v)
                  setSelEntity(0)
                }}
                placeholder="Search entities…"
                count={visibleEntities.length}
                total={graph.entities.length}
              />
            }
            list={
              <MasterList
                items={visibleEntities.map((e) => ({
                  key: e.key,
                  title: e.name || humanize(e.table),
                  subtitle: e.table,
                  empty: isBlank(e.name) || isBlank(e.description),
                  dirty: changes.entities.get(e.key) === "edited",
                  isNew: changes.entities.get(e.key) === "new",
                }))}
                selected={Math.min(
                  selEntity,
                  Math.max(0, visibleEntities.length - 1)
                )}
                onSelect={setSelEntity}
              />
            }
          >
            {entity && (
              <EntityEditor
                key={entity.key}
                source={source}
                entity={entity}
                entities={graph.entities}
                metricNames={metricNames}
                aiConfigured={aiConfigured}
                onAddMetrics={addMetrics}
                onChange={(patch) => editEntity(entity.key, patch)}
                onDimensionChange={(column, patch) =>
                  editDimension(entity.key, column, patch)
                }
              />
            )}
          </MasterDetail>
        </TabsContent>

        <TabsContent value="metrics" className="flex min-h-0 flex-1 flex-col">
          <MasterDetail
            header={
              <SearchBar
                value={metricQuery}
                onChange={(v) => {
                  setMetricQuery(v)
                  setSelMetric(0)
                }}
                placeholder="Search metrics…"
                count={visibleMetrics.length}
                total={graph.metrics.length}
              />
            }
            list={
              <MasterList
                items={visibleMetrics.map((m) => ({
                  key: m.id,
                  title: m.name || "(unnamed metric)",
                  subtitle: recipeSummary(m, graph.entities),
                  // Which of the two kinds it is, without opening it.
                  badge: m.kind === "derived" ? "calculated" : "measured",
                  empty: metricIncomplete(m),
                  dirty: changes.metrics.get(m.id) === "edited",
                  isNew: changes.metrics.get(m.id) === "new",
                }))}
                selected={Math.min(
                  selMetric,
                  Math.max(0, visibleMetrics.length - 1)
                )}
                onSelect={setSelMetric}
                footer={
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full"
                    onClick={addMetric}
                  >
                    <RiAddLine data-icon="inline-start" />
                    Add metric
                  </Button>
                }
              />
            }
          >
            {metric && (
              <MetricEditor
                key={metric.id}
                source={source}
                metric={metric}
                entities={graph.entities}
                metrics={graph?.metrics ?? []}
                aiConfigured={aiConfigured}
                onChange={(patch) => editMetric(metric.id, patch)}
                onDelete={() => removeMetric(metric.id)}
              />
            )}
          </MasterDetail>
        </TabsContent>

        <TabsContent
          value="relationships"
          className="flex min-h-0 flex-1 flex-col"
        >
          {switching ? (
            <TabLoading />
          ) : (
            <RelationshipEditor
              source={source}
              entities={graph.entities}
              relationships={graph.relationships}
              savedSignatures={changes.savedRelSigs}
              onChange={setRelationships}
            />
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}

function matches(query: string, ...fields: string[]): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return fields.some((f) => f.toLowerCase().includes(q))
}

function SearchBar({
  value,
  onChange,
  placeholder,
  count,
  total,
}: {
  value: string
  onChange: (v: string) => void
  placeholder: string
  count: number
  total: number
}) {
  return (
    <div className="flex items-center gap-2">
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="h-7 w-56 text-xs"
      />
      <span className="tnum">
        {count === total ? `${total}` : `${count} of ${total}`}
      </span>
    </div>
  )
}

function EntityEditor({
  source,
  entity,
  entities,
  metricNames,
  aiConfigured,
  onChange,
  onDimensionChange,
  onAddMetrics,
}: {
  source: string
  entity: Entity
  entities: Entity[]
  metricNames: string[]
  aiConfigured: boolean
  onChange: (patch: Partial<Entity>) => void
  onDimensionChange: (column: string, patch: Partial<Dimension>) => void
  onAddMetrics: (metrics: MetricDefinition[]) => void
}) {
  const [drafting, setDrafting] = useState(false)
  const [aiFields, setAiFields] = useState<string[]>([])
  const [reasoning, setReasoning] = useState("")
  const [warnings, setWarnings] = useState<string[]>([])
  const locked = entity.provenance.locked
  const visible = entity.dimensions.filter((d) => !d.hidden).length

  // Ask about the entity you are looking at, rather than re-running naming
  // across the whole model and hoping this one improves.
  const describe = (prompt: string) =>
    void (async () => {
      setDrafting(true)
      try {
        const result = await draftEntity(source, {
          prompt,
          entity_key: entity.key,
        })
        onChange({
          name: result.name,
          description: result.description,
          provenance: USER,
        })
        setAiFields(result.changed_fields)
        setReasoning(result.reasoning)
        setWarnings(result.warnings)
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Could not read that")
      } finally {
        setDrafting(false)
      }
    })()

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {entity.provenance.origin === "ai" && "Named by AI"}
          {entity.provenance.origin === "user" && "Edited by you"}
        </span>
        <div className="flex items-center gap-2">
          {aiConfigured && (
            <SuggestMetricsButton
              source={source}
              entity={entity}
              entities={entities}
              existingNames={metricNames}
              onAdd={onAddMetrics}
            />
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              onChange({
                provenance: { ...entity.provenance, locked: !locked },
              })
            }
            title={
              locked
                ? "Unlock — let AI improve this again"
                : "Lock — keep this exactly as it is"
            }
          >
            {locked ? (
              <RiLockLine data-icon="inline-start" />
            ) : (
              <RiLockUnlockLine data-icon="inline-start" />
            )}
            {locked ? "Locked" : "Lock"}
          </Button>
        </div>
      </div>

      {aiConfigured && !locked && (
        <PromptBox
          id="entity-prompt"
          label="Describe it in words"
          placeholder="this table holds one tuition invoice per student"
          hint="Only the name and description change — the table and its columns come from the database."
          busy={drafting}
          onSubmit={describe}
        />
      )}

      <DraftNotes reasoning={reasoning} warnings={warnings} />

      <FormField label="Name" highlighted={aiFields.includes("name")}>
        <EditCell
          value={entity.name}
          onChange={(v) => onChange({ name: v, provenance: USER })}
          label="Entity name"
          className="font-medium"
          highlighted={aiFields.includes("name")}
        />
      </FormField>
      <FormField label="Table" hint="Source table — read-only.">
        <ReadOnlyValue>
          {entity.schema_name}.{entity.table}
        </ReadOnlyValue>
      </FormField>
      <FormField
        label="What it is"
        highlighted={aiFields.includes("description")}
      >
        <EditArea
          value={entity.description ?? ""}
          onChange={(v) => onChange({ description: v, provenance: USER })}
          label="Entity description"
          placeholder="What this entity means to the business…"
          highlighted={aiFields.includes("description")}
        />
      </FormField>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-muted-foreground">
            Ways to slice it ({visible} shown of {entity.dimensions.length})
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          Hidden columns stay in the model but are not published — use it for
          internal ids and free-text notes nobody groups by.
        </p>
        <div className="flex flex-col divide-y border">
          {entity.dimensions.map((dim) => (
            <DimensionRow
              key={dim.column}
              dimension={dim}
              onChange={(patch) => onDimensionChange(dim.column, patch)}
            />
          ))}
          {entity.dimensions.length === 0 && (
            <p className="p-3 text-sm text-muted-foreground">
              No columns to slice by.
            </p>
          )}
        </div>
      </div>
    </>
  )
}

function DimensionRow({
  dimension,
  onChange,
}: {
  dimension: Dimension
  onChange: (patch: Partial<Dimension>) => void
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 p-2",
        dimension.hidden && "opacity-50"
      )}
    >
      <EditCell
        value={dimension.name}
        onChange={(v) => onChange({ name: v, provenance: USER })}
        label={`Name for ${dimension.column}`}
        className="min-w-40 flex-1"
      />
      <span className="w-40 truncate font-mono text-xs text-muted-foreground">
        {dimension.column}
      </span>
      <Badge variant="outline" className="w-20 justify-center">
        {dimension.kind}
      </Badge>
      {dimension.sample_values.length > 0 && (
        <span
          className="max-w-40 truncate text-xs text-muted-foreground"
          title={dimension.sample_values.map(String).join(", ")}
        >
          {dimension.sample_values.slice(0, 3).map(String).join(", ")}
        </span>
      )}
      <Button
        variant="ghost"
        size="sm"
        aria-label={dimension.hidden ? "Show this column" : "Hide this column"}
        title={dimension.hidden ? "Show this column" : "Hide this column"}
        onClick={() => onChange({ hidden: !dimension.hidden })}
      >
        {dimension.hidden ? <RiEyeOffLine /> : <RiCheckLine />}
      </Button>
    </div>
  )
}

/** Shown while a heavy tab builds. Several seconds of the previous tab still
 *  being on screen reads as a click that did nothing. */
function TabLoading() {
  return (
    <div className="flex flex-1 items-center justify-center gap-2 border bg-wash py-14 text-sm text-muted-foreground">
      <RiLoader4Line className="size-4 animate-spin" />
      Loading…
    </div>
  )
}

/** The model's warnings and its skipped tables, as two icons beside the
 *  version.
 *
 *  They were full-width banners above the tabs. Both are things to know once
 *  and then work past — a warning that a formula spans two tables, a note that
 *  a table without a primary key was left out — and neither changes while the
 *  page is open, so they sat there taking a fifth of the screen from the thing
 *  being edited. As icons they are still one glance away, and absent entirely
 *  when there is nothing to say.
 *
 *  Errors are the exception: those block publishing, so they keep the loud
 *  colour even at this size.
 */
function ModelNotes({
  errors,
  warnings,
  skipped,
}: {
  errors: ValidationIssue[]
  warnings: ValidationIssue[]
  skipped: { table: string; reason: string }[]
}) {
  const issues = [...errors, ...warnings]
  if (issues.length === 0 && skipped.length === 0) return null

  return (
    <TooltipProvider delayDuration={150}>
      <span className="flex items-center gap-1">
        {issues.length > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                aria-label={
                  errors.length > 0
                    ? `${errors.length} problem(s) block publishing`
                    : `${warnings.length} thing(s) worth checking`
                }
                className={cn(
                  "inline-flex items-center gap-1 rounded-sm px-1 py-0.5 text-xs",
                  errors.length > 0
                    ? "text-destructive hover:bg-destructive/10"
                    : "text-amber-600 hover:bg-amber-500/10 dark:text-amber-400"
                )}
              >
                <RiAlertLine className="size-3.5" />
                {issues.length}
              </button>
            </TooltipTrigger>
            {/* One child, not two: the tooltip lays its children out in a
                row, so a heading beside a list became two narrow columns. */}
            <TooltipContent className="max-w-sm">
              <div className="flex flex-col gap-1">
                <p className="font-medium">
                  {errors.length > 0
                    ? `${errors.length} problem(s) block publishing`
                    : `${warnings.length} thing(s) worth checking`}
                </p>
                <ul className="flex flex-col gap-0.5">
                  {issues.slice(0, 6).map((issue, i) => (
                    <li key={`${issue.code}-${i}`}>• {issue.message}</li>
                  ))}
                  {issues.length > 6 && <li>…and {issues.length - 6} more</li>}
                </ul>
              </div>
            </TooltipContent>
          </Tooltip>
        )}

        {skipped.length > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                aria-label={`${skipped.length} table(s) left out`}
                className="inline-flex items-center gap-1 rounded-sm px-1 py-0.5 text-xs hover:bg-wash"
              >
                <RiInformationLine className="size-3.5" />
                {skipped.length}
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-sm">
              <div className="flex flex-col gap-1">
                <p className="font-medium">
                  {skipped.length} table(s) were left out because they have no
                  primary key
                </p>
                <p className="font-mono">
                  {skipped
                    .slice(0, 12)
                    .map((s) => s.table)
                    .join(", ")}
                  {skipped.length > 12 && ` and ${skipped.length - 12} more`}
                </p>
              </div>
            </TooltipContent>
          </Tooltip>
        )}
      </span>
    </TooltipProvider>
  )
}

function PublishButton({
  errors,
  warnings,
  graph,
  busy,
  disabled,
  nothingToPublish,
  onConfirm,
}: {
  errors: number
  warnings: number
  graph: SemanticGraph
  busy: boolean
  disabled: boolean
  nothingToPublish?: boolean
  onConfirm: () => void
}) {
  const title =
    errors > 0
      ? "Fix the problems above first"
      : nothingToPublish
        ? "Already published — nothing to publish"
        : "Publish this model"
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button size="sm" disabled={disabled} title={title}>
          {busy ? (
            <RiLoader4Line data-icon="inline-start" className="animate-spin" />
          ) : (
            <RiUploadLine data-icon="inline-start" />
          )}
          Publish
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Publish this model?</AlertDialogTitle>
          <AlertDialogDescription>
            {graph.entities.length} entities · {graph.metrics.length} metrics ·{" "}
            {graph.relationships.length} relationships
            {warnings > 0 && ` · ${warnings} warning(s)`}
            <br />
            It becomes the model queries run against, compiled for the query
            engine.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>Publish</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
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
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          aria-label="Delete semantic model"
        >
          {busy ? (
            <RiLoader4Line className="animate-spin" />
          ) : (
            <RiDeleteBinLine />
          )}
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
