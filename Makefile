.PHONY: help install install-backend install-frontend lint format typecheck test \
	migrate migration db-reset run-backend run-frontend dev \
	ingest-full ingest-incremental docker-up docker-down clean \
	docker-test docker-lint docker-typecheck docker-format docker-shell docker-build

help:
	@echo "Targets:"
	@echo ""
	@echo "  --- Local (requires Python + Node installed) ---"
	@echo "  install              Install backend + frontend deps"
	@echo "  lint                 ruff check"
	@echo "  format               ruff format"
	@echo "  typecheck            mypy --strict"
	@echo "  test                 pytest"
	@echo "  migrate              alembic upgrade head"
	@echo "  migration m=msg      alembic revision --autogenerate"
	@echo "  db-reset             Drop SQLite db and re-run migrations"
	@echo "  run-backend          uvicorn dev server"
	@echo "  run-frontend         vite dev server"
	@echo "  ingest-full          Full 5y backfill"
	@echo "  ingest-incremental   Daily delta"
	@echo ""
	@echo "  --- Docker (recommended for cross-platform) ---"
	@echo "  dev                  docker compose up (backend + frontend)"
	@echo "  docker-build         Rebuild backend dev image"
	@echo "  docker-test          Run pytest inside the backend container"
	@echo "  docker-lint          Run ruff check inside the backend container"
	@echo "  docker-typecheck     Run mypy --strict inside the backend container"
	@echo "  docker-format        Run ruff format inside the backend container"
	@echo "  docker-shell         Open a bash shell in the backend container"
	@echo "  docker-up / docker-down"
	@echo "  clean                Remove caches"

# ---------------------------------------------------------------------------
# Local targets (Mac / Linux with Python + Node installed)
# ---------------------------------------------------------------------------

install: install-backend install-frontend

install-backend:
	cd backend && pip install -e ".[dev]"

install-frontend:
	cd frontend && npm install

lint:
	cd backend && ruff check .

format:
	cd backend && ruff format .

typecheck:
	cd backend && mypy --strict .

test:
	cd backend && pytest

migrate:
	cd backend && alembic upgrade head

migration:
	cd backend && alembic revision --autogenerate -m "$(m)"

db-reset:
	rm -f data/wheel.db
	$(MAKE) migrate

run-backend:
	cd backend && uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

run-frontend:
	cd frontend && npm run dev

ingest-full:
	cd backend && python -m ingestion.pipeline --full

ingest-incremental:
	cd backend && python -m ingestion.pipeline --incremental

# ---------------------------------------------------------------------------
# Docker targets (works on any OS with Docker Desktop)
# ---------------------------------------------------------------------------

dev:
	docker compose up

docker-build:
	docker compose build backend

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-test:
	docker compose exec backend pytest -q

docker-lint:
	docker compose exec backend ruff check .

docker-format:
	docker compose exec backend ruff format .

docker-typecheck:
	docker compose exec backend mypy --strict .

docker-shell:
	docker compose exec backend bash

docker-migrate:
	docker compose exec backend alembic upgrade head

docker-ingest-full:
	docker compose exec backend python -m ingestion.pipeline --full

docker-ingest-incremental:
	docker compose exec backend python -m ingestion.pipeline --incremental

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
