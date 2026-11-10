PYTHON ?= python3
NODE ?= node

.PHONY: check test package

check: test
	git diff --check

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v
	$(NODE) tests/test_herdr_app.cjs

package:
	mkdir -p dist
	git archive --format=tar.gz --prefix=herdr-claude-accounts/ --output=dist/herdr-claude-accounts.tar.gz HEAD
