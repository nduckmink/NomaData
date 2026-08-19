"use client"

/**
 * Shared form primitives and vocabulary for the semantic editor.
 *
 * The vocabulary matters as much as the widgets: the people who know what
 * "revenue" means are not the people who know what `count_distinct` means, and
 * the editor is unusable to them if it speaks in aggregation names. The stored
 * model keeps its precise terms; only the labels change.
 */

import * as React from "react"
import { RiLoader4Line, RiSparkling2Line } from "@remixicon/react"

import type {
  Aggregation,
  Dimension,
  FilterOperator,
  MetricKind,
  Provenance,
} from "@/lib/api-client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

export const isBlank = (v: string | null | undefined) => !v || v.trim() === ""

/** Plain-language labels. The value stored is unchanged. */
export const AGGREGATION_LABEL: Record<Aggregation, string> = {
  count: "Count rows",
  count_distinct: "Count unique values",
  sum: "Add up",
  avg: "Average",
  min: "Smallest",
  max: "Largest",
}

export const METRIC_KIND_LABEL: Record<MetricKind, string> = {
  base: "Measured from data",
  derived: "Calculated from other metrics",
}

export const OPERATOR_LABEL: Record<FilterOperator, string> = {
  eq: "is",
  neq: "is not",
  gt: "is greater than",
  gte: "is at least",
  lt: "is less than",
  lte: "is at most",
  in: "is one of",
  not_in: "is not one of",
  contains: "contains",
  set: "has a value",
  not_set: "is empty",
}

/** Operators that take no value — the value box is hidden for these. */
export const VALUELESS_OPERATORS: FilterOperator[] = ["set", "not_set"]

export const FORMAT_LABEL: Record<string, string> = {
  number: "Plain number",
  currency: "Money",
  percent: "Percentage",
}

/** count needs no column; every other aggregation measures one. */
export const needsColumn = (a: Aggregation | null | undefined) =>
  a !== null && a !== undefined && a !== "count"

/** Marks a field as hand-edited so a later AI pass leaves it alone. */
export const USER: Provenance = { origin: "user", locked: false }

export function dimensionsFor(
  entityKey: string | null | undefined,
  entities: { key: string; dimensions: Dimension[] }[]
): Dimension[] {
  return entities.find((e) => e.key === entityKey)?.dimensions ?? []
}

/** One labelled field in the editor form. */
export function FormField({
  label,
  hint,
  highlighted,
  children,
}: {
  label: string
  hint?: string
  /** The AI just filled this in — say so, so the user knows what to check. */
  highlighted?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        {label}
        {highlighted && (
          <span className="rounded-sm bg-accent-brand/15 px-1 text-[10px] text-accent-brand">
            AI
          </span>
        )}
      </span>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function ReadOnlyValue({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-9 items-center rounded-md border border-border/50 bg-muted/40 px-3 font-mono text-sm text-muted-foreground">
      {children}
    </div>
  )
}

/** An editable single-line field. Blank fields are tinted so the reviewer can
 *  see at a glance what still needs attention. */
export function EditCell({
  value,
  onChange,
  label,
  placeholder,
  className,
  mono,
  list,
  highlighted,
}: {
  value: string
  onChange: (v: string) => void
  label: string
  placeholder?: string
  className?: string
  mono?: boolean
  list?: string
  highlighted?: boolean
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
        "h-8 border-border/50 bg-transparent shadow-none hover:border-border focus-visible:border-border",
        mono && "font-mono text-xs",
        empty && "border-accent-brand/50 bg-accent-brand/5",
        highlighted && "border-accent-brand bg-accent-brand/10",
        className
      )}
    />
  )
}

export function EditArea({
  value,
  onChange,
  label,
  placeholder,
  highlighted,
}: {
  value: string
  onChange: (v: string) => void
  label: string
  placeholder?: string
  highlighted?: boolean
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
        empty && "border-accent-brand/50 bg-accent-brand/5",
        highlighted && "border-accent-brand bg-accent-brand/10"
      )}
    />
  )
}

/** Lightweight native <select> — cheap enough for hundreds of rows, which a
 *  Radix Select would not be. */
