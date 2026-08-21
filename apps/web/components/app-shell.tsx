"use client"

import Image from "next/image"
import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  type RemixiconComponentType,
  RiChat3Line,
  RiDashboardLine,
  RiDatabase2Line,
  RiNodeTree,
  RiSettings3Line,
} from "@remixicon/react"

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { ThemeToggle } from "@/components/theme-toggle"

type NavItem = {
  href: string
  label: string
  icon: RemixiconComponentType
}

// Flat, only routes that exist. Data Source = schema explorer + connections;
// Semantic Models = the cross-source model overview.
const NAV: NavItem[] = [
  // Ask is the product; the rest configure what it answers from.
  { href: "/", label: "Overview", icon: RiDashboardLine },
  { href: "/chat", label: "Chat", icon: RiChat3Line },
  { href: "/schema", label: "Data Source", icon: RiDatabase2Line },
  { href: "/semantic", label: "Semantic Models", icon: RiNodeTree },
]

const TITLES: Record<string, string> = {
  "/chat": "Chat",
  "/": "Overview",
  "/schema": "Data Source",
  "/semantic": "Semantic Models",
  "/settings": "Settings",
}

// Active-state cue that reads in BOTH themes: the default sidebar-accent is
// almost invisible in light mode, so brand orange marks the current page — an
// orange rail + icon + soft tint. Text stays foreground-dark (orange text on a
// light rail fails the 4.5:1 body-text contrast). Larger label than the shadcn
// default text-xs. twMerge lets this override the variant's data-active styles.
const NAV_ITEM_CLASS =
  "text-sm border-l-2 border-l-transparent transition-colors " +
  // Overrides the shadcn default hover:bg-sidebar-accent — that grey is
  // near-white on the light rail, so hover was invisible. Orange tint instead.
  "hover:bg-accent-brand/10 " +
  "data-active:border-l-accent-brand data-active:bg-accent-brand/10 " +
  "data-active:text-foreground data-active:font-medium " +
  "data-active:[&_svg]:text-accent-brand"

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const title = TITLES[pathname] ?? "NomaData"

  return (
    <SidebarProvider>
      {/* One gradient across the whole rail — painting header/content/footer
          separately would restart it and band at the seams. */}
      <Sidebar
        collapsible="icon"
        className="border-r [&_[data-slot=sidebar-inner]]:bg-rail"
      >
        <SidebarHeader>
          <div className="flex items-center gap-2 px-1 py-1.5">
            <Image
              src="/logo-transparent.svg"
              alt=""
              width={24}
              height={24}
              className="size-6 shrink-0"
            />
            <div className="flex min-w-0 flex-col group-data-[collapsible=icon]:hidden">
              <span className="truncate font-mono text-sm font-semibold tracking-tight">
                NomaData
              </span>
              <span className="truncate text-[0.6875rem] text-muted-foreground">
                Know My Data.
              </span>
            </div>
          </div>
        </SidebarHeader>

        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupContent>
              <SidebarMenu>
                {NAV.map((item) => (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      asChild
                      isActive={pathname === item.href}
                      tooltip={item.label}
                      className={NAV_ITEM_CLASS}
                    >
                      <Link href={item.href}>
                        <item.icon />
                        <span>{item.label}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>

        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton
                asChild
                isActive={pathname === "/settings"}
                tooltip="Settings"
                className={NAV_ITEM_CLASS}
              >
                <Link href="/settings">
                  <RiSettings3Line />
                  <span>Settings</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
          <div className="flex items-center justify-end">
            <ThemeToggle />
          </div>
        </SidebarFooter>
      </Sidebar>

      <SidebarInset className="flex h-svh min-h-0 flex-col overflow-hidden">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
          <SidebarTrigger />
          <div aria-hidden className="h-4 w-px bg-border" />
          <span className="truncate text-sm font-medium">{title}</span>
        </header>
        <div className="min-h-0 flex-1">{children}</div>
      </SidebarInset>
    </SidebarProvider>
  )
}
