#!/usr/bin/env bash
# Anderson County full pipeline:
#   party-splitter → publicsearch_docs fetch (multi-page) → grantor-side release
#   verification → OCR every page → clause extraction → curative engine
#   → report rendering
#
# Idempotent: re-runs skip already-done work. Pass --force to wipe doc images
# and re-fetch everything (does NOT wipe DB rows).
#
# Required env:
#   FT_DB_PASSWORD=<pg pw for daniel on facttrack db>
#
# Usage: ./scripts/run_anderson_pipeline.sh [--force]
set -euo pipefail

cd "$(dirname "$0")/.."

PY=".venv/bin/python"
export PYTHONPATH=src

if [[ "${1:-}" == "--force" ]]; then
    echo "[force] clearing cached doc images + parsed_metadata.image_paths …"
    rm -rf cache/lease_images/48001/*
    PGPASSWORD="${FT_DB_PASSWORD:-werds}" psql -h 127.0.0.1 -U daniel -d facttrack \
        -c "UPDATE facttrack.lease SET parsed_metadata = parsed_metadata - 'image_paths' WHERE county_fips = '48001';"
fi

echo "[1/7] party split (lessor/lessee → lease_party rows + deceased flags)"
$PY -m facttrack.ingest.party_splitter --county 48001

echo "[2/7] fetch doc images (multi-page MPV)"
$PY -m facttrack.ingest.publicsearch_docs --county 48001 --max 30

echo "[3/7] grantor-side release verification (publicsearch.us)"
mkdir -p docs/verification
$PY -m facttrack.ingest.release_verifier \
    --county 48001 \
    --audit "docs/verification/release_verifier_48001_$(date +%Y-%m-%d).txt"

echo "[4/7] OCR every cached image"
$PY -c "
from pathlib import Path
from facttrack.ocr.tesseract import ocr_image
root = Path('cache/lease_images/48001')
n = 0
for png in sorted(root.rglob('page_*.png')):
    if '_ocr' in str(png):
        continue
    try:
        ocr_image(png)
        n += 1
    except Exception as e:
        print(f'OCR FAIL {png}: {e}')
print(f'OCR pages processed: {n}')
"

echo "[5/7] extract clauses → lease.* columns + lease_party.is_deceased"
$PY -m facttrack.engine.clause_parser --county 48001

echo "[6/7] run curative engine"
$PY -m facttrack.engine.run --project county_research_48001

echo "[7/7] build report artifacts (map, xlsx, html, pdf)"
$PY -m facttrack.render.build_report --project county_research_48001

echo "DONE — Anderson pipeline complete."
