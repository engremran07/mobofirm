---
description: "Use when changing Copilot hooks, MCP profiles, workspace AI settings, or provider integrations."
applyTo: [".mcp.json", ".mcp-profiles/**/*.json", ".vscode/settings.json", ".vscode/extensions.json", ".github/hooks/**"]
---
# Copilot Workspace and Provider Configuration

- Keep the root `.mcp.json` to one profile with no more than five servers.
  Alternative files in `.mcp-profiles/` are templates; VS Code Copilot does not
  load them until a user deliberately selects one as the workspace `.mcp.json`.
- Never commit credentials. Use environment-variable references or provider
  OAuth, and do not start a server or authorize an account as part of configuration
  editing.
- PostgreSQL MCP must use `--access-mode=restricted` and a database role whose
  actual grants are read-only. Point it only at an explicitly approved local/test
  database. Restricted MCP mode does not turn an overprivileged database role into
  a read-only role.
- The Redis profile is restricted to read-oriented MCP tools and still must use a
  read-only Redis ACL account on a disposable local instance. Never point it at a
  production broker, cache, session store, or rate-limit database. Its upstream
  server currently requires Python 3.14+, separate from this project's runtime.
- Keep payment-provider, Sentry, and browser-automation configurations in separate
  opt-in profiles. Do not combine payment access with untrusted web content or a
  production database. Use test-mode provider accounts and require human review
  before any write-capable operation.
- Do not add the archived `semgrep/mcp` server or an unverified community GSC
  server. Prefer maintained, publisher-verified tools; report when a requested
  integration cannot be verified.
- Hooks are guardrails, not a security sandbox. Preserve their documented
  camelCase event payload/output shapes, avoid logging tool arguments or results,
  and fail visibly if a check cannot run. Never auto-approve edits or tools.
- Keep extension recommendations limited to tools that match the existing stack.
  Do not configure broad edit auto-approval or weaken workspace trust.
