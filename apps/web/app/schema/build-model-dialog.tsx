"use client"

/**
 * The step before a model gets built.
 *
 * Business context used to be an optional side panel almost nobody opened, and
 * the result was a model full of generic English guesses. It is now part of the
 * build itself: you say what the business does, then the model is generated.
 * Thirty seconds of typing here changes every name the AI proposes.
 *
 * The same dialog doubles as the plain editor (`mode="edit"`) so the context can
 * be revised later without rebuilding — it also feeds the per-metric prompt.
 */

import { useEffect, useState } from "react"
import {
  RiLoader4Line,
  RiSparkling2Line,
  RiTranslate2,
} from "@remixicon/react"
import { toast } from "sonner"

import {
  type BusinessContext,
  type DatabaseCatalog,
  getBusinessContext,
  getSchema,
  saveBusinessContext,
} from "@/lib/api-client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

import { defaultSelection, rankTables, TablePicker } from "./table-picker"

const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "vi", label: "Vietnamese" },
]

/** The browser's own zone is the best first guess: someone setting up a
 *  database usually sits in the same place as the data. */
const LOCAL_TIMEZONE =
  typeof Intl !== "undefined"
    ? (Intl.DateTimeFormat().resolvedOptions().timeZone ?? "UTC")
    : "UTC"

const EMPTY: BusinessContext = {
  source_id: "",
  domain: "",
  glossary: "",
  conventions: "",
  language: "en",
  instructions: "",
  timezone: LOCAL_TIMEZONE,
}

type Mode = "generate" | "rebuild" | "edit"

const COPY: Record<Mode, { title: string; description: string; submit: string }> = {
  generate: {
    title: "Tell us about this data, then we'll build the model",
    description:
      "The AI can reads your table names, but not your business.",
    submit: "Save & build model",
  },
  rebuild: {
    title: "Rebuild the model",
    description:
      "New tables and columns are added. Anything you edited or locked is kept, and entities whose table no longer exists are removed.",
    submit: "Save & rebuild",
  },
  edit: {
    title: "What should the AI know about this data?",
    description:
      "Written once per data source and used in every suggestion, including the per-metric prompts.",
    submit: "Save",
  },
}

