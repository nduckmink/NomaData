"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import {
  RiChat3Line,
  RiErrorWarningLine,
  RiLoader4Line,
  RiSendPlane2Line,
} from "@remixicon/react"

import {
  type AgentTurn,
  ask,
  getSemanticOverview,
  type QueryResult,
  type SemanticModelSummary,
} from "@/lib/api-client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { PageContainer } from "@/components/page-header"
import { cn } from "@/lib/utils"

/** A single exchange the page tracks locally (no persistence yet — that's a
 *  later wave). `pending` is in flight; `failed` is an HTTP-level error (no AI
 *  provider, no published model); a `turn` is whatever the agent decided. */
type Exchange = {
  question: string
  status: "pending" | "done" | "failed"
  turn?: AgentTurn
  error?: string
}

const MAX_TABLE_ROWS = 50

export default function AskPage() {
  const [sources, setSources] = useState<SemanticModelSummary[]>([])
  const [source, setSource] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [exchanges, setExchanges] = useState<Exchange[]>([])
  const [input, setInput] = useState("")
  const [asking, setAsking] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Only sources with a published model can answer — the query layer reads what
  // was published, never a draft.
  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const overview = await getSemanticOverview(controller.signal)
        if (controller.signal.aborted) return
        const askable = overview.filter((r) => r.published_version != null)
        setSources(askable)
        setSource(askable[0]?.source_id ?? null)
      } catch {
        // Leave empty — the page shows the "nothing to ask yet" state.
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    })()
    return () => controller.abort()
  }, [])

  // Keep the newest exchange in view. Pure DOM, so no setState-in-effect.
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
  }, [exchanges])

  async function submit() {
    const question = input.trim()
    if (!question || !source || asking) return
    setInput("")
    setAsking(true)
    setExchanges((xs) => [...xs, { question, status: "pending" }])
    try {
      const turn = await ask(source, question)
      setExchanges((xs) => _replaceLast(xs, { question, status: "done", turn }))
    } catch (e) {
      setExchanges((xs) =>
        _replaceLast(xs, {
          question,
          status: "failed",
          error: e instanceof Error ? e.message : "Something went wrong.",
        })
      )
    } finally {
      setAsking(false)
    }
  }

  if (loading) {
    return (
      <PageContainer variant="fill">
        <div className="flex flex-col gap-3 p-6">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-24 w-full" />
        </div>
      </PageContainer>
    )
  }

  if (sources.length === 0) {
    return (
      <PageContainer variant="fill">
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <RiChat3Line className="size-8 text-muted-foreground" />
          <h2 className="text-sm font-medium">Nothing to ask yet</h2>
          <p className="max-w-sm text-sm text-balance text-muted-foreground">
            Publish a semantic model for a data source, then ask questions of it
            in plain language here.
          </p>
          <Button asChild variant="outline" size="sm">
            <Link href="/semantic">Go to Semantic Models</Link>
          </Button>
        </div>
      </PageContainer>
    )
  }

  return (
    <PageContainer variant="fill">
      <div className="mx-auto flex h-full w-full max-w-3xl flex-col">
        <div className="flex items-center justify-between gap-3 border-b px-4 py-2.5">
          <span className="text-sm font-medium">Ask</span>
          {sources.length > 1 ? (
            <Select
              value={source ?? ""}
              onValueChange={(v) => setSource(v)}
              disabled={asking}
            >
              <SelectTrigger
                className="w-56 font-mono"
                aria-label="Data source"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {sources.map((s) => (
                  <SelectItem key={s.source_id} value={s.source_id}>
                    {s.source_id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Badge variant="outline" className="font-mono">
              {source}
            </Badge>
          )}
        </div>

        <div ref={scrollRef} className="flex-1 space-y-6 overflow-y-auto p-4">
          {exchanges.length === 0 && (
            <p className="pt-10 text-center text-sm text-muted-foreground">
              Ask about {source} in plain language — e.g. a total this month,
              broken down by a category.
            </p>
          )}
          {exchanges.map((x, i) => (
            <ExchangeView key={i} exchange={x} />
          ))}
        </div>

        <div className="border-t p-3">
          <div className="flex items-end gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  void submit()
                }
              }}
              placeholder={`Ask ${source}…`}
              rows={1}
              className="max-h-40 min-h-9 flex-1 resize-none"
              disabled={asking}
            />
            <Button
              onClick={() => void submit()}
              disabled={asking || !input.trim()}
              size="icon"
              aria-label="Send"
            >
              {asking ? (
                <RiLoader4Line className="animate-spin" />
              ) : (
                <RiSendPlane2Line />
              )}
            </Button>
          </div>
        </div>
      </div>
    </PageContainer>
  )
}

