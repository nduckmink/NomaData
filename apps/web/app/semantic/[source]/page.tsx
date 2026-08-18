"use client"

/**
 * Full-width semantic editor for one data source.
 *
 * The model editor used to live as a cramped tab inside the schema browser,
 * where it shared the page with table listings nobody needs while modelling.
 * Opening a source from the Semantic Models index lands here instead: the whole
 * width, one job — read and shape the model.
 */

import { use, useEffect, useState } from "react"
import Link from "next/link"
import { RiArrowLeftLine } from "@remixicon/react"

import { getDataSource } from "@/lib/api-client"
import { SemanticPanel } from "@/app/schema/semantic-panel"
import { DbLogo } from "@/components/icons/db-logo"
import { PageContainer } from "@/components/page-header"

export default function SemanticSourcePage({
  params,
}: {
  params: Promise<{ source: string }>
}) {
  const { source } = use(params)
  const name = decodeURIComponent(source)
  const [kind, setKind] = useState<string | null>(null)

  // The engine, only to show its logo. A failure just hides the logo.
  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const info = await getDataSource(name)
        if (!controller.signal.aborted) setKind(info.kind)
      } catch {
        // No logo, no problem.
      }
    })()
    return () => controller.abort()
  }, [name])

  return (
    <PageContainer variant="fill" className="max-w-none">
      <header className="flex shrink-0 flex-col gap-1">
        <Link
          href="/semantic"
          className="flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <RiArrowLeftLine className="size-3.5" />
          All semantic models
        </Link>
        <div className="flex items-center gap-2.5">
          <span className="flex w-8 shrink-0 justify-center">
            {kind && (
              <DbLogo
                engine={kind}
                monogram={name.slice(0, 2).toUpperCase()}
                className="h-7 w-auto"
              />
            )}
          </span>
          <div className="flex flex-wrap items-baseline gap-2">
            <h1 className="font-mono text-xl font-semibold tracking-tight">
              {name}
            </h1>
            <span className="text-sm text-muted-foreground">Semantic model</span>
          </div>
        </div>
      </header>

      {/* Keyed by source so a different source remounts clean. */}
      <div className="flex min-h-0 flex-1 flex-col">
        <SemanticPanel key={name} source={name} />
      </div>
    </PageContainer>
  )
}
