import { cn } from "@/lib/utils"

/**
 * Page frame. `fill` keeps the page exactly one viewport tall so panes can
 * scroll independently (app-like); `scroll` lets the whole page scroll.
 * Filling only applies from `md` up — on a phone, stacked panes squeezed into
 * one viewport are worse than an ordinary scrolling page.
 *
 * `bleed` fills too, but drops the reading column and the padding: a page whose
 * own layout is the frame — a sidebar against a conversation — should meet the
 * window, not sit in the middle of it with its own margins.
 */
export function PageContainer({
  variant = "scroll",
  className,
  children,
}: {
  variant?: "scroll" | "fill" | "bleed"
  className?: string
  children: React.ReactNode
}) {
  const fills = variant === "fill" || variant === "bleed"
  return (
    <div
      className={cn("h-full overflow-y-auto", fills && "md:overflow-hidden")}
    >
      <div
        className={cn(
          variant === "bleed"
            ? "flex h-full w-full min-w-0 flex-col"
            : "mx-auto flex w-full max-w-6xl flex-col gap-5 p-4 md:p-6",
          fills && "md:h-full md:min-h-0",
          className
        )}
      >
        {children}
      </div>
    </div>
  )
}

/** The single `h1` of a page, plus its actions and optional stat row. */
export function PageHeader({
  title,
  description,
  actions,
  meta,
}: {
  title: string
  description?: string
  actions?: React.ReactNode
  meta?: React.ReactNode
}) {
  return (
    <header className="flex shrink-0 flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {description && (
            <p className="text-sm text-balance text-muted-foreground">
              {description}
            </p>
          )}
        </div>
        {actions && (
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        )}
      </div>
      {meta}
      <div aria-hidden className="h-px rule-fade" />
    </header>
  )
}
