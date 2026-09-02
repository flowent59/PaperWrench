.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND  := backend
FRONTEND := frontend
VENV     := $(BACKEND)/.venv
PY       := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

.PHONY: install
install: install-backend install-frontend ## Install all dependencies

.PHONY: install-backend
install-backend: ## Create the venv and install the backend (editable)
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	cd $(BACKEND) && .venv/bin/pip install -e ".[dev]"

.PHONY: install-frontend
install-frontend: ## Install frontend dependencies from the lockfile
	cd $(FRONTEND) && npm ci

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------

.PHONY: dev-backend
dev-backend: ## Run the API with autoreload on :8000
	cd $(BACKEND) && .venv/bin/uvicorn paperwrench.main:app --reload --port 8000

.PHONY: dev-frontend
dev-frontend: ## Run the Vite dev server on :5173 (proxies /api to :8000)
	cd $(FRONTEND) && npm run dev

# ---------------------------------------------------------------------------
# Quality gates - these are exactly what CI runs
# ---------------------------------------------------------------------------

.PHONY: check
check: lint typecheck test ## Run every quality gate

.PHONY: lint
lint: lint-backend lint-frontend ## Lint everything

.PHONY: lint-backend
lint-backend:
	cd $(BACKEND) && .venv/bin/ruff check . ../tests

.PHONY: lint-frontend
lint-frontend:
	cd $(FRONTEND) && npm run lint

.PHONY: format
format: ## Autofix formatting and import order
	cd $(BACKEND) && .venv/bin/ruff check --fix . ../tests && .venv/bin/ruff format . ../tests

.PHONY: typecheck
typecheck: typecheck-backend typecheck-frontend ## Type-check everything

.PHONY: typecheck-backend
typecheck-backend:
	cd $(BACKEND) && .venv/bin/mypy src ../tests

.PHONY: typecheck-frontend
typecheck-frontend:
	cd $(FRONTEND) && npx tsc -b

.PHONY: test
test: test-backend test-frontend ## Run every test suite

.PHONY: test-backend
test-backend:
	cd $(BACKEND) && .venv/bin/pytest

.PHONY: test-frontend
test-frontend:
	cd $(FRONTEND) && npm run test

.PHONY: coverage
coverage: ## Backend tests with a coverage report
	cd $(BACKEND) && .venv/bin/pytest --cov=paperwrench --cov-report=term-missing

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

.PHONY: build
build: build-frontend build-backend ## Build the SPA then the wheel

.PHONY: build-frontend
build-frontend: ## Build the SPA into the Python package
	cd $(FRONTEND) && npm run build

.PHONY: build-backend
build-backend:
	cd $(BACKEND) && .venv/bin/python -m build --wheel

.PHONY: docker-build
docker-build: ## Build the single-container image
	docker build -t paperwrench:dev .

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

.PHONY: migrate
migrate: ## Apply migrations up to head
	cd $(BACKEND) && .venv/bin/alembic upgrade head

.PHONY: migration
migration: ## Autogenerate a migration: make migration m="add jobs table"
	cd $(BACKEND) && .venv/bin/alembic revision --autogenerate -m "$(m)"

# ---------------------------------------------------------------------------
# Development Paperless-ngx sandbox (never your real instance)
# ---------------------------------------------------------------------------

.PHONY: dev-paperless-up
dev-paperless-up: ## Start the disposable Paperless-ngx 3.1.2 sandbox on :8010
	docker compose -f docker-compose.dev.yml up -d

.PHONY: dev-paperless-seed
dev-paperless-seed: ## Seed the sandbox with the reference dataset
	docker compose -f docker-compose.dev.yml --profile seed run --rm seed

.PHONY: dev-paperless-down
dev-paperless-down: ## Stop the sandbox and DELETE its data
	docker compose -f docker-compose.dev.yml down -v

.PHONY: clean
clean: ## Remove build artefacts and caches
	rm -rf $(BACKEND)/dist $(BACKEND)/build $(BACKEND)/src/paperwrench/static
	rm -rf $(FRONTEND)/dist $(FRONTEND)/node_modules/.tmp
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	find . -name '.pytest_cache' -type d -prune -exec rm -rf {} +
	find . -name '.mypy_cache' -type d -prune -exec rm -rf {} +
	find . -name '.ruff_cache' -type d -prune -exec rm -rf {} +
