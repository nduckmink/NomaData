"use client"

import { type FormEvent, useState } from "react"
import { RiAddLine } from "@remixicon/react"

import { createDataSource, type DataSourceKind } from "@/lib/api-client"
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

export function AddDataSourceDialog({
  onCreated,
}: {
  onCreated: (name: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<DataSourceKind>("mysql")
  const [port, setPort] = useState(DEFAULT_PORT.mysql)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setKind("mysql")
    setPort(DEFAULT_PORT.mysql)
    setError(null)
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    const data = new FormData(e.currentTarget)
    try {
      const info = await createDataSource({
        name: String(data.get("name") ?? "").trim(),
        kind,
        host: String(data.get("host") ?? "").trim(),
        port: Number(port),
        database: String(data.get("database") ?? "").trim(),
        user: String(data.get("user") ?? "").trim(),
        password: String(data.get("password") ?? ""),
      })
      setOpen(false)
      reset()
      onCreated(info.name)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to create data source"
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o)
        if (!o) reset()
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <RiAddLine data-icon="inline-start" />
          Add source
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Add data source</DialogTitle>
            <DialogDescription>
              Connect a database. It is stored and connected immediately.
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
                name="name"
                placeholder="prod_mysql"
                required
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="ds-kind">Engine</FieldLabel>
              <Select
                value={kind}
                onValueChange={(v) => {
                  const k = v as DataSourceKind
                  setKind(k)
                  setPort(DEFAULT_PORT[k])
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
                  name="host"
                  defaultValue="localhost"
                  required
                />
              </Field>
              <Field className="w-24">
                <FieldLabel htmlFor="ds-port">Port</FieldLabel>
                <Input
                  id="ds-port"
                  name="port"
                  inputMode="numeric"
                  value={port}
                  onChange={(e) => setPort(e.target.value)}
                  required
                />
              </Field>
            </div>
            <Field>
              <FieldLabel htmlFor="ds-database">Database</FieldLabel>
              <Input id="ds-database" name="database" required />
            </Field>
            <div className="flex gap-3">
              <Field className="flex-1">
                <FieldLabel htmlFor="ds-user">User</FieldLabel>
                <Input id="ds-user" name="user" />
              </Field>
              <Field className="flex-1">
                <FieldLabel htmlFor="ds-password">Password</FieldLabel>
                <Input id="ds-password" name="password" type="password" />
              </Field>
            </div>
          </FieldGroup>

          <DialogFooter className="mt-6">
            <Button type="submit" disabled={submitting}>
              {submitting ? "Connecting…" : "Add source"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
