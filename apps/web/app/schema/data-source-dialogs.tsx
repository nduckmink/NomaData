"use client"

import { type FormEvent, type ReactNode, useState } from "react"

import {
  createDataSource,
  type DataSourceKind,
  deleteDataSource,
  getDataSource,
  updateDataSource,
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
  const [kind, setKind] = useState<DataSourceKind>("mysql")
  const [form, setForm] = useState<FormState>(BLANK)
  const [password, setPassword] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Runs in the trigger's event handler, so setState here is fine.
  async function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) return
    setError(null)
    setPassword("")
    if (mode === "edit" && name) {
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

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    const payload = {
      name: form.name.trim(),
      kind,
      host: form.host.trim(),
      port: Number(form.port),
      database: form.database.trim(),
      user: form.user.trim(),
      password,
    }
    try {
      const info =
        mode === "create"
          ? await createDataSource(payload)
          : await updateDataSource(name ?? payload.name, payload)
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

  const isEdit = mode === "edit"

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

          {error && (
            <Alert variant="destructive" className="mt-4">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <FieldGroup className="mt-4">
            <Field>
              <FieldLabel htmlFor="ds-name">Name</FieldLabel>
              <Input
                id="ds-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
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
                  onChange={(e) => setForm({ ...form, host: e.target.value })}
                  required
                />
              </Field>
              <Field className="w-24">
                <FieldLabel htmlFor="ds-port">Port</FieldLabel>
                <Input
                  id="ds-port"
                  inputMode="numeric"
                  value={form.port}
                  onChange={(e) => setForm({ ...form, port: e.target.value })}
                  required
                />
              </Field>
            </div>
            <Field>
              <FieldLabel htmlFor="ds-database">Database</FieldLabel>
              <Input
                id="ds-database"
                value={form.database}
                onChange={(e) => setForm({ ...form, database: e.target.value })}
                required
              />
            </Field>
            <div className="flex gap-3">
              <Field className="flex-1">
                <FieldLabel htmlFor="ds-user">User</FieldLabel>
                <Input
                  id="ds-user"
                  value={form.user}
                  onChange={(e) => setForm({ ...form, user: e.target.value })}
                />
              </Field>
              <Field className="flex-1">
                <FieldLabel htmlFor="ds-password">Password</FieldLabel>
                <Input
                  id="ds-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={isEdit ? "unchanged" : undefined}
                />
              </Field>
            </div>
          </FieldGroup>

          <DialogFooter className="mt-6">
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
