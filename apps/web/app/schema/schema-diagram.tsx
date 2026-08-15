"use client"

import { memo, useCallback, useMemo, useRef, useState } from "react"
import {
  RiAddLine,
  RiFocus3Line,
  RiSearchLine,
  RiSubtractLine,
} from "@remixicon/react"

import type { DatabaseCatalog, TableInfo } from "@/lib/api-client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

/**
 * Whole-database ER diagram.
 *
 * Hand-drawn SVG rather than a diagram library, for the same three reasons as
 * before: colours/fonts are `var(--token)` so both themes are inherited for
 * free, monospaced text means every box can be measured arithmetically, and a
 * viewBox equal to the content's bounding box always fits on first paint.
 *
 * Layout is layered per connected component (BFS from the busiest table, one
 * column per hop, barycentre ordering inside a column to cut crossings), then
 * the components are shelf-packed. It is deterministic — no simulation, so the
 * same schema always draws the same map.
 */

const ROW_FONT = 11
const HEADER_FONT = 12
const CHAR_W = ROW_FONT * 0.6
const HEADER_CHAR_W = HEADER_FONT * 0.6
const ROW_H = 16
const HEADER_H = 26
const PAD_X = 9
const NODE_MIN_W = 128
const MAX_NAME_CHARS = 26
const KEY_ROWS = 10
const ALL_ROWS = 40
const NODE_GAP_X = 68
const NODE_GAP_Y = 26
const COMPONENT_GAP = 90
const SHELF_WIDTH = 3400
/** A hub table can have 40 neighbours; stacked in one column they make a
 *  ribbon 4000px tall. Wrap long levels into side-by-side sub-columns. */
const MAX_COLUMN_NODES = 11
/** Tables with no foreign keys are parked in their own grid, not the graph. */
const UNLINKED_PER_ROW = 8
const CANVAS_PAD = 60
const MIN_ZOOM = 0.08
const MAX_ZOOM = 10
/** Below this scale the column rows are unreadable, so only names are drawn. */
const DETAIL_ZOOM = 0.55

type Row = { label: string; tag: "PK" | "FK" | "" }

type Node = {
  name: string
  rows: Row[]
  hiddenRows: number
  degree: number
  x: number
  y: number
  w: number
  h: number
}

type Edge = {
  id: string
  /** many side → one side; the arrow head sits on the referenced table. */
  from: string
  to: string
  columns: string[]
  path: string
  midX: number
  midY: number
}

type Box = { x: number; y: number; width: number; height: number }

function ellipsis(value: string): string {
  return value.length > MAX_NAME_CHARS
    ? `${value.slice(0, MAX_NAME_CHARS - 1)}…`
    : value
}

function measureNode(name: string, rows: Row[]): number {
  const header = ellipsis(name).length * HEADER_CHAR_W
  const widest = rows.reduce(
    (max, r) => Math.max(max, (ellipsis(r.label).length + 4) * CHAR_W),
    0
  )
  return Math.max(NODE_MIN_W, Math.ceil(Math.max(header, widest)) + PAD_X * 2)
}

function makeNode(table: TableInfo, detail: "keys" | "all"): Node {
  const fkColumns = new Set(table.foreign_keys.map((fk) => fk.column))
  const candidates =
    detail === "all"
      ? table.columns
      : table.columns.filter((c) => c.is_primary_key || fkColumns.has(c.name))
  const limit = detail === "all" ? ALL_ROWS : KEY_ROWS
  const rows: Row[] = candidates.slice(0, limit).map((c) => ({
    label: c.name,
    tag: c.is_primary_key ? "PK" : fkColumns.has(c.name) ? "FK" : "",
  }))
  return {
    name: table.name,
    rows,
    hiddenRows: Math.max(0, candidates.length - rows.length),
    degree: 0,
    x: 0,
    y: 0,
    w: measureNode(table.name, rows),
    h: HEADER_H + rows.length * ROW_H + (rows.length ? 5 : 0),
  }
}

