import type { Metadata } from "next"
import { Geist, JetBrains_Mono } from "next/font/google"

import "./globals.css"
import { AppShell } from "@/components/app-shell"
import { ThemeProvider } from "@/components/theme-provider"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export const metadata: Metadata = {
  title: {
    default: "NomaData",
    template: "%s · NomaData",
  },
  description: "Know My Data. Model-agnostic AI client for conversational BI.",
}

// Two typefaces, two jobs: Geist carries the interface, JetBrains Mono is
// reserved for anything that exists inside a database.
const geist = Geist({ subsets: ["latin"], variable: "--font-sans" })

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
})

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={cn("antialiased", geist.variable, jetbrainsMono.variable)}
    >
      <body>
        <ThemeProvider>
          <TooltipProvider delayDuration={300}>
            <AppShell>{children}</AppShell>
          </TooltipProvider>
          <Toaster position="bottom-right" />
        </ThemeProvider>
      </body>
    </html>
  )
}
