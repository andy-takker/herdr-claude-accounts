# Changelog

## Unreleased — 0.1.0

Initial public-source preparation. No GitHub release has been published yet.

- Independent Claude account profiles, browser sign-in and per-attempt cancellation.
- Native Herdr app dialog, optional sidebar indicator, TUI overlay and standalone CLI.
- Shared shell selection with pinned launches and preserved zsh/fish configuration.
- Shared settings and optional migration of existing conversation context.
- Per-account quotas, reset times, manual refresh and persistent usage caching.
- Collapsible active sessions, retained removal and restoration.
- Shared status cache, stale-result guards and identity-bound online checks.
- Process cleanup on termination signals and interruption before paid checks.
- Regression coverage for concurrency, migration, cancellation and PTY behavior.
- Recreate a missing shared context directory when profile links still point to
  the expected location, so a removed `tasks` directory does not block switching.
