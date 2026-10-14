from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.discovery.constraints import enforce_domain_uniqueness  # noqa: E402
from norway_company_agent.web.first_party import assess_g4_ownership  # noqa: E402


def _profile(
    org: str,
    name: str,
    declared_url: str,
    *,
    email: str,
    postcode: str = "1366",
    legal_form: str = "AS",
    municipality: str = "Bærum",
) -> dict:
    return {
        "organisation_number": org,
        "name": name,
        "website": declared_url,
        "email": email,
        "postal_code": postcode,
        "municipality": municipality,
        "legal_form": legal_form,
        "raw": {"hjemmeside": declared_url, "epostadresse": email},
        "evidence": {},
    }


def _page(requested_url: str, final_url: str, title: str, text: str, *, org_numbers: list[str] | None = None) -> dict:
    return {
        "status": "available",
        "source_url": requested_url,
        "value": {
            "requested_url": requested_url,
            "final_url": final_url,
            "title": title,
            "description": title,
            "main_text_excerpt": text,
            "pages": [],
            "status_code": 200,
            "identity_assessment": {"publishable": False, "score": 0.3, "page_organisation_numbers": org_numbers or []},
        },
    }


def _g3(profile: dict, page: dict, *, overrides: dict | None = None) -> dict:
    value = page["value"]
    final_domain = value["final_url"].split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")
    signals = {
        "identity_verified": False,
        "blocked_host": False,
        "directory_marker": False,
        "listing_path_marker": False,
        "contradicted": False,
        "group_related_only": False,
        "third_party_marker": False,
        "three_or_more_org_numbers": False,
        "parking_marker": False,
        "delegated_contact": False,
        "sports_name_only": False,
        "registry_email_domain_match": profile["email"].rsplit("@", 1)[-1] == final_domain,
        "phone_match": False,
        "address_match": False,
        "legal_org_number_match": False,
        "structured_organisation_number_match": False,
        "other_page_organisation_numbers": 0,
    }
    signals.update(overrides or {})
    return {"publishable": False, "status": "insufficient_evidence", "signals": signals}


def _assess(profile: dict, page: dict, *, allow_registry_floor: bool = True, overrides: dict | None = None) -> dict:
    return assess_g4_ownership(
        profile,
        page,
        candidate_source="registry_website",
        g3_assessment=_g3(profile, page, overrides=overrides),
        allow_registry_floor=allow_registry_floor,
    )


def test_registry_abbreviation_page_publishes_with_email_domain_tie() -> None:
    profile = _profile("926790137", "Bærum Eiendomstaksering AS", "https://betakst.no/", email="post@betakst.no", municipality="Oslo")
    page = _page("https://betakst.no/", "https://betakst.no/", "BE Takst", "BE Takst post@betakst.no")

    result = _assess(profile, page)

    assert result["publishable"]
    assert result["g4_signals"]["registry_declared_floor"]["publishable"]


def test_registry_redirect_to_new_domain_publishes_from_declared_host() -> None:
    profile = _profile("923609016", "Appelx AS", "https://appelx.no/", email="hello@studiox.no")
    page = _page("https://appelx.no/", "https://studiox.no/", "StudioX", "StudioX hello@studiox.no")

    result = _assess(profile, page)

    assert result["rule"] == "g4_registry_declared_floor"
    assert result["published_url"] == "https://studiox.no/"


def test_registry_floor_rejects_coop_manager_different_number_directory_and_flag_off() -> None:
    cases = [
        (
            _profile("923609016", "Sameiet St Olav", "https://sameiet.no/", email="post@bate.no", legal_form="ESEK"),
            _page("https://sameiet.no/", "https://bate.no/", "BATE Boligbyggelag", "BATE Boligbyggelag post@bate.no"),
            {},
        ),
        (
            _profile("923609016", "Appelx AS", "https://appelx.no/", email="hello@studiox.no"),
            _page("https://appelx.no/", "https://studiox.no/", "Company directory", "Company directory hello@studiox.no"),
            {},
        ),
        (
            _profile("923609016", "Appelx AS", "https://appelx.no/", email="hello@studiox.no"),
            _page("https://appelx.no/", "https://studiox.no/", "StudioX", "StudioX Org.nr 999 999 999 hello@studiox.no", org_numbers=["999999999"]),
            {},
        ),
        (
            _profile("923609016", "Appelx AS", "https://appelx.no/", email="hello@studiox.no"),
            _page("https://appelx.no/", "https://studiox.no/", "StudioX", "StudioX hello@studiox.no"),
            {"sports_name_only": True},
        ),
    ]
    for profile, page, overrides in cases:
        result = _assess(profile, page, overrides=overrides)
        assert not result["g4_signals"]["registry_declared_floor"]["publishable"]
        assert result["rule"] != "g4_registry_declared_floor"

    redirect_profile = _profile("923609016", "Appelx AS", "https://appelx.no/", email="hello@studiox.no")
    redirect_page = _page("https://appelx.no/", "https://studiox.no/", "StudioX", "StudioX hello@studiox.no")
    disabled = _assess(redirect_profile, redirect_page, allow_registry_floor=False)
    assert not disabled["publishable"]
    assert not disabled["g4_signals"]["registry_declared_floor"]["publishable"]


def test_registry_floor_preserves_branch_veto() -> None:
    profile = _profile("923609016", "Alpha Levanger AS", "https://alpha.no/", email="hello@brand.no", municipality="Levanger")
    page = _page("https://alpha.no/", "https://brand.no/", "Brand", "Brand hello@brand.no")

    result = _assess(profile, page)

    assert result["g4_signals"]["registry_declared_floor"]["branch_site"]
    assert not result["publishable"]


def test_two_registry_floor_claimants_are_both_demoted() -> None:
    profiles = []
    for org, name, declared in (("923609016", "Appelx AS", "appelx.no"), ("923609024", "Anotherx AS", "anotherx.no")):
        profile = _profile(org, name, f"https://{declared}/", email="hello@studiox.no")
        page = _page(f"https://{declared}/", "https://studiox.no/", "StudioX", "StudioX hello@studiox.no")
        result = _assess(profile, page)
        assert result["rule"] == "g4_registry_declared_floor"
        profile["evidence"]["website"] = deepcopy(page)
        profile["evidence"]["website_g4"] = {"first_party_gate": result, "evidence": deepcopy(page)}
        profile["claims"] = {"official_website_g4": {"final_url": "https://studiox.no/"}}
        profiles.append(profile)

    conflicts = enforce_domain_uniqueness(profiles)

    assert len(conflicts) == 2
    assert all("official_website_g4" not in profile["claims"] for profile in profiles)
