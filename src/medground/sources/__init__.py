"""Source-specific async clients. Each module returns `Paper` records and nothing else.

Source modules MUST NOT touch storage. They fetch, parse, normalize, and return. This keeps
the ingestion pipeline composable and lets us add sources without disturbing the store layer.
"""
