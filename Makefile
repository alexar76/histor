.PHONY: test integration lint run crawl scanner

scanner:
	npm ci --prefix scanner --no-audit --no-fund

test: scanner
	uv sync --extra dev --project .
	uv run --project . ruff check .
	uv run --project . pytest --cov=histor --cov-branch -m "not integration"

integration: scanner
	uv run --project . pytest -m integration

lint:
	uv run --project . ruff check .

run: scanner
	uv run --project . python -m histor serve

crawl: scanner
	uv run --project . python -m histor crawl
