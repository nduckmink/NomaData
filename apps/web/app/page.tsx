"use client"

import Link from "next/link"
import { useCallback, useEffect, useState } from "react"
import {
  RiArrowRightLine,
  RiBrainLine,
  RiCheckboxCircleLine,
  RiCloseCircleLine,
  RiDatabase2Line,
  RiRefreshLine,
  RiServerLine,
} from "@remixicon/react"

import { API_BASE_URL, getHealth, type HealthResponse } from "@/lib/api-client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { PageContainer, PageHeader } from "@/components/page-header"
import { cn } from "@/lib/utils"

type LoadState = "loading" | "ok" | "error"

export default function Page() {
  const [state, setState] = useState<LoadState>("loading")
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [checkedAt, setCheckedAt] = useState<string | null>(null)

  // Used by the Refresh button (an event handler, where setState is allowed).
  const load = useCallback(async () => {
    try {
      const data = await getHealth()
      setHealth(data)
      setState("ok")
    } catch {
      setState("error")
    } finally {
      setCheckedAt(new Date().toLocaleTimeString())
    }
  }, [])

  // Fetch once on mount. The setState calls live after `await` inside this
  // async IIFE, so nothing runs synchronously in the effect body.
  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const data = await getHealth(controller.signal)
        setHealth(data)
        setState("ok")
      } catch {
        if (!controller.signal.aborted) setState("error")
      } finally {
        if (!controller.signal.aborted) {
          setCheckedAt(new Date().toLocaleTimeString())
        }
      }
    })()
    return () => controller.abort()
  }, [])

  const sources = health?.data_sources ?? []
  const providers = health?.providers ?? []

  return (
    <PageContainer>
      <PageHeader
        title="Overview"
        description="What NomaData is connected to right now."
        actions={
          <>
            <span className="hidden text-xs text-muted-foreground sm:inline">
              {checkedAt ? `Checked ${checkedAt}` : "Checking…"}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setState("loading")
                void load()
              }}
              disabled={state === "loading"}
            >
              <RiRefreshLine data-icon="inline-start" />
              Refresh
            </Button>
          </>
        }
      />

      {state === "error" && (
        <Alert variant="destructive">
          <RiCloseCircleLine />
          <AlertTitle>API unreachable</AlertTitle>
          <AlertDescription>
            Could not reach the API at <code>{API_BASE_URL}</code>. Start it
            with <code>pnpm api:dev</code>.
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card className="bg-panel">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <RiServerLine className="size-4 text-muted-foreground" />
              API
            </CardTitle>
            <CardDescription>Backend service and environment.</CardDescription>
          </CardHeader>
          <CardContent>
            {state === "loading" ? (
              <SkeletonRows rows={3} />
            ) : (
              <dl className="flex flex-col divide-y divide-border">
                <Row
                  label="Status"
                  value={state === "ok" ? (health?.status ?? "ok") : "offline"}
                  tone={state === "ok" ? "positive" : "negative"}
                />
                <Row label="Version" value={health?.version ?? "—"} />
                <Row label="Environment" value={health?.env ?? "—"} />
              </dl>
            )}
          </CardContent>
        </Card>

        <Card className="bg-panel">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <RiDatabase2Line className="size-4 text-muted-foreground" />
              Data sources
            </CardTitle>
            <CardDescription>
              Databases registered and connected.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {state === "loading" ? (
              <SkeletonRows rows={2} />
            ) : sources.length > 0 ? (
              <>
                <ul className="flex flex-col divide-y divide-border">
                  {sources.map((name) => (
                    <li
                      key={name}
                      className="flex items-center justify-between gap-2 py-2"
                    >
                      <span className="truncate font-mono text-sm">{name}</span>
                      <RiCheckboxCircleLine
                        role="img"
                        aria-label="connected"
                        className="size-4 shrink-0 text-muted-foreground"
                      />
                    </li>
                  ))}
                </ul>
                <CardLink href="/schema">Explore schema</CardLink>
              </>
            ) : (
              <EmptyHint
                action={<CardLink href="/schema">Add a source</CardLink>}
              >
                No database connected yet.
              </EmptyHint>
            )}
          </CardContent>
        </Card>

        <Card className="bg-panel">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <RiBrainLine className="size-4 text-muted-foreground" />
              AI providers
            </CardTitle>
            <CardDescription>
              The reasoning engine behind questions.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {state === "loading" ? (
              <SkeletonRows rows={2} />
            ) : providers.length > 0 ? (
              <ul className="flex flex-col divide-y divide-border">
                {providers.map((name) => (
                  <li key={name} className="py-2 font-mono text-sm">
                    {name}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyHint action={<Badge variant="outline">Phase 3</Badge>}>
                None registered. Conversational queries arrive with the agent
                runtime.
              </EmptyHint>
            )}
          </CardContent>
        </Card>
      </div>

      <NextStep sources={sources.length} ready={state === "ok"} />
    </PageContainer>
  )
}

function Row({
  label,
  value,
  tone = "neutral",
}: {
  label: string
  value: string
  tone?: "neutral" | "positive" | "negative"
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "truncate font-mono text-sm",
          tone === "negative" && "text-destructive"
        )}
      >
        {value}
      </dd>
    </div>
  )
}

function CardLink({
  href,
  children,
}: {
  href: string
  children: React.ReactNode
}) {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-1.5 text-sm text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline"
    >
      {children}
      <RiArrowRightLine className="size-3.5" />
    </Link>
  )
}

function EmptyHint({
  children,
  action,
}: {
  children: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-start gap-2 py-1">
      <p className="text-sm text-balance text-muted-foreground">{children}</p>
      {action}
    </div>
  )
}

/** One clear next action, so the page answers "what do I do now?". */
function NextStep({ sources, ready }: { sources: number; ready: boolean }) {
  if (!ready) return null
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border bg-wash p-4">
      <div className="flex min-w-0 flex-col gap-1">
        <p className="text-sm font-medium">
          {sources === 0
            ? "Connect your first database"
            : "Next: define what your data means"}
        </p>
        <p className="text-sm text-balance text-muted-foreground">
          {sources === 0
            ? "NomaData introspects tables, columns and relationships as soon as a source is connected."
            : "Schema is discovered. The semantic model that turns it into business concepts is the next milestone."}
        </p>
      </div>
      <Button variant="outline" size="sm" asChild>
        <Link href="/schema">
          {sources === 0 ? "Add data source" : "Open schema"}
          <RiArrowRightLine data-icon="inline-end" />
        </Link>
      </Button>
    </div>
  )
}

function SkeletonRows({ rows }: { rows: number }) {
  return (
    <div className="flex flex-col divide-y divide-border">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center justify-between gap-4 py-2">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-16" />
        </div>
      ))}
    </div>
  )
}
