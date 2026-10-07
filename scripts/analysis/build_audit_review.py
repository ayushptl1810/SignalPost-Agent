#!/usr/bin/env python3
"""Create the offline, browser-only audit review page."""
from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any


def _rows(worksheet: str | Path, evidence_root: str | Path) -> list[dict[str, Any]]:
    with Path(worksheet).open(newline="", encoding="utf-8") as handle:
        source_rows = list(csv.DictReader(handle))
    result = []
    for row in source_rows:
        evidence_path = Path(evidence_root) / str(row.get("id")) / "evidence.json"
        pack = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.exists() else {"status": "missing_pack", "sources": []}
        result.append({"row": row, "evidence": pack})
    return result


def build_review_page(worksheet: str | Path, evidence_root: str | Path, output: str | Path) -> dict[str, Any]:
    rows = _rows(worksheet, evidence_root)
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signalpost observation audit</title>
<style>
body{{font:15px system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:24px;background:#f7f7f5;color:#20211f}} header{{display:flex;justify-content:space-between;gap:12px;align-items:center}} button{{padding:9px 14px;margin:3px;border:1px solid #999;border-radius:6px;background:#fff;cursor:pointer}} button.selected{{background:#173f5f;color:white;border-color:#173f5f}} .card{{background:white;border:1px solid #ddd;border-radius:10px;padding:18px;margin-top:14px}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:8px}} dt{{font-weight:700;color:#555}} dd{{margin:2px 0 10px;overflow-wrap:anywhere}} pre{{white-space:pre-wrap;background:#f1f1ee;padding:12px;border-radius:6px}} textarea{{width:100%;min-height:70px}} .muted{{color:#666}} a{{color:#075985}}
</style></head><body>
<header><div><h1>Observation audit</h1><div class="muted" id="progress"></div></div><div><button id="prev">← Previous</button><button id="next">Next →</button><button id="export">Export labels</button></div></header>
<p class="muted">Keyboard: <kbd>Y</kbd>/<kbd>N</kbd>/<kbd>U</kbd> exact entity, <kbd>1</kbd>/<kbd>2</kbd>/<kbd>3</kbd>/<kbd>4</kbd> metric yes/no/not applicable/unsure, <kbd>←</kbd>/<kbd>→</kbd> navigate. Answers autosave in this browser. Unsure rows are not exported until resolved.</p>
<main id="app"></main>
<script id="audit-data" type="application/json">{payload}</script>
<script>
const rows=JSON.parse(document.getElementById('audit-data').textContent); const key='signalpost-audit-v1';
const state=JSON.parse(localStorage.getItem(key)||'{{}}'); let index=Number(state._index||0);
function save(){{localStorage.setItem(key,JSON.stringify(state));}}
function esc(value){{return String(value??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));}}
function current(){{return rows[index]||null}}
function answer(field,value){{const id=current().row.id; state[id]=Object.assign({{}},state[id],{{[field]:value}}); save(); render();}}
function choice(field,value,label){{const selected=(state[current().row.id]||{{}})[field]===value?' selected':''; return `<button class="${{selected}}" data-field="${{field}}" data-value="${{value}}">${{label}}</button>`;}}
function render(){{const item=current(); if(!item){{document.getElementById('app').innerHTML='<div class="card">No audit rows.</div>';return;}} const r=item.row,e=item.evidence||{{}},s=state[r.id]||{{}};
document.getElementById('progress').textContent=`Row ${{index+1}} of ${{rows.length}} · ${{Object.keys(state).filter(k=>k!=='_index').length}} saved`;
const source=(e.sources||[]).map(x=>`<li><b>${{esc(x.status)}}</b> <a target="_blank" rel="noreferrer" href="${{esc(x.final_url||x.requested_url||'')}}">${{esc(x.final_url||x.requested_url||'')}}</a><br><span class="muted">${{esc(x.title||'')}} · org numbers: ${{esc((x.org_numbers||[]).join(', '))}} · phone: ${{esc((x.phone_found||[]).join(', '))}}</span><pre>${{esc(x.excerpt||x.note||'')}}</pre></li>`).join('');
document.getElementById('app').innerHTML=`<section class="card"><h2>${{esc(r.registry_name||r.id)}}</h2><div class="grid"><dl><dt>Organisation number</dt><dd>${{esc(r.organisation_number)}}</dd><dt>Registry address</dt><dd>${{esc(r.registry_address)}}</dd><dt>Registry phone/email</dt><dd>${{esc(r.registry_phone)}} · ${{esc(r.registry_email_domain)}}</dd></dl><dl><dt>Platform / signal</dt><dd>${{esc(r.platform)}} / ${{esc(r.signal_type)}}</dd><dt>Source</dt><dd><a target="_blank" rel="noreferrer" href="${{esc(r.source_url)}}">${{esc(r.source_url)}}</a></dd><dt>Found on</dt><dd>${{r.found_on_url?`<a target="_blank" rel="noreferrer" href="${{esc(r.found_on_url)}}">${{esc(r.found_on_url)}}</a>`:'—'}}</dd></dl></div><p><b>Context:</b> ${{esc(r.evidence_excerpt)}} ${{r.handle_text?` · handle: ${{esc(r.handle_text)}}`:''}}</p></section>
<section class="card"><h3>Evidence summary · ${{esc(e.status)}}</h3><ul>${{source||'<li>No evidence pack found.</li>'}}</ul></section>
<section class="card"><h3>Human label</h3><p><b>Exact entity?</b><br>${{choice('exact_entity','yes','Yes')}} ${{choice('exact_entity','no','No')}} ${{choice('exact_entity','unsure','Unsure')}}</p><p><b>Metric correct?</b><br>${{choice('metric_correct','yes','Yes')}} ${{choice('metric_correct','no','No')}} ${{choice('metric_correct','not_applicable','Not applicable')}} ${{choice('metric_correct','unsure','Unsure')}}</p><p><b>Sentiment correct?</b> <span class="muted">optional</span><br>${{choice('sentiment_correct','yes','Yes')}} ${{choice('sentiment_correct','no','No')}} ${{choice('sentiment_correct','unsure','Unsure')}}</p><label>Notes<br><textarea id="notes">${{esc(s.notes||'')}}</textarea></label></section>`;
document.querySelectorAll('button[data-field]').forEach(b=>b.onclick=()=>answer(b.dataset.field,b.dataset.value)); document.getElementById('notes').oninput=e=>{{state[r.id]=Object.assign({{}},state[r.id],{{notes:e.target.value}});save();}};}}
function move(delta){{index=Math.max(0,Math.min(rows.length-1,index+delta));state._index=index;save();render();}}
document.getElementById('prev').onclick=()=>move(-1); document.getElementById('next').onclick=()=>move(1);
document.getElementById('export').onclick=()=>{{const lines=[]; for(const item of rows){{const a=state[item.row.id]||{{}}; if(['yes','no'].includes(a.exact_entity)&&['yes','no','not_applicable'].includes(a.metric_correct)) lines.push(JSON.stringify({{id:item.row.id,exact_entity:a.exact_entity==='yes',metric_correct:a.metric_correct!=='no',sentiment_correct:['yes','no'].includes(a.sentiment_correct)?a.sentiment_correct==='yes':null,labeler:'owner',labeled_at:new Date().toISOString()}}));}} const blob=new Blob([lines.join('\\n')+(lines.length?'\\n':'')],{{type:'application/jsonl'}}); const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='observation-labels-owner.jsonl';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);}};
document.addEventListener('keydown',e=>{{if(e.target.tagName==='TEXTAREA')return; const k=e.key.toLowerCase(); if(k==='y')answer('exact_entity','yes'); else if(k==='n')answer('exact_entity','no'); else if(k==='u')answer('exact_entity','unsure'); else if(k==='1')answer('metric_correct','yes'); else if(k==='2')answer('metric_correct','no'); else if(k==='3')answer('metric_correct','not_applicable'); else if(k==='4')answer('metric_correct','unsure'); else if(e.key==='ArrowLeft')move(-1); else if(e.key==='ArrowRight')move(1);}}); render();
</script></body></html>"""
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return {"rows": len(rows), "output": str(destination), "offline": True, "autosave": True, "labeler": "owner"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the offline audit review page.")
    parser.add_argument("--worksheet", required=True)
    parser.add_argument("--evidence-root", default="out/proxy/evidence")
    parser.add_argument("--output", default="out/proxy/audit-review.html")
    args = parser.parse_args()
    print(json.dumps(build_review_page(args.worksheet, args.evidence_root, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
