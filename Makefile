# NomaData — developer entrypoints
# Usage: make <target>

.DEFAULT_GOAL := help
.PHONY: help infra infra-down infra-logs up down logs fmt lint typecheck test api-dev web-dev install

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

## ---------- Recommended dev: infra in Docker, apps in watch mode ----------
infra: ## Start backing services only (postgres + cube) in the background
	docker compose up -d postgres cube

infra-down: ## Stop backing services
	docker compose stop postgres cube

infra-logs: ## Tail infra logs (postgres + cube)
	docker compose logs -f postgres cube

install: ## Install backend + frontend dependencies
	cd apps/api && uv sync
	cd apps/web && pnpm install

api-dev: ## Run the API locally with hot reload (needs `make infra`)
	cd apps/api && uv run uvicorn nomadata.main:app --reload --host 0.0.0.0 --port 8000

web-dev: ## Run the web client locally with HMR (needs api-dev)
	cd apps/web && pnpm dev

## ---------- Full stack in Docker (demo / prod-like, no reload) ----------
up: ## Boot EVERYTHING in Docker (postgres + cube + api + web)
	docker compose --profile full up --build

down: ## Stop the full stack and remove containers
	docker compose --profile full down

logs: ## Tail logs from all services
	docker compose --profile full logs -f

## ---------- Quality ----------
fmt: ## Format all code
	cd apps/api && uv run ruff format .
	cd apps/web && pnpm exec prettier --write .

lint: ## Lint all code + verify architecture boundaries
	cd apps/api && uv run ruff check . && uv run lint-imports
	cd apps/web && pnpm exec eslint .

typecheck: ## Type-check backend + frontend
	cd apps/api && uv run mypy nomadata
	cd apps/web && pnpm exec tsc --noEmit

test: ## Run backend + frontend tests
	cd apps/api && uv run pytest
	cd apps/web && pnpm test --if-present
