# MusicContext developer tasks. `make` on its own prints this list.
# PY points at the project venv so the targets work in a fresh checkout.
PY ?= .venv/bin/python
PIP ?= $(PY) -m pip
RUN := PYTHONPATH=src $(PY)
FIXTURES ?= tests/_fixtures

.DEFAULT_GOAL := help
.PHONY: help install fixtures test test-fast test-security lint fix types bench build clean docker

help: ## Show this list
	@echo "MusicContext make targets:"
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Create .venv and install the package with dev extras (uv if present)
	@if command -v uv >/dev/null 2>&1; then \
		uv venv .venv && uv pip install --python .venv/bin/python -e '.[dev]'; \
	else \
		python3 -m venv .venv && .venv/bin/python -m pip install --upgrade pip && \
		.venv/bin/python -m pip install -e '.[dev]'; \
	fi

fixtures: ## Synthesize the demo video and click track into $(FIXTURES)
	$(RUN) scripts/make_fixtures.py $(FIXTURES)

test: ## Run the whole test suite
	$(RUN) -m pytest -q --timeout=600

test-fast: ## Run the suite without the tests that need ffmpeg
	$(RUN) -m pytest -q -m 'not ffmpeg'

test-security: ## Run only the security suite
	$(RUN) -m pytest tests/security -q

lint: ## Lint with ruff (no formatting check: the tree is not ruff-format clean yet)
	$(PY) -m ruff check src tests benchmarks scripts

fix: ## Apply ruff's safe autofixes
	$(PY) -m ruff check --fix src tests benchmarks scripts

types: ## Type-check src with mypy
	$(PY) -m mypy src

bench: ## Benchmark the pipeline on 30 s and 60 s clips
	$(RUN) benchmarks/bench.py --durations 30 60

build: ## Build the sdist and wheel into dist/
	$(PY) -m build

clean: ## Remove build output, caches and generated fixtures
	rm -rf dist build *.egg-info src/*.egg-info .pytest_cache .ruff_cache .mypy_cache $(FIXTURES)
	find . -path ./.venv -prune -o -name '__pycache__' -type d -print0 | xargs -0 rm -rf

docker: ## Build the container image
	docker build -t musiccontext:dev .
