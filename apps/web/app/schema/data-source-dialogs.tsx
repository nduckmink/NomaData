"use client"

import { type FormEvent, type ReactNode, useMemo, useState } from "react"
import {
  RiArrowLeftLine,
  RiEyeLine,
  RiEyeOffLine,
  RiSearchLine,
} from "@remixicon/react"
import { toast } from "sonner"

import {
  createDataSource,
  type DataSourceInput,
  type DataSourceKind,
  deleteDataSource,
  getDataSource,
  updateDataSource,
  verifyDataSource,
} from "@/lib/api-client"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Alert, AlertDescription } from "@/components/ui/alert"
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
import { DbLogo } from "@/components/icons/db-logo"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"

/**
 * Engine catalogue. Order and status mirror docs/CONNECTORS.md — engines with
 * no connector yet are shown but disabled, so the picker tells the truth about
 * what NomaData can reach today without pretending the list is complete.
 */
type Engine = {
  id: string
  name: string
  /** Fallback when no vendor logo is shipped for this engine. */
  monogram: string
  port: string
  aliases: string[]
  ready: boolean
}

const ENGINES: Engine[] = [
  {
    id: "mysql",
    name: "MySQL",
    monogram: "MY",
    port: "3306",
    aliases: ["oracle mysql"],
    ready: true,
  },
  {
    id: "sqlserver",
    name: "SQL Server",
    monogram: "MS",
    port: "1433",
    aliases: ["mssql", "microsoft", "azure sql"],
    ready: true,
  },
  {
    id: "postgresql",
    name: "PostgreSQL",
    monogram: "PG",
    port: "5432",
    aliases: ["postgres"],
    ready: false,
  },
  {
    id: "mariadb",
    name: "MariaDB",
    monogram: "MA",
    port: "3306",
    aliases: [],
    ready: false,
  },
  {
    id: "clickhouse",
    name: "ClickHouse",
    monogram: "CH",
    port: "8123",
    aliases: ["olap"],
    ready: false,
  },
  {
    id: "bigquery",
    name: "BigQuery",
    monogram: "BQ",
    port: "",
    aliases: ["google", "gcp"],
    ready: false,
  },
  {
    id: "snowflake",
    name: "Snowflake",
    monogram: "SF",
    port: "",
    aliases: ["warehouse"],
    ready: false,
  },
  {
    id: "redshift",
    name: "Redshift",
    monogram: "RS",
    port: "5439",
    aliases: ["aws", "amazon"],
    ready: false,
  },
  {
    id: "duckdb",
    name: "DuckDB",
    monogram: "DD",
    port: "",
    aliases: ["embedded", "parquet"],
    ready: false,
  },
  {
    id: "oracle",
    name: "Oracle",
    monogram: "OR",
    port: "1521",
    aliases: [],
    ready: false,
  },
  {
    id: "databricks",
    name: "Databricks SQL",
    monogram: "DB",
    port: "",
    aliases: ["spark", "lakehouse"],
    ready: false,
  },
  {
    id: "sqlite",
    name: "SQLite",
    monogram: "SL",
    port: "",
    aliases: ["file"],
    ready: false,
  },
]

function findEngine(id: string): Engine {
  return ENGINES.find((e) => e.id === id) ?? ENGINES[0]
}

/** `id` doubles as its own URI scheme (mysql://, sqlserver://) — see parseUri. */
function uriExample(engine: Engine): string {
  const port = engine.port || "port"
  return `${engine.id}://user:pass@host:${port}/database`
}

type FormState = {
  name: string
  host: string
  port: string
  database: string
  user: string
}

/** Compose a connection URI from the field values — the live mirror the user
 * edits below. Password is included so what they see is exactly what connects;
 * userinfo is omitted entirely when there's no user yet. */
function buildUri(kind: string, form: FormState, password: string): string {
  const enc = encodeURIComponent
  const auth = form.user
    ? password
      ? `${enc(form.user)}:${enc(password)}@`
      : `${enc(form.user)}@`
    : ""
  const host = form.host || "host"
  const port = form.port ? `:${form.port}` : ""
  return `${kind}://${auth}${host}${port}/${form.database}`
}

const BLANK: FormState = {
  name: "",
  host: "localhost",
  port: "3306",
  database: "",
  user: "",
}

