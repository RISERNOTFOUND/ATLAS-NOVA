# Security

**Created by RISERNOTFOUND.**

- Never commit `.env`, `token.txt`, `token2.txt`, databases, `config.json` or logs (all git-ignored). If a token leaks, reset it in the Discord Developer Portal immediately.
- Report vulnerabilities privately using GitHub's "Report a vulnerability" (Security tab). Do not open public issues for them.
- Design: no shell/eval commands; tokens are never shown by diagnostics; destructive commands need typed confirmation; verification/automod never ban or kick automatically.
- Run `python scripts/scan_secrets.py` before every push.
