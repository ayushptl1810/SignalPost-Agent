#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 <commit> --organisations PATH --bulk PATH" >&2
  exit 2
}

commit="${1:-}"
if [[ -z "$commit" ]]; then usage; fi
shift
organisations=""
bulk=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --organisations) organisations="${2:-}"; shift 2 ;;
    --bulk) bulk="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$organisations" && -n "$bulk" ]] || usage
[[ -f "$organisations" && -f "$bulk" ]] || { echo "smoke inputs do not exist" >&2; exit 1; }
# The check runs inside the fresh clone, so inputs must be absolute paths.
organisations="$(cd "$(dirname "$organisations")" && pwd)/$(basename "$organisations")"
bulk="$(cd "$(dirname "$bulk")" && pwd)/$(basename "$bulk")"

repo="$(git rev-parse --show-toplevel)"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/signalpost-check.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
clone="$tmp/repo"
mkdir -p "$tmp/home" "$tmp/cache" "$tmp/tmp"
git clone --no-local "$repo" "$clone" >/dev/null
git -C "$clone" checkout --quiet "$commit"
cd "$clone/backend"
uv_path="$(command -v uv)"
uv_dir="$(dirname "$uv_path")"
run_clean() {
  # UV_HTTP_TIMEOUT is passed through only when set: slow networks need longer than uv's 30 s
  # default to download the managed Python build (S25 and S29 checks failed on exactly that).
  env -i PATH="$uv_dir:/usr/bin:/bin" HOME="$tmp/home" XDG_CACHE_HOME="$tmp/cache" TMPDIR="$tmp/tmp" ${UV_HTTP_TIMEOUT:+UV_HTTP_TIMEOUT="$UV_HTTP_TIMEOUT"} "$uv_path" "$@"
}

echo "[1/6] uv sync"
run_clean sync
echo "[2/6] pytest"
run_clean run --with pytest pytest -q
echo "[3/6] 100-company G4 smoke"
mkdir -p out/clean-machine
run_clean run python scripts/run/run_competition_batch.py \
  --organisations "$organisations" \
  --bulk "$bulk" \
  --profiles-output out/clean-machine/profiles.jsonl \
  --output out/clean-machine/envelopes.jsonl \
  --report out/clean-machine/report.json \
  --run-id clean-machine-100 \
  --expected-count 100 \
  --discovery g4 \
  --nav-index data/nav-employer-index.jsonl
echo "[4/6] envelope validator"
run_clean run python - out/clean-machine/envelopes.jsonl <<'PY'
import json
import sys
from pathlib import Path
sys.path.insert(0, "src")
from norway_company_agent.registry.batch import validate_envelopes

rows = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines() if line.strip()]
result = validate_envelopes(rows, 100)
if not result["passed"]:
    raise SystemExit(json.dumps(result, indent=2))
print(json.dumps(result))
PY
echo "[5/6] starter shape check"
run_clean run python scripts/analysis/starter_shape_check.py \
  out/clean-machine/envelopes.jsonl
echo "[6/6] full-shape contract self-check"
run_clean run python scripts/analysis/contract_check.py \
  --envelopes out/clean-machine/envelopes.full.jsonl \
  --organisations "$organisations"
echo "PASS"
