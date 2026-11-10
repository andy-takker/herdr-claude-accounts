PYTHON ?= python3
NODE ?= node

.PHONY: check test package wheel wheel-check

check: test
	git diff --check

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v
	$(NODE) tests/test_herdr_app.cjs

package:
	mkdir -p dist
	git archive --format=tar.gz --prefix=herdr-claude-accounts/ --output=dist/herdr-claude-accounts.tar.gz HEAD

wheel:
	$(PYTHON) -m pip wheel --no-deps --wheel-dir dist .

wheel-check: wheel
	$(PYTHON) scripts/check_wheel.py dist/herdr_claude_accounts-*.whl
