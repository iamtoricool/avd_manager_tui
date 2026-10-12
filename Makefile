.PHONY: help install dev test lint fmt run shot clean

VENV := .venv
PY   := $(VENV)/bin/python

help:
	@echo "install  - install the package"
	@echo "dev      - install with dev extras (pytest, ruff)"
	@echo "test     - run the test suite"
	@echo "lint     - ruff check"
	@echo "run      - launch the TUI"
	@echo "shot     - render a screenshot of the running app"
	@echo "clean    - remove build artefacts"

$(VENV):
	python3 -m venv $(VENV)

install: $(VENV)
	$(PY) -m pip install -e .

dev: $(VENV)
	$(PY) -m pip install -e ".[dev]"

test: dev
	$(VENV)/bin/pytest -q

lint: dev
	$(VENV)/bin/ruff check src tests

fmt: dev
	$(VENV)/bin/ruff check --fix src tests
	$(VENV)/bin/ruff format src tests

run: dev
	$(VENV)/bin/avdmanager

shot: dev
	$(PY) tools/screenshot.py

clean:
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +