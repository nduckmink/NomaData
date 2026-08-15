"use client"

import { useCallback, useEffect, useState } from "react"
import {
  type RemixiconComponentType,
  RiBrainLine,
  RiCheckboxCircleFill,
  RiCloseCircleLine,
  RiDatabase2Line,
  RiPulseLine,
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
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
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

  return (
    <main className="flex min-h-svh items-center justify-center bg-background p-6">
      <div className="flex w-full max-w-lg flex-col gap-6">
        <header className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-2xl font-semibold tracking-tight">
              NomaData
            </span>
            <Badge variant="outline">v{health?.version ?? "0.0.1"}</Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            Know My Data. — Foundation skeleton (M0)
          </p>
        </header>

        <Card>
          <CardHeader>
            <div className="flex items-start justify-between gap-4">
              <div className="flex flex-col gap-1.5">
                <CardTitle>System Status</CardTitle>
                <CardDescription>
                  Live check of the NomaData API and its registries.
                </CardDescription>
              </div>
              <StatusBadge state={state} status={health?.status} />
            </div>
          </CardHeader>

          <CardContent className="flex flex-col gap-3">
            {state === "loading" && <LoadingRows />}

            {state === "error" && (
              <Alert variant="destructive">
                <RiCloseCircleLine />
                <AlertTitle>API unreachable</AlertTitle>
                <AlertDescription>
                  Could not reach the API at {API_BASE_URL}. Start it with{" "}
                  <code className="font-mono">pnpm api:dev</code>.
                </AlertDescription>
              </Alert>
            )}

            {state === "ok" && health && (
              <div className="flex flex-col divide-y divide-border">
                <StatusRow
                  icon={RiServerLine}
                  label="API"
                  value={health.checks.api ?? "unknown"}
                />
                <StatusRow
                  icon={RiPulseLine}
                  label="Environment"
                  value={health.env}
                />
                <StatusRow
                  icon={RiBrainLine}
                  label="AI providers"
                  value={
                    health.providers.length
                      ? health.providers.join(", ")
                      : "none registered (M3)"
                  }
                  muted={health.providers.length === 0}
                />
                <StatusRow
                  icon={RiDatabase2Line}
                  label="Data sources"
                  value={
                    health.data_sources.length
                      ? health.data_sources.join(", ")
                      : "none registered (M1)"
                  }
                  muted={health.data_sources.length === 0}
                />
              </div>
            )}
          </CardContent>

          <CardFooter className="flex items-center justify-between gap-4">
            <span className="text-xs text-muted-foreground">
              {checkedAt ? `Last checked ${checkedAt}` : "Checking…"}
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
          </CardFooter>
        </Card>

        <p className="text-center font-mono text-xs text-muted-foreground">
          {API_BASE_URL}
        </p>
      </div>
    </main>
  )
}

function StatusBadge({ state, status }: { state: LoadState; status?: string }) {
  if (state === "loading") return <Badge variant="secondary">Checking…</Badge>
  if (state === "error") return <Badge variant="destructive">Unreachable</Badge>
  return (
    <Badge>
      <RiCheckboxCircleFill data-icon="inline-start" />
      {status === "ok" ? "Operational" : (status ?? "Unknown")}
    </Badge>
  )
}

function StatusRow({
  icon: Icon,
  label,
  value,
  muted = false,
}: {
  icon: RemixiconComponentType
  label: string
  value: string
  muted?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Icon className="size-4 text-muted-foreground" />
        {label}
      </div>
      <span
        className={cn("font-mono text-sm", muted && "text-muted-foreground")}
      >
        {value}
      </span>
    </div>
  )
}

function LoadingRows() {
  return (
    <div className="flex flex-col gap-3">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex items-center justify-between gap-4 py-2.5">
          <Skeleton className="h-4 w-28" />
          <Skeleton className="h-4 w-20" />
        </div>
      ))}
    </div>
  )
}
