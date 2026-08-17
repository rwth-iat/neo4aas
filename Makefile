.PHONY: help install test test-unit test-integration lint fmt build up down logs clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Install the package with every extra into .venv
	uv sync --all-extras

test:  ## Run the whole suite (integration needs Neo4j or Docker)
	uv run pytest

test-unit:  ## Run only tests that need no database
	uv run pytest -m "not integration"

test-integration:  ## Run only tests that need Neo4j
	uv run pytest -m integration

lint:  ## Check style and correctness
	uv run ruff check .

fmt:  ## Apply the fixable lint findings
	uv run ruff check --fix .

build:  ## Build the wheel into dist/
	uv build --wheel

up:  ## Start the demonstrator stack
	docker compose -f deploy/demonstrator/docker-compose.yml up -d --build

down:  ## Stop the demonstrator stack
	docker compose -f deploy/demonstrator/docker-compose.yml down

logs:  ## Follow the demonstrator logs
	docker compose -f deploy/demonstrator/docker-compose.yml logs -f

clean:  ## Remove build artefacts and caches
	rm -rf build dist *.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -not -path "./.venv/*" -exec rm -rf {} +
