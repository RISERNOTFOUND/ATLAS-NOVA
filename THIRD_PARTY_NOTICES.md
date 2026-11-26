# Third-party notices

ATLAS & NOVA (MIT, © 2026 RISERNOTFOUND) install these separately; none are bundled in this repository. Verify each license upstream.

| Component | Use | License (per upstream, verify) |
|---|---|---|
| discord.py | Discord API | MIT |
| aiohttp, PyNaCl, tzdata | networking, voice encryption, timezones | Apache-2.0 |
| onnxruntime | runs Piper voices | MIT |
| **piper-tts** | text-to-speech engine | **GPL-3.0** |
| FFmpeg (downloaded by `install_assets.py` on Windows) | audio encoding | build-dependent (the downloaded build is GPL) |
| Piper voice models (downloaded on first use) | speech voices | each voice has its own license: check its model card |
| LM Studio + a model of your choice (optional) | local AI | your choice; not included |

## Piper licensing review (checked 2026-11-26)
- `piper-tts` (project OHF-Voice/piper1-gpl, the maintained release) is **GPL-3.0** (listed GPL-3.0-or-later by distributions). The older MIT-licensed `rhasspy/piper` is archived and is not what `requirements.txt` installs.
- This repository contains only our own MIT-licensed source; it does **not** include Piper, espeak-ng or any voice, and users install them via pip/download. Publishing source that merely imports Piper is a common pattern and MIT remains acceptable for it. MIT is GPL-compatible, so our code can be combined into a GPL-3.0 whole.
- **Obligations if you redistribute a bundle** (installer, Docker image, portable zip) containing Piper: that bundle must meet GPL-3.0 (license text, corresponding source offer). Do not bundle Piper or voices in the repository or GitHub release assets without doing so.
- Whether importing a GPL library makes the running program a derivative work is debated; if you want zero doubt, relicense this project GPL-3.0 or make TTS an optional separate install. Voice models carry their own licenses (check each model card: en_US-lessac, en_US-amy, hi_IN-priyamvada were not individually verified here).
- Conclusion: **MIT is appropriate for the source as published**, with the caveats above. This is not legal advice.
