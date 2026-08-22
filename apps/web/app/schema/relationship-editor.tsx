"use client"

/**
 * Links between entities — editable, and findable when the database forgot them.
 *
 * Relationships come from foreign keys, which is fine until a schema does not
 * declare them: the SQL Server source here has 196 tables and 12 foreign keys.
 * Without links, every question that crosses two tables is unanswerable, and a
 * read-only list leaves the user no way out.
 *
 * "Find missing links" reads naming conventions (`enterprise_id` →
 * `enterprises.id`) with a rule, not a model: a wrong join silently pairs
 * unrelated rows, so an ambiguous name is skipped rather than guessed.
 */

import { useState } from "react"
import {
  RiAddLine,
  RiDeleteBinLine,
  RiLoader4Line,
  RiSearchLine,
} from "@remixicon/react"
import { toast } from "sonner"

import {
  type Entity,
  type Relationship,
  suggestRelationships,
} from "@/lib/api-client"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

import { MiniSelect, relSignature } from "./semantic-fields"
import { cn } from "@/lib/utils"

const KINDS = [
  { value: "many_to_one", label: "many → one" },
  { value: "one_to_many", label: "one → many" },
  { value: "one_to_one", label: "one → one" },
]

export function RelationshipEditor({
  source,
  entities,
  relationships,
  savedSignatures,
  onChange,
}: {
  source: string
  entities: Entity[]
  relationships: Relationship[]
  /** Signatures of the last-saved links, so a row not among them shows as
   *  unsaved — the same "new / edited" cue the entity and metric lists use. */
  savedSignatures: Set<string>
  onChange: (next: Relationship[]) => void
}) {
  const [finding, setFinding] = useState(false)
  const byKey = new Map(entities.map((e) => [e.key, e]))
  // An entity is far easier to verify by its table than by a business name like
  // "Người dùng (Nhân viên)" — so the picker shows both.
  const entityOptions = entities.map((e) => ({
    value: e.key,
    label: `${e.name} · ${e.table}`,
  }))

  const columnsOf = (key: string) => {
    const entity = byKey.get(key)
    if (!entity) return []
    return [entity.primary_key, ...entity.dimensions.map((d) => d.column)]
  }

  const update = (index: number, patch: Partial<Relationship>) =>
    onChange(
      relationships.map((r, i) => (i === index ? { ...r, ...patch } : r))
    )

  const add = () => {
    const first = entities[0]
    const second = entities[1] ?? entities[0]
    if (!first) return
    onChange([
      ...relationships,
      {
        from_entity_key: first.key,
        to_entity_key: second.key,
        from_column: first.primary_key,
        to_column: second.primary_key,
        kind: "many_to_one",
      },
    ])
  }

  const find = () =>
    void (async () => {
      setFinding(true)
      try {
        const found = await suggestRelationships(source)
        if (found.length === 0) {
          toast.info("No further links could be inferred from column names.")
          return
        }
        onChange([...relationships, ...found])
        toast.success(
          `Added ${found.length} link${found.length === 1 ? "" : "s"} — review and save`
        )
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Could not look for links")
      } finally {
        setFinding(false)
      }
    })()

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">
          How entities join. Without a link, a question cannot cross two tables.
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={find} disabled={finding}>
            {finding ? (
              <RiLoader4Line
                data-icon="inline-start"
                className="animate-spin"
              />
            ) : (
              <RiSearchLine data-icon="inline-start" />
            )}
            Find missing links
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={add}
            disabled={!entities.length}
          >
            <RiAddLine data-icon="inline-start" />
            Add link
          </Button>
        </div>
      </div>

      <div className="relative min-h-0 flex-1 overflow-auto border [&_[data-slot=table-container]]:overflow-visible">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-background">
            <TableRow>
              <TableHead>From</TableHead>
              <TableHead>Column</TableHead>
              <TableHead>To</TableHead>
              <TableHead>Column</TableHead>
              <TableHead className="w-36">Kind</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {relationships.map((r, i) => {
              const isNew = !savedSignatures.has(relSignature(r))
              return (
                <TableRow
                  key={`${r.from_entity_key}-${r.from_column}-${r.to_entity_key}-${i}`}
                  className={cn(isNew && "bg-accent-brand/5")}
                >
                  <TableCell>
                    <span className="flex items-center gap-2">
                      <span
                        className={cn(
                          "size-1.5 shrink-0 rounded-full",
                          isNew ? "bg-accent-brand" : "bg-transparent"
                        )}
                        title={isNew ? "Unsaved" : undefined}
                      />
                      <PickCell
                        value={r.from_entity_key}
                        onChange={(v) =>
                          update(i, {
                            from_entity_key: v,
                            from_column: byKey.get(v)?.primary_key ?? "",
                          })
                        }
                        label="From entity"
                        options={entityOptions}
                      />
                    </span>
                  </TableCell>
                  <TableCell>
                    <PickCell
                      value={r.from_column}
                      onChange={(v) => update(i, { from_column: v })}
                      label="From column"
                      options={columnsOf(r.from_entity_key).map((c) => ({
                        value: c,
                        label: c,
                      }))}
                    />
                  </TableCell>
                  <TableCell>
                    <PickCell
                      value={r.to_entity_key}
                      onChange={(v) =>
                        update(i, {
                          to_entity_key: v,
                          to_column: byKey.get(v)?.primary_key ?? "",
                        })
                      }
                      label="To entity"
                      options={entityOptions}
                    />
                  </TableCell>
                  <TableCell>
                    <PickCell
                      value={r.to_column}
                      onChange={(v) => update(i, { to_column: v })}
                      label="To column"
                      options={columnsOf(r.to_entity_key).map((c) => ({
                        value: c,
                        label: c,
                      }))}
                    />
                  </TableCell>
                  <TableCell>
                    <PickCell
                      value={r.kind}
                      onChange={(v) => update(i, { kind: v })}
                      label="Link kind"
                      options={KINDS}
                    />
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label="Remove link"
                      onClick={() =>
                        onChange(relationships.filter((_, j) => j !== i))
                      }
                    >
                      <RiDeleteBinLine />
                    </Button>
                  </TableCell>
                </TableRow>
              )
            })}
            {relationships.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="text-sm text-muted-foreground"
                >
                  No links yet. This source may not declare foreign keys — try
                  “Find missing links”.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

/** A cell that reads as text and becomes a picker where it is clicked.
 *
 *  183 rows with five dropdowns each is 915 select controls mounted so that
 *  somebody can change one of them. A relationship is read far more often than
 *  it is edited, so the row is text until it isn't — which is both cheaper and
 *  quieter to look at than a wall of dropdowns.
 */
function PickCell({
  value,
  options,
  label,
  placeholder,
  onChange,
}: {
  value: string
  options: { value: string; label: string }[]
  label: string
  placeholder?: string
  onChange: (v: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const shown = options.find((o) => o.value === value)?.label

  if (editing) {
    return (
      <MiniSelect
        value={value}
        onChange={(v) => {
          onChange(v)
          setEditing(false)
        }}
        label={label}
        placeholder={placeholder}
        options={options}
        className="w-full"
        defaultOpen
        onClose={() => setEditing(false)}
      />
    )
  }

  return (
    <button
      type="button"
      onClick={() => setEditing(true)}
      aria-label={`${label}: ${shown ?? "not set"}. Click to change.`}
      className={cn(
        "flex h-8 w-full items-center rounded-md border border-transparent px-2 text-left text-sm transition-colors hover:border-border/60 hover:bg-wash",
        !shown && "text-muted-foreground"
      )}
    >
      <span className="truncate">{shown ?? placeholder ?? "—"}</span>
    </button>
  )
}
