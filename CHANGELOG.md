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
- Redesigned Herdr dialog: account list beside a detail pane; inspecting an account no longer selects it.
- New-session flow (account, folder, new / `--continue` / resume) and per-session “continue with another account”.
- Step-by-step account sign-in with retry; TUI shows the highlighted account's details and a session cursor.
- Sign-in is confirmed even when Claude prints a notice right after login.
- Conversation sharing accepts Claude's internal subagent transcript links in forked sessions.
- Recreate a missing shared context directory when profile links still point to
  the expected location, so a removed `tasks` directory does not block switching.
- Package the backend under `herdr_claude_accounts/` with standard Python
  metadata, module/console entrypoints and a clean-environment wheel check.
- Add README artwork, a switch-and-resume walkthrough, FAQ and Linux test scope.
