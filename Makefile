PYTHON ?= python3
export PYTHONDONTWRITEBYTECODE := 1

.PHONY: help lint typecheck test translations-check metadata-check release-check build clean release

help:
	@printf '%s\n' 'Targets: lint typecheck test translations-check metadata-check release-check build clean release'

lint:
	$(PYTHON) -m ruff check --no-cache src tests tools
	$(PYTHON) -m ruff format --check --no-cache src tests tools

# Invoked as a command, not as `python3 -m pyright`: the distribution package
# and the npm build both provide the executable, but only the pip wrapper
# provides an importable module.
typecheck:
	pyright

# The launcher tests execute a staged copy of usr/bin/big-remote-play, which
# fails on hosts that mount /tmp noexec. Keep pytest's temporary tree on a
# filesystem that allows execution.
TEST_TMPDIR ?= $(HOME)/.cache/big-remote-play/pytest

test:
	@mkdir -p '$(TEST_TMPDIR)'
	TMPDIR='$(TEST_TMPDIR)' GDK_BACKEND=x11 xvfb-run -a dbus-run-session -- $(PYTHON) -m pytest -q -p no:cacheprovider

translations-check:
	$(PYTHON) tools/i18n/validate_catalogs.py

metadata-check:
	@set -eu; \
	for f in usr/share/applications/*.desktop; do desktop-file-validate "$$f"; done; \
	for f in usr/share/metainfo/*.xml; do appstreamcli validate --no-net "$$f"; done

release-check:
	$(PYTHON) tools/release_check.py
	$(PYTHON) tools/release/check_source_tree.py
	$(PYTHON) -c 'from pathlib import Path; files=[p for d in ("src", "tests", "tools") for p in Path(d).rglob("*.py")]; [compile(p.read_bytes(), str(p), "exec") for p in files]; print("Python syntax:", len(files), "files OK")'
	@set -eu; find usr -type f -name '*.sh' -exec bash -n {} \;
	bash -n pkgbuild/PKGBUILD

build:
	$(PYTHON) -m build --no-isolation

clean:
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache .pyright .coverage htmlcov
	find src tests tools -type d -name __pycache__ -prune -exec rm -rf {} +
	find src tests tools -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

# Recursive makes enforce order even when the caller uses -j. Nothing is
# published or tagged, and no developer virtual environment is removed.
release: clean
	$(MAKE) release-check
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) translations-check
	$(MAKE) metadata-check
	$(MAKE) test
	$(MAKE) build
