"use client"

/**
 * The metric editor — where a business definition actually gets written.
 *
 * Three things here are load-bearing:
 *
 * - **Filters.** "Revenue = SUM(amount) WHERE status = PAID" is the canonical
 *   thing a semantic layer exists to record, and it was previously impossible
 *   to express in the UI at all. Values come from profiling, so the user picks
 *   a real value instead of typing a guess.
 * - **Describe it in words.** One sentence fills the whole form. The AI does
 *   the hard part (which column, which condition, which date) and the human
 *   still sees every field and presses Save.
 * - **Test run.** A number the user recognises is the only proof the definition
 *   is right, and it works before publishing — which is when the question
 *   matters.
 */

import { useState } from "react"
import {
  RiAddLine,
  RiDeleteBinLine,
  RiLoader4Line,
  RiPlayLine,
} from "@remixicon/react"
import { toast } from "sonner"

import {
  type Aggregation,
  type Dimension,
  draftMetric,
  type Entity,
  FILTER_OPERATORS,
  type FilterOperator,
  type MetricDefinition,
  type MetricFilter,
  type MetricPreview,
  previewMetric,
} from "@/lib/api-client"
import { Button } from "@/components/ui/button"

import {
  AGGREGATION_LABEL,
  DraftNotes,
  EditArea,
  EditCell,
  FORMAT_LABEL,
  FormField,
  isBlank,
  METRIC_KIND_LABEL,
  MiniSelect,
  needsColumn,
  OPERATOR_LABEL,
  PromptBox,
  USER,
  VALUELESS_OPERATORS,
} from "./semantic-fields"

const AGGREGATIONS: Aggregation[] = [
  "count",
  "sum",
  "avg",
  "count_distinct",
  "min",
  "max",
]

