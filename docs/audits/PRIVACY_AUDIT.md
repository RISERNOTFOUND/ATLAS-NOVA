# Privacy & secret audit — final ZIP (2026-11-26)

Scope: the extracted final ZIP (83 files). Not shipped: runtime JSON data, databases, logs, backups, the pre-upgrade backup folder, tokens (none existed in the source).
Method: compared the ZIP against ~120 Discord IDs and the original server/category/channel/user names collected from the private data and pasted logs; checked for forbidden files (`.env`, tokens, `*.db`, logs, `config.json`, `__pycache__`); checked for AI-tool names; ran `scripts/scan_secrets.py`.

| Check | Result |
|---|---|
| Private IDs / original server names / usernames | 0 hits |
| Forbidden files (.env, tokens, databases, logs, runtime JSON, caches) | 0 |
| Bot tokens, webhooks, API keys | 0 |
| AI tool names in repository | 0 |
| `scan_secrets.py` | 2 expected hits: obviously fake owner ID in `tests/conftest.py` and `tests/test_flows.py` |
| Old display names (Atlas/Nova) | 0 (internal module/file/DB names and persistent button IDs kept for compatibility) |

Notes: default timezone `Asia/Kolkata` and Hinglish content are product defaults (`COMMUNITY_TIMEZONE` overrides). No git history exists in the ZIP; run `git init` fresh.
