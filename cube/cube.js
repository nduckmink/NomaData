// NomaData — Cube configuration.
//
// Cube gets each data source's connection from NomaData's app database (the
// `data_sources` table, edited in the UI) — not from static CUBEJS_DB_* env.
// Every generated cube declares `data_source: <name>`; driverFactory looks that
// name up and returns a DriverConfig with the credentials you configured in the
// UI. This keeps the UI the single source of truth and supports multiple sources.
//
// Cube v1.7+: driverFactory returns a DriverConfig object `{ type, ... }` (the
// removed `dbType` option is folded into it). Requires CUBEJS_APP_DB_URL (the
// app Postgres). Passwords are read as stored (plaintext for now — Phase 6).

const { Pool } = require("pg");

// One small, long-lived pool to the app DB — a new client per lookup would
// exhaust Postgres connections (Cube calls driverFactory a lot).
const appDb = new Pool({ connectionString: process.env.CUBEJS_APP_DB_URL, max: 4 });

// Cache connection configs by data source name (creds rarely change; restart
// Cube to pick up edits). Concurrent first-lookups share one promise.
const configCache = new Map();

function loadConfig(dataSource) {
  if (!configCache.has(dataSource)) {
    configCache.set(
      dataSource,
      appDb
        .query(
          "SELECT kind, host, port, database, username, password FROM data_sources WHERE name = $1",
          [dataSource]
        )
        .then(({ rows }) => {
          if (rows.length === 0) throw new Error(`Unknown data source: ${dataSource}`);
          return rows[0];
        })
        .catch((err) => {
          configCache.delete(dataSource); // don't cache failures
          throw err;
        })
    );
  }
  return configCache.get(dataSource);
}

// Cube asks driverFactory for a "default" data source (orchestrator/queue) even
// when cubes name their own. Resolve it to the first configured source.
let defaultName;
function resolveName(dataSource) {
  if (dataSource && dataSource !== "default") return Promise.resolve(dataSource);
  if (!defaultName) {
    defaultName = appDb
      .query("SELECT name FROM data_sources ORDER BY name LIMIT 1")
      .then(({ rows }) => {
        if (rows.length === 0) throw new Error("No data sources configured.");
        return rows[0].name;
      })
      .catch((err) => {
        defaultName = undefined;
        throw err;
      });
  }
  return defaultName;
}

// A host the app reaches at localhost is reached from the Cube container via
// host.docker.internal.
function hostFor(host) {
  return host === "127.0.0.1" || host === "localhost" ? "host.docker.internal" : host;
}

module.exports = {
  driverFactory: async ({ dataSource }) => {
    const cfg = await loadConfig(await resolveName(dataSource));
    const host = hostFor(cfg.host);
    if (cfg.kind === "mysql") {
      return {
        type: "mysql",
        host,
        port: cfg.port,
        database: cfg.database,
        user: cfg.username,
        password: cfg.password,
      };
    }
    if (cfg.kind === "sqlserver" || cfg.kind === "mssql") {
      return {
        type: "mssql",
        server: host,
        host,
        port: cfg.port,
        database: cfg.database,
        user: cfg.username,
        password: cfg.password,
      };
    }
    throw new Error(`Unsupported data source kind: ${cfg.kind}`);
  },
};
