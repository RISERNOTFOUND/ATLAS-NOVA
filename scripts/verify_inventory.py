#!/usr/bin/env python3
"""Cross-checks FEATURES.md and docs/command_inventory.json against the source (regex-based, independent of the AST extractor).
Run from anywhere: python scripts/verify_inventory.py   (exit 1 on any mismatch)"""
import json, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; os.chdir(ROOT)
src = {p: p.read_text(encoding="utf-8") for p in ROOT.rglob("*.py") if "tests" not in p.parts and "scripts" not in p.parts and p.name not in ("ai_approval.py", "install_assets.py")}
rows = json.load(open("docs/command_inventory.json", encoding="utf-8"))
feat = Path("FEATURES.md").read_text(encoding="utf-8")
errors = []
# 1) independent count of declarations
dec = re.compile(r"^\s*@[\w.]+\.(?:command|hybrid_command|group|hybrid_group)\(", re.M)
n_dec = sum(len(dec.findall(t)) for t in src.values())
n_menu = sum(len(re.findall(r"ContextMenu\(name=", t)) for t in src.values())
n_handlers = len(re.findall(r'\("\w+", handle_\w+\)', src[ROOT / "nova.py"]))
expected = n_dec + n_menu + n_handlers
print(f"regex count: {n_dec} decorators + {n_menu} context menus + {n_handlers} NOVA handlers = {expected}; inventory rows = {len(rows)}")
if expected != len(rows): errors.append("declaration count differs from inventory")
# 2) every row's name/aliases exist in its source file; diag commands (f-string names) are checked via common/diag.py
for r in rows:
    text = src.get(ROOT / r["file"], "")
    last = r["name"].split(" ")[-1]
    if r["file"] == "common/diag.py":
        text = src[ROOT / "common/diag.py"]; ok = re.search(r'name=f"\{p\}%s"' % re.escape(r["fn"].replace("_cmd", "").replace("config_export", "configexport").replace("config_import", "configimport").replace("config_backups", "configbackups")), text) or re.search(r'(?:name|\w+)\s*=\s*"%s"' % re.escape(last), text) or f'"{last}"' in text
    elif r["kind"] == "context-menu": ok = f'name="{last}"' in text or f'"{r["name"]}"' in text
    else: ok = (f'"{last}"' in text) or (f"def {r['fn']}" in text)
    if not ok: errors.append(f"name not found in source: {r['file']}: {r['name']}")
    for a in r["aliases"]:
        if f'"{a}"' not in text and r["file"] != "common/diag.py": errors.append(f"alias not found: {r['file']}: {a}")
# 3) every row is documented
for r in rows:
    if r["file"] == "common/diag.py": continue
    nm = r["name"]
    if not any(t in feat for t in (f"`!{nm}`", f"`/{nm}`", f"**{nm}**")): errors.append(f"not in FEATURES.md: {nm}")
# 4) modules registered in code vs documented config panels
mods = set()
for p, t in src.items(): mods |= set(re.findall(r'Module\(\s*"(\w+)"', t))
for m in sorted(mods):
    if not re.search(r"config[ ]+%s\b" % m, feat): errors.append(f"module '{m}' has no `!config {m}` mention in FEATURES.md")
print(f"modules registered in code: {len(mods)} -> {sorted(mods)}")
# 5) extension files loaded exist
for lst in re.findall(r'"((?:atlas|nova)_ext\.\w+)"', src[ROOT / "bot.py"] + src[ROOT / "nova.py"]):
    if not (ROOT / (lst.replace(".", "/") + ".py")).exists(): errors.append(f"extension file missing: {lst}")
print("PASS: inventory matches source" if not errors else "FAIL:\n  " + "\n  ".join(errors))
sys.exit(1 if errors else 0)
