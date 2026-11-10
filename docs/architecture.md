# Architecture

The same Python backend serves the native app, terminal overlay and shell.
No background service is installed.

| Component | Responsibility |
| --- | --- |
| `accounts.py` | Private profile registry, selection, removal/restoration and atomic metadata |
| `launcher.py`, `shell_install.py` | Resolve one profile per launch and preserve shell integration |
| `claude_cli.py`, `login_attempt.py` | Native CLI calls, process cleanup and bounded login cancellation |
| `status_cache.py`, `usage.py` | Shared polling caches and stale-result protection |
| `shared_settings.py` | Reconcile allowed settings while retaining private identity/runtime fields |
| `shared_conversations.py` | Validate, back up, merge and link opt-in conversation context |
| `manager.py` | JSON CLI and operation orchestration |
| `ui.py` | Curses UI with an owned background worker |
| `herdr-app/main.js` | Host-native dialog/sidebar; serialized mutations and nonblocking polling |
| `herdr-plugin.toml` | Herdr TUI action and overlay entrypoint |

## Identity and context

A profile ID is a UUID, not an email. Claude's local auth status supplies the
email/organization when available. System default uses the normal system profile.
Switching affects new processes; a running process keeps its original account.

Settings sharing copies a restricted set of preferences and links extension
directories. History sharing is an explicit migration into the system source.
It links resumable data while preserving separate credentials and process
registries. Conflicting histories fail before cutover. Active profiles import
context but defer replacement of their original directories until safe.

## Concurrency and cancellation

Registry writes use a process lock and atomic replacement. Status probes use a
15-second shared cache with per-profile OS locks; concurrent callers reuse a
snapshot. Authentication generations stop old checks publishing after re-login.
Usage has a five-minute identity-bound cache and unique request reservations;
cancellation cannot overwrite a newer result or reserve a failed poll for five
minutes. Manual usage refresh bypasses that account's TTL only.

Native GUI background responses carry a revision guard. Highlighting follows
account IDs rather than array indices. Mutations remain serialized while
background polling leaves controls usable. Terminal polling belongs to one
worker and is cancelled at shutdown. CLI children have explicit process-group
cleanup; SIGKILL and power loss cannot execute Python cleanup handlers.

## Runtime data

The private manager directory is outside the repository. Metadata, caches,
backups and conversation files must never enter a source archive. There is no
credential export feature and no telemetry added by the plugin.
