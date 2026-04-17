.PHONY: install lint test eval eval-full bench run replay doctor ci clean

install:
	uv sync

lint:
	uv run ruff check signalforge/ evals/ tests/

test:
	uv run pytest tests/ -q

ci: lint test eval

eval:
	uv run python evals/run_regression.py --deterministic-only

eval-full:
	uv run python evals/run_regression.py

bench:
	uv run python evals/bench_models.py

run:
	uv run signalforge run --config icp.yaml --limit 10

doctor:
	uv run signalforge doctor

clean:
	rm -rf data/ .pytest_cache/ .ruff_cache/ __pycache__/
	find . -name __pycache__ -type d -exec rm -rf {} +
