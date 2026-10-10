from __future__ import annotations

import re
from typing import Any

# Norwegian organisation numbers are nine digits; the last is a mod-11 check digit.
_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
# Nine digits, optionally grouped 3-3-3 with space/dot/nbsp, never part of a longer digit run.
_CANDIDATE = re.compile(r"(?<![\d])(\d{3})[ . ]?(\d{3})[ . ]?(\d{3})(?![\d])")
# A "+47 ..." style prefix means the digits that follow are a phone number, not an org number.
_PHONE_PREFIX = re.compile(r"(?:\+|00)\s*\d{1,3}[\s-]*$")


def digits_only(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def is_valid_org_number(value: Any) -> bool:
    digits = digits_only(value)
    if len(digits) != 9:
        return False
    total = sum(int(d) * w for d, w in zip(digits[:8], _WEIGHTS))
    remainder = total % 11
    check = 0 if remainder == 0 else 11 - remainder
    return check != 10 and check == int(digits[8])


def extract_org_numbers(text: Any) -> set[str]:
    """Return checksum-valid organisation numbers found as standalone tokens in text.

    Raw digit-run matching is deliberately avoided: stripping all non-digits from a
    page and searching for a substring lets adjacent phone numbers, postcodes and
    prices concatenate into a false match.
    """
    haystack = str(text or "")
    found: set[str] = set()
    for match in _CANDIDATE.finditer(haystack):
        if _PHONE_PREFIX.search(haystack[: match.start()][-8:]):
            continue
        candidate = "".join(match.groups())
        if is_valid_org_number(candidate):
            found.add(candidate)
    return found
