"use client"

import { useEffect, useMemo, useState } from "react"
import { RiCheckLine, RiLoader4Line, RiSparkling2Line } from "@remixicon/react"
import { toast } from "sonner"

import {
  type AIProviderInput,
  getAIConfig,
  saveAIConfig,
  testAIConfig,
} from "@/lib/api-client"
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
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { PageContainer, PageHeader } from "@/components/page-header"

// Presets just prefill the base URL — every option speaks the OpenAI-compatible
// wire format, so the provider kind stays the same.
const PRESETS: { label: string; baseUrl: string }[] = [
  { label: "OpenAI", baseUrl: "https://api.openai.com/v1" },
  { label: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1" },
  { label: "DeepSeek", baseUrl: "https://api.deepseek.com/v1" },
  { label: "Custom", baseUrl: "" },
]

const DEFAULTS: AIProviderInput = {
  provider: "openai_compatible",
  base_url: "https://api.openai.com/v1",
  api_key: "",
  model: "gpt-4o-mini",
}

export default function SettingsPage() {
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState<AIProviderInput>(DEFAULTS)
  // A key is already stored server-side. The masked hint (first 5 + dots + last
  // 3) is prefilled into the field value; the real key never reaches the browser.
  const [hasStoredKey, setHasStoredKey] = useState(false)
  // Until the user edits the key field it still holds the mask, which must NOT
  // be sent back — a blank tells the BE to keep the stored key.
  const [keyDirty, setKeyDirty] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const cfg = await getAIConfig(controller.signal)
        if (controller.signal.aborted) return
        if (cfg) {
          setForm({
            provider: cfg.provider,
            base_url: cfg.base_url,
            model: cfg.model,
            // Show the masked stored key as the actual value, not a placeholder.
            api_key: cfg.key_hint ?? "",
          })
          setHasStoredKey(cfg.configured)
          setKeyDirty(false)
        }
      } catch {
        // Unconfigured (or API down) — keep defaults, the form still works.
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    })()
    return () => controller.abort()
  }, [])

  const activePreset = useMemo(
    () => PRESETS.find((p) => p.baseUrl === form.base_url)?.label ?? "Custom",
    [form.base_url]
  )

  // The key may be omitted only when one is already stored; a first-time config
  // must supply one.
  const canSubmit =
    form.base_url.trim() !== "" &&
    form.model.trim() !== "" &&
    (hasStoredKey || (keyDirty && form.api_key.trim() !== ""))

  const set = (patch: Partial<AIProviderInput>) =>
    setForm((f) => ({ ...f, ...patch }))

  // Send the typed key only if the user changed it; otherwise blank, which the
  // BE reads as "keep the stored key" (the field is prefilled with the mask).
  const outbound = (): AIProviderInput => ({
    ...form,
    api_key: keyDirty ? form.api_key : "",
  })

  async function handleTest() {
    setTesting(true)
    try {
      const status = await testAIConfig(outbound())
      if (status.state === "ok") {
        const ms = status.latency_ms ? ` (${Math.round(status.latency_ms)}ms)` : ""
        toast.success(`Connection OK${ms}`)
      } else {
        toast.error(status.message ?? "Connection failed")
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Test failed")
    } finally {
      setTesting(false)
    }
  }

  async function handleSave() {
    setSaving(true)
    try {
      const info = await saveAIConfig(outbound())
      setHasStoredKey(info.configured)
      set({ api_key: info.key_hint ?? "" })
      setKeyDirty(false)
      toast.success(
        info.configured
          ? "AI provider saved and activated"
          : "Saved — but no usable key, AI stays off"
      )
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed")
    } finally {
      setSaving(false)
    }
  }

  return (
    <PageContainer>
      <PageHeader
        title="Settings"
        description="Configure the AI provider NomaData uses to enrich semantic models. Stored in the app database — the key is never sent back to the browser."
      />

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <RiSparkling2Line className="size-4 text-accent-brand" />
            AI provider
            {hasStoredKey && (
              <Badge variant="secondary" className="ml-1">
                <RiCheckLine data-icon="inline-start" />
                configured
              </Badge>
            )}
          </CardTitle>
          <CardDescription>
            Any OpenAI-compatible endpoint (OpenAI, OpenRouter, DeepSeek, or a
            local model). Without a key, semantic suggestions fall back to the
            heuristic baseline.
          </CardDescription>
        </CardHeader>

        <CardContent className="flex flex-col gap-5">
          <Field>
            <FieldLabel htmlFor="preset">Provider preset</FieldLabel>
            <Select
              value={activePreset}
              onValueChange={(label) => {
                const preset = PRESETS.find((p) => p.label === label)
                if (preset && preset.baseUrl) set({ base_url: preset.baseUrl })
              }}
            >
              <SelectTrigger id="preset" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PRESETS.map((p) => (
                  <SelectItem key={p.label} value={p.label}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <FieldDescription>
              Picks a base URL. Choose Custom to enter your own.
            </FieldDescription>
          </Field>

          <Field>
            <FieldLabel htmlFor="base_url">Base URL</FieldLabel>
            <Input
              id="base_url"
              value={form.base_url}
              onChange={(e) => set({ base_url: e.target.value })}
              placeholder="https://api.openai.com/v1"
              className="font-mono"
            />
          </Field>

          <Field>
            <FieldLabel htmlFor="model">Model</FieldLabel>
            <Input
              id="model"
              value={form.model}
              onChange={(e) => set({ model: e.target.value })}
              placeholder="gpt-4o-mini"
              className="font-mono"
            />
          </Field>

          <Field>
            <FieldLabel htmlFor="api_key">API key</FieldLabel>
            <Input
              id="api_key"
              // Show the masked stored key as plain text (it's already masked,
              // safe); mask real input the moment the user starts typing one.
              type={hasStoredKey && !keyDirty ? "text" : "password"}
              value={form.api_key}
              onChange={(e) => {
                setKeyDirty(true)
                set({ api_key: e.target.value })
              }}
              placeholder={hasStoredKey ? undefined : "sk-…"}
              className="font-mono"
              autoComplete="off"
              spellCheck={false}
            />
            <FieldDescription>
              {hasStoredKey
                ? "The stored key is shown masked. Type a new one to replace it."
                : "Stored in the app database (plaintext for now — encryption lands in Phase 6)."}
            </FieldDescription>
          </Field>
        </CardContent>

        <CardFooter className="justify-end gap-2">
          <Button
            variant="outline"
            onClick={handleTest}
            disabled={loading || testing || !canSubmit}
          >
            {testing && <RiLoader4Line data-icon="inline-start" className="animate-spin" />}
            Test connection
          </Button>
          <Button onClick={handleSave} disabled={loading || saving || !canSubmit}>
            {saving && <RiLoader4Line data-icon="inline-start" className="animate-spin" />}
            Save
          </Button>
        </CardFooter>
      </Card>
    </PageContainer>
  )
}
