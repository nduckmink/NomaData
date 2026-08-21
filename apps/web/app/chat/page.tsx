"use client"

import { useCallback, useEffect, useState } from "react"
import Link from "next/link"
import {
  RiChat3Line,
  RiErrorWarningLine,
  RiForbid2Line,
} from "@remixicon/react"
import { toast } from "sonner"

import {
  type AgentStep,
  type AgentTurn,
  chatStream,
  type Conversation,
  type ConversationTurn,
  deleteConversation,
  getConversation,
  getSemanticOverview,
  listConversations,
  type QueryResult,
  type SemanticModelSummary,
  type TurnUsage,
} from "@/lib/api-client"
import {
  Conversation as ConversationView,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation"
import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtHeader,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought"
import { Loader } from "@/components/ai-elements/loader"
import { Message, MessageContent } from "@/components/ai-elements/message"
import {
  PromptInput,
  type PromptInputMessage,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/ai-elements/prompt-input"
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
import { PageContainer } from "@/components/page-header"
import { cn } from "@/lib/utils"
import { ConversationList } from "@/app/chat/conversation-list"

/** A single exchange on screen. `pending` is in flight; `failed` is an
 *  HTTP-level error (no AI provider, no published model); a `turn` is whatever
 *  the agent decided. */
type Exchange = {
  question: string
  status: "pending" | "done" | "failed"
  turn?: AgentTurn
  error?: string
  /** Steps received so far. While pending this is the only thing to show; once
   *  the turn lands its own copy is used, which is what a reopened thread has. */
  steps: AgentStep[]
}

const MAX_TABLE_ROWS = 50

/** A stored turn read back from a thread, shown the same way a live one is. */
function toExchange(turn: ConversationTurn): Exchange {
  return {
    question: turn.question,
    status: "done",
    steps: turn.steps,
    turn: {
      kind: turn.kind,
      question: turn.question,
      query: turn.query,
      result: turn.result,
      answer: turn.answer,
      explanation: turn.explanation,
      notes: turn.notes,
      // Stored turns keep one user-facing text column whatever the kind, so
      // a reopened clarification reads as it did when it was asked.
      clarification: turn.kind === "clarify" ? turn.answer : "",
      reason: turn.kind === "refuse" ? turn.answer : turn.error,
      conversation_id: "",
      ordinal: turn.ordinal,
      model_version: turn.model_version,
      usage: turn.usage,
      steps: turn.steps,
    },
  }
}

export default function ChatPage() {
  const [sources, setSources] = useState<SemanticModelSummary[]>([])
  const [source, setSource] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [exchanges, setExchanges] = useState<Exchange[]>([])
  const [asking, setAsking] = useState(false)
  // The thread this page is adding to. Null until the first question — the API
  // starts one and hands the id back, so nothing has to be created up front.
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [threadsLoading, setThreadsLoading] = useState(false)

  const live = sources.find((s) => s.source_id === source)

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

  const loadThreads = useCallback(
    async (name: string, signal?: AbortSignal) => {
      setThreadsLoading(true)
      try {
        const list = await listConversations(name, signal)
        if (!signal?.aborted) setConversations(list)
      } catch {
        // The app database may be down; asking still works without history.
        if (!signal?.aborted) setConversations([])
      } finally {
        if (!signal?.aborted) setThreadsLoading(false)
      }
    },
    []
  )

  // Switching source starts a fresh thread: a conversation belongs to the model
  // it was asked against, where its metric names mean what they meant.
  useEffect(() => {
    if (!source) return
    const controller = new AbortController()
    setExchanges([])
    setConversationId(null)
    void loadThreads(source, controller.signal)
    return () => controller.abort()
  }, [source, loadThreads])

  async function send(raw: string) {
    const question = raw.trim()
    if (!question || !source || asking) return
    setAsking(true)
    setExchanges((xs) => [...xs, { question, status: "pending", steps: [] }])
    const seen: AgentStep[] = []
    try {
      const turn = await chatStream(
        source,
        question,
        conversationId,
        (step) => {
          seen.push(step)
          setExchanges((xs) =>
            _replaceLast(xs, {
              question,
              status: "pending",
              steps: [...seen],
            })
          )
        }
      )
      setExchanges((xs) =>
        _replaceLast(xs, { question, status: "done", turn, steps: turn.steps })
      )
      if (turn.conversation_id) setConversationId(turn.conversation_id)
      void loadThreads(source)
    } catch (e) {
      setExchanges((xs) =>
        _replaceLast(xs, {
          question,
          status: "failed",
          steps: seen,
          error: e instanceof Error ? e.message : "Something went wrong.",
        })
      )
    } finally {
      setAsking(false)
    }
  }

  async function openThread(id: string) {
    if (!source || asking) return
    try {
      const thread = await getConversation(source, id)
      setConversationId(thread.id)
      setExchanges(thread.turns.map(toExchange))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not open that.")
    }
  }

  async function removeThread(id: string) {
    if (!source) return
    try {
      await deleteConversation(source, id)
      if (id === conversationId) {
        setConversationId(null)
        setExchanges([])
      }
      void loadThreads(source)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete that.")
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
    <PageContainer variant="bleed">
      <div className="flex h-full w-full min-w-0">
        <ConversationList
          conversations={conversations}
          activeId={conversationId}
          loading={threadsLoading}
          onOpen={(id) => void openThread(id)}
          onNew={() => {
            setConversationId(null)
            setExchanges([])
          }}
          onDelete={(id) => void removeThread(id)}
        />

        {/* Full width, not a centred column: an answer here is usually a table,
            and a 3xl column made wide results scroll sideways inside a page
            with empty space either side of them. */}
        <div className="flex h-full min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between gap-3 border-b px-4 py-2.5">
            <span className="text-sm font-medium">Chat</span>
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

          <ConversationView className="min-h-0 flex-1">
            {/* Messages keep a reading column even though the page is full
                width: a table wants the room, a sentence does not. */}
            <ConversationContent className="mx-auto w-full max-w-4xl gap-6">
              {exchanges.length === 0 && (
                <div className="flex flex-col items-center gap-2 pt-10 text-center">
                  <p className="text-sm text-muted-foreground">
                    Ask about {source} in plain language.
                  </p>
                </div>
              )}
              {exchanges.map((x, i) => (
                <ExchangeView
                  key={i}
                  exchange={x}
                  liveVersion={live?.published_version ?? null}
                />
              ))}
            </ConversationContent>
            <ConversationScrollButton />
          </ConversationView>

          <div className="mx-auto w-full max-w-4xl shrink-0 px-4 pb-4">
            <PromptInput
              onSubmit={(message: PromptInputMessage) =>
                void send(message.text)
              }
            >
              {/* Direct children of the group on purpose: it grows to fit a
                  textarea through `has-[>textarea]`, and stacks its footer
                  through `has-[>[data-align=block-end]]`. Wrapping these in
                  anything — even a `display:contents` element — leaves the
                  group one line tall with the textarea clipped out of it. */}
              <PromptInputTextarea
                placeholder={
                  exchanges.length > 0
                    ? "Ask a follow-up…"
                    : `Ask ${source} in plain language…`
                }
                disabled={asking}
              />
              <PromptInputFooter>
                <PromptInputTools>
                  <span className="px-1 text-xs text-muted-foreground">
                    Enter to send · Shift+Enter for a new line
                  </span>
                </PromptInputTools>
                <PromptInputSubmit
                  disabled={asking}
                  status={asking ? "submitted" : undefined}
                />
              </PromptInputFooter>
            </PromptInput>
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

function ExchangeView({
  exchange,
  liveVersion,
}: {
  exchange: Exchange
  liveVersion: number | null
}) {
  return (
    <div className="flex flex-col gap-4">
      <Message from="user">
        <MessageContent>{exchange.question}</MessageContent>
      </Message>

      <Message from="assistant">
        <MessageContent className="w-full">
          <StepTrail
            steps={exchange.steps}
            running={exchange.status === "pending"}
          />

          {exchange.status === "pending" && exchange.steps.length === 0 && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader size={16} />
              Thinking…
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
            <TurnView turn={exchange.turn} liveVersion={liveVersion} />
          )}
        </MessageContent>
      </Message>
    </div>
  )
}

/** What the agent did, live while it runs and folded away once it is done.
 *
 *  Open while running because the wait is the reason it exists; closed
 *  afterwards because by then the answer is the point, and the trail is there
 *  for the reader who wants to know how the number was reached. */
function StepTrail({
  steps,
  running,
}: {
  steps: AgentStep[]
  running: boolean
}) {
  const [opened, setOpened] = useState(false)
  if (steps.length === 0) return null
  const label = running
    ? steps[steps.length - 1].label
    : `Worked through ${steps.length} ${steps.length === 1 ? "step" : "steps"}`

  return (
    // Controlled the whole way: switching between controlled and uncontrolled
    // mid-life is a React warning, and forcing it open while running is the
    // point — the wait is what the trail is for.
    <ChainOfThought
      open={running || opened}
      onOpenChange={setOpened}
      className="max-w-full"
    >
      <ChainOfThoughtHeader>{label}</ChainOfThoughtHeader>
      <ChainOfThoughtContent>
        {steps.map((step, i) => (
          <ChainOfThoughtStep
            key={i}
            label={step.label}
            description={step.detail || undefined}
            status={running && i === steps.length - 1 ? "active" : "complete"}
          />
        ))}
      </ChainOfThoughtContent>
    </ChainOfThought>
  )
}

function TurnView({
  turn,
  liveVersion,
}: {
  turn: AgentTurn
  liveVersion: number | null
}) {
  if (turn.kind === "clarify") {
    return (
      <div className="flex flex-col gap-1.5">
        <Alert>
          <RiChat3Line />
          <AlertTitle>One thing first</AlertTitle>
          <AlertDescription>{turn.clarification}</AlertDescription>
        </Alert>
        <CostLine usage={turn.usage} />
      </div>
    )
  }
  if (turn.kind === "refuse") {
    return (
      <div className="flex flex-col gap-1.5">
        <div className="flex items-start gap-2 rounded-lg border border-dashed p-3 text-sm text-muted-foreground">
          <RiForbid2Line className="mt-0.5 size-4 shrink-0" />
          <span>{turn.reason}</span>
        </div>
        <CostLine usage={turn.usage} />
      </div>
    )
  }
  if (turn.kind === "error") {
    return (
      <div className="flex flex-col gap-1.5">
        <Alert variant="destructive">
          <RiErrorWarningLine />
          <AlertTitle>Couldn&apos;t answer</AlertTitle>
          <AlertDescription>{turn.reason}</AlertDescription>
        </Alert>
        <CostLine usage={turn.usage} />
      </div>
    )
  }

  // kind === "answer"
  const result = turn.result
  const scalar =
    result != null && result.rows.length === 1 && result.columns.length === 1

  return (
    <div className="flex w-full flex-col gap-3">
      {scalar && result ? (
        <div className="text-2xl font-semibold tracking-tight tnum">
          {formatValue(result.rows[0][result.columns[0].name])}
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

      <StaleModelNote
        answeredOn={turn.model_version}
        liveVersion={liveVersion}
      />

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

      <CostLine usage={turn.usage} />
    </div>
  )
}

/** An answer from an older model version cannot be reproduced against the one
 *  that is live now. Saying nothing lets the number stand as if it still held. */
function StaleModelNote({
  answeredOn,
  liveVersion,
}: {
  answeredOn: number | null
  liveVersion: number | null
}) {
  if (answeredOn == null || liveVersion == null || answeredOn >= liveVersion) {
    return null
  }
  return (
    <p className="text-xs text-amber-700 dark:text-amber-400">
      Answered on model v{answeredOn}; v{liveVersion} is live now. Ask again to
      get this number from the current model.
    </p>
  )
}

/** What the turn cost, small and always present. It is the only thing that
 *  stops the price of a question from growing unnoticed. */
function CostLine({ usage }: { usage?: TurnUsage }) {
  if (!usage || usage.llm_calls === 0) return null
  const seconds = (usage.latency_ms / 1000).toFixed(1)
  const tokens = usage.tokens_in + usage.tokens_out
  const parts = [
    `${usage.llm_calls} ${usage.llm_calls === 1 ? "call" : "calls"}`,
    `${seconds}s`,
  ]
  if (tokens > 0) parts.push(`${formatTokens(tokens)} tokens`)
  if (usage.tool_calls > 0) {
    parts.push(
      `${usage.tool_calls} ${usage.tool_calls === 1 ? "tool" : "tools"}`
    )
  }
  return (
    <p className="text-[11px] text-muted-foreground tnum">
      {parts.join(" · ")}
    </p>
  )
}

function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
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