function layout(catalog: DatabaseCatalog, detail: "keys" | "all") {
  const nodes = new Map<string, Node>()
  for (const table of catalog.tables) {
    nodes.set(table.name, makeNode(table, detail))
  }

  // Collapse composite/duplicate foreign keys into one edge per table pair.
  const pairs = new Map<
    string,
    { from: string; to: string; columns: string[] }
  >()
  for (const table of catalog.tables) {
    for (const fk of table.foreign_keys) {
      if (!nodes.has(fk.references_table)) continue
      if (fk.references_table === table.name) continue // self-reference
      const id = `${table.name}→${fk.references_table}`
      const existing = pairs.get(id)
      if (existing) existing.columns.push(fk.column)
      else
        pairs.set(id, {
          from: table.name,
          to: fk.references_table,
          columns: [fk.column],
        })
    }
  }

  const neighbours = new Map<string, Set<string>>()
  const link = (a: string, b: string) => {
    if (!neighbours.has(a)) neighbours.set(a, new Set())
    neighbours.get(a)?.add(b)
  }
  for (const { from, to } of pairs.values()) {
    link(from, to)
    link(to, from)
  }
  for (const [name, node] of nodes) {
    node.degree = neighbours.get(name)?.size ?? 0
  }

  // --- connected components, busiest table first ---
  const seen = new Set<string>()
  const components: string[][] = []
  const order = [...nodes.values()].sort((a, b) => b.degree - a.degree)
  for (const start of order) {
    if (seen.has(start.name)) continue
    const queue = [start.name]
    const members: string[] = []
    seen.add(start.name)
    while (queue.length) {
      const current = queue.shift() as string
      members.push(current)
      for (const next of neighbours.get(current) ?? []) {
        if (seen.has(next)) continue
        seen.add(next)
        queue.push(next)
      }
    }
    components.push(members)
  }

  // --- layer each linked component, then shelf-pack the components ---
  const linked = components.filter((c) => c.length > 1)
  const unlinked = components.filter((c) => c.length === 1).map((c) => c[0])

  let shelfX = 0
  let shelfY = 0
  let shelfHeight = 0

  for (const members of linked) {
    const inComponent = new Set(members)
    const root = members.reduce((best, name) =>
      (nodes.get(name)?.degree ?? 0) > (nodes.get(best)?.degree ?? 0)
        ? name
        : best
    )

    // BFS levels = distance from the busiest table.
    const level = new Map<string, number>([[root, 0]])
    const queue = [root]
    while (queue.length) {
      const current = queue.shift() as string
      const depth = level.get(current) ?? 0
      for (const next of neighbours.get(current) ?? []) {
        if (!inComponent.has(next) || level.has(next)) continue
        level.set(next, depth + 1)
        queue.push(next)
      }
    }

    const columns: string[][] = []
    for (const name of members) {
      const depth = level.get(name) ?? 0
      ;(columns[depth] ??= []).push(name)
    }

    // Order each level, then wrap it into sub-columns of a readable height.
    const placedColumns: string[][] = []
    columns.forEach((names, depth) => {
      const barycentre = (name: string): number => {
        const previous = [...(neighbours.get(name) ?? [])].filter(
          (n) => (level.get(n) ?? -1) === depth - 1
        )
        if (previous.length === 0) return Number.MAX_SAFE_INTEGER
        const sum = previous.reduce((acc, n) => acc + (nodes.get(n)?.y ?? 0), 0)
        return sum / previous.length
      }
      // Barycentre: sort by the average y of already-placed neighbours, so
      // edges between columns stay roughly horizontal.
      const sorted =
        depth === 0
          ? [...names].sort(
              (a, b) =>
                (nodes.get(b)?.degree ?? 0) - (nodes.get(a)?.degree ?? 0)
            )
          : [...names].sort((a, b) => barycentre(a) - barycentre(b))

      for (let i = 0; i < sorted.length; i += MAX_COLUMN_NODES) {
        placedColumns.push(sorted.slice(i, i + MAX_COLUMN_NODES))
      }
    })

    let x = 0
    const columnHeights: number[] = []
    placedColumns.forEach((names, index) => {
      let y = 0
      let width = 0
      for (const name of names) {
        const node = nodes.get(name)
        if (!node) continue
        node.x = x
        node.y = y
        y += node.h + NODE_GAP_Y
        width = Math.max(width, node.w)
      }
      columnHeights[index] = y - NODE_GAP_Y
      x += width + NODE_GAP_X
    })

    // Centre every column against the tallest one, then place the component.
    const tallest = Math.max(...columnHeights, 0)
    placedColumns.forEach((names, index) => {
      const shift = (tallest - (columnHeights[index] ?? 0)) / 2
      for (const name of names) {
        const node = nodes.get(name)
        if (node) node.y += shift
      }
    })

    const width = x - NODE_GAP_X
    if (shelfX > 0 && shelfX + width > SHELF_WIDTH) {
      shelfY += shelfHeight + COMPONENT_GAP
      shelfX = 0
      shelfHeight = 0
    }
    for (const name of members) {
      const node = nodes.get(name)
      if (!node) continue
      node.x += shelfX
      node.y += shelfY
    }
    shelfX += width + COMPONENT_GAP
    shelfHeight = Math.max(shelfHeight, tallest)
  }

  // Unlinked tables sit under the graph in a plain grid — they have no edges
  // to place them by, and threading them through the shelf packing would
  // stretch the canvas for no information.
  if (unlinked.length > 0) {
    const gridY = shelfY + shelfHeight + (shelfHeight > 0 ? COMPONENT_GAP : 0)
    const columnWidth =
      Math.max(...unlinked.map((n) => nodes.get(n)?.w ?? NODE_MIN_W)) +
      NODE_GAP_X
    let rowY = gridY
    let rowHeight = 0
    unlinked.forEach((name, i) => {
      const node = nodes.get(name)
      if (!node) return
      const column = i % UNLINKED_PER_ROW
      if (column === 0 && i > 0) {
        rowY += rowHeight + NODE_GAP_Y
        rowHeight = 0
      }
      node.x = column * columnWidth
      node.y = rowY
      rowHeight = Math.max(rowHeight, node.h)
    })
  }

  // --- edges ---
  const edges: Edge[] = []
  for (const [id, pair] of pairs) {
    const from = nodes.get(pair.from)
    const to = nodes.get(pair.to)
    if (!from || !to) continue

    let path: string
    let midX: number
    let midY: number
    const fromMidY = from.y + from.h / 2
    const toMidY = to.y + to.h / 2

    if (to.x >= from.x + from.w) {
      // target sits to the right: exit right, enter left. Smooth horizontal
      // S-curve — control points pulled out sideways from each end.
      const sx = from.x + from.w
      const tx = to.x
      midX = (sx + tx) / 2
      midY = (fromMidY + toMidY) / 2
      path = `M ${sx} ${fromMidY} C ${midX} ${fromMidY}, ${midX} ${toMidY}, ${tx} ${toMidY}`
    } else if (from.x >= to.x + to.w) {
      const sx = from.x
      const tx = to.x + to.w
      midX = (sx + tx) / 2
      midY = (fromMidY + toMidY) / 2
      path = `M ${sx} ${fromMidY} C ${midX} ${fromMidY}, ${midX} ${toMidY}, ${tx} ${toMidY}`
    } else {
      // overlapping columns: route vertically with a smooth S-curve.
      const sx = from.x + from.w / 2
      const tx = to.x + to.w / 2
      const downward = to.y > from.y
      const sy = downward ? from.y + from.h : from.y
      const ty = downward ? to.y : to.y + to.h
      midY = (sy + ty) / 2
      midX = (sx + tx) / 2
      path = `M ${sx} ${sy} C ${sx} ${midY}, ${tx} ${midY}, ${tx} ${ty}`
    }

    edges.push({
      id,
      from: pair.from,
      to: pair.to,
      columns: pair.columns,
      path,
      midX,
      midY,
    })
  }

  const all = [...nodes.values()]
  const minX = Math.min(...all.map((n) => n.x))
  const minY = Math.min(...all.map((n) => n.y))
  const maxX = Math.max(...all.map((n) => n.x + n.w))
  const maxY = Math.max(...all.map((n) => n.y + n.h))

  return {
    nodes: all,
    edges,
    byName: nodes,
    viewBox: {
      x: minX - CANVAS_PAD,
      y: minY - CANVAS_PAD,
      width: maxX - minX + CANVAS_PAD * 2,
      height: maxY - minY + CANVAS_PAD * 2,
    } as Box,
    isolated: all.filter((n) => n.degree === 0).length,
  }
}

