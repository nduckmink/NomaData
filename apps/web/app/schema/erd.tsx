"use client"

import { useEffect, useId, useMemo, useState } from "react"
import { useTheme } from "next-themes"

import type { DatabaseCatalog, TableInfo } from "@/lib/api-client"
import { Skeleton } from "@/components/ui/skeleton"

const NEIGHBOUR_CAP = 12

function token(value: string): string {
  return value.replace(/[^A-Za-z0-9_]/g, "_") || "x"
}

/** Build a Mermaid erDiagram for the focus table + its FK neighbours. */
function buildErd(
  catalog: DatabaseCatalog,
  focusName: string
): { def: string; hidden: number; hasRelations: boolean } {
  const byName = new Map(catalog.tables.map((t) => [t.name, t]))
  const focus = byName.get(focusName)
  if (!focus) return { def: "", hidden: 0, hasRelations: false }

  const related = new Set<string>()
  for (const fk of focus.foreign_keys) {
    if (byName.has(fk.references_table)) related.add(fk.references_table)
  }
  for (const t of catalog.tables) {
    for (const fk of t.foreign_keys) {
      if (fk.references_table === focus.name) related.add(t.name)
    }
  }
  related.delete(focus.name)

  const relatedList = [...related]
  const shown = relatedList.slice(0, NEIGHBOUR_CAP)
  const inDiagram = new Set([focus.name, ...shown])

  const lines: string[] = ["erDiagram"]
  for (const name of inDiagram) {
    const table = byName.get(name)
    if (!table) continue
    const isFocus = name === focus.name
    const fkCols = new Set(table.foreign_keys.map((fk) => fk.column))
    const cols = table.columns.filter((c) =>
      isFocus ? c.is_primary_key || fkCols.has(c.name) : c.is_primary_key
    )
    if (cols.length === 0) continue // entity still appears via its relationship
    lines.push(`  "${name}" {`)
    for (const c of cols) {
      const key = c.is_primary_key ? "PK" : fkCols.has(c.name) ? "FK" : ""
      lines.push(`    ${token(c.data_type)} ${token(c.name)} ${key}`.trimEnd())
    }
    lines.push("  }")
  }

  const seen = new Set<string>()
  let relationCount = 0
  for (const name of inDiagram) {
    const table = byName.get(name)
    if (!table) continue
    for (const fk of table.foreign_keys) {
      if (!inDiagram.has(fk.references_table)) continue
      const key = `${fk.references_table}->${name}:${fk.column}`
      if (seen.has(key)) continue
      seen.add(key)
      relationCount += 1
      lines.push(
        `  "${fk.references_table}" ||--o{ "${name}" : "${token(fk.column)}"`
      )
    }
  }

  return {
    def: lines.join("\n"),
    hidden: relatedList.length - shown.length,
    hasRelations: relationCount > 0,
  }
}

export function TableErd({
  catalog,
  table,
}: {
  catalog: DatabaseCatalog
  table: TableInfo
}) {
  const { def, hidden, hasRelations } = useMemo(
    () => buildErd(catalog, table.name),
    [catalog, table.name]
  )

  if (!hasRelations) {
    return (
      <p className="text-sm text-muted-foreground">
        <span className="font-mono">{table.name}</span> has no foreign-key
        relationships to diagram.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <MermaidDiagram definition={def} />
      {hidden > 0 && (
        <p className="text-xs text-muted-foreground">
          +{hidden} more related table{hidden > 1 ? "s" : ""} not shown.
        </p>
      )}
    </div>
  )
}

function MermaidDiagram({ definition }: { definition: string }) {
  const rawId = useId()
  const { resolvedTheme } = useTheme()
  const [svg, setSvg] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const mermaid = (await import("mermaid")).default
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: resolvedTheme === "dark" ? "dark" : "neutral",
          er: { useMaxWidth: true },
        })
        const id = `erd-${rawId.replace(/[^a-zA-Z0-9]/g, "")}`
        const { svg: rendered } = await mermaid.render(id, definition)
        if (!cancelled) {
          setSvg(rendered)
          setFailed(false)
        }
      } catch {
        if (!cancelled) setFailed(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [definition, resolvedTheme, rawId])

  if (failed) {
    return (
      <p className="text-sm text-muted-foreground">
        Could not render the diagram.
      </p>
    )
  }
  if (svg === null) {
    return <Skeleton className="h-64 w-full" />
  }
  return (
    <div
      className="overflow-auto rounded-none border p-3 [&_svg]:h-auto [&_svg]:max-w-none"
      // Mermaid output; input is our own schema metadata, rendered with
      // securityLevel: "strict".
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  )
}
