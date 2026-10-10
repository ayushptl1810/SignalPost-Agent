#!/usr/bin/env python3
"""Fetch bounded page evidence for independently researched annotations."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.core.identity import name_tokens  # noqa: E402
from norway_company_agent.web.discovery import BLOCKED_DISCOVERY_HOSTS  # noqa: E402
from norway_company_agent.web.website import fetch_website, registered_domain  # noqa: E402


SOCIAL_HOSTS = {"facebook.com", "instagram.com", "linkedin.com", "no.linkedin.com", "youtube.com", "x.com", "twitter.com"}
NON_COMPANY_DOMAINS = {
    "virksomhet.brreg.no", "brreg.no", "proff.no", "gulesider.no", "1881.no", "1850.no", "firmadatabasen.no",
    "vexter.no", "opplysning.byndle.no", "purehelp.no", "forvalt.no", "norgelei.no", "fagfolkguiden.no",
    "firmabasen.no", "listings.no", "lokalebedrifter.no", "regnskapstall.no", "aksjegrafen.com", "yra.no",
    "sokfirma.no", "northdata.com", "firmaportalen.no", "foretaksinfo.no", "foretak.io.no", "io.no", "unrealfund.com",
    "regnskapsforere.no", "bedriftsoversikten.no", "kredittverdi.no", "norskefirma.no", "generate.no", "oljelandet.no",
    "soom.no", "finansavisen.no", "creditsafe.com", "dnb.com", "cylex.no", "headly.com", "tillo.no", "firmview.no",
    "opplaeringskontoret.no", "vilbli.no", "haandverkere.no", "haandverkerportalen.no", "renholdsplassen.no",
    "renholdere.no", "eiendomsrenhold.no", "mesterbedrifter.no", "tannlegeplassen.no", "tannlege.nu", "legelisten.no",
    "legebiblioteket.no", "felleskatalogen.no", "travelcamp.no", "kursagenten.no", "mittanbud.no", "utdanning.no",
    "norskeutslipp.no", "tinn.kommune.no", "rablad.no", "mn24.no", "bt.no", "aftenbladet.no", "adressa.no", "ht.no",
    "karmoynytt.no", "fosna-folket.no", "ostlendingen.no", "laagendalsposten.no", "derdubor.no", "berlingske.no",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def candidate_score(row: dict[str, Any], result: dict[str, Any]) -> float:
    parsed = urllib.parse.urlparse(result.get("url") or "")
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    domain = registered_domain(result.get("url") or "")
    tokens = set(name_tokens(row.get("name")))
    host_tokens = set(name_tokens(host.replace(".", " ").replace("-", " ")))
    title_tokens = set(name_tokens(result.get("title")))
    score = 0.0
    if domain and domain == registered_domain(row.get("registry", {}).get("regsite") or ""):
        score += 4.0
    score += min(2.0, len(tokens & host_tokens) * 0.8)
    score += min(1.5, len(tokens & title_tokens) * 0.5)
    if parsed.path in {"", "/"}:
        score += 0.6
    score -= (result.get("rank") or 99) * 0.01
    return score


def selected_candidates(row: dict[str, Any], *, max_per_company: int) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for result in row.get("results") or []:
        url = result.get("url") or ""
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        domain = registered_domain(url)
        if not domain or host in SOCIAL_HOSTS or domain in SOCIAL_HOSTS:
            continue
        if any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_DISCOVERY_HOSTS):
            continue
        if host in NON_COMPANY_DOMAINS or domain in NON_COMPANY_DOMAINS:
            continue
        result = {**result, "selection_score": candidate_score(row, result)}
        previous = selected.get(domain)
        if previous is None or result["selection_score"] > previous["selection_score"]:
            selected[domain] = result
    return sorted(selected.values(), key=lambda item: (-item["selection_score"], item.get("rank") or 99, item.get("url") or ""))[:max_per_company]


def fetch_one(row: dict[str, Any], result: dict[str, Any], timeout: float) -> dict[str, Any]:
    website, operations = fetch_website(result["url"], timeout=timeout, max_bytes=1_000_000)
    return {
        "organisation_number": row["organisation_number"],
        "name": row["name"],
        "split": row.get("split"),
        "stratum": row.get("stratum"),
        "search_result": result,
        "website": website,
        "operations": operations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch bounded page evidence for annotation review.")
    parser.add_argument("--input", required=True, help="Search evidence JSONL")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-per-company", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    rows = read_jsonl(Path(args.input))
    jobs = [(row, result) for row in rows for result in selected_candidates(row, max_per_company=args.max_per_company)]
    fetched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, row, result, args.timeout): (row, result) for row, result in jobs}
        for future in as_completed(futures):
            row, result = futures[future]
            try:
                fetched.append(future.result())
            except Exception as exc:
                fetched.append({
                    "organisation_number": row["organisation_number"],
                    "name": row["name"],
                    "split": row.get("split"),
                    "stratum": row.get("stratum"),
                    "search_result": result,
                    "website": {"status": "source_error", "source_url": result.get("url"), "error": type(exc).__name__},
                    "operations": {"requests": 0},
                })
    fetched.sort(key=lambda item: (str(item["organisation_number"]), item["search_result"].get("url") or ""))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in fetched), encoding="utf-8")
    print(json.dumps({"companies": len(rows), "page_jobs": len(jobs), "fetched": len(fetched), "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