export function MetricEditor({
  source,
  metric,
  entities,
  metricNames,
  aiConfigured,
  onChange,
  onDelete,
}: {
  source: string
  metric: MetricDefinition
  entities: Entity[]
  metricNames: string[]
  aiConfigured: boolean
  onChange: (patch: Partial<MetricDefinition>) => void
  onDelete: () => void
}) {
  const [drafting, setDrafting] = useState(false)
  // Which fields the last AI pass touched — highlighted so the user knows
  // exactly what to check rather than re-reading the whole form.
  const [aiFields, setAiFields] = useState<string[]>([])
  const [reasoning, setReasoning] = useState("")
  const [warnings, setWarnings] = useState<string[]>([])
  const [preview, setPreview] = useState<MetricPreview | null>(null)
  const [running, setRunning] = useState(false)

  const entity = entities.find((e) => e.key === metric.entity_key)
  const dimensions = entity?.dimensions ?? []
  const valueColumns = dimensions.filter((d) =>
    metric.aggregation === "sum" || metric.aggregation === "avg"
      ? d.kind === "number"
      : true
  )
  const timeColumns = dimensions.filter((d) => d.kind === "time")

  // Any edit is a human decision: mark it so a later AI pass leaves it alone.
  const edit = (patch: Partial<MetricDefinition>) => {
    setPreview(null) // the number no longer describes what is on screen
    onChange({ ...patch, provenance: USER })
  }

  const describe = (prompt: string) =>
    void (async () => {
      setDrafting(true)
      try {
        const result = await draftMetric(source, {
          prompt,
          // An existing metric with a name is an edit; a blank one is a create.
          base: isBlank(metric.name) ? null : metric,
          entity_key: metric.entity_key ?? null,
        })
        // Keep this metric's own id and mark the result as the user's: they are
        // about to review it, and a later AI pass must not undo their decision.
        const fields = { ...result.metric }
        delete (fields as Partial<MetricDefinition>).id
        onChange({ ...fields, provenance: USER })
        setAiFields(result.changed_fields)
        setReasoning(result.reasoning)
        setWarnings(result.warnings)
        setPreview(null)
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Could not read that")
      } finally {
        setDrafting(false)
      }
    })()

  const runPreview = () =>
    void (async () => {
      setRunning(true)
      try {
        setPreview(await previewMetric(source, metric))
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Test run failed")
      } finally {
        setRunning(false)
      }
    })()

  const touched = (field: string) => aiFields.includes(field)

  return (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {metric.provenance.origin === "ai" && "Suggested by AI"}
          {metric.provenance.origin === "user" && "Edited by you"}
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground hover:text-destructive"
          onClick={onDelete}
        >
          <RiDeleteBinLine data-icon="inline-start" />
          Delete
        </Button>
      </div>

      {aiConfigured && (
        <PromptBox
          id="metric-prompt"
          label="Describe it in words"
          placeholder={
            isBlank(metric.name)
              ? "total paid tuition, by payment date"
              : "change it to use the created date instead"
          }
          hint="The form below is filled in for you — nothing is saved until you press Save draft."
          busy={drafting}
          onSubmit={describe}
        />
      )}

      <DraftNotes reasoning={reasoning} warnings={warnings} />

      <FormField label="Name" highlighted={touched("name")}>
        <EditCell
          value={metric.name}
          onChange={(v) => edit({ name: v })}
          label="Metric name"
          className="font-medium"
          highlighted={touched("name")}
        />
      </FormField>

      <FormField label="How it is built" highlighted={touched("kind")}>
        <MiniSelect
          value={metric.kind}
          onChange={(v) => edit({ kind: v as MetricDefinition["kind"] })}
          label="Metric kind"
          options={Object.entries(METRIC_KIND_LABEL).map(([value, label]) => ({
            value,
            label,
          }))}
          className="w-full"
        />
      </FormField>

      {metric.kind === "derived" ? (
        <FormulaEditor
          expression={metric.expression ?? ""}
          metricNames={metricNames.filter((n) => n !== metric.name)}
          highlighted={touched("expression")}
          onChange={(v) => edit({ expression: v })}
        />
      ) : (
        <>
          <FormField label="Taken from" highlighted={touched("entity_key")}>
            <MiniSelect
              value={metric.entity_key ?? ""}
              onChange={(v) => edit({ entity_key: v, column: null })}
              label="Metric entity"
              placeholder="Choose what it measures…"
              empty={isBlank(metric.entity_key)}
              options={entities.map((e) => ({
                value: e.key,
                label: `${e.name} (${e.table})`,
              }))}
              className="w-full"
              highlighted={touched("entity_key")}
            />
          </FormField>

          <FormField label="Calculation" highlighted={touched("aggregation")}>
            <MiniSelect
              value={metric.aggregation ?? ""}
              onChange={(v) => edit({ aggregation: v as Aggregation })}
              label="Metric aggregation"
              placeholder="Choose…"
              options={AGGREGATIONS.map((a) => ({
                value: a,
                label: AGGREGATION_LABEL[a],
              }))}
              className="w-full"
              highlighted={touched("aggregation")}
            />
          </FormField>

          {needsColumn(metric.aggregation) && (
            <FormField label="Of which column" highlighted={touched("column")}>
              <MiniSelect
                value={metric.column ?? ""}
                onChange={(v) => edit({ column: v })}
                label="Metric column"
                placeholder="Choose a column…"
                empty={isBlank(metric.column)}
                options={valueColumns.map((c) => ({
                  value: c.column,
                  label: `${c.name} — ${c.column}`,
                }))}
                className="w-full"
                highlighted={touched("column")}
              />
            </FormField>
          )}

          <FilterEditor
            filters={metric.filters}
            dimensions={dimensions}
            highlighted={touched("filters")}
            onChange={(filters) => edit({ filters })}
          />

          <FormField
            label="Measured by date"
            hint="Which date this metric belongs to — often not the created date."
            highlighted={touched("time_dimension")}
          >
            <MiniSelect
              value={metric.time_dimension ?? ""}
              onChange={(v) => edit({ time_dimension: v || null })}
              label="Metric time dimension"
              placeholder="None"
              options={timeColumns.map((c) => ({
                value: c.column,
                label: `${c.name} — ${c.column}`,
              }))}
              className="w-full"
              highlighted={touched("time_dimension")}
            />
          </FormField>
        </>
      )}

      <FormField label="Shown as" highlighted={touched("format")}>
        <MiniSelect
          value={metric.format ?? ""}
          onChange={(v) => edit({ format: v || null })}
          label="Metric format"
          placeholder="Plain number"
          options={Object.entries(FORMAT_LABEL).map(([value, label]) => ({
            value,
            label,
          }))}
          className="w-full"
        />
      </FormField>

      <FormField label="What it means" highlighted={touched("description")}>
        <EditArea
          value={metric.description ?? ""}
          onChange={(v) => edit({ description: v })}
          label="Metric definition"
          placeholder="What this metric means to the business…"
          highlighted={touched("description")}
        />
      </FormField>

      <div className="flex flex-col gap-1 border-t pt-4">
        <span className="text-xs font-medium text-muted-foreground">
          What this works out to
        </span>
        <code className="overflow-x-auto border bg-wash px-2 py-1.5 font-mono text-xs">
          {formulaLine(metric, entities)}
        </code>
      </div>

      <TestRun
        preview={preview}
        running={running}
        disabled={metric.kind === "derived"}
        format={metric.format}
        onRun={runPreview}
      />
    </>
  )
}

