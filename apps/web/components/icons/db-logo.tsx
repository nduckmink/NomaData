import Image from "next/image"

import { cn } from "@/lib/utils"

/**
 * Static vendor artwork in `public/db-logos/`, one file per engine — full
 * brand colour, used as-is rather than recoloured to the theme's ink (the
 * earlier registry-icon approach forced `currentColor`; these are the real
 * marks, so they keep their own palette like SQL Server always did).
 *
 * `w`/`h` are each file's intrinsic size, not a display size — Next's
 * <Image> uses the ratio to reserve space and avoid a layout jump before the
 * asset decodes. Actual display size comes from `className` (h-* + w-auto).
 */
const LOGOS: Record<string, { src: string; w: number; h: number }> = {
  mysql: { src: "/db-logos/logo-mysql.png", w: 176, h: 119 },
  sqlserver: { src: "/db-logos/microsoft-sql-server.svg", w: 48, h: 48 },
  postgresql: { src: "/db-logos/Postgresql.svg", w: 432, h: 445 },
  mariadb: { src: "/db-logos/MariaDB.svg", w: 416, h: 118 },
  clickhouse: { src: "/db-logos/ClickHouse_Logo.svg", w: 649, h: 198 },
  bigquery: { src: "/db-logos/google_bigquery-ar21.svg", w: 120, h: 60 },
  snowflake: { src: "/db-logos/Snowflake_Logo.svg", w: 184, h: 44 },
  redshift: { src: "/db-logos/Amazon-Redshift-Logo.svg", w: 40, h: 44 },
  duckdb: { src: "/db-logos/DuckDB_logo.svg", w: 770, h: 592 },
  oracle: { src: "/db-logos/Oracle_logo.svg", w: 231, h: 30 },
  databricks: { src: "/db-logos/Databricks-logo.svg", w: 107, h: 45 },
  sqlite: { src: "/db-logos/SQLite370.svg", w: 382, h: 181 },
}

export function hasDbLogo(engine: string): boolean {
  return engine in LOGOS
}

/**
 * Vendor mark for a database engine, falling back to a monogram for engines
 * whose logo we do not ship. Size it by height (`h-*`); width follows the
 * asset's own aspect ratio.
 */
export function DbLogo({
  engine,
  monogram,
  className,
}: {
  engine: string
  monogram: string
  className?: string
}) {
  const logo = LOGOS[engine]
  if (logo) {
    return (
      <Image
        src={logo.src}
        alt=""
        width={logo.w}
        height={logo.h}
        unoptimized
        // Default lazy-loading relies on the browser's own visibility check
        // at mount time. These render inside a Radix Dialog portal, which is
        // display:none until its open animation starts — the check happens
        // before that, so the native lazy-load path never fires and the
        // image never requests. Loading eagerly sidesteps it; the assets are
        // a handful of small icons that only mount once the dialog is open.
        loading="eager"
        className={cn("w-auto object-contain", className)}
      />
    )
  }
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex items-center font-mono text-sm font-semibold",
        className
      )}
    >
      {monogram}
    </span>
  )
}
