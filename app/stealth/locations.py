"""Open meeting places; the campus gates are aliases, not a whitelist."""

import re
import unicodedata

ALIASES = {
    "main_gate": ("政大正門", "正門"),
    "east_gate": ("政大東側門", "東側門", "東門"),
    "south_gate": ("政大南門", "南門"),
}


def normalize_location(value: str) -> str:
    value = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()
    if not value or len(value) > 120 or any(ord(c) < 32 for c in value):
        raise ValueError("invalid meeting place")
    return next((key for key, aliases in ALIASES.items() if value in aliases), value)
