# Working in this repository

Use the Python standard library and the host-provided Herdr UI APIs. Read
README.md and docs/architecture.md before changing routing or persistence.
Run `make check`; additionally run `herdr-app check herdr-app` when changing
the native mod and the host is available.

Tests must use temporary stores and fake Claude executables. Do not invoke
real OAuth, paid model requests, or user-profile migrations as test fixtures.
Preserve private credential boundaries, deterministic account routing, terminal
cleanup, cancellation, backups and conflict detection. Add a regression test
for behavior fixes. Do not commit generated SDK declarations or runtime data.

Python code belongs in `herdr_claude_accounts/`. Packaging/entrypoint changes
require `make wheel-check` using a current pip in a virtual environment.
Keep `pyproject.toml` and `herdr-plugin.toml` versions aligned.