/** The formula for a metric built from other metrics.
 *
 *  A formula really is the natural way to write "Revenue / Order Count", so
 *  this one field is free text — but never *blind* free text: names are
 *  insertable with a click and checked as you type, because a mistyped name
 *  silently drops the metric out of the published model.
 */
function FormulaEditor({
  expression,
  metricNames,
  highlighted,
  onChange,
}: {
  expression: string
  metricNames: string[]
  highlighted?: boolean
  onChange: (v: string) => void
}) {
  const unknown = unknownMetricNames(expression, metricNames)
  const insert = (text: string) =>
    onChange(`${expression}${expression && !expression.endsWith(" ") ? " " : ""}${text} `)

  return (
    <FormField
      label="Formula"
      hint="Built from other metrics, e.g. Revenue / Order Count. Both parts must be metrics on the same entity."
      highlighted={highlighted}
    >
      <EditCell
        value={expression}
        onChange={onChange}
        label="Metric formula"
        placeholder="Revenue / Order Count"
        mono
        highlighted={highlighted}
      />
      {unknown.length > 0 && (
        <p className="text-xs text-destructive">
          Not a metric: {unknown.map((n) => `"${n}"`).join(", ")}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-1">
        {["+", "−", "×", "÷", "(", ")"].map((op) => (
          <Button
            key={op}
            variant="outline"
            size="sm"
            className="h-6 w-7 px-0 font-mono"
            onClick={() => insert(OPERATOR_INPUT[op] ?? op)}
          >
            {op}
          </Button>
        ))}
        {metricNames.slice(0, 8).map((n) => (
          <Button
            key={n}
            variant="ghost"
            size="sm"
            className="h-6 px-1.5 text-xs"
            onClick={() => insert(n)}
          >
            {n}
          </Button>
        ))}
      </div>
    </FormField>
  )
}

/** The buttons show maths symbols; the formula stores what SQL understands. */
const OPERATOR_INPUT: Record<string, string> = {
  "−": "-",
  "×": "*",
  "÷": "/",
}

/** Names in the formula that are not metrics. Everything that is not an
 *  operator, a number or a known metric name is a typo waiting to drop the
 *  metric out of the published model. */
export function unknownMetricNames(
  expression: string,
  metricNames: string[]
): string[] {
  let remaining = expression
  for (const name of [...metricNames].sort((a, b) => b.length - a.length)) {
    remaining = remaining.split(name).join(" ")
  }
  // Split on operators only, not whitespace: an unknown "Số khách" should be
  // reported as one name, not as "Số" and "khách".
  return [
    ...new Set(
      remaining
        .split(/[+\-*/(),]+/)
        .map((t) => t.trim())
        .filter((t) => t && !/^\d+(\.\d+)?$/.test(t))
    ),
  ]
}

/** Business rules, as pickers rather than SQL. Without this a metric could only
 *  ever mean "the whole table". */
function FilterEditor({
  filters,
  dimensions,
  highlighted,
  onChange,
}: {
  filters: MetricFilter[]
  dimensions: Dimension[]
  highlighted?: boolean
  onChange: (filters: MetricFilter[]) => void
}) {
  const update = (index: number, patch: Partial<MetricFilter>) =>
    onChange(filters.map((f, i) => (i === index ? { ...f, ...patch } : f)))

  const add = () =>
    onChange([
      ...filters,
      {
        field: dimensions[0]?.column ?? "",
        operator: "eq" as FilterOperator,
        value: "",
      },
    ])

  return (
    <FormField
      label="Only count when"
      hint="Leave empty to include every row."
      highlighted={highlighted}
    >
      <div className="flex flex-col gap-2">
        {filters.map((filter, index) => {
          const dimension = dimensions.find((d) => d.column === filter.field)
          const values = dimension?.sample_values ?? []
          const valueless = VALUELESS_OPERATORS.includes(filter.operator)
          return (
            <div key={index} className="flex flex-wrap items-center gap-2">
              <MiniSelect
                value={filter.field}
                onChange={(v) => update(index, { field: v, value: "" })}
                label="Filter column"
                placeholder="Column…"
                empty={isBlank(filter.field)}
                options={dimensions.map((d) => ({
                  value: d.column,
                  label: d.name,
                }))}
                className="min-w-32 flex-1"
              />
              <MiniSelect
                value={filter.operator}
                onChange={(v) =>
                  update(index, { operator: v as FilterOperator })
                }
                label="Filter operator"
                options={FILTER_OPERATORS.map((op) => ({
                  value: op,
                  label: OPERATOR_LABEL[op],
                }))}
                className="w-36"
              />
              {!valueless &&
                (values.length > 0 ? (
                  // Real values from the database: no more guessing whether the
                  // code is 'PAID', 'Paid' or 'DA_THU'.
                  <MiniSelect
                    value={String(filter.value ?? "")}
                    onChange={(v) => update(index, { value: v })}
                    label="Filter value"
                    placeholder="Value…"
                    empty={isBlank(String(filter.value ?? ""))}
                    options={values.map((v) => ({
                      value: String(v),
                      label: String(v),
                    }))}
                    className="min-w-32 flex-1"
                  />
                ) : (
                  <EditCell
                    value={String(filter.value ?? "")}
                    onChange={(v) => update(index, { value: v })}
                    label="Filter value"
                    placeholder="Value…"
                    className="min-w-32 flex-1"
                  />
                ))}
              <Button
                variant="ghost"
                size="sm"
                aria-label="Remove condition"
                onClick={() => onChange(filters.filter((_, i) => i !== index))}
              >
                <RiDeleteBinLine />
              </Button>
            </div>
          )
        })}
        <Button
          variant="outline"
          size="sm"
          className="self-start"
          onClick={add}
          disabled={dimensions.length === 0}
        >
          <RiAddLine data-icon="inline-start" />
          Add condition
        </Button>
      </div>
    </FormField>
  )
}

/** The number, straight from the database. Someone who knows the business can
 *  tell instantly whether the definition is right — and this works on an
 *  unsaved draft, which is when it matters. */
function TestRun({
  preview,
  running,
  disabled,
  format,
  onRun,
}: {
  preview: MetricPreview | null
  running: boolean
  disabled: boolean
  format?: string | null
  onRun: () => void
}) {
  const [showSql, setShowSql] = useState(false)
  return (
    <div className="flex flex-col gap-2 border-t pt-4">
      <div className="flex items-center gap-3">
        <Button variant="outline" size="sm" onClick={onRun} disabled={running || disabled}>
          {running ? (
            <RiLoader4Line data-icon="inline-start" className="animate-spin" />
          ) : (
            <RiPlayLine data-icon="inline-start" />
          )}
          Test run
        </Button>
        {disabled && (
          <span className="text-xs text-muted-foreground">
            Calculated metrics are built from the others — test those instead.
          </span>
        )}
        {preview?.error && (
          <span className="text-xs text-destructive">{preview.error}</span>
        )}
        {preview && !preview.error && (
          <span className="flex flex-wrap items-baseline gap-2">
            <span className="text-lg font-medium tnum">
              {formatValue(preview.value, format)}
            </span>
            {preview.row_count !== null && preview.row_count !== undefined && (
              <span className="text-xs text-muted-foreground tnum">
                from {preview.row_count.toLocaleString()} rows
              </span>
            )}
            {/* The period is what exposes a metric measured by the wrong date:
                the figure looks plausible either way, the span does not. */}
            {preview.time_column && preview.period_start != null && (
              <span className="text-xs text-muted-foreground">
                {preview.time_column}: {formatDate(preview.period_start)} →{" "}
                {formatDate(preview.period_end)}
              </span>
            )}
          </span>
        )}
      </div>
      {preview?.sql && (
        <div className="flex flex-col gap-1">
          <button
            type="button"
            onClick={() => setShowSql((s) => !s)}
            className="self-start text-xs text-muted-foreground underline-offset-2 hover:underline"
          >
            {showSql ? "Hide the query" : "Show the query"}
          </button>
          {showSql && (
            <pre className="overflow-x-auto border bg-wash p-2 font-mono text-xs">
              {preview.sql}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

function formatDate(value: unknown): string {
  if (value === null || value === undefined) return "—"
  const text = String(value)
  // Dates arrive ISO-formatted from the connector; the time half is noise here.
  return text.length > 10 && text[10] === "T" ? text.slice(0, 10) : text
}

function formatValue(value: unknown, format?: string | null): string {
  if (value === null || value === undefined) return "—"
  const n = typeof value === "number" ? value : Number(value)
  if (Number.isNaN(n)) return String(value)
  if (format === "percent") return `${n.toLocaleString()}%`
  if (format === "currency") return n.toLocaleString()
  return n.toLocaleString()
}

/** One-line summary of a metric's recipe, shown under its name in the list. */
export function recipeSummary(
  metric: MetricDefinition,
  entities: Entity[]
): string {
  if (metric.kind === "derived") return metric.expression || "= …"
  const entity = entities.find((e) => e.key === metric.entity_key)
  // The table (technical), not the entity name — the title already carries the
  // business name, so repeating it here just read as "…Count / Count rows · …".
  const where = metric.filters.length
    ? ` · ${metric.filters.length} filter${metric.filters.length === 1 ? "" : "s"}`
    : ""
  return `${measurePhrase(metric)} · ${entity?.table ?? "?"}${where}`
}

/** "Add up payment_amount" / "Count rows" — the label already says "rows" for a
 *  plain count, so appending the column (or nothing) must not repeat it. */
function measurePhrase(metric: MetricDefinition): string {
  const label = AGGREGATION_LABEL[metric.aggregation ?? "count"]
  return metric.column ? `${label} ${metric.column}` : label
}

/** The same recipe written the way a spreadsheet would write it.
 *
 *  Read-only on purpose: the pickers stay the source of truth, so nothing can
 *  be typed into an invalid state — but people who think in formulas get to see
 *  one, and can check at a glance that it says what they meant. */
export function formulaLine(metric: MetricDefinition, entities: Entity[]): string {
  if (metric.kind === "derived") return `= ${metric.expression || "…"}`
  const entity = entities.find((e) => e.key === metric.entity_key)
  const fn = (metric.aggregation ?? "count").toUpperCase().replace("_", " ")
  const arg = metric.column || "*"
  const from = entity?.table ?? "?"
  const where = metric.filters
    .map((f) => `${f.field} ${OPERATOR_LABEL[f.operator]} ${formatFilterValue(f.value)}`)
    .join(" AND ")
  const by = metric.time_dimension ? ` BY ${metric.time_dimension}` : ""
  return `${fn}(${arg}) FROM ${from}${where ? ` WHERE ${where}` : ""}${by}`
}

function formatFilterValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "…"
  if (Array.isArray(value)) return value.map(String).join(", ")
  return typeof value === "number" ? String(value) : `'${String(value)}'`
}

export const metricIncomplete = (m: MetricDefinition) =>
  isBlank(m.name) ||
  isBlank(m.description) ||
  (m.kind === "base" &&
    (isBlank(m.entity_key) ||
      !m.aggregation ||
      (needsColumn(m.aggregation) && isBlank(m.column)))) ||
  (m.kind === "derived" && isBlank(m.expression))