export function MiniSelect({
  value,
  onChange,
  options,
  label,
  placeholder,
  className,
  empty,
  highlighted,
}: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  label: string
  placeholder?: string
  className?: string
  empty?: boolean
  highlighted?: boolean
}) {
  const seen = new Set<string>()
  const unique = options.filter((o) => !seen.has(o.value) && seen.add(o.value))
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={label}
      data-empty={empty ? "true" : undefined}
      className={cn(
        "h-8 rounded-md border border-border/50 bg-transparent px-2 text-sm outline-none transition-colors hover:border-border focus-visible:border-border",
        empty && "border-accent-brand/50 bg-accent-brand/5",
        highlighted && "border-accent-brand bg-accent-brand/10",
        className
      )}
    >
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {unique.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}

export interface MasterItem {
  key: string
  title: string
  subtitle?: string
  /** Still has a field nobody has filled in. */
  empty: boolean
  /** Changed since the last save. */
  dirty?: boolean
  /** Added since the last save, so it does not exist on the server yet. */
  isNew?: boolean
}

/** Master list on the left of the editor.
 *
 *  Three separate signals share the left gutter, because they answer three
 *  different questions: *is something missing here* (hollow dot), *did I change
 *  this* (filled dot), *does this exist yet* (new). A single "modified" marker
 *  would collapse them and the reviewer would have to open every row to find
 *  out which. */
export function MasterList({
  items,
  selected,
  onSelect,
  footer,
}: {
  items: MasterItem[]
  selected: number
  onSelect: (i: number) => void
  /** Pinned below the list, outside the scroll area. An "add" action belongs
   *  with the things it adds to — and has to stay reachable at row 400. */
  footer?: React.ReactNode
}) {
  return (
    <div className="flex min-h-0 flex-col border">
      <ul className="flex min-h-0 flex-1 flex-col overflow-y-auto">
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
                  : it.dirty || it.isNew
                    ? "border-l-accent-brand/40 hover:bg-accent-brand/8"
                    : "hover:bg-accent-brand/8"
              )}
            >
              <span className="flex min-w-0 items-center gap-2">
                {(it.dirty || it.isNew) && (
                  <span
                    className="size-1.5 shrink-0 rounded-full bg-accent-brand"
                    title="Unsaved changes"
                  />
                )}
                {!it.dirty && !it.isNew && it.empty && (
                  <span
                    className="size-1.5 shrink-0 rounded-full border border-accent-brand/70"
                    title="Has empty fields"
                  />
                )}
                <span
                  className={cn(
                    "truncate text-sm",
                    selected === i && "font-medium",
                    (it.dirty || it.isNew) && "font-medium"
                  )}
                >
                  {it.title}
                </span>
                {it.isNew && (
                  <span className="shrink-0 rounded-sm bg-accent-brand/15 px-1 text-[10px] text-accent-brand">
                    new
                  </span>
                )}
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
      {footer && <div className="shrink-0 border-t bg-background p-2">{footer}</div>}
    </div>
  )
}

/** Master–detail layout: list on the left, the selected item's editor on the
 *  right. */
export function MasterDetail({
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
      <div className="grid min-h-0 flex-1 gap-4 md:grid-cols-[minmax(0,17rem)_1fr]">
        {list}
        <div className="min-h-0 overflow-y-auto">
          <div className="flex max-w-2xl flex-col gap-4 pr-1 pb-4">{children}</div>
        </div>
      </div>
    </div>
  )
}

/** The "ask right here" box.
 *
 * One of these sits at the top of whatever the user is editing — an entity, a
 * metric. It replaced a single global "Enhance with AI" pass, which rewrote
 * objects nobody had asked about and gave no way to say *what* to change. Here
 * the request is scoped to the thing on screen, and the answer lands in the
 * form fields below, where it can be read and corrected before saving.
 */
export function PromptBox({
  id,
  label,
  placeholder,
  hint,
  busy,
  onSubmit,
}: {
  id: string
  label: string
  placeholder: string
  hint: string
  busy: boolean
  onSubmit: (prompt: string) => void
}) {
  const [value, setValue] = React.useState("")
  const submit = () => {
    if (!value.trim()) return
    onSubmit(value)
    setValue("")
  }
  return (
    <div className="flex flex-col gap-2 border border-accent-brand/30 bg-accent-brand/5 p-3">
      <label htmlFor={id} className="flex items-center gap-1.5 text-xs font-medium">
        <RiSparkling2Line className="size-3.5 text-accent-brand" />
        {label}
      </label>
      <div className="flex gap-2">
        <input
          id={id}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit()
          }}
          placeholder={placeholder}
          className="h-8 flex-1 rounded-md border border-border/50 bg-background px-2 text-sm outline-none focus-visible:border-border"
        />
        <Button size="sm" variant="outline" onClick={submit} disabled={busy || !value.trim()}>
          {busy ? (
            <RiLoader4Line data-icon="inline-start" className="animate-spin" />
          ) : (
            <RiSparkling2Line data-icon="inline-start" />
          )}
          Fill in
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  )
}

/** What the AI just said, and anything it refused to do. */
export function DraftNotes({
  reasoning,
  warnings,
}: {
  reasoning: string
  warnings: string[]
}) {
  return (
    <>
      {reasoning && <p className="text-xs text-muted-foreground italic">{reasoning}</p>}
      {warnings.length > 0 && (
        <ul className="flex flex-col gap-1 border border-amber-500/40 bg-amber-500/5 p-2 text-xs">
          {warnings.map((w) => (
            <li key={w}>⚠ {w}</li>
          ))}
        </ul>
      )}
    </>
  )
}


/** A tab label that says how much of what is behind it is unsaved.

 *  Without this the only unsaved indicator was a single line in the toolbar,
 *  which said *that* something changed but never *where* — on a model with 122
 *  entities and 122 metrics that is not usable information. */
export function TabLabel({
  label,
  count,
  unsaved,
}: {
  label: string
  count: number
  unsaved: number
}) {
  return (
    <span className="flex items-center gap-1.5">
      {label} ({count})
      {unsaved > 0 && (
        <span
          className="rounded-full bg-accent-brand px-1.5 text-[10px] leading-4 font-medium text-background"
          title={`${unsaved} unsaved change${unsaved === 1 ? "" : "s"}`}
        >
          {unsaved}
        </span>
      )}
    </span>
  )
}

/** A relationship's identity for change tracking — it has no stable id, so an
 *  edit reads as the old signature leaving and a new one arriving. Shared so
 *  the panel (diff) and the editor (per-row "new" marker) agree exactly. */
export function relSignature(r: {
  from_entity_key: string
  from_column: string
  to_entity_key: string
  to_column: string
  kind: string
}): string {
  return `${r.from_entity_key}|${r.from_column}|${r.to_entity_key}|${r.to_column}|${r.kind}`
}
