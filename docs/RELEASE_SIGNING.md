# Signing the release (SSH signing, Git ≥ 2.34)

Never commit or share a private key (`id_ed25519`, no `.pub`). Only the `.pub` file is public.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/atlas_nova_signing -C "atlas-nova-release"   # set a passphrase
git config user.name "RISERNOTFOUND"
git config user.email "<ID>+<username>@users.noreply.github.com"   # GitHub no-reply address: hides your real email
git config gpg.format ssh
git config user.signingkey ~/.ssh/atlas_nova_signing.pub
git config commit.gpgsign true && git config tag.gpgsign true
echo "<that no-reply email> $(cat ~/.ssh/atlas_nova_signing.pub)" > ~/.ssh/allowed_signers
git config gpg.ssh.allowedSignersFile ~/.ssh/allowed_signers
```
GitHub → Settings → SSH and GPG keys → **New SSH key**, Key type **Signing Key**, paste the `.pub` contents.

Sign and verify:
```bash
git commit -S -m "Initial public release"
git tag -s v1.0.0 -m "ATLAS & NOVA 1.0.0"
git verify-commit HEAD            # expect: Good "git" signature
git verify-tag v1.0.0
git log --show-signature -1
```
Verified means `git verify-*` reports a good signature and GitHub shows the **Verified** badge (the commit email must match a verified email on your GitHub account, or its no-reply address).
Optionally sign the checksum file: `ssh-keygen -Y sign -f ~/.ssh/atlas_nova_signing -n file ATLAS-NOVA-PUBLIC-RELEASE.zip.sha256` and publish the `.sig`.
