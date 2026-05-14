#!/usr/bin/env bash
# Scan a single TX county on publicsearch.us — modern records (last 5 years) —
# end-to-end: OPR scrape → doc fetch → release verifier → OCR → clause extract
# → curative engine → report render.
#
# Usage: ./scripts/scan_county.sh <FIPS> [date_from]
#   FIPS:       Texas county FIPS code (e.g. 48423 for Smith)
#   date_from:  ISO date, defaults to 5 years ago.
#
# Project IDs are auto-bootstrapped as `county_research_<FIPS>`.
#
# Sends an iMessage alert when the engine returns any CRITICAL finding.

set -euo pipefail

FIPS="${1:?usage: scan_county.sh <FIPS> [date_from]}"
DATE_FROM="${2:-$(date -d '5 years ago' +%Y-%m-%d 2>/dev/null || python3 -c 'import datetime; print((datetime.date.today() - datetime.timedelta(days=5*365)).isoformat())')}"
DATE_TO="$(date +%Y-%m-%d)"
PROJECT="county_research_${FIPS}"

cd "$(dirname "$0")/.."
PY=".venv/bin/python"
export PYTHONPATH=src
export FT_DB_PASSWORD="${FT_DB_PASSWORD:-werds}"

COUNTY_NAME=$($PY -c "from facttrack.config import COUNTIES; print(COUNTIES['$FIPS'].name)")

echo
echo "════════════════════════════════════════════════════════════════"
echo " FactTrack scan — ${COUNTY_NAME} County (FIPS ${FIPS})"
echo " date window: ${DATE_FROM} → ${DATE_TO}"
echo " project: ${PROJECT}"
echo "════════════════════════════════════════════════════════════════"

# 0. Bootstrap project + project_tract entries if needed
PGPASSWORD="$FT_DB_PASSWORD" psql -h 127.0.0.1 -U daniel -d facttrack -v ON_ERROR_STOP=1 <<EOF
INSERT INTO facttrack.county (fips, name, state) VALUES ('$FIPS', '$COUNTY_NAME', 'TX')
  ON CONFLICT (fips) DO NOTHING;
INSERT INTO facttrack.project (id, label) VALUES ('$PROJECT', '$COUNTY_NAME County Research — All Tracts')
  ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label;
EOF

echo "[1/8] OPR scrape from publicsearch.us"
$PY -m facttrack.ingest.publicsearch --county "$FIPS" --from "$DATE_FROM" --to "$DATE_TO" --max 300 || true

echo "[2/8] parse legal descriptions → tracts + project_tract"
$PY -m facttrack.ingest.legal_parser --county-fips "$FIPS" --county-name "$COUNTY_NAME" || true

echo "[3/8] party split (lessor/lessee → lease_party + deceased flags)"
$PY -m facttrack.ingest.party_splitter --county "$FIPS"

echo "[4/8] fetch doc images (multi-page MPV)"
$PY -m facttrack.ingest.publicsearch_docs --county "$FIPS" --max 50 || true

echo "[5/8] grantor-side release verifier"
mkdir -p docs/verification
$PY -m facttrack.ingest.release_verifier \
    --county "$FIPS" \
    --audit "docs/verification/release_verifier_${FIPS}_$(date +%Y-%m-%d).txt" || true

echo "[6/8] OCR every cached image"
$PY -c "
from pathlib import Path
from facttrack.ocr.tesseract import ocr_image
root = Path(f'cache/lease_images/$FIPS')
n = 0
if root.exists():
    for png in sorted(root.rglob('page_*.png')):
        if '_ocr' in str(png):
            continue
        try:
            ocr_image(png); n += 1
        except Exception as e:
            print(f'OCR FAIL {png}: {e}')
print(f'OCR pages processed: {n}')
"

echo "[7/8] extract clauses + run curative engine"
$PY -m facttrack.engine.clause_parser --county "$FIPS" || true
ENGINE_OUT=$($PY -m facttrack.engine.run --project "$PROJECT" 2>&1)
echo "$ENGINE_OUT"

echo "[8/8] build report"
$PY -m facttrack.render.build_report --project "$PROJECT" || true

# Critical-finding alert
CRIT=$(PGPASSWORD="$FT_DB_PASSWORD" psql -h 127.0.0.1 -U daniel -d facttrack -t -A \
    -c "SELECT count(*) FROM facttrack.curative_item WHERE project_id = '$PROJECT' AND severity = 'critical';")
echo
echo "════════════════════════════════════════════════════════════════"
echo " ${COUNTY_NAME} County scan complete — ${CRIT} critical finding(s)"
echo "════════════════════════════════════════════════════════════════"

if [[ "${CRIT:-0}" -gt 0 ]]; then
  MSG="FactTrack: ${CRIT} CRITICAL finding(s) in ${COUNTY_NAME} County. Project ${PROJECT}. Report at reports/${PROJECT}/report.pdf"
  if command -v imsg >/dev/null 2>&1; then
    imsg send +14053151310 "$MSG" >/dev/null 2>&1 || true
  fi
fi
