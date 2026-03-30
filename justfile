default:
    @just --list

install:
    uv sync

format:
    uv run ruff format . && uv run ruff check --fix . || true

lint:
    uv run ruff check .

test:
    uv run pytest tests/ -q

ci: lint test
