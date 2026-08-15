"use client"

import { RiAddLine, RiDeleteBinLine, RiEditLine } from "@remixicon/react"

import type { DataSourceInfo } from "@/lib/api-client"
import { DbLogo } from "@/components/icons/db-logo"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

import { DataSourceDialog, DeleteDataSourceDialog } from "./data-source-dialogs"

/**
 * The data source picker + its add/edit/delete actions, as a standalone list
 * rather than squeezed into the page header — the header overflowed once a
 * source had a long name or the viewport was narrow (the header previously
 * carried a ToggleGroup *and* two icon buttons *and* an Add button all in one
 * row). Edit/delete only appear on the selected row: those actions apply to
 * "the source I'm looking at", matching what the header buttons did before.
 */
export function DataSourceSidebar({
  loading,
  sources,
  selected,
  onSelect,
  onSaved,
  onDeleted,
}: {
  loading: boolean
  sources: DataSourceInfo[]
  selected: string | null
  onSelect: (name: string) => void
  onSaved: (name: string) => void
  onDeleted: () => void
}) {
  return (
    <aside className="flex min-h-0 flex-col gap-2">
      <div className="flex items-center justify-between gap-2 px-0.5">
        <h2 className="text-xs font-medium text-muted-foreground">
          Data sources
        </h2>
        <DataSourceDialog
          mode="create"
          onSaved={onSaved}
          trigger={
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label="Add data source"
              title="Add data source"
            >
              <RiAddLine />
            </Button>
          }
        />
      </div>

      <ScrollArea className="h-[26svh] border md:h-auto md:min-h-0 md:flex-1">
        {loading ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : sources.length === 0 ? (
          <p className="p-3 text-sm text-balance text-muted-foreground">
            No sources yet.
          </p>
        ) : (
          <ul className="flex flex-col">
            {sources.map((s) => {
              const active = s.name === selected
              return (
                <li
                  key={s.name}
                  className={cn(
                    "group/row flex items-center gap-1.5 border-b border-l-2 border-l-transparent py-1 pr-1.5 pl-2.5 transition-colors",
                    // Orange-tint family for both states — grey bg-accent is
                    // near-white on the light theme, so hover was invisible.
                    // Hover = faint tint, active = stronger tint + orange border.
                    active
                      ? "border-l-accent-brand bg-accent-brand/15"
                      : "hover:bg-accent-brand/8"
                  )}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(s.name)}
                    aria-current={active ? "true" : undefined}
                    title={s.name}
                    className="flex min-w-0 flex-1 items-center gap-2 py-1 text-left text-sm"
                  >
                    <span className="flex h-4 w-5 shrink-0 items-center justify-center">
                      <DbLogo
                        engine={s.kind}
                        monogram={s.kind.slice(0, 2).toUpperCase()}
                        className="h-4 max-w-5"
                      />
                    </span>
                    <span className="truncate font-mono">{s.name}</span>
                  </button>
                  {/* Revealed on hover / keyboard focus — no need to select the
                      row first. Kept in the DOM so hover works on any row. */}
                  <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100">
                    <DataSourceDialog
                      mode="edit"
                      name={s.name}
                      onSaved={onSaved}
                      trigger={
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={`Edit connection ${s.name}`}
                          title="Edit connection"
                        >
                          <RiEditLine />
                        </Button>
                      }
                    />
                    <DeleteDataSourceDialog
                      name={s.name}
                      onDeleted={onDeleted}
                      trigger={
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={`Remove connection ${s.name}`}
                          title="Remove connection"
                        >
                          <RiDeleteBinLine />
                        </Button>
                      }
                    />
                  </span>
                </li>
              )
            })}
          </ul>
        )}
      </ScrollArea>
    </aside>
  )
}
