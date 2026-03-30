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

typecheck:
    uv run pyright

test:
    uv run pytest tests/ -q

ci: lint typecheck test

release VERSION: ci
    #!/usr/bin/env bash
    set -euo pipefail
    VERSION="{{VERSION}}"
    if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
      echo "error: version must be semver (e.g. 0.0.2), got: $VERSION"
      exit 1
    fi
    if git tag -l "v$VERSION" | grep -q .; then
      echo "error: tag v$VERSION already exists"
      exit 1
    fi
    sed -i '' "s/^version = .*/version = \"$VERSION\"/" pyproject.toml
    sed -i '' "s/^__version__ = .*/__version__ = \"$VERSION\"/" ccmeter/__init__.py
    git diff --quiet pyproject.toml ccmeter/__init__.py || \
      git commit pyproject.toml ccmeter/__init__.py -m "release(ccmeter): v$VERSION"
    git tag "v$VERSION"
    rm -rf dist
    uv build
    uv publish
    echo "published ccmeter v$VERSION"
