"""Whole-word, case-insensitive keyword matching (multi-word phrases allowed)."""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=1024)
def _pattern(keyword: str) -> re.Pattern | None:
    """Compile a whole-word, case-insensitive matcher for a keyword.

    - Word-boundary lookarounds prevent partial-word hits ("art" won't match
      "started"), while still working when the keyword starts/ends with a
      non-word character.
    - Multi-word phrases match across flexible whitespace ("for sale" matches
      "for  sale" / "for\\nsale").
    """
    kw = keyword.strip()
    if not kw:
        return None
    # Escape the keyword, then let any run of whitespace match flexibly.
    escaped = r"\s+".join(re.escape(tok) for tok in kw.split())
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def first_match(text: str, keywords: list[str]) -> str | None:
    """Return the first keyword whose whole-word form appears in text, else None.

    Keywords are returned with their original casing.
    """
    if not text:
        return None
    for kw in keywords:
        pattern = _pattern(kw)
        if pattern and pattern.search(text):
            return kw
    return None