export function SchemaDiagram({
  catalog,
  onOpenTable,
}: {
  catalog: DatabaseCatalog
  onOpenTable: (name: string) => void
}) {
  const [detail, setDetail] = useState<"keys" | "all">("keys")
  const graph = useMemo(() => layout(catalog, detail), [catalog, detail])
  const svgRef = useRef<SVGSVGElement>(null)
  const [box, setBox] = useState<Box>(graph.viewBox)
  // What the user clicked to highlight (a table name or an edge id), or null.
  // Click-to-highlight, not hover — a cursor drifting across a dense map
  // shouldn't flash every table it passes.
  const [picked, setPicked] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const drag = useRef<{
    ox: number
    oy: number
    px: number
    py: number
    moved: boolean
    captured: boolean
    id: number
  } | null>(null)
  // Whether the last pointer gesture was a pan — so a click that ends a drag
  // doesn't also toggle a selection.
  const panned = useRef(false)

  const toggle = useCallback((id: string) => {
    setPicked((prev) => (prev === id ? null : id))
  }, [])

  // Re-layout (detail toggle, new source) resets the camera and selection.
  const [seen, setSeen] = useState(graph.viewBox)
  if (seen !== graph.viewBox) {
    setSeen(graph.viewBox)
    setBox(graph.viewBox)
    setPicked(null)
  }

  const zoom = graph.viewBox.width / box.width

  /** Tables and edges touched by the picked table or edge. */
  const active = useMemo(() => {
    if (!picked) return null
    const edge = graph.edges.find((e) => e.id === picked)
    if (edge) {
      return {
        tables: new Set([edge.from, edge.to]),
        edges: new Set([edge.id]),
      }
    }
    const tables = new Set<string>([picked])
    const edges = new Set<string>()
    for (const e of graph.edges) {
      if (e.from === picked || e.to === picked) {
        edges.add(e.id)
        tables.add(e.from)
        tables.add(e.to)
      }
    }
    return { tables, edges }
  }, [picked, graph.edges])

  const toLocal = useCallback((clientX: number, clientY: number) => {
    const svg = svgRef.current
    const ctm = svg?.getScreenCTM()
    if (!svg || !ctm) return null
    const point = svg.createSVGPoint()
    point.x = clientX
    point.y = clientY
    return point.matrixTransform(ctm.inverse())
  }, [])

  const zoomBy = useCallback(
    (factor: number, focal?: { x: number; y: number }) => {
      setBox((current) => {
        const base = graph.viewBox.width
        const next = Math.min(
          base / MIN_ZOOM,
          Math.max(base / MAX_ZOOM, current.width / factor)
        )
        const scale = next / current.width
        const origin = focal ?? {
          x: current.x + current.width / 2,
          y: current.y + current.height / 2,
        }
        return {
          x: origin.x - (origin.x - current.x) * scale,
          y: origin.y - (origin.y - current.y) * scale,
          width: next,
          height: current.height * scale,
        }
      })
    },
    [graph.viewBox.width]
  )

  /** Centre the camera on one table at a readable scale. */
  const focusTable = useCallback(
    (name: string) => {
      const node = graph.byName.get(name)
      if (!node) return
      const width = Math.max(node.w * 6, 900)
      const height = width * (box.height / box.width)
      setBox({
        x: node.x + node.w / 2 - width / 2,
        y: node.y + node.h / 2 - height / 2,
        width,
        height,
      })
      setPicked(name)
    },
    [graph.byName, box.height, box.width]
  )

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    return graph.nodes
      .filter((n) => n.name.toLowerCase().includes(q))
      .slice(0, 6)
  }, [query, graph.nodes])

  const showRows = zoom >= DETAIL_ZOOM

  return (
    <div className="flex h-full min-h-0 flex-col border">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-2 py-1.5">
        <div className="flex min-w-0 items-center gap-2">
          <div className="relative">
            <RiSearchLine
              aria-hidden
              className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && matches[0]) {
                  e.preventDefault()
                  focusTable(matches[0].name)
                  setQuery("")
                }
              }}
              placeholder="Find a table…"
              aria-label="Find a table in the diagram"
              className="h-7 w-44 pl-7 font-mono text-xs"
            />
            {matches.length > 0 && (
              <ul className="absolute top-8 left-0 z-10 max-h-56 w-56 overflow-auto border bg-popover">
                {matches.map((m) => (
                  <li key={m.name}>
                    <button
                      type="button"
                      className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left font-mono text-xs hover:bg-accent"
                      onClick={() => {
                        focusTable(m.name)
                        setQuery("")
                      }}
                    >
                      <span className="truncate">{m.name}</span>
                      <span className="shrink-0 text-muted-foreground tnum">
                        {m.degree}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <p className="hidden truncate text-xs text-muted-foreground sm:block">
            {graph.nodes.length} tables · {graph.edges.length} relationships
            {graph.isolated > 0 && ` · ${graph.isolated} unlinked`}
            {picked ? " · click empty space to clear" : " · click to highlight"}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <ToggleGroup
            type="single"
            value={detail}
            onValueChange={(v) => v && setDetail(v as "keys" | "all")}
            aria-label="Column detail"
          >
            <ToggleGroupItem value="keys" className="text-xs">
              Keys
            </ToggleGroupItem>
            <ToggleGroupItem value="all" className="text-xs">
              All columns
            </ToggleGroupItem>
          </ToggleGroup>
          <span className="mx-1 font-mono text-xs text-muted-foreground tnum">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            variant="outline"
            size="icon-xs"
            aria-label="Zoom out"
            onClick={() => zoomBy(1 / 1.3)}
          >
            <RiSubtractLine />
          </Button>
          <Button
            variant="outline"
            size="icon-xs"
            aria-label="Zoom in"
            onClick={() => zoomBy(1.3)}
          >
            <RiAddLine />
          </Button>
          <Button
            variant="outline"
            size="icon-xs"
            aria-label="Fit diagram to view"
            title="Fit"
            onClick={() => setBox(graph.viewBox)}
          >
            <RiFocus3Line />
          </Button>
        </div>
      </div>

      <svg
        ref={svgRef}
        viewBox={`${box.x} ${box.y} ${box.width} ${box.height}`}
        role="img"
        aria-label={`Entity relationship diagram: ${graph.nodes.length} tables, ${graph.edges.length} relationships`}
        className="min-h-0 flex-1 cursor-grab touch-none [user-select:none] active:cursor-grabbing"
        onWheel={(e) => {
          const local = toLocal(e.clientX, e.clientY)
          zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15, local ?? undefined)
        }}
        onPointerDown={(e) => {
          // NO pointer capture here — capturing on pointerdown retargets the
          // click to the <svg>, so node/edge clicks never fire. Capture is
          // taken lazily on the first real move (a pan), below.
          drag.current = {
            ox: e.clientX,
            oy: e.clientY,
            px: e.clientX,
            py: e.clientY,
            moved: false,
            captured: false,
            id: e.pointerId,
          }
        }}
        onPointerMove={(e) => {
          const s = drag.current
          if (!s) return
          const ctm = svgRef.current?.getScreenCTM()
          if (!ctm) return
          if (
            !s.moved &&
            Math.abs(e.clientX - s.ox) + Math.abs(e.clientY - s.oy) > 4
          ) {
            s.moved = true
            e.currentTarget.setPointerCapture(s.id)
            s.captured = true
          }
          if (!s.moved) return
          const dx = (e.clientX - s.px) / ctm.a
          const dy = (e.clientY - s.py) / ctm.d
          s.px = e.clientX
          s.py = e.clientY
          setBox((c) => ({ ...c, x: c.x - dx, y: c.y - dy }))
        }}
        onPointerUp={(e) => {
          const s = drag.current
          if (s?.captured) e.currentTarget.releasePointerCapture(s.id)
          panned.current = s?.moved ?? false
          drag.current = null
        }}
        onClick={(e) => {
          // Click on empty canvas clears the selection; clicks on a node/edge
          // hit their own handlers (target !== the svg). A pan that ends here
          // is not a clear.
          if (e.target === e.currentTarget && !panned.current) setPicked(null)
        }}
      >
        <defs>
          <marker
            id="erd-arrow-all"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 8 4 L 0 8 z" fill="var(--muted-foreground)" />
          </marker>
          <marker
            id="erd-arrow-active"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="8"
            markerHeight="8"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 8 4 L 0 8 z" fill="var(--accent-brand)" />
          </marker>
        </defs>

        <g>
          {graph.edges.map((edge) => {
            const on = active?.edges.has(edge.id) ?? false
            const dim = active !== null && !on
            return (
              <g key={edge.id} opacity={dim ? 0.08 : 1}>
                <path
                  d={edge.path}
                  fill="none"
                  stroke={
                    on ? "var(--accent-brand)" : "var(--muted-foreground)"
                  }
                  strokeWidth={on ? 2 : 1}
                  strokeOpacity={on ? (dim ? 1 : 0.35) : 0.5}
                  markerEnd={`url(#${on ? "erd-arrow-active" : "erd-arrow-all"})`}
                />
                {/* Dots travelling along the highlighted relationship, in the
                    arrow's direction. Track above is dimmed so they read. */}
                {on && (
                  <path
                    className="erd-flow"
                    d={edge.path}
                    fill="none"
                    stroke="var(--accent-brand)"
                    strokeWidth={3}
                    pointerEvents="none"
                  />
                )}
                {/* Invisible fat stroke: a 1px line is impossible to click. */}
                <path
                  d={edge.path}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={14}
                  className="cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation()
                    if (!panned.current) toggle(edge.id)
                  }}
                />
                {on && (
                  <text
                    x={edge.midX}
                    y={edge.midY - 5}
                    textAnchor="middle"
                    fontSize={ROW_FONT}
                    fill="var(--foreground)"
                    style={{
                      fontFamily: "var(--font-mono)",
                      paintOrder: "stroke",
                    }}
                    stroke="var(--background)"
                    strokeWidth={4}
                  >
                    {edge.columns.join(", ")}
                  </text>
                )}
              </g>
            )
          })}
        </g>

        <g>
          {graph.nodes.map((node) => (
            <DiagramNode
              key={node.name}
              node={node}
              state={
                active === null
                  ? "normal"
                  : active.tables.has(node.name)
                    ? picked === node.name
                      ? "focus"
                      : "linked"
                    : "dim"
              }
              showRows={showRows}
              onPick={() => {
                if (!panned.current) toggle(node.name)
              }}
              onOpen={() => {
                if (panned.current) return
                onOpenTable(node.name)
              }}
            />
          ))}
        </g>
      </svg>
    </div>
  )
}

