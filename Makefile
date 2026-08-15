# NomaData — developer entrypoints
# Usage: make <target>

.DEFAULT_GOAL := help
.PHONY: help up down logs fmt lint typecheck test api-dev web-dev install

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

## ---------- Full stack (Docker Compose) ----------
up: ## Boot the full stack (postgres + api + web + cube)
	docker compose up --build

down: ## Stop the stack and remove containers
	docker compose down

logs: ## Tail logs from all services
	docker compose logs -f

## ---------- Local dev (without Docker) ----------
install: ## Install backend + frontend dependencies
	cd apps/api && uv sync
	cd apps/web && pnpm install

api-dev: ## Run the API locally with reload
	cd apps/api && uv run uvicorn nomadata.main:app --reload --host 0.0.0.0 --port 8000

web-dev: ## Run the web client locally
	cd apps/web && pnpm dev

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
