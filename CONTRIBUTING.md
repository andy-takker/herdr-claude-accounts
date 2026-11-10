# Contributing

Use Python 3.9+ and Node 22+. Runtime code uses the Python standard library;
Herdr app provides its JavaScript UI runtime. Do not install a replacement UI
framework or add dependencies for small helpers.

Run `make check` before opening a pull request. The native mod can additionally
be checked locally with `herdr-app check herdr-app`; generated SDK declarations
are ignored and should not be committed. Hosted CI does not run the Herdr app.

For a behavior fix, add a regression test that fails before the change. Use
private temporary stores and fake Claude executables. Never use real OAuth,
Keychain, user histories or paid model calls in tests. Process tests must clean
up only processes they created, restore terminal state and wait for cleanup.

Preserve explicit account routing, cancellation, private identity boundaries,
atomic metadata writes, conflict detection and backups. Changes to shared
history need tests for concurrent writers and interrupted migration. Explain
performance claims with call counts or measurements rather than assumptions.

Keep user-facing strings in English. Update documentation and CHANGELOG for
visible behavior changes. In a PR, describe the trigger, resulting behavior,
validation, and any remaining limitation. Remove private paths, account emails,
conversation excerpts and tokens from issue reports and screenshots.
