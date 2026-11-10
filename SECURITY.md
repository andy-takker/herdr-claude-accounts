# Security

The latest source revision is the supported development version. No public
security review or independent audit certification is claimed.

Do not post credentials, tokens, Keychain exports, conversation files or the
manager data directory in a public issue. Use GitHub's private vulnerability
reporting for this repository if enabled. If it is unavailable, open only a
request for a private contact channel, without exploit details or private data.

The manager invokes your installed Claude executable. It does not replace
Claude's authentication, organization policy, or trust decisions. Managed
profiles reject conflicting authentication environment variables. Secrets and
live-process registries remain profile-specific; shared history is opt-in.

Backups are private local files and may contain sensitive conversation/settings
content. They are retained to support recovery and are not safe to publish.
Removal hides a profile and keeps its data; it does not revoke credentials.

Report the affected version, OS, installation method, minimal synthetic
reproduction and expected impact. Keep any real account material out of it.
