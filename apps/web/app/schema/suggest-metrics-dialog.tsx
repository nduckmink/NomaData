"use client"

/**
 * "What should I measure on this table?"
 *
 * The heuristic build gives every entity a row count and nothing else, on
 * purpose: summing every numeric column produces `SUM(bank_id)` and a hundred
 * other meaningless figures. Deciding which columns are worth measuring needs
 * the column names, their types and their real values — a judgement, not a
 * rule. So the model makes the shortlist and the human picks from it.
 *
 * Scoped to one entity: in a 122-table schema most tables are lookups nobody
 * measures, and proposing across all of them would bury the few that matter.
 */

import { useState } from "react"
import { RiLoader4Line, RiSparkling2Line } from "@remixicon/react"
import { toast } from "sonner"

import {
  type Entity,
  type MetricDefinition,
  suggestMetrics,
} from "@/lib/api-client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

import { formulaLine } from "./metric-editor"

export function SuggestMetricsButton({
  source,
  entity,
  entities,
  existingNames,
  onAdd,
}: {
  source: string
  entity: Entity
  entities: Entity[]
  existingNames: string[]
  /** Called with the metrics the user kept. Nothing is saved until they do. */
  onAdd: (metrics: MetricDefinition[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [metrics, setMetrics] = useState<MetricDefinition[]>([])
  const [reasons, setReasons] = useState<string[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  // Everything the model proposes starts selected: it already passed the
  // catalog checks, so the common case is "yes, all of these".
  const [chosen, setChosen] = useState<Set<string>>(new Set())

  const run = () =>
    void (async () => {
      setOpen(true)
      setLoading(true)
      setMetrics([])
      try {
        const result = await suggestMetrics(source, { entity_key: entity.key })
        setMetrics(result.metrics)
        setReasons(result.reasons)
        setWarnings(result.warnings)
        setChosen(new Set(result.metrics.map((m) => m.id)))
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Could not suggest metrics")
        setOpen(false)
      } finally {
        setLoading(false)
      }
    })()

  const toggle = (id: string) =>
    setChosen((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const add = () => {
    const keep = metrics.filter((m) => chosen.has(m.id))
    onAdd(keep)
    setOpen(false)
    toast.success(
      `Added ${keep.length} metric${keep.length === 1 ? "" : "s"} — review and save`
    )
  }

  const duplicate = (name: string) => existingNames.includes(name)

  return (
    <>
      <Button variant="outline" size="sm" onClick={run}>
        <RiSparkling2Line data-icon="inline-start" />
        Suggest metrics
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>What to measure on {entity.name}</DialogTitle>
            <DialogDescription>
              Nothing is added until you choose. Each one is checked against the
              real columns first — you can edit and test-run them afterwards.
            </DialogDescription>
          </DialogHeader>

          {loading && (
            <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
              <RiLoader4Line className="size-4 animate-spin" />
              Reading the columns and their values…
            </div>
          )}

          {!loading && metrics.length === 0 && (
            <p className="py-8 text-sm text-muted-foreground">
              Nothing worth measuring here — this looks like a lookup table.
            </p>
          )}

          {!loading && metrics.length > 0 && (
            <ul className="flex max-h-[55vh] flex-col gap-2 overflow-y-auto pr-1">
              {metrics.map((metric, i) => (
                <li key={metric.id}>
                  <label
                    className={cn(
                      "flex cursor-pointer gap-3 border p-3 transition-colors",
                      chosen.has(metric.id)
                        ? "border-accent-brand/50 bg-accent-brand/5"
                        : "hover:bg-wash"
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={chosen.has(metric.id)}
                      onChange={() => toggle(metric.id)}
                      className="mt-1 size-4 shrink-0"
                      aria-label={`Add ${metric.name}`}
                    />
                    <div className="flex min-w-0 flex-col gap-1">
                      <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
                        {metric.name}
                        {duplicate(metric.name) && (
                          <span className="rounded-sm bg-amber-500/15 px-1 text-[10px] text-amber-700 dark:text-amber-400">
                            name already used
                          </span>
                        )}
                      </span>
                      {metric.description && (
                        <span className="text-xs text-muted-foreground">
                          {metric.description}
                        </span>
                      )}
                      <code className="overflow-x-auto font-mono text-xs text-muted-foreground">
                        {formulaLine(metric, entities)}
                      </code>
                      {reasons[i] && (
                        <span className="text-xs text-muted-foreground italic">
                          {reasons[i]}
                        </span>
                      )}
                    </div>
                  </label>
                </li>
              ))}
            </ul>
          )}

          {warnings.length > 0 && (
            <ul className="flex flex-col gap-1 border border-amber-500/40 bg-amber-500/5 p-2 text-xs">
              {warnings.map((w) => (
                <li key={w}>⚠ {w}</li>
              ))}
            </ul>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={add} disabled={loading || chosen.size === 0}>
              Add {chosen.size || ""} metric{chosen.size === 1 ? "" : "s"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
