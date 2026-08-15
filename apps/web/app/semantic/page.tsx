"use client"

import { useCallback, useEffect, useState } from "react"
import {
  RiAddLine,
  RiDeleteBinLine,
  RiLoader4Line,
  RiNodeTree,
  RiRefreshLine,
} from "@remixicon/react"
import { toast } from "sonner"

import {
  deleteSemantic,
  getSemanticOverview,
  saveSemanticDraft,
  type SemanticModelSummary,
  suggestSemantic,
} from "@/lib/api-client"
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
  // Per-source in-flight action (generate/delete) so only that row shows a spinner.
  const [busy, setBusy] = useState<Record<string, boolean>>({})

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await getSemanticOverview(signal)
      if (signal?.aborted) return
      setRows(data)
      setStatus("ready")
    } catch {
      if (!signal?.aborted) setStatus("error")
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      await load(controller.signal)
    })()
    return () => controller.abort()
  }, [load])

  const withBusy = async (name: string, fn: () => Promise<void>) => {
    setBusy((b) => ({ ...b, [name]: true }))
    try {
      await fn()
    } finally {
      setBusy((b) => ({ ...b, [name]: false }))
    }
  }

  const handleGenerate = (name: string) =>
    void withBusy(name, async () => {
      try {
        // Suggest a draft (AI when configured, else heuristic) and persist it.
        const graph = await suggestSemantic(name)
        await saveSemanticDraft(name, graph)
        toast.success(`Draft generated for ${name} (${graph.provenance})`)
        await load()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Generate failed")
      }
    })

  const handleDelete = (name: string) =>
    void withBusy(name, async () => {
      try {
        await deleteSemantic(name)
        toast.success(`Semantic model deleted for ${name}`)
        await load()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Delete failed")
      }
    })

  return (
    <PageContainer>
      <PageHeader
        title="Semantic Models"
        description="Every data source and the state of its semantic model. Generate, review, and manage models here, or open a source to edit its model in detail."
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => void load()}
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
                <TableHead className="w-20 text-right">Entities</TableHead>
                <TableHead className="w-20 text-right">Metrics</TableHead>
                <TableHead className="w-24 text-right">Relations</TableHead>
                <TableHead className="w-44 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.source_id}>
                  <TableCell className="font-mono font-medium">
                    {row.source_id}
                    {row.kind && (
                      <span className="ml-2 text-xs text-muted-foreground">
                        {row.kind}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <StatusBadge row={row} />
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
                  <TableCell className="text-right">
                    <RowActions
                      row={row}
                      busy={!!busy[row.source_id]}
                      onGenerate={() => handleGenerate(row.source_id)}
                      onDelete={() => handleDelete(row.source_id)}
                    />
                  </TableCell>
                </TableRow>
              ))}
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
  const version =
    row.status === "published"
      ? `v${row.published_version ?? row.latest_version}`
      : `v${row.latest_version}`
  return (
    <span className="flex items-center gap-2">
      <Badge variant={row.status === "published" ? "default" : "secondary"}>
        {row.status}
      </Badge>
      <span className="text-xs text-muted-foreground tnum">{version}</span>
      {row.provenance && (
        <span className="text-xs text-muted-foreground">{row.provenance}</span>
      )}
    </span>
  )
}

function RowActions({
  row,
  busy,
  onGenerate,
  onDelete,
}: {
  row: SemanticModelSummary
  busy: boolean
  onGenerate: () => void
  onDelete: () => void
}) {
  return (
    <div className="flex items-center justify-end gap-2">
      <Button variant="outline" size="sm" onClick={onGenerate} disabled={busy}>
        {busy ? (
          <RiLoader4Line data-icon="inline-start" className="animate-spin" />
        ) : (
          <RiAddLine data-icon="inline-start" />
        )}
        {row.has_model ? "Regenerate" : "Generate"}
      </Button>
      {row.has_model && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onDelete}
          disabled={busy}
          aria-label={`Delete semantic model for ${row.source_id}`}
        >
          <RiDeleteBinLine />
        </Button>
      )}
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
