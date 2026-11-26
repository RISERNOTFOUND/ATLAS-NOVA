"""Shared building blocks for ATLAS and NOVA (storage, settings, permissions, UI, logging).

Each bot runs as its own process and opens its OWN database file, so the two bots never
share live state: ATLAS uses atlas.db and NOVA uses nova.db.
"""
