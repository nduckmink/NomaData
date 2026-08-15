"use client"

import { useTheme } from "next-themes"
import { RiMoonLine, RiSunLine } from "@remixicon/react"

import { Button } from "@/components/ui/button"

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  return (
    <Button
      variant="outline"
      size="icon-sm"
      aria-label="Toggle light/dark theme"
      title="Toggle theme (D)"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
    >
      {/* Icon visibility follows the `.dark` class next-themes sets on <html>,
          so there's no client-only state and no hydration flash. */}
      <RiSunLine className="hidden dark:block" />
      <RiMoonLine className="dark:hidden" />
    </Button>
  )
}
