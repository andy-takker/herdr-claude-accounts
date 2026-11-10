# Compatibility and verification

| Component | Requirement / local validation |
| --- | --- |
| OS | macOS; local integration checked on Apple Silicon |
| Python | 3.9+; local full suite checked with Python 3.9 |
| Claude Code | `auth`, `agents --json`, control-protocol usage; locally checked with 2.1.322 |
| Herdr TUI | 0.9.5+ manifest requirement; locally checked with 0.9.5 |
| Herdr app | Locally checked with 0.1.14 and its native mod checker |
| Node | 22+ for development tests only |

Herdr TUI and Herdr app use different configuration and extension mechanisms.
The project is macOS-targeted even when backend tests run on a Linux CI runner.
Do not infer Windows support from Python portability: file locks, process
signals and terminal integration use POSIX facilities.

Local validation before publication includes 92 Python tests, the JavaScript
native-UI harness, Herdr's mod checker, temporary PTY flows and synthetic
process-signal tests. History migration checks cover conflicts, backups, active
writers, new files during copying, and interrupted cutover. The local source
relocation is checked through the installed launcher and linked TUI manifest.

CI is configured for Python 3.9/3.13 on macOS and Python 3.13 on Linux with Node
24. It must actually complete on GitHub before any hosted-CI success is claimed.
The Herdr desktop app is not installed in CI; its harness is not a replacement
for a manual host smoke test.

Usage's control protocol may change between Claude releases. Unsupported or
invalid output is shown as unavailable, never as unused quota. MFA/browser login,
MCP OAuth and organization policy remain Claude/provider responsibilities.

Before a release, manually verify browser sign-in/cancellation, profile switching,
resume in the original project, usage refresh, removal/restoration and both host
entrypoints. Run with synthetic data where possible. Sustained native-app memory
profiling, power-loss recovery and every Claude version are not covered.
