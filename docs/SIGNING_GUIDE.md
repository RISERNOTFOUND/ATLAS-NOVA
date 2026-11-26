# ATLAS-NOVA Release Signing Guide

Created by RISERNOTFOUND.  
Developed with AI-assisted coding tools.  
Project direction, integration, debugging and testing by RISERNOTFOUND.

## Quick Start

1. Double-click `SIGN_ATLAS_NOVA.bat` in the `ATLAS-NOVA` root directory.
2. Choose a signing method:
   - **1) GPG**: Uses `gpg --armor --detach-sign` to create `ATLAS-NOVA-PUBLIC-RELEASE.zip.asc` and verifies it immediately.
   - **2) SSH**: Uses `ssh-keygen -Y sign` to create `ATLAS-NOVA-PUBLIC-RELEASE.zip.sig`.
   - **3) Checksum-only**: Generates `ATLAS-NOVA-PUBLIC-RELEASE.zip.sha256` without a digital signature.
3. All outputs are written to `RELEASE\`:
   - `ATLAS-NOVA-PUBLIC-RELEASE.zip`
   - `ATLAS-NOVA-PUBLIC-RELEASE.zip.sha256`
   - `ATLAS-NOVA-PUBLIC-RELEASE.zip.asc` or `.sig` (if signed)
   - `RELEASE_MANIFEST.txt`
   - `VERIFY_RELEASE.bat`

## Verifying a Release

Recipients can double-click `RELEASE\VERIFY_RELEASE.bat` to verify the SHA-256 checksum and GPG signature automatically.
