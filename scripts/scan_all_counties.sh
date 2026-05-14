#!/usr/bin/env bash
# Fleet scan — every TX county FactTrack supports on publicsearch.us.
# Runs sequentially on whatever node executes this. Tail the logs to watch.
#
# Priority order: Smith (Tyler, Monument HQ) → Leon (south Anderson) →
# Freestone → Nacogdoches → Walker → Madison. Anderson is already done.

set -uo pipefail

cd "$(dirname "$0")/.."

mkdir -p logs state
RUN_TAG="${RUN_TAG:-$(date +%Y%m%d)}"
CKPT="state/scan_all_counties_${RUN_TAG}.checkpoint"
touch "$CKPT"

COUNTIES=(
  "48001:Anderson"     # re-scan with 1990+ window for depth (was 1957 demo)
  "48423:Smith"        # Tyler — Monument HQ, highest priority
  "48289:Leon"         # south of Anderson
  "48161:Freestone"    # southwest of Anderson
  "48347:Nacogdoches"  # east, big oil county
  "48471:Walker"       # south, has Odyssey probate access
  "48313:Madison"      # south of Houston Co
)

for entry in "${COUNTIES[@]}"; do
  FIPS="${entry%%:*}"
  NAME="${entry##*:}"
  if grep -q "^${FIPS}$" "$CKPT" 2>/dev/null; then
    echo "═════ SKIP ${NAME} (${FIPS}) — already completed in run ${RUN_TAG} ═════"
    continue
  fi
  LOG="logs/scan_${FIPS}_$(date +%Y%m%d_%H%M%S).log"
  echo "═════ Starting ${NAME} County (${FIPS}) — log: ${LOG} ═════"
  if bash "scripts/scan_county.sh" "$FIPS" 2>&1 | tee "$LOG"; then
    echo "${FIPS}" >> "$CKPT"
    echo "═════ ${NAME} complete ═════"
  else
    echo "═════ ${NAME} FAILED — continuing without checkpoint ═════"
  fi
  sleep 5
done

echo
echo "All-county scan finished."
PGPASSWORD="${FT_DB_PASSWORD:-werds}" psql -h 127.0.0.1 -U daniel -d facttrack -P pager=off -c "
SELECT
  project_id,
  count(*) FILTER (WHERE severity='critical') AS critical,
  count(*) FILTER (WHERE severity='high')     AS high,
  count(*) AS total
  FROM facttrack.curative_item
 WHERE project_id LIKE 'county_research_%'
 GROUP BY project_id
 ORDER BY critical DESC, high DESC;
"
