# MORPH — the paved path.
#
# Every target runs through .venv/bin/python, never a bare `python` or `pip`, so it
# behaves the same whether or not you remembered to activate the virtual environment
# in this particular shell. That is the single most common way a first run goes
# wrong, and on Debian and Ubuntu it fails confusingly: a system pip refuses with
# "externally-managed-environment" (PEP 668) rather than saying "you are not in your
# venv".
#
#   make install     create .venv if needed and install the dev extra
#   make reinstall   force the install again, ignoring the stamp
#   make test        the Python suite
#   make e2e         the browser suite (needs Node and Playwright)
#   make demo        the end-to-end acceptance script
#   make serve       demo applications + dashboard, left running
#   make check       install, test, bundle freshness — what CI runs
#   make validate    the Hermes catalog admission checks
#   make clean       remove .venv, caches and test artefacts

VENV    := .venv
PY      := $(VENV)/bin/python
STAMP   := $(VENV)/.installed
SYSPY   ?= python3

.DEFAULT_GOAL := help
.PHONY: help install reinstall test e2e demo serve check validate bundle clean

help:
	@grep -E '^#   make' $(MAKEFILE_LIST) | sed 's/^#   /  /'

$(PY):
	$(SYSPY) -m venv $(VENV)
	$(PY) -m pip install --upgrade --quiet pip

# The stamp is newer than pyproject.toml once the dev extra is installed, so the
# other targets depend on it and reinstall only when the dependencies change.
$(STAMP): pyproject.toml | $(PY)
	$(PY) -m pip install -e ".[dev]"
	@$(PY) -c "import fastapi, uvicorn, httpx, pytest; print('ready: ' + __import__('sys').executable)"
	@touch $(STAMP)

install: $(STAMP)

reinstall:
	rm -f $(STAMP)
	@$(MAKE) --no-print-directory install

test: $(STAMP)
	$(PY) -m pytest

e2e: $(STAMP)
	PYTHON=$(abspath $(PY)) ./scripts/e2e.sh

demo: $(STAMP)
	$(PY) -m demo.run_demo

serve: $(STAMP)
	$(PY) -m demo.run_demo --serve

bundle:
	node scripts/build_dashboard.mjs --check

check: test bundle

validate: $(STAMP)
	hermes plugins doctor . --ci
	hermes plugins validate . --install-deps

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .e2e-artifacts node_modules
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
