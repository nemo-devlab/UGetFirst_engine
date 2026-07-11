"""Whole-word, case-insensitive keyword matching (multi-word phrases allowed)."""
from __future__ import annotations

import re
from functools import lru_cache

_WRAP_QUOTES_RE = re.compile(r"""^[\s"'“”‘’«»]+|[\s"'“”‘’«»]+$""")
_ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200D\uFEFF]")
# Explicit list separators only — spaces mean phrase, not list.
_LIST_SPLIT_RE = re.compile(r"[,*;|\n\r]+")

MAX_KEYWORD_LENGTH = 60


def normalize_keyword(raw: str) -> str:
    """Strip wrapping quotes, zero-width chars, and collapse whitespace.

    Does not split on spaces or list separators — phrases stay intact.
    """
    if not raw:
        return ""
    text = _ZERO_WIDTH_RE.sub("", raw)
    text = _WRAP_QUOTES_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def soft_split_keywords(raw: str) -> list[str]:
    """Split on explicit list separators only; spaces stay as phrases."""
    if not raw:
        return []
    parts = _LIST_SPLIT_RE.split(raw) if _LIST_SPLIT_RE.search(raw) else [raw]
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        kw = normalize_keyword(part)
        if not kw or len(kw) > MAX_KEYWORD_LENGTH:
            continue
        key = kw.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(kw)
    return out


@lru_cache(maxsize=1024)
def _pattern(keyword: str) -> re.Pattern | None:
    """Compile a whole-word, case-insensitive matcher for a keyword.

    - Word-boundary lookarounds prevent partial-word hits ("art" won't match
      "started"), while still working when the keyword starts/ends with a
      non-word character.
    - Multi-word phrases match across flexible whitespace ("for sale" matches
      "for  sale" / "for\\nsale").
    """
    kw = normalize_keyword(keyword)
    if not kw:
        return None
    # Escape the keyword, then let any run of whitespace match flexibly.
    escaped = r"\s+".join(re.escape(tok) for tok in kw.split())
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def first_match(text: str, keywords: list[str]) -> str | None:
    """Return the first keyword whose whole-word form appears in text, else None.

    Keywords are returned lightly normalized (quotes/whitespace cleaned).
    """
    if not text:
        return None
    for kw in keywords:
        pattern = _pattern(kw)
        if pattern and pattern.search(text):
            return normalize_keyword(kw) or kw
    return None
