from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.identity import assess_website_identity  # noqa: E402
from norway_company_agent.core.orgnumber import extract_org_numbers, is_valid_org_number  # noqa: E402
from norway_company_agent.core.text import fold_tokens  # noqa: E402

TARGET = "923609016"
OTHERS = ["982942942", "985589003", "935095190", "936326005"]  # all checksum-valid


def profile(name: str = "Norsk Fiskeeksport AS") -> dict:
    return {"name": name, "organisation_number": "923 609 016"}


def page(text: str, title: str = "") -> dict:
    return {"evidence": {"website": {"status": "available", "value": {
        "title": title, "main_text_excerpt": text, "identity_text_excerpt": text, "final_url": "https://example.no/",
    }}}}


class OrgNumberTests(unittest.TestCase):
    def test_checksum(self) -> None:
        self.assertTrue(all(is_valid_org_number(o) for o in [TARGET, *OTHERS]))
        self.assertFalse(is_valid_org_number("923609017"))
        self.assertFalse(is_valid_org_number("12345"))

    def test_extraction_formats(self) -> None:
        text = "Org.nr 923 609 016 MVA, NO923609016MVA, 982.942.942, tlf 12 34 56 78"
        self.assertEqual(extract_org_numbers(text), {TARGET, "982942942"})

    def test_adjacent_digits_do_not_concatenate_into_a_match(self) -> None:
        # Previously "tlf 99 92 36 09" + "016 Oslo" stripped to ...923609016 and matched.
        self.assertEqual(extract_org_numbers("tlf 99 92 36 09 016 Oslo"), set())
        self.assertEqual(extract_org_numbers("ref 1923609016"), set())

    def test_phone_prefixed_nine_digits_are_ignored(self) -> None:
        self.assertEqual(extract_org_numbers("Ring +47 923 609 016"), set())


class TextTests(unittest.TestCase):
    def test_nordic_digraph_variants_match(self) -> None:
        self.assertEqual(fold_tokens("Bjørn Åsmund"), fold_tokens("Bjoern Aasmund"))
        self.assertEqual(fold_tokens("Æble"), ["aeble"])


class IdentityOrgNumberTests(unittest.TestCase):
    def test_exact_number_scores_one(self) -> None:
        result = assess_website_identity({**profile(), **page("Kontakt oss. Org.nr 923 609 016 MVA")})
        self.assertEqual(result["score"], 1.0)
        self.assertTrue(result["publishable"])

    def test_concatenated_digits_are_not_identity_proof(self) -> None:
        result = assess_website_identity({**profile("Zzyzx Qwerty AS"), **page("tlf 99 92 36 09 016 Oslo")})
        self.assertNotEqual(result["score"], 1.0)
        self.assertFalse(result["publishable"])

    def test_directory_like_page_with_many_org_numbers_is_downgraded(self) -> None:
        text = f"Org.nr {TARGET} " + " ".join(OTHERS)
        result = assess_website_identity({**profile("Zzyzx Qwerty AS"), **page(text)})
        self.assertEqual(result["score"], 0.85)
        self.assertFalse(result["publishable"])
        self.assertIn("directory-like", result["reasons"][0])

    def test_spelling_variant_of_legal_name_still_matches(self) -> None:
        result = assess_website_identity({**profile("Bjørnstad Håndverk AS"), **page("Velkommen til Bjoernstad Haandverk", "Bjoernstad Haandverk")})
        self.assertTrue(result["publishable"])


if __name__ == "__main__":
    unittest.main()
