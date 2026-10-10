from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.identity import assess_website_identity  # noqa: E402
from norway_company_agent.web.first_party import assess_g4_ownership  # noqa: E402

ORG = "923609016"


def _declared(name: str, domain: str, title: str, text: str, identity: dict) -> tuple[dict, dict]:
    profile = {"organisation_number": ORG, "name": name, "website": f"https://{domain}/", "raw": {"hjemmeside": f"https://{domain}/", "epostadresse": f"post@{domain}"}}
    page = {"status": "available", "source_url": f"https://{domain}/", "value": {
        "final_url": f"https://{domain}/", "registered_domain": domain, "title": title, "main_text_excerpt": text,
        "status_code": 200, "identity_assessment": identity, "pages": []}}
    return profile, page


def test_registry_declared_publishes_when_name_identity_fails() -> None:
    # Live Work System / Kitchn pages score 0.3 in the identity gate; the declaration plus corroboration decides.
    profile, page = _declared("Work System Norway AS", "worksystem.no", "Hjem", "Work System leverer arbeidsklær og verneutstyr. post@worksystem.no",
                              {"publishable": False, "score": 0.3, "group_or_brand": True})
    result = assess_g4_ownership(profile, page, candidate_source="registry_website")
    assert result["rule"] == "g4_registry_declared"


def test_exact_org_number_from_identity_gate_publishes() -> None:
    profile = {"organisation_number": ORG, "name": "Takstolfabrikken AS", "website": "", "raw": {}}
    page = {"status": "available", "source_url": "https://takstolfabrikken.com/", "value": {
        "final_url": "https://takstolfabrikken.com/", "registered_domain": "takstolfabrikken.com", "title": "Takstolfabrikken",
        "main_text_excerpt": "Takstoler levert over hele landet", "status_code": 200, "pages": [],
        "identity_assessment": {"publishable": True, "score": 1.0, "page_organisation_numbers": [ORG]}}}
    result = assess_g4_ownership(profile, page, candidate_source="name_derived_distinctive_com")
    assert result["rule"] == "g4_exact_org_number"


def test_cooperative_manager_page_needs_own_number_or_full_name() -> None:
    profile, page = _declared("Sameiet St Olav", "bate.no", "BATE Boligbyggelag", "Sameiet og borettslag forvaltning. St. Olavs gate. post@bate.no",
                              {"publishable": False, "score": 0.3})
    assert assess_g4_ownership(profile, page, candidate_source="registry_website")["rule"] != "g4_registry_declared"
    page["value"]["main_text_excerpt"] += " Velkommen til Sameiet St Olav"
    assert assess_g4_ownership(profile, page, candidate_source="registry_website")["rule"] == "g4_registry_declared"


def test_own_legal_name_is_not_a_foreign_brand() -> None:
    profile = {"organisation_number": ORG, "name": "RSV GRUPPEN AS", "raw": {}, "evidence": {"website": {"status": "available", "value": {
        "final_url": "https://rsvgruppen.no/", "title": "Hjem - rsvgruppen", "main_text_excerpt": "RSV Gruppen leverer renhold og vaktmestertjenester i hele regionen."}}}}
    assert not assess_website_identity(profile)["group_or_brand"]


def test_brand_page_with_only_shared_contact_details_is_not_published() -> None:
    # Gran Boligbygg -> mollereiendom.no: parent's site, only a shared postcode/phone.
    profile, page = _declared("Gran Boligbygg AS", "mollereiendom.no", "Møller Eiendom", "Møller Eiendom utvikler boliger. post@mollereiendom.no",
                              {"publishable": False, "score": 0.3, "group_or_brand": True})
    assert assess_g4_ownership(profile, page, candidate_source="registry_website")["rule"] != "g4_registry_declared"
