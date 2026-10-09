#!/usr/bin/env python3
"""Build a self-contained, keyboard-friendly SignalPost HTML report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


AREAS = (
    ("company_brief", "Brief"),
    ("latest_annual_accounts", "Financials"),
    ("leadership", "Leadership"),
    ("registered_workplaces", "Locations"),
    ("hiring", "Hiring"),
    ("public_activity", "Activity"),
)


def _rows(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = []
    for row in rows:
        profile = row.get("profile") or {}
        raw = profile.get("raw") or {}
        claims = row.get("claims") or []
        payload.append({
            "organisation_number": row.get("organisation_number"),
            "name": profile.get("name") or raw.get("navn") or row.get("organisation_number"),
            "municipality": profile.get("municipality") or raw.get("forretningsadresse.kommune") or raw.get("postadresse.kommune") or "",
            "industry": profile.get("industry_label") or raw.get("naeringskode1.beskrivelse") or raw.get("naeringskode1_beskrivelse") or "",
            "claims": claims,
            "evidence": row.get("evidence") or [],
            "unknown_areas": row.get("unknown_areas") or [claim.get("field") for claim in claims if claim.get("availability") not in {"available", "not_applicable"}],
            "changes": row.get("changes") or [],
        })
    return payload


def build_report(rows: list[dict[str, Any]]) -> str:
    data = json.dumps(_payload(rows), ensure_ascii=False, separators=(",", ":"))
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    css = r"""
:root { color-scheme: light; --ink:#17202a; --muted:#5f6b76; --line:#d9e0e7; --accent:#075985; --soft:#f4f7fa; }
* { box-sizing:border-box; }
body { margin:0; font:15px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--ink); background:#fff; }
header { padding:20px clamp(16px,4vw,48px); border-bottom:1px solid var(--line); background:linear-gradient(135deg,#f7fafc,#eef6fb); }
header h1 { margin:0 0 5px; font-size:clamp(1.4rem,3vw,2rem); }
header p { margin:0; color:var(--muted); }
main { max-width:1400px; margin:0 auto; padding:18px clamp(12px,3vw,40px) 48px; }
.toolbar { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:16px; }
label { font-weight:650; }
input[type=search], select { min-height:40px; border:1px solid #aebac5; border-radius:7px; padding:8px 10px; background:#fff; color:var(--ink); }
input[type=search] { flex:1 1 280px; }
button { min-height:40px; border:1px solid #7d8a96; border-radius:7px; padding:8px 12px; background:#fff; color:var(--ink); cursor:pointer; }
button:hover,button:focus-visible { border-color:var(--accent); outline:3px solid #bae6fd; outline-offset:1px; }
.layout { display:grid; grid-template-columns:minmax(250px,330px) 1fr; gap:18px; align-items:start; }
.panel { border:1px solid var(--line); border-radius:10px; background:#fff; overflow:hidden; }
.panel-title { padding:12px 14px; background:var(--soft); border-bottom:1px solid var(--line); font-weight:700; }
#company-list { max-height:calc(100vh - 220px); overflow:auto; }
.company-row { display:block; width:100%; text-align:left; border:0; border-bottom:1px solid var(--line); border-radius:0; padding:12px 14px; }
.company-row[aria-current=true] { background:#e0f2fe; border-left:4px solid var(--accent); padding-left:10px; }
.company-name { font-weight:700; display:block; }
.company-meta,.muted { color:var(--muted); font-size:.88rem; }
.count { float:right; border-radius:999px; background:#dbeafe; color:#0c4a6e; padding:2px 8px; font-size:.78rem; }
.company-head { display:flex; gap:12px; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; }
.company-head h2 { margin:0 0 4px; font-size:1.35rem; }
.company-head p { margin:0; color:var(--muted); }
.unknown { margin:14px 0; padding:10px 12px; background:#fff7ed; border:1px solid #fed7aa; border-radius:7px; }
.unknown strong { color:#9a3412; }
.areas { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; margin-top:14px; }
.area { border:1px solid var(--line); border-radius:8px; padding:12px; min-width:0; }
.area h3 { margin:0 0 8px; font-size:1rem; }
.state { display:inline-block; margin-left:6px; padding:1px 6px; border-radius:999px; font-size:.73rem; font-weight:700; background:#e5e7eb; color:#374151; }
.state.available { background:#dcfce7; color:#166534; }.state.ambiguous { background:#fef3c7; color:#92400e; }.state.not_available,.state.not_checked { background:#f3f4f6; color:#4b5563; }
.fact { padding:9px 0; border-top:1px solid #edf0f2; }.fact:first-of-type { border-top:0; }
.fact-text { white-space:pre-wrap; overflow-wrap:anywhere; }
.evidence { margin-top:5px; color:var(--muted); font-size:.82rem; }
.evidence a { color:var(--accent); overflow-wrap:anywhere; }
.compare-box { margin-top:16px; padding:12px; background:var(--soft); border-radius:8px; }
.compare-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:10px; }
.compare-card { background:#fff; border:1px solid var(--line); border-radius:8px; padding:10px; min-width:0; }
@media (max-width:700px) { .layout { grid-template-columns:1fr; } #company-list { max-height:280px; } .compare-grid { grid-template-columns:1fr; } }
@media (max-width:360px) { body { font-size:14px; } main { padding:10px 8px 30px; } header { padding:14px 10px; } .areas { grid-template-columns:1fr; } input[type=search],select,button { width:100%; } }
"""
    js = r"""
const companies = JSON.parse(document.getElementById('report-data').textContent);
const areaDefs = [%s];
let selectedId = companies[0]?.organisation_number || null;
const selectedCompare = new Set();
const list = document.getElementById('company-list');
const view = document.getElementById('company-view');
const search = document.getElementById('search');
const sort = document.getElementById('sort');
const esc = value => String(value ?? '').replace(/\s+/g, ' ').trim();
const claimMap = company => Object.fromEntries((company.claims || []).map(claim => [claim.field, claim]));
const evidenceMap = company => Object.fromEntries((company.evidence || []).map(row => [row.id, row]));
function factsFor(claim) {
  if (!claim) return [];
  const value = claim.value;
  if (claim.field === 'public_activity') return value?.items || [];
  if (claim.field === 'hiring' && value && typeof value === 'object') return value.postings || value.ads || value.nav_ads || [];
  if (claim.field === 'company_brief' && value && typeof value === 'object' && value.parts) return value.parts;
  if (Array.isArray(value)) return value;
  return value && typeof value === 'object' ? [value] : (value == null ? [] : [value]);
}
function filled(company) { return areaDefs.filter(([field]) => (claimMap(company)[field]?.availability === 'available')).length; }
function companyText(company) { return [company.name, company.organisation_number, company.municipality, company.industry].join(' ').toLowerCase(); }
function filtered() {
  const query = search.value.toLowerCase().trim();
  let rows = companies.filter(company => !query || companyText(company).includes(query));
  rows.sort((a,b) => sort.value === 'filled' ? filled(b)-filled(a) || String(a.name).localeCompare(String(b.name)) : sort.value === 'org' ? String(a.organisation_number).localeCompare(String(b.organisation_number)) : String(a.name).localeCompare(String(b.name)));
  return rows;
}
function renderList() {
  list.replaceChildren();
  for (const company of filtered()) {
    const button = document.createElement('button'); button.className='company-row'; button.type='button'; button.setAttribute('aria-current', String(company.organisation_number === selectedId));
    button.innerHTML = `<span class="count">${filled(company)}/${areaDefs.length}</span><span class="company-name"></span><span class="company-meta"></span>`;
    button.querySelector('.company-name').textContent = company.name;
    button.querySelector('.company-meta').textContent = `${company.organisation_number} · ${company.municipality || 'Municipality unknown'}`;
    button.addEventListener('click', () => { selectedId = company.organisation_number; renderList(); renderCompany(); });
    list.append(button);
  }
  document.getElementById('result-count').textContent = `${filtered().length} of ${companies.length} companies`;
}
function evidenceHtml(company, claim) {
  const ev = evidenceMap(company); const refs = claim?.evidence_ids || [];
  return refs.map(ref => { const row = ev[ref] || {}; const link = row.source_url ? `<a href="${row.source_url}" target="_blank" rel="noopener">source</a>` : 'source unavailable'; return `<div class="evidence">${link} · retrieved ${row.retrieved_at || 'unknown'} · state ${claim.availability}<br><span>${row.claim_span || 'No evidence snippet'}</span></div>`; }).join('');
}
function factText(fact) { if (typeof fact === 'string') return fact; if (!fact || typeof fact !== 'object') return String(fact ?? ''); return fact.text || fact.title || fact.summary || fact.name || JSON.stringify(fact); }
function areaHtml(company, field, label) {
  const claim = claimMap(company)[field]; const state = claim?.availability || 'not_checked'; const facts = factsFor(claim);
  const section = document.createElement('section'); section.className='area';
  section.innerHTML = `<h3>${label}<span class="state ${state}">${state}</span></h3>`;
  if (facts.length) facts.forEach(fact => { const node=document.createElement('div'); node.className='fact'; node.innerHTML=`<div class="fact-text"></div>`; node.querySelector('.fact-text').textContent=factText(fact); node.innerHTML += evidenceHtml(company, claim); section.append(node); });
  else { const node=document.createElement('div'); node.className='fact muted'; node.textContent = state === 'not_available' ? 'No checked fact was available.' : state === 'not_checked' ? 'This area was not checked.' : state === 'blocked' ? 'The source was blocked.' : state === 'failed' ? 'The source failed.' : 'No fact was published.'; section.append(node); }
  return section;
}
function renderCompany() {
  const company = companies.find(item => item.organisation_number === selectedId); if (!company) { view.textContent='No company selected.'; return; }
  view.replaceChildren(); const head=document.createElement('div'); head.className='company-head'; head.innerHTML='<div><h2></h2><p></p></div><div class="muted"></div>'; head.querySelector('h2').textContent=company.name; head.querySelector('p').textContent=`${company.organisation_number} · ${company.municipality || 'Municipality unknown'} · ${company.industry || 'Industry unknown'}`; head.querySelector('.muted').textContent=`${filled(company)} of ${areaDefs.length} data areas filled`; view.append(head);
  const unknown=document.createElement('div'); unknown.className='unknown'; unknown.innerHTML='<strong>What remains unknown:</strong> <span></span>'; unknown.querySelector('span').textContent=(company.unknown_areas || []).length ? company.unknown_areas.join(', ') : 'Nothing marked unknown.'; view.append(unknown);
  const areas=document.createElement('div'); areas.className='areas'; areaDefs.forEach(([field,label]) => areas.append(areaHtml(company,field,label))); view.append(areas);
  const compare=document.createElement('div'); compare.className='compare-box'; compare.innerHTML='<label><input type="checkbox" id="compare-current"> Compare this company</label><div class="compare-grid" id="compare-grid"></div>'; const checkbox=compare.querySelector('#compare-current'); checkbox.checked=selectedCompare.has(company.organisation_number); checkbox.addEventListener('change',()=>{ checkbox.checked ? selectedCompare.add(company.organisation_number) : selectedCompare.delete(company.organisation_number); renderCompare(compare.querySelector('#compare-grid')); }); view.append(compare); renderCompare(compare.querySelector('#compare-grid'));
}
function renderCompare(target) { target.replaceChildren(); [...selectedCompare].slice(0,3).forEach(id => { const company=companies.find(item=>item.organisation_number===id); if (!company) return; const card=document.createElement('div'); card.className='compare-card'; card.innerHTML='<strong></strong><div></div>'; card.querySelector('strong').textContent=company.name; card.querySelector('div').textContent=areaDefs.map(([field,label])=>`${label}: ${claimMap(company)[field]?.availability || 'not_checked'}`).join(' · '); target.append(card); }); }
search.addEventListener('input', renderList); sort.addEventListener('change', renderList); renderList(); renderCompany();
""" % json.dumps(list(AREAS), ensure_ascii=False)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SignalPost company report</title><style>{css}</style></head>
<body><header><h1>SignalPost company report</h1><p>Find, compare and verify source-attributed company information. Missing areas are shown explicitly.</p></header>
<main><div class="toolbar"><label for="search">Search</label><input id="search" type="search" placeholder="Name, organisation number, municipality or industry" autocomplete="off"><label for="sort">Sort</label><select id="sort"><option value="name">Name</option><option value="filled">Data areas filled</option><option value="org">Organisation number</option></select><span id="result-count" class="muted" aria-live="polite"></span></div>
<div class="layout"><section class="panel" aria-label="Companies"><div class="panel-title">Companies</div><div id="company-list"></div></section><section class="panel" aria-live="polite"><div class="panel-title">Company view</div><div id="company-view" style="padding:14px"></div></section></div></main>
<script type="application/json" id="report-data">{data}</script><script>{js}</script></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = build_report(_rows(args.envelopes))
    if len(report.encode("utf-8")) >= 3_000_000:
        raise SystemExit("report exceeds the 3 MB acceptance limit")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(json.dumps({"output": str(output), "companies": len(_rows(args.envelopes)), "bytes": len(report.encode("utf-8"))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