const DiagramNode = memo(function DiagramNode({
  node,
  state,
  showRows,
  onPick,
  onOpen,
}: {
  node: Node
  state: "normal" | "focus" | "linked" | "dim"
  showRows: boolean
  onPick: () => void
  onOpen: () => void
}) {
  const highlighted = state === "focus" || state === "linked"
  return (
    <g
      transform={`translate(${node.x} ${node.y})`}
      opacity={state === "dim" ? 0.12 : 1}
      role="button"
      tabIndex={0}
      // Single click highlights the table and its relationships; double click
      // (or Enter) opens it in the Schema tab.
      aria-label={`Table ${node.name} — click to highlight, double-click to open`}
      className="cursor-pointer outline-none"
      onClick={(e) => {
        e.stopPropagation()
        onPick()
      }}
      onDoubleClick={(e) => {
        e.stopPropagation()
        onOpen()
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault()
          onOpen()
        } else if (e.key === " ") {
          e.preventDefault()
          onPick()
        }
      }}
    >
      <rect
        width={node.w}
        height={node.h}
        fill={state === "focus" ? "var(--muted)" : "var(--card)"}
        stroke={highlighted ? "var(--accent-brand)" : "var(--muted-foreground)"}
        strokeWidth={state === "focus" ? 2 : highlighted ? 1.5 : 1}
        strokeOpacity={highlighted ? 1 : 0.45}
      />
      <text
        x={PAD_X}
        y={HEADER_H / 2 + 1}
        dominantBaseline="central"
        fontSize={HEADER_FONT}
        fontWeight={600}
        fill="var(--foreground)"
        style={{ fontFamily: "var(--font-mono)" }}
      >
        {ellipsis(node.name)}
      </text>
      {showRows && node.rows.length > 0 && (
        <>
          <line
            x1={0}
            y1={HEADER_H}
            x2={node.w}
            y2={HEADER_H}
            stroke="var(--muted-foreground)"
            strokeOpacity={0.4}
          />
          {node.rows.map((row, i) => (
            <g
              key={row.label}
              transform={`translate(0 ${HEADER_H + i * ROW_H})`}
            >
              <text
                x={PAD_X}
                y={ROW_H / 2 + 3}
                dominantBaseline="central"
                fontSize={ROW_FONT}
                fill="var(--foreground)"
                style={{ fontFamily: "var(--font-mono)" }}
              >
                {ellipsis(row.label)}
              </text>
              {row.tag && (
                <text
                  x={node.w - PAD_X}
                  y={ROW_H / 2 + 3}
                  textAnchor="end"
                  dominantBaseline="central"
                  fontSize={ROW_FONT - 1}
                  fill="var(--muted-foreground)"
                  style={{ fontFamily: "var(--font-mono)" }}
                >
                  {row.tag}
                </text>
              )}
            </g>
          ))}
        </>
      )}
    </g>
  )
})
