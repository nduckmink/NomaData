# Getting Started (Developers)

> **Know My Data.** — how to run NomaData locally.

This guide covers the M0 foundation skeleton: a monorepo where the web client
reads the API health endpoint end-to-end. See
[M0-FOUNDATION.md](./M0-FOUNDATION.md) for the plan and
[ARCHITECTURE.md](./ARCHITECTURE.md) for the boundary rules.

---

## 1. Prerequisites

| Tool           | Version | Purpose                          |
| -------------- | ------- | -------------------------------- |
| **Node.js**    | ≥ 20    | Web client (Next.js)             |
| **pnpm**       | ≥ 9     | Web package manager              |
| **Python**     | ≥ 3.11  | API backend                      |
| **uv**         | latest  | Python deps + venv               |
| **Docker**     | latest  | Backing services (Postgres, Cube)|

Install the toolchain if you don't have it:

```bash
npm install -g pnpm                                    # pnpm (web)

# uv (API) — the official installer puts uv on your PATH:
#   Windows (PowerShell):
#     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
#   macOS / Linux:
#     curl -LsSf https://astral.sh/uv/install.sh | sh
```

> **`make` is NOT required.** Every command below runs through `pnpm` scripts
> defined in the root `package.json`. (A `Makefile` exists as an optional
> alternative on macOS/Linux/WSL.)
>
> After installing uv, open a **new terminal** so `uv` is on your PATH
> (`uv --version` should print a version). It installs to `~/.local/bin`.

---

## 2. Ports

| Service   | URL                              |
| --------- | -------------------------------- |
| Web       | http://localhost:3000            |
| API       | http://localhost:8000            |
| API docs  | http://localhost:8000/docs       |
| Health    | http://localhost:8000/api/v1/health |
| Cube      | http://localhost:4000            |
| Postgres  | localhost:5432                   |

---

## 3. Environment config

Copy the example env file at the repo root:

```bash
cp .env.example .env
```

For local (non-Docker) dev the defaults work as-is. Key variables:

- `NOMADATA_DATABASE_URL` — NomaData's own metadata Postgres.
- `NEXT_PUBLIC_API_BASE_URL` — where the browser reaches the API (default
  `http://localhost:8000`).
- `NOMADATA_AI_*` — AI provider config. **Not used in M0** (no LLM calls yet).

Secrets stay in `.env`; they are never sent to the LLM.

---

## 4. Recommended dev workflow (infra in Docker, apps in watch mode)

The day-to-day loop: run only the **backing services** (Postgres + Cube) in
Docker, and run the **API and web client locally** so you get hot reload / HMR.
This is the pnpm-monorepo pattern — infra is disposable, apps stay on watch.

First time only — install deps:

```bash
pnpm setup                   # pnpm install (web) + uv sync (api)
```

Then, each session:

```bash
# 1. Backing services in the background (postgres + cube)
pnpm infra

# 2. API with hot reload            (terminal A)
pnpm api:dev                 # http://localhost:8000 — reloads on .py changes

# 3. Web client with HMR            (terminal B)
pnpm web:dev                 # http://localhost:3000 — reloads on .tsx changes
```

Open http://localhost:3000 — the **System Status** page fetches `/health` and
shows the API as *Operational*. Verify the API directly:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok","version":"0.0.1","env":"development","checks":{"api":"ok"},"providers":[],"data_sources":[]}
```

Stop infra when done:

```bash
pnpm infra:down              # stop postgres + cube
```

> `pnpm infra` publishes Postgres on `localhost:5432` and Cube on
> `localhost:4000` — exactly what the local API/web default to, so no env
> changes are needed. The api/web services live behind the compose `full`
> profile, so a bare `docker compose up` starts only the backing services.

---

## 5. Alternative: run everything in Docker (demo / prod-like)

No reload, but boots the whole system with one command — good for a quick demo
or to sanity-check the container builds.

```bash
pnpm up          # postgres + cube + api + web  →  http://localhost:3000
pnpm down        # stop and remove containers
pnpm logs        # tail logs from all services
```

> First `pnpm up` pulls/builds images, so it takes a few minutes.

---

## 6. Quality commands

From the repo root:

```bash
pnpm lint        # ruff + import-linter (api) + eslint (web)
pnpm typecheck   # mypy (api) + tsc (web)
pnpm test        # pytest
pnpm fmt         # ruff format + prettier
pnpm build:web   # production build of the web client
```

Scoped variants: `pnpm lint:api`, `pnpm lint:web`, `pnpm typecheck:api`,
`pnpm typecheck:web`, `pnpm test:api`. These wrap `uv` (api) and `pnpm --filter`
(web), so you can also run the underlying tools directly from each app dir.

> **Architecture is enforced, not just documented.** `lint-imports` fails the
> check if any module outside `providers/` imports an LLM SDK, any module
> outside `connectors/` imports a DB driver, or `core/` imports an outer layer.
> See [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## 7. Project layout (quick map)

```text
apps/
  api/    FastAPI backend + agent runtime (Python, uv)
    nomadata/
      core/         interfaces + shared models + registry   ← depend on this
      providers/    AIProvider impls        (empty until M3)
      connectors/   DataSource impls        (empty until M1)
      query/        QueryEngine / Cube      (empty until M2/M3)
      semantic/     semantic artifact home  (empty until M2)
      agent/        agent runtime           (empty until M3)
  web/    Next.js + shadcn client (pnpm)
cube/     Cube semantic/query layer config
docs/     plans, architecture, this guide
```

---

## 8. Troubleshooting

| Symptom | Fix |
| ------- | --- |
| `make: command not found` (Windows) | Expected — use the `pnpm` scripts (§4). `make` is optional. |
| `uv: command not found` | Open a new terminal after installing uv; ensure `~/.local/bin` is on PATH. |
| Status page shows "API unreachable" | Start the API (`pnpm api:dev`); check it's on port 8000. |
| Port already in use | Stop the other process or change the port flag. |
| `pnpm: command not found` | `npm install -g pnpm`. |
| Docker build slow first time | Expected — images pull/build once, then cache. |
| Line-ending / CRLF warnings on commit | Expected on Windows; `.gitattributes` normalizes to LF. |
| `create-next-app` left a nested `.git` in `apps/web` | Delete it before `git add` so it isn't tracked as a gitlink. |

---

## 9. What works in M0 (and what doesn't yet)

**Works:** stack boots, web reads live API health, architecture boundaries
enforced, all checks green.

**Not yet (by design):** real database connection (M1), semantic model (M2),
LLM/agent (M3), charts from real data (M4). M0 is the stage; M1 puts the first
real actor on it — connect a PostgreSQL database and introspect its schema.
