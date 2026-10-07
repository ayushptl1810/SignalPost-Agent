from __future__ import annotations

import re
import unicodedata
from typing import Any

_NORDIC = str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"})
_WORD = re.compile(r"[a-z0-9]+")


def _canonical(token: str) -> str:
    # Pages often spell ø/å as oe/aa. Collapsing the digraphs on both sides makes
    # "Bjørn" and "Bjoern", "Aas" and "Ås" compare equal without a variant search.
    return token.replace("aa", "a").replace("oe", "o")


def ascii_fold(value: Any) -> str:
    text = str(value or "").translate(_NORDIC)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()


def fold_tokens(value: Any) -> list[str]:
    """Lower-case ASCII word tokens with Norwegian letter variants unified."""
    return [_canonical(token) for token in _WORD.findall(ascii_fold(value))]