export function BuildModelDialog({
  source,
  mode,
  aiConfigured,
  disabled,
  scope,
  onBuild,
  children,
}: {
  source: string
  mode: Mode
  aiConfigured: boolean
  disabled?: boolean
  /** Tables the current model covers, so a rebuild starts from that choice. */
  scope?: string[]
  /** Called after the context is saved, with the tables to build over. */
  onBuild?: (tables: string[]) => void
  /** The trigger button. */
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [context, setContext] = useState<BusinessContext>(EMPTY)
  const [saving, setSaving] = useState(false)
  const [showError, setShowError] = useState(false)
  const [catalog, setCatalog] = useState<DatabaseCatalog | null>(null)
  const [tables, setTables] = useState<Set<string>>(new Set())

  const copy = COPY[mode]
  // Context only matters if there is a model to prompt. With no AI provider the
  // build is purely structural, so requiring it would be busywork.
  const domainRequired = aiConfigured && mode !== "edit"
  const domainMissing = context.domain.trim() === ""

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    void (async () => {
      try {
        const loaded = await getBusinessContext(source, controller.signal)
        if (!controller.signal.aborted) {
          setContext({ ...loaded, timezone: loaded.timezone || LOCAL_TIMEZONE })
        }
      } catch {
        // A missing context is an empty form, not an error.
      }
    })()
    return () => controller.abort()
  }, [open, source])

  // The scope step needs the real schema. Loaded only when the dialog opens,
  // and only for a build — the edit mode has nothing to scope.
  useEffect(() => {
    if (!open || mode === "edit") return
    const controller = new AbortController()
    void (async () => {
      try {
        const loaded = await getSchema(source, controller.signal)
        if (controller.signal.aborted) return
        setCatalog(loaded)
        setTables(
          new Set(
            scope?.length ? scope : defaultSelection(rankTables(loaded))
          )
        )
      } catch {
        // Without the schema the picker is skipped and the build covers
        // everything, which is what it did before this step existed.
      }
    })()
    return () => controller.abort()
  }, [open, mode, source, scope])

  const set = (patch: Partial<BusinessContext>) =>
    setContext((c) => ({ ...c, ...patch }))

  const submit = () =>
    void (async () => {
      if (domainRequired && domainMissing) {
        setShowError(true)
        return
      }
      setSaving(true)
      try {
        await saveBusinessContext(source, { ...context, source_id: source })
        setOpen(false)
        if (mode === "edit") toast.success("Business context saved")
        else onBuild?.([...tables])
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Could not save")
      } finally {
        setSaving(false)
      }
    })()

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Clear the "required" warning on reopen — it belongs to the last
        // attempt, not to a fresh one.
        if (next) setShowError(false)
        setOpen(next)
      }}
    >
      <DialogTrigger asChild disabled={disabled}>
        {children}
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{copy.title}</DialogTitle>
          <DialogDescription>{copy.description}</DialogDescription>
        </DialogHeader>

        <div className="flex max-h-[60vh] flex-col gap-4 overflow-y-auto pr-1">
          {!aiConfigured && mode !== "edit" && (
            <p className="border border-amber-500/40 bg-amber-500/5 p-2 text-xs">
              No AI provider is configured, so this build only reads the schema.
              Anything you write here is saved for later.
            </p>
          )}

          {mode !== "edit" && catalog && (
            <Field
              label="Which tables should the model cover?"
              hint="Everything else in the database is ignored. Most schemas have a
                handful of tables anyone measures and a long tail of lookups."
            >
              <TablePicker
                catalog={catalog}
                selected={tables}
                onChange={setTables}
              />
            </Field>
          )}

          <Field
            label="What does this business do?"
            required={domainRequired}
            error={showError && domainMissing ? "Please add one sentence." : undefined}
          >
            <Textarea
              rows={2}
              value={context.domain}
              onChange={(e) => set({ domain: e.target.value })}
              placeholder="A school management platform; our customers are private schools."
              aria-invalid={showError && domainMissing}
              className={cn(
                showError && domainMissing && "border-destructive"
              )}
            />
          </Field>

          <Field
            label="Words only your team would know"
            hint="Abbreviations, internal codes, anything a new hire would have to ask about."
          >
            <Textarea
              rows={3}
              value={context.glossary}
              onChange={(e) => set({ glossary: e.target.value })}
              placeholder={"dot = payment round\nhs = student\ntk = account"}
            />
          </Field>

          <Field
            label="Anything odd about how tables are named?"
            hint="Optional."
          >
            <Textarea
              rows={2}
              value={context.conventions}
              onChange={(e) => set({ conventions: e.target.value })}
              placeholder="Tables starting with commerce_ are the core business; ignore anything ending in _tmp."
            />
          </Field>

          <Field
            label="Language for the names it writes"
            hint="Entity names, metric names and descriptions are written in this language. The app stays in English."
          >
            <select
              value={context.language}
              onChange={(e) => set({ language: e.target.value })}
              aria-label="Output language"
              className="h-9 w-48 rounded-md border border-border/50 bg-transparent px-2 text-sm outline-none hover:border-border"
            >
              {LANGUAGES.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label="Time zone of this data"
            hint={`Decides what "this month" means. Detected: ${LOCAL_TIMEZONE}.`}
          >
            <Input
              value={context.timezone}
              onChange={(e) => set({ timezone: e.target.value })}
              placeholder={LOCAL_TIMEZONE}
              aria-label="Time zone"
              className="w-64 font-mono text-sm"
            />
          </Field>

          <Field
            label="Anything you want it to focus on?"
          >
            <Input
              value={context.instructions}
              onChange={(e) => set({ instructions: e.target.value })}
              placeholder="Focus on revenue and outstanding payments."
            />
          </Field>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving}>
            {saving ? (
              <RiLoader4Line data-icon="inline-start" className="animate-spin" />
            ) : mode === "edit" ? (
              <RiTranslate2 data-icon="inline-start" />
            ) : (
              <RiSparkling2Line data-icon="inline-start" />
            )}
            {copy.submit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Field({
  label,
  hint,
  required,
  error,
  children,
}: {
  label: string
  hint?: string
  required?: boolean
  error?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="flex items-center gap-1.5 text-sm font-medium">
        {label}
        {required && <span className="text-xs text-muted-foreground">required</span>}
      </span>
      {children}
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : (
        hint && <p className="text-xs text-muted-foreground">{hint}</p>
      )}
    </div>
  )
}
