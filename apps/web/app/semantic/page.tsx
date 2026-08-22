"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import {
  RiAddLine,
  RiArrowRightLine,
  RiCheckLine,
  RiErrorWarningLine,
  RiLoader4Line,
  RiNodeTree,
  RiRefreshLine,
} from "@remixicon/react"
import { toast } from "sonner"

import {
  buildPhase,
  getActiveJob,
  getAIConfig,
  type GenerationJob,
  getJob,
  getSemanticOverview,
  type SemanticModelSummary,
  startGenerate,
} from "@/lib/api-client"
import { BuildModelDialog } from "@/app/schema/build-model-dialog"
import { DbLogo } from "@/components/icons/db-logo"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { PageContainer, PageHeader } from "@/components/page-header"

type Status = "idle" | "loading" | "error" | "ready"

export default function SemanticModelsPage() {
  const [status, setStatus] = useState<Status>("loading")
  const [rows, setRows] = useState<SemanticModelSummary[]>([])
  // Per-source live build job, so only that row shows progress (done/total).
  const [jobs, setJobs] = useState<Record<string, GenerationJob | null>>({})
  // Whether business context is worth asking for before a build.
  const [aiConfigured, setAiConfigured] = useState(false)
  const router = useRouter()

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await getSemanticOverview(signal)
      if (signal?.aborted) return []
      setRows(data)
      setStatus("ready")
      return data
    } catch {
      if (!signal?.aborted) setStatus("error")
      return []
    }
  }, [])

  const setJob = (name: string, job: GenerationJob | null) =>
    setJobs((j) => ({ ...j, [name]: job }))

  // Follow a build to its end, wherever it was started from.
  const watch = useCallback(
    async (name: string, initial: GenerationJob) => {
      let job = initial
      setJob(name, job)
      try {
        while (job.status === "running") {
          await new Promise((r) => setTimeout(r, 1500))
          const active = await getActiveJob(name)
          job = active ?? (await getJob(name, job.id))
          setJob(name, job)
        }
        if (job.status === "error") {
          toast.error(job.error ?? "Generate failed")
        } else {
          toast.success(`Draft generated for ${name}`)
        }
        await load()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Generate failed")
      } finally {
        setJob(name, null)
      }
    },
    [load]
  )

  // A build outlives the page that started it. Without asking, a source whose
  // model is being built right now reads as "none" with a Generate button next
  // to it — and pressing it runs a second build over the first.
  const adoptRunning = useCallback(
    async (sources: SemanticModelSummary[], signal?: AbortSignal) => {
      const found = await Promise.all(
        sources.map(async (row) => {
          try {
            return [
              row.source_id,
              await getActiveJob(row.source_id, signal),
            ] as const
          } catch {
            return [row.source_id, null] as const
          }
        })
      )
      if (signal?.aborted) return
      for (const [name, job] of found) {
        if (job && job.status === "running") void watch(name, job)
      }
    },
    [watch]
  )

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      const sources = await load(controller.signal)
      void adoptRunning(sources, controller.signal)
      try {
        const cfg = await getAIConfig(controller.signal)
        if (!controller.signal.aborted) setAiConfigured(!!cfg?.configured)
      } catch {
        // Leave false — a build without AI needs no business context.
      }
    })()
    return () => controller.abort()
  }, [load, adoptRunning])

  // Same background job the per-source editor uses. Building a model means
  // introspection, profiling and batched AI calls — far too slow to hold a
  // request open, and a second click must not start a duplicate build.
  const handleGenerate = (name: string, tables: string[]) =>
    void (async () => {
      try {
        void watch(name, await startGenerate(name, true, { tables }))
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Generate failed")
        setJob(name, null)
      }
    })()

  return (
    <PageContainer>
      <PageHeader
        title="Semantic Models"
        description="Every data source and the state of its semantic model. Open a source to review and edit its model; generate one for a source that has none."
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              void (async () => {
                // Refresh looks for builds too: this is what a person presses
                // when a row does not match what they believe is happening.
                void adoptRunning(await load())
              })()
            }
            disabled={status === "loading"}
          >
            <RiRefreshLine data-icon="inline-start" />
            Refresh
          </Button>
        }
      />

      {status === "loading" && <LoadingState />}

      {status === "error" && (
        <p className="text-sm text-muted-foreground">
          Could not load semantic models. Is the API running?
        </p>
      )}

      {status === "ready" && rows.length === 0 && (
        <div className="flex flex-col items-center gap-3 border bg-wash px-6 py-14 text-center">
          <RiNodeTree className="size-8 text-muted-foreground" />
          <div className="flex flex-col gap-1">
            <h2 className="text-sm font-medium">No data sources yet</h2>
            <p className="max-w-sm text-sm text-balance text-muted-foreground">
              Connect a data source first, then generate a semantic model for it
              here.
            </p>
          </div>
        </div>
      )}

      {status === "ready" && rows.length > 0 && (
        <div className="overflow-hidden border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Source</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Health</TableHead>
                <TableHead className="w-20 text-right">Entities</TableHead>
                <TableHead className="w-20 text-right">Metrics</TableHead>
                <TableHead className="w-24 text-right">Relations</TableHead>
                <TableHead className="w-44 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => {
                const job = jobs[row.source_id] ?? null
                return (
                  <TableRow
                    key={row.source_id}
                    onClick={
                      row.has_model
                        ? () =>
                            router.push(
                              `/semantic/${encodeURIComponent(row.source_id)}`
                            )
                        : undefined
                    }
                    className={
                      row.has_model ? "cursor-pointer hover:bg-wash" : undefined
                    }
                  >
                    <TableCell>
                      <span className="flex items-center gap-2.5">
                        <span className="flex w-7 shrink-0 justify-center">
                          {row.kind && (
                            <DbLogo
                              engine={row.kind}
                              monogram={row.source_id.slice(0, 2).toUpperCase()}
                              className="h-5 w-auto"
                            />
                          )}
                        </span>
                        <span className="font-mono font-medium">
                          {row.source_id}
                        </span>
                      </span>
                    </TableCell>
                    <TableCell>
                      {job ? (
                        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                          <RiLoader4Line className="size-3.5 animate-spin" />
                          Building…
                        </span>
                      ) : (
                        <StatusBadge row={row} />
                      )}
                    </TableCell>
                    <TableCell>
                      <HealthChip row={row} />
                    </TableCell>
                    <TableCell className="text-right tnum">
                      {row.has_model ? row.entity_count : "—"}
                    </TableCell>
                    <TableCell className="text-right tnum">
                      {row.has_model ? row.metric_count : "—"}
                    </TableCell>
                    <TableCell className="text-right tnum">
                      {row.has_model ? row.relationship_count : "—"}
                    </TableCell>
                    <TableCell
                      className="text-right"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {job ? (
                        <BuildProgress job={job} />
                      ) : (
                        <RowActions
                          row={row}
                          aiConfigured={aiConfigured}
                          onGenerate={(tables) =>
                            handleGenerate(row.source_id, tables)
                          }
                        />
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </PageContainer>
  )
}

function StatusBadge({ row }: { row: SemanticModelSummary }) {
  if (!row.has_model) {
    return <Badge variant="outline">none</Badge>
  }
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <Badge variant={row.status === "published" ? "default" : "secondary"}>
        {row.status}
      </Badge>
      <span className="text-xs text-muted-foreground tnum">
        v{row.latest_version}
      </span>
      {/* A draft newer than what is live: the work here is not yet queryable. */}
      {row.has_unpublished_changes && (
        <span className="rounded-sm bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-400">
          unpublished
          {row.published_version != null && ` · live v${row.published_version}`}
        </span>
      )}
    </span>
  )
}

/** Structural health, from the same validator the Publish button uses. Errors
 *  block a publish; warnings are advisory; neither means it is ready. */
function HealthChip({ row }: { row: SemanticModelSummary }) {
  if (!row.has_model) return <span className="text-muted-foreground">—</span>
  if (row.error_count > 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-destructive">
        <RiErrorWarningLine className="size-3.5" />
        {row.error_count} to fix
      </span>
    )
  }
  if (row.warning_count > 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700 dark:text-amber-400">
        <RiErrorWarningLine className="size-3.5" />
        {row.warning_count} to finish
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 dark:text-emerald-400">
      <RiCheckLine className="size-3.5" />
      Ready
    </span>
  )
}

function RowActions({
  row,
  aiConfigured,
  onGenerate,
}: {
  row: SemanticModelSummary
  aiConfigured: boolean
  onGenerate: (tables: string[]) => void
}) {
  // A modelled source is opened, not managed from here — rebuild and delete
  // live inside its editor, one place instead of two.
  if (row.has_model) {
    return (
      <Link
        href={`/semantic/${encodeURIComponent(row.source_id)}`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        Open
        <RiArrowRightLine className="size-4" />
      </Link>
    )
  }
  return (
    <div className="flex items-center justify-end gap-2">
      {/* Describe the business, then build — same gate as the editor. */}
      <BuildModelDialog
        source={row.source_id}
        mode="generate"
        aiConfigured={aiConfigured}
        onBuild={onGenerate}
      >
        <Button variant="outline" size="sm">
          <RiAddLine data-icon="inline-start" />
          Generate
        </Button>
      </BuildModelDialog>
    </div>
  )
}

/** The row's live build progress — mirrors the editor panel's spinner + bar. */
function BuildProgress({ job }: { job: GenerationJob }) {
  const phase = buildPhase(job)
  return (
    <div className="flex items-center justify-end gap-2.5">
      <RiLoader4Line className="size-4 shrink-0 animate-spin text-accent-brand" />
      <div className="flex flex-col items-end gap-1">
        <span className="text-xs font-medium">{phase.label}</span>
        {phase.percent !== null && (
          <div className="h-1 w-36 overflow-hidden rounded-full bg-border">
            <div
              className="h-full bg-accent-brand transition-[width] duration-300"
              style={{ width: `${phase.percent}%` }}
            />
          </div>
        )}
      </div>
    </div>
  )
}

function LoadingState() {
  return (
    <div className="flex flex-col gap-2 border p-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex items-center justify-between gap-3">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-8 w-40" />
        </div>
      ))}
    </div>
  )
}
