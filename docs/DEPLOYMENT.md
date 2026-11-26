# Publishing to GitHub (after your approval)

1. Verify the download: `sha256sum -c ATLAS-NOVA-PUBLIC-RELEASE.zip.sha256` (Windows: `certutil -hashfile ATLAS-NOVA-PUBLIC-RELEASE.zip SHA256`, compare with the file).
2. Unzip, `cd ATLAS-NOVA`, run `pip install -r requirements.txt pytest pytest-asyncio && pytest -q tests` (expect 34 collected), then start both bots once.
3. Set up signing: [RELEASE_SIGNING.md](RELEASE_SIGNING.md).
4. Create an **empty** GitHub repo `ATLAS-NOVA` (no README/licence; check it doesn't already exist). Description: *Two modular Discord bots featuring moderation, community management, role automation, temporary voice channels, analytics, integrations and more. Created by RISERNOTFOUND.* Topics: `discord-bot python discord-py moderation automation community-management`.
5. `python scripts/scan_secrets.py` (expect only the fake `1111…` test IDs), then:
```bash
git init -b main && git add . && git status      # confirm no .env / tokens / data files
git commit -S -m "Initial public release"
git remote add origin https://github.com/<you>/ATLAS-NOVA.git && git push -u origin main
git tag -s v1.0.0 -m "ATLAS & NOVA 1.0.0" && git push origin v1.0.0
```
6. Create a Release from `v1.0.0`; paste the 1.0.0 notes from CHANGELOG.md. Attach the ZIP and `.sha256` only if you want; do not attach Piper, FFmpeg or voices.
7. Enable Security → Private vulnerability reporting.
