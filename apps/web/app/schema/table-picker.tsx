"use client"

/**
 * Choosing which tables the model covers.
 *
 * Building over a whole schema produces a model nobody can review — 122
 * entities, 122 row-count metrics, and the handful of tables anyone actually
 * measures buried among lookup tables and audit logs. Roughly four in five
 * questions touch a small set of tables, so the build asks which ones.
 *
 * The ranking below is a hint, not a decision: it puts the likely fact tables
 * at the top and pre-selects them, and the user changes it freely.
 */

import { useMemo, useState } from "react"

import type { DatabaseCatalog, TableInfo } from "@/lib/api-client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

/** How many tables to tick by default when nothing was chosen before. */
const DEFAULT_SELECTION = 15

const NOISE =
  /(_log|_logs|_tmp|_temp|_bak|_backup|_archive|_history|_histories)$|^(sys_|tmp_)/i
const BUSINESS_WORDS =
  /(order|invoice|payment|transaction|contract|customer|client|enterprise|employee|student|product|sale|revenue|fee|account)/i
const MONEY = /(amount|price|total|value|salary|fee|cost|balance|so_tien|tien)/i

export interface TableChoice {
  name: string
  columns: number
  links: number
  score: number
  reason: string
}

/** Rank tables by how likely they are to be worth measuring.
 *
 * Signals, in order of weight: how many other tables point at it (a table with
 * children is a fact table), whether it has both a date and a money-shaped
 * column, and whether its name reads like a business concept. */
export function rankTables(catalog: DatabaseCatalog): TableChoice[] {
  const incoming = new Map<string, number>()
  for (const table of catalog.tables) {
    for (const fk of table.foreign_keys) {
      incoming.set(
        fk.references_table,
        (incoming.get(fk.references_table) ?? 0) + 1
      )
    }
  }

  return catalog.tables
    .map((table) => {
      const referenced = incoming.get(table.name) ?? 0
      const hasDate = table.columns.some((c) => /date|time/i.test(c.data_type))
      const hasMoney = table.columns.some(
        (c) =>
          MONEY.test(c.name) &&
          /int|dec|num|float|double|real|money/i.test(c.data_type)
      )
      const reasons: string[] = []
      let score = 0

      if (referenced > 0) {
        score += Math.min(referenced, 10) * 3
        reasons.push(
          `${referenced} table${referenced === 1 ? "" : "s"} link to it`
        )
      }
      if (hasDate && hasMoney) {
        score += 12
        reasons.push("has dates and amounts")
      } else if (hasMoney) {
        score += 6
        reasons.push("has amounts")
      } else if (hasDate) {
        score += 3
      }
      if (BUSINESS_WORDS.test(table.name)) {
        score += 5
        reasons.push("business-sounding name")
      }
      if (table.foreign_keys.length > 0)
        score += Math.min(table.foreign_keys.length, 5)
      if (NOISE.test(table.name)) {
        score -= 25
        reasons.length = 0
        reasons.push("looks like a log or backup")
      }
      if (table.primary_key.length === 0) {
        score -= 40
        reasons.push("no primary key — cannot be measured")
      }

      return {
        name: table.name,
        columns: table.columns.length,
        links: table.foreign_keys.length + referenced,
        score,
        reason: reasons.join(" · "),
      }
    })
    .sort((a, b) => b.score - a.score || a.name.localeCompare(b.name))
}

/** The tables ticked when the user has not chosen before. */
export function defaultSelection(ranked: TableChoice[]): string[] {
  return ranked
    .filter((t) => t.score > 0)
    .slice(0, DEFAULT_SELECTION)
    .map((t) => t.name)
}

export function TablePicker({
  catalog,
  selected,
  onChange,
}: {
  catalog: DatabaseCatalog
  selected: Set<string>
  onChange: (next: Set<string>) => void
}) {
  const [query, setQuery] = useState("")
  const ranked = useMemo(() => rankTables(catalog), [catalog])
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? ranked.filter((t) => t.name.toLowerCase().includes(q)) : ranked
  }, [ranked, query])

  const toggle = (name: string) => {
    const next = new Set(selected)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    onChange(next)
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search tables…"
          aria-label="Search tables"
          className="h-8 flex-1"
        />
        <span className="text-xs text-muted-foreground tnum">
          {selected.size} of {ranked.length} selected
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onChange(new Set(visible.map((t) => t.name)))}
        >
          Select all
        </Button>
        <Button variant="ghost" size="sm" onClick={() => onChange(new Set())}>
          Clear
        </Button>
      </div>

      <ul className="flex max-h-[52vh] min-h-48 flex-col overflow-y-auto border">
        {visible.map((table) => (
          <li key={table.name}>
            <label
              className={cn(
                "flex cursor-pointer items-center gap-3 border-b px-3 py-1.5 text-sm transition-colors",
                selected.has(table.name) ? "bg-accent-brand/5" : "hover:bg-wash"
              )}
            >
              <input
                type="checkbox"
                checked={selected.has(table.name)}
                onChange={() => toggle(table.name)}
                className="size-4 shrink-0"
                aria-label={table.name}
              />
              <span className="min-w-0 flex-1 truncate font-mono text-xs">
                {table.name}
              </span>
              {table.reason && (
                <span className="hidden max-w-56 truncate text-xs text-muted-foreground sm:block">
                  {table.reason}
                </span>
              )}
              <span className="shrink-0 text-xs text-muted-foreground tnum">
                {table.columns} cols
              </span>
            </label>
          </li>
        ))}
        {visible.length === 0 && (
          <li className="p-3 text-sm text-muted-foreground">
            No table matches.
          </li>
        )}
      </ul>

      <p className="text-xs text-muted-foreground">
        Only the tables you tick become part of the model. You can add more
        later with Rebuild — anything you already edited is kept.
      </p>
    </div>
  )
}

export type { TableInfo }