/** Parse mysql://user:pass@host:port/db (or sqlserver://, mssql://). */
function parseUri(
  raw: string
): { kind: DataSourceKind; state: FormState; password: string } | null {
  try {
    const u = new URL(raw.trim())
    const proto = u.protocol.replace(/:$/, "").toLowerCase()
    const kind: DataSourceKind | null =
      proto === "mysql"
        ? "mysql"
        : proto === "sqlserver" || proto === "mssql"
          ? "sqlserver"
          : null
    if (!kind) return null
    return {
      kind,
      password: decodeURIComponent(u.password),
      state: {
        name: "",
        host: u.hostname,
        port: u.port || findEngine(kind).port,
        database: decodeURIComponent(u.pathname.replace(/^\//, "")),
        user: decodeURIComponent(u.username),
      },
    }
  } catch {
    return null
  }
}

export function DataSourceDialog({
  mode,
  name,
  trigger,
  onSaved,
}: {
  mode: "create" | "edit"
  name?: string
  trigger: ReactNode
  onSaved: (name: string) => void
}) {
  const [open, setOpen] = useState(false)
  // Pick the engine first (DBeaver-style), then fill in the connection.
  const [step, setStep] = useState<"engine" | "details">("engine")
  // URI and fields are two views of one connection, shown together and kept in
  // sync: editing a field rewrites the URI, editing the URI refills the fields.
  const [uri, setUri] = useState("")
  const [kind, setKind] = useState<DataSourceKind>("mysql")
  const [form, setForm] = useState<FormState>(BLANK)
  const [password, setPassword] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showPassword, setShowPassword] = useState(false)

  const isEdit = mode === "edit"
  const engine = findEngine(kind)

  function payload(): DataSourceInput {
    return {
      name: form.name.trim(),
      kind,
      host: form.host.trim(),
      port: Number(form.port),
      database: form.database.trim(),
      user: form.user.trim(),
      password,
    }
  }

  // Runs in the trigger's event handler, so setState here is fine.
  async function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) return
    setError(null)
    setPassword("")
    setShowPassword(false)
    if (isEdit && name) {
      setStep("details")
      try {
        const info = await getDataSource(name)
        const kind = info.kind as DataSourceKind
        const loaded: FormState = {
          name: info.name,
          host: info.host,
          port: String(info.port),
          database: info.database,
          user: info.user,
        }
        setKind(kind)
        setForm(loaded)
        setUri(buildUri(kind, loaded, ""))
      } catch {
        setError("Could not load this connection.")
      }
    } else {
      setStep("engine")
      setKind("mysql")
      setForm(BLANK)
      setUri("")
    }
  }

  function pickEngine(picked: Engine) {
    const kind = picked.id as DataSourceKind
    const next = { ...form, port: picked.port || form.port }
    setKind(kind)
    setForm(next)
    setUri(buildUri(kind, next, password))
    setError(null)
    setStep("details")
  }

  /** Field edited → rewrite the URI to match. */
  function edit<K extends keyof FormState>(key: K, value: string) {
    const next = { ...form, [key]: value }
    setForm(next)
    setUri(buildUri(kind, next, password))
  }

  function editPassword(value: string) {
    setPassword(value)
    setUri(buildUri(kind, form, value))
  }

  /** URI edited → parse and refill the fields (keeping our own `name`). A
   * scheme we don't support leaves the fields untouched, so nothing is lost
   * mid-type. */
  function editUri(value: string) {
    setUri(value)
    const parsed = parseUri(value)
    if (!parsed) return
    setKind(parsed.kind)
    setForm({ ...parsed.state, name: form.name })
    setPassword(parsed.password)
  }

  async function handleTest() {
    setTesting(true)
    setError(null)
    try {
      // A toast, not inline text — an inline result grew the form and shoved
      // every field upward. Matches the AI-provider test in Settings.
      const status = await verifyDataSource(payload())
      if (status.state === "ok") {
        const ms = status.latency_ms
          ? ` in ${Math.round(status.latency_ms)}ms`
          : ""
        toast.success(`Connected${ms}`)
      } else {
        toast.error(status.message ?? "Connection failed")
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Test failed")
    } finally {
      setTesting(false)
    }
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (step !== "details") return
    setSubmitting(true)
    setError(null)
    try {
      const info = isEdit
        ? await updateDataSource(name ?? form.name, payload())
        : await createDataSource(payload())
      setOpen(false)
      toast.success(
        isEdit ? `Reconnected ${info.name}` : `Connected ${info.name}`,
        {
          description: `${info.kind} · ${info.host}:${info.port}/${info.database}`,
        }
      )
      onSaved(info.name)
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to save data source"
      setError(message)
      toast.error(isEdit ? "Could not save changes" : "Could not add source", {
        description: message,
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => void handleOpenChange(o)}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {/* The engine grid needs more room than the default dialog width. */}
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>
              {step === "engine"
                ? "Select a database engine"
                : isEdit
                  ? "Edit data source"
                  : "Connection details"}
            </DialogTitle>
            <DialogDescription>
              {step === "engine"
                ? "Pick the engine to connect to. Filter by name."
                : isEdit
                  ? "Changes reconnect the source immediately."
                  : "It is stored and connected immediately."}
            </DialogDescription>
          </DialogHeader>

          {error && (
            <Alert variant="destructive" className="mt-4">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {step === "engine" ? (
            <EnginePicker onPick={pickEngine} />
          ) : (
            <>
              <div className="mt-4 flex items-center justify-between gap-2 border p-2">
                <span className="flex min-w-0 items-center gap-2.5">
                  <DbLogo
                    engine={engine.id}
                    monogram={engine.monogram}
                    className="h-5 max-w-24 shrink-0"
                  />
                  <span className="truncate text-sm font-medium">
                    {engine.name}
                  </span>
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => setStep("engine")}
                >
                  <RiArrowLeftLine data-icon="inline-start" />
                  Change
                </Button>
              </div>

              {/* Name first (our own label), then the connection itself. The
                  URI is the live composite of the fields below it, and editing
                  it refills them. */}
              <FieldGroup className="mt-4">
                <Field>
                  <FieldLabel htmlFor="ds-name">Name</FieldLabel>
                  <Input
                    id="ds-name"
                    value={form.name}
                    onChange={(e) => edit("name", e.target.value)}
                    placeholder={`prod_${engine.id}`}
                    required
                    disabled={isEdit}
                    autoFocus={!isEdit}
                    className="font-mono"
                    aria-describedby={isEdit ? "ds-name-help" : undefined}
                  />
                  {isEdit ? (
                    <p
                      id="ds-name-help"
                      className="text-xs text-muted-foreground"
                    >
                      The name identifies this source across NomaData and cannot
                      be changed. Remove and re-add to rename.
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      A label for this connection inside NomaData — not part of
                      the URI.
                    </p>
                  )}
                </Field>

                <div
                  aria-hidden
                  className="rule-fade my-1 h-px"
                  role="separator"
                />

                <Field>
                  <FieldLabel htmlFor="ds-uri">Connection URI</FieldLabel>
                  <Input
                    id="ds-uri"
                    value={uri}
                    onChange={(e) => editUri(e.target.value)}
                    placeholder={uriExample(engine)}
                    autoComplete="off"
                    spellCheck={false}
                    className="font-mono"
                  />
                  <p className="text-xs text-muted-foreground">
                    Paste a full URI, or fill the fields below — each mirrors the
                    other.
                  </p>
                </Field>
                <div className="flex gap-3">
                  <Field className="flex-1">
                    <FieldLabel htmlFor="ds-host">Host</FieldLabel>
                    <Input
                      id="ds-host"
                      value={form.host}
                      onChange={(e) => edit("host", e.target.value)}
                      required
                      className="font-mono"
                    />
                  </Field>
                  <Field className="w-24">
                    <FieldLabel htmlFor="ds-port">Port</FieldLabel>
                    <Input
                      id="ds-port"
                      inputMode="numeric"
                      value={form.port}
                      onChange={(e) => edit("port", e.target.value)}
                      required
                      className="font-mono tnum"
                    />
                  </Field>
                </div>
                <Field>
                  <FieldLabel htmlFor="ds-database">Database</FieldLabel>
                  <Input
                    id="ds-database"
                    value={form.database}
                    onChange={(e) => edit("database", e.target.value)}
                    required
                    className="font-mono"
                  />
                </Field>
                <div className="flex gap-3">
                  <Field className="flex-1">
                    <FieldLabel htmlFor="ds-user">User</FieldLabel>
                    <Input
                      id="ds-user"
                      value={form.user}
                      onChange={(e) => edit("user", e.target.value)}
                      autoComplete="off"
                      className="font-mono"
                    />
                  </Field>
                  <Field className="flex-1">
                    <FieldLabel htmlFor="ds-password">Password</FieldLabel>
                    <div className="relative">
                      <Input
                        id="ds-password"
                        type={showPassword ? "text" : "password"}
                        value={password}
                        onChange={(e) => editPassword(e.target.value)}
                        placeholder={isEdit ? "unchanged" : undefined}
                        autoComplete="off"
                        className="pr-8 font-mono"
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-xs"
                        aria-label={
                          showPassword ? "Hide password" : "Show password"
                        }
                        aria-pressed={showPassword}
                        onClick={() => setShowPassword((v) => !v)}
                        className="absolute top-1/2 right-1 -translate-y-1/2 text-muted-foreground"
                      >
                        {showPassword ? <RiEyeOffLine /> : <RiEyeLine />}
                      </Button>
                    </div>
                  </Field>
                </div>
              </FieldGroup>

              <DialogFooter className="mt-6 gap-2 sm:justify-between">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void handleTest()}
                  disabled={testing}
                >
                  {testing ? "Testing…" : "Test connection"}
                </Button>
                <Button type="submit" disabled={submitting}>
                  {submitting
                    ? isEdit
                      ? "Saving…"
                      : "Connecting…"
                    : isEdit
                      ? "Save changes"
                      : "Add source"}
                </Button>
              </DialogFooter>
            </>
          )}
        </form>
      </DialogContent>
    </Dialog>
  )
}

function EnginePicker({ onPick }: { onPick: (engine: Engine) => void }) {
  const [filter, setFilter] = useState("")

  const matches = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return ENGINES
    return ENGINES.filter((e) =>
      [e.name, e.id, ...e.aliases].some((t) => t.toLowerCase().includes(q))
    )
  }, [filter])

  const firstReady = matches.find((e) => e.ready)

  return (
    <div className="mt-4 flex flex-col gap-3">
      <div className="relative">
        <RiSearchLine
          aria-hidden
          className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          autoFocus
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => {
            // Enter picks the first available match instead of submitting the
            // half-empty form behind this step.
            if (e.key === "Enter") {
              e.preventDefault()
              if (firstReady) onPick(firstReady)
            }
          }}
          placeholder="Type part of a database name to filter…"
          aria-label="Filter database engines"
          className="pl-8"
        />
      </div>

      <ScrollArea className="h-64 border">
        {matches.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">
            No engine matches “{filter}”.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-2 p-2 sm:grid-cols-3">
            {matches.map((engine) => (
              <button
                key={engine.id}
                type="button"
                disabled={!engine.ready}
                onClick={() => onPick(engine)}
                title={
                  engine.ready
                    ? `Connect to ${engine.name}`
                    : `${engine.name} isn't supported yet`
                }
                className={cn(
                  "flex flex-col items-center gap-2 border p-3 text-center transition-colors",
                  engine.ready
                    ? "hover:border-foreground hover:bg-accent"
                    : "cursor-not-allowed opacity-45"
                )}
              >
                <span className="flex h-9 w-full items-center justify-center">
                  {/* Height-normalised, width-capped: without the cap a
                      wordmark like Oracle swallows the whole tile. */}
                  <DbLogo
                    engine={engine.id}
                    monogram={engine.monogram}
                    className="h-8 max-w-[6rem]"
                  />
                </span>
                <span className="w-full truncate text-xs font-medium">
                  {engine.name}
                </span>
                {!engine.ready && (
                  <span className="text-[0.6875rem] text-muted-foreground">
                    soon
                  </span>
                )}
              </button>
            ))}
          </div>
        )}
      </ScrollArea>
    </div>
  )
}

export function DeleteDataSourceDialog({
  name,
  trigger,
  onDeleted,
}: {
  name: string
  trigger: ReactNode
  onDeleted: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleConfirm() {
    setBusy(true)
    setError(null)
    try {
      await deleteDataSource(name)
      toast.success(`Removed ${name}`, {
        description: "The connection is gone; the database is untouched.",
      })
      onDeleted()
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to delete"
      setError(message)
      toast.error(`Could not remove ${name}`, { description: message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Remove <span className="font-mono">{name}</span>?
          </AlertDialogTitle>
          <AlertDialogDescription>
            This removes the connection from NomaData. The database itself is
            not affected.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={(e) => {
              e.preventDefault()
              void handleConfirm()
            }}
            disabled={busy}
          >
            {busy ? "Removing…" : "Remove"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
