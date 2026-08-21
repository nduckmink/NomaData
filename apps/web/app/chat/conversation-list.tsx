"use client"

import { RiAddLine, RiChat3Line, RiDeleteBinLine } from "@remixicon/react"

import type { Conversation } from "@/lib/api-client"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

/** Past threads for one source, newest first.
 *
 *  A thread is worth reopening for a reason the chat window doesn't show: every
 *  turn in it kept the query and the model version behind its number, so an
 *  answer from last week can still be checked. */
export function ConversationList({
  conversations,
  activeId,
  loading,
  onOpen,
  onNew,
  onDelete,
}: {
  conversations: Conversation[]
  activeId: string | null
  loading: boolean
  onOpen: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
}) {
  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r md:flex">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
        <span className="text-sm font-medium">Conversations</span>
        <Button
          variant="ghost"
          size="icon"
          onClick={onNew}
          aria-label="New conversation"
          title="New conversation"
        >
          <RiAddLine />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loading && (
          <div className="flex flex-col gap-2 p-1">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        )}

        {!loading && conversations.length === 0 && (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">
            No conversations yet. Ask something and it will be kept here.
          </p>
        )}

        {!loading &&
          conversations.map((c) => (
            <div
              key={c.id}
              className={cn(
                "group flex items-center gap-1 rounded-sm px-2 py-1.5 hover:bg-wash",
                c.id === activeId && "bg-wash"
              )}
            >
              <button
                type="button"
                onClick={() => onOpen(c.id)}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
              >
                <RiChat3Line className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-sm">
                    {c.title || "Untitled"}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    {c.turn_count}{" "}
                    {c.turn_count === 1 ? "question" : "questions"}
                  </span>
                </span>
              </button>
              <Button
                variant="ghost"
                size="icon"
                className="size-6 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                onClick={() => onDelete(c.id)}
                aria-label={`Delete ${c.title || "conversation"}`}
              >
                <RiDeleteBinLine className="size-3.5" />
              </Button>
            </div>
          ))}
      </div>
    </aside>
  )
}
