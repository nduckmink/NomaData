"use client"

import { type FormEvent, type ReactNode, useState } from "react"

import {
  type ConnectionStatus,
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
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { cn } from "@/lib/utils"

const DEFAULT_PORT: Record<DataSourceKind, string> = {
  mysql: "3306",
  sqlserver: "1433",
}

type FormState = {
  name: string
  host: string
  port: string
  database: string
  user: string
}

const BLANK: FormState = {
  name: "",
  host: "localhost",
  port: DEFAULT_PORT.mysql,
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
        port: u.port || DEFAULT_PORT[kind],
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
  const [view, setView] = useState<"fields" | "uri">("fields")
  const [uri, setUri] = useState("")
  const [kind, setKind] = useState<DataSourceKind>("mysql")
  const [form, setForm] = useState<FormState>(BLANK)
  const [password, setPassword] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<ConnectionStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  const isEdit = mode === "edit"

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
    setTestResult(null)
    setPassword("")
    setUri("")
    setView("fields")
    if (isEdit && name) {
      try {
        const info = await getDataSource(name)
        setKind(info.kind as DataSourceKind)
        setForm({
          name: info.name,
          host: info.host,
          port: String(info.port),
          database: info.database,
          user: info.user,
        })
      } catch {
        setError("Could not load this connection.")
      }
    } else {
      setKind("mysql")
      setForm(BLANK)
    }
  }

  function applyUri() {
    const parsed = parseUri(uri)
    if (!parsed) {
      setError(
        "Could not parse the URI. Example: mysql://user:pass@host:3307/db"
      )
      return
    }
    setKind(parsed.kind)
    setForm({ ...parsed.state, name: form.name })
    setPassword(parsed.password)
    setTestResult(null)
    setError(null)
    setView("fields")
  }

  function edit<K extends keyof FormState>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
    setTestResult(null)
  }

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    setError(null)
    try {
      setTestResult(await verifyDataSource(payload()))
    } catch (err) {
      setTestResult({
        state: "error",
        message: err instanceof Error ? err.message : "Test failed",
      })
    } finally {
      setTesting(false)
    }
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const info = isEdit
        ? await updateDataSource(name ?? form.name, payload())
        : await createDataSource(payload())
      setOpen(false)
      onSaved(info.name)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to save data source"
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => void handleOpenChange(o)}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>
              {isEdit ? "Edit data source" : "Add data source"}
            </DialogTitle>
            <DialogDescription>
              {isEdit
                ? "Changes reconnect the source immediately."
                : "Connect a database. It is stored and connected immediately."}
            </DialogDescription>
          </DialogHeader>

          <div className="mt-4 flex justify-center">
            <ToggleGroup
              type="single"
              value={view}
              onValueChange={(v) => v && setView(v as "fields" | "uri")}
            >
              <ToggleGroupItem value="fields">Fields</ToggleGroupItem>
              <ToggleGroupItem value="uri">Connection URI</ToggleGroupItem>
            </ToggleGroup>
          </div>

          {error && (
            <Alert variant="destructive" className="mt-4">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {view === "uri" ? (
            <FieldGroup className="mt-4">
              <Field>
                <FieldLabel htmlFor="ds-uri">Connection URI</FieldLabel>
                <Input
                  id="ds-uri"
                  value={uri}
                  onChange={(e) => setUri(e.target.value)}
                  placeholder="mysql://user:pass@host:3307/database"
                  className="font-mono"
                />
              </Field>
              <p className="text-xs text-muted-foreground">
                Schemes: <span className="font-mono">mysql://</span>,{" "}
                <span className="font-mono">sqlserver://</span>. URL-encode
                special characters in the password. Click below to fill the
                fields for review.
              </p>
              <Button type="button" variant="outline" onClick={applyUri}>
                Fill fields from URI
              </Button>
            </FieldGroup>
          ) : (
            <FieldGroup className="mt-4">
              <Field>
                <FieldLabel htmlFor="ds-name">Name</FieldLabel>
                <Input
                  id="ds-name"
                  value={form.name}
                  onChange={(e) => edit("name", e.target.value)}
                  placeholder="prod_mysql"
                  required
                  disabled={isEdit}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="ds-kind">Engine</FieldLabel>
                <Select
                  value={kind}
                  onValueChange={(v) => {
                    const k = v as DataSourceKind
                    setKind(k)
                    setForm((f) => ({ ...f, port: DEFAULT_PORT[k] }))
                    setTestResult(null)
                  }}
                >
                  <SelectTrigger id="ds-kind">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value="mysql">MySQL</SelectItem>
                      <SelectItem value="sqlserver">SQL Server</SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              <div className="flex gap-3">
                <Field className="flex-1">
                  <FieldLabel htmlFor="ds-host">Host</FieldLabel>
                  <Input
                    id="ds-host"
                    value={form.host}
                    onChange={(e) => edit("host", e.target.value)}
                    required
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
                />
              </Field>
              <div className="flex gap-3">
                <Field className="flex-1">
                  <FieldLabel htmlFor="ds-user">User</FieldLabel>
                  <Input
                    id="ds-user"
                    value={form.user}
                    onChange={(e) => edit("user", e.target.value)}
                  />
                </Field>
                <Field className="flex-1">
                  <FieldLabel htmlFor="ds-password">Password</FieldLabel>
                  <Input
                    id="ds-password"
                    type="password"
                    value={password}
                    onChange={(e) => {
                      setPassword(e.target.value)
                      setTestResult(null)
                    }}
                    placeholder={isEdit ? "unchanged" : undefined}
                  />
                </Field>
              </div>
            </FieldGroup>
          )}

          {testResult && (
            <p
              className={cn(
                "mt-3 text-sm",
                testResult.state === "error"
                  ? "text-destructive"
                  : "text-muted-foreground"
              )}
            >
              {testResult.state === "ok"
                ? `✓ Connected${
                    testResult.latency_ms
                      ? ` in ${Math.round(testResult.latency_ms)}ms`
                      : ""
                  }.`
                : `✕ ${testResult.message ?? "Connection failed."}`}
            </p>
          )}

          <DialogFooter className="mt-6 gap-2 sm:justify-between">
            <Button
              type="button"
              variant="outline"
              onClick={() => void handleTest()}
              disabled={testing || view === "uri"}
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
        </form>
      </DialogContent>
    </Dialog>
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
      onDeleted()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete")
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
