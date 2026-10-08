#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 [--output-dir DIR] [--subunits PATH] [--universe PATH]" >&2
  exit 2
}

root="$(cd "$(dirname "$0")/../.." && pwd)"
output_dir="$root/data"
subunits="${BRREG_SUBUNITS:-}"
universe="${SIGNALPOST_NAV_UNIVERSE:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir) output_dir="$(cd "${2:?}" && pwd)"; shift 2 ;;
    --subunits) subunits="$2"; shift 2 ;;
    --universe) universe="$2"; shift 2 ;;
    *) usage ;;
  esac
done

mkdir -p "$output_dir"
temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/signalpost-nav.XXXXXX")"
trap 'rm -rf "$temporary_dir"' EXIT
if [[ -z "$subunits" ]]; then
  subunits="$temporary_dir/brreg-underenheter.json.gz"
  curl --fail --location --retry 2 --output "$subunits" \
    "https://data.brreg.no/enhetsregisteret/api/underenheter/lastned/json"
fi
[[ -f "$subunits" ]] || { echo "sub-unit bulk is missing: $subunits" >&2; exit 1; }
raw="$output_dir/nav-employer-index.subunit.jsonl"
report="$output_dir/nav-employer-index.build-report.json"
connector_args=(--output "$raw" --report "$report")
if [[ -n "$universe" ]]; then
  connector_args+=(--universe "$universe")
fi
python "$root/scripts/connectors/run_nav_jobs_connector.py" "${connector_args[@]}"
python "$root/scripts/connectors/rekey_nav_index.py" \
  --index "$raw" \
  --underenheter "$subunits" \
  --output "$output_dir/nav-employer-index.jsonl"
rm -f "$raw" "$raw.meta.json"
