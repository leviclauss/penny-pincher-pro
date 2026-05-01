.PHONY: help install install-backend install-frontend lint format typecheck test \
	migrate migration db-reset run-backend run-frontend dev \
	ingest-full ingest-incremental docker-up docker-down clean

help:
	@echo "Targets:"
	@echo "  install              Install backend + frontend deps"
	@echo "  install-backend      Install Python deps (editable)"
	@echo "  install-frontend     npm install"
	@echo "  lint                 ruff check"
	@echo "  format               ruff format"
	@echo "  typecheck            mypy --strict"
	@echo "  test                 pytest"
	@echo "  migrate              alembic upgrade head"
	@echo "  migration m=msg      alembic revision --autogenerate -m \"\$$m\""
	@echo "  db-reset             Drop SQLite db and re-run migrations"
	@echo "  run-backend          uvicorn dev server"
	@echo "  run-frontend         vite dev server"
	@echo "  dev                  docker compose up"
	@echo "  ingest-full          python -m ingestion.pipeline --full"
	@echo "  ingest-incremental   python -m ingestion.pipeline --incremental"
	@echo "  docker-up / docker-down"
	@echo "  clean                Remove caches"

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

dev:
	docker compose up

ingest-full:
	cd backend && python -m ingestion.pipeline --full

ingest-incremental:
	cd backend && python -m ingestion.pipeline --incremental

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
