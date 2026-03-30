default:
    @just --list

install:
    uv sync
    mkdir -p ~/.local/bin
    ln -sf "$(pwd)/.venv/bin/ccmeter" ~/.local/bin/ccmeter
    @echo "ccmeter → ~/.local/bin/ccmeter"

format:
    uv run ruff format . && uv run ruff check --fix . || true

lint:
    uv run ruff check .

test:
    uv run pytest tests/ -q

ci: lint test
