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
| **Docker**     | latest  | Full-stack run (optional)        |

Install the two package managers if you don't have them:

```bash
npm install -g pnpm          # pnpm
pip install uv               # uv  (or: https://docs.astral.sh/uv/)
```

> **Windows note:** if `uv` is installed via `pip install --user` it may not be
> on your PATH. Either add it to PATH, or run it as `python -m uv ...`
> everywhere this guide (and the Makefile) says `uv ...`.

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

## 4. Run the whole stack with Docker (one command)

```bash
make up          # docker compose up --build: postgres + api + web + cube
```

Then open http://localhost:3000 — you should see the **System Status** page
reporting the API as *Operational*.

```bash
make down        # stop and remove containers
make logs        # tail logs from all services
```

> Docker pulls the `postgres`, `cubejs/cube`, and builds the api/web images on
> first run, so the first `make up` takes a few minutes.

---

## 5. Run locally without Docker

Two terminals — one for the API, one for the web client.

### 5.1 Backend (`apps/api`)

```bash
cd apps/api
uv sync                                                   # install deps + venv
uv run uvicorn nomadata.main:app --reload --port 8000     # run with hot reload
```

Verify it's up:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok","version":"0.0.1","env":"development","checks":{"api":"ok"},"providers":[],"data_sources":[]}
```

### 5.2 Frontend (`apps/web`)

```bash
cd apps/web
pnpm install
pnpm dev                                                  # http://localhost:3000
```

Open http://localhost:3000. The page fetches `/health` from the API and renders
the status card. If the API is down you'll see an "API unreachable" alert.

> The backend must be running (step 5.1) for the status page to show
> *Operational*. CORS is preconfigured for `http://localhost:3000`.

### 5.3 Cube (optional in M0)

Cube has no models yet in M0. It only needs to run for the full Docker demo.
To run it standalone, use `cp cube/.env.example cube/.env` and Docker, or rely
on `make up`.

---

## 6. Quality commands

From the repo root (each also runnable per-app):

```bash
make fmt         # format backend (ruff) + frontend (prettier)
make lint        # ruff + import-linter (architecture) + eslint
make typecheck   # mypy + tsc
make test        # pytest + web tests
```

Per-app equivalents:

```bash
# Backend
cd apps/api
uv run pytest                    # tests
uv run ruff check .              # lint
uv run lint-imports              # architecture boundary contracts
uv run mypy nomadata             # types

# Frontend
cd apps/web
pnpm exec eslint app lib         # lint
pnpm exec tsc --noEmit           # types
pnpm build                       # production build
```

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
| `uv: command not found` | Use `python -m uv ...`, or add uv to PATH. |
| Status page shows "API unreachable" | Start the backend (step 5.1); check it's on port 8000. |
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