function _replaceLast(xs: Exchange[], next: Exchange): Exchange[] {
  const copy = [...xs]
  copy[copy.length - 1] = next
  return copy
}

function ExchangeView({ exchange }: { exchange: Exchange }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="self-end rounded-lg bg-accent-brand/10 px-3 py-2 text-sm">
        {exchange.question}
      </div>

      {exchange.status === "pending" && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <RiLoader4Line className="size-4 animate-spin" />
          Reading the model, planning the query…
        </div>
      )}

      {exchange.status === "failed" && (
        <Alert variant="destructive">
          <RiErrorWarningLine />
          <AlertTitle>Couldn&apos;t answer</AlertTitle>
          <AlertDescription>{exchange.error}</AlertDescription>
        </Alert>
      )}

      {exchange.status === "done" && exchange.turn && (
        <TurnView turn={exchange.turn} />
      )}
    </div>
  )
}

function TurnView({ turn }: { turn: AgentTurn }) {
  if (turn.kind === "clarify") {
    return (
      <Alert>
        <RiChat3Line />
        <AlertTitle>One thing first</AlertTitle>
        <AlertDescription>{turn.clarification}</AlertDescription>
      </Alert>
    )
  }
  if (turn.kind === "refuse") {
    return <p className="text-sm text-muted-foreground">{turn.reason}</p>
  }
  if (turn.kind === "error") {
    return (
      <Alert variant="destructive">
        <RiErrorWarningLine />
        <AlertTitle>Couldn&apos;t answer</AlertTitle>
        <AlertDescription>{turn.reason}</AlertDescription>
      </Alert>
    )
  }

  // kind === "answer"
  const result = turn.result
  const scalar =
    result != null && result.rows.length === 1 && result.columns.length === 1

  return (
    <div className="flex flex-col gap-3 rounded-lg border p-3">
      {scalar && result ? (
        <div className="text-2xl font-semibold tracking-tight tnum">
          {formatValue(Object.values(result.rows[0])[0])}
        </div>
      ) : (
        result && <ResultTable result={result} />
      )}

      {/* The trust line — always shown, never collapsed. */}
      <p className="text-sm text-muted-foreground">{turn.explanation}</p>

      {turn.notes.map((note, i) => (
        <p key={i} className="text-xs text-amber-700 dark:text-amber-400">
          {note}
        </p>
      ))}

      {turn.query && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none hover:text-foreground">
            View query
          </summary>
          <pre className="mt-2 overflow-x-auto rounded-none border bg-wash p-2 font-mono">
            {JSON.stringify(turn.query, null, 2)}
          </pre>
        </details>
      )}
    </div>
  )
}

function ResultTable({ result }: { result: QueryResult }) {
  const rows = result.rows.slice(0, MAX_TABLE_ROWS)
  return (
    <div className="flex flex-col gap-1">
      <div className="overflow-x-auto rounded-none border">
        <Table>
          <TableHeader>
            <TableRow>
              {result.columns.map((c) => (
                <TableHead key={c.name}>{c.name}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row, i) => (
              <TableRow key={i}>
                {result.columns.map((c) => (
                  <TableCell
                    key={c.name}
                    className={cn(isNumeric(row[c.name]) && "text-right tnum")}
                  >
                    {formatValue(row[c.name])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {(result.truncated || result.row_count > rows.length) && (
        <p className="text-xs text-muted-foreground">
          Showing {rows.length} of {result.row_count}
          {result.truncated ? "+" : ""} rows.
        </p>
      )}
    </div>
  )
}

function isNumeric(v: unknown): boolean {
  return (
    typeof v === "number" ||
    (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v))
  )
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—"
  if (isNumeric(v)) return Number(v).toLocaleString()
  return String(v)
}
