#!/usr/bin/env bash
# Mac Mini half of the fleet sweep.
# iMac (.230) handles Anderson + Smith + Leon serially (already in flight).
# Mini (.212) handles Freestone + Nacogdoches + Walker + Madison in parallel.
# Both nodes write to the iMac Postgres at 192.168.1.230.
#
# Run from the Mini.

set -uo pipefail
cd "$(dirname "$0")/.."

# Force DB to the iMac
export FT_DB_HOST="${FT_DB_HOST:-192.168.1.230}"
export FT_DB_USER="${FT_DB_USER:-daniel}"
export FT_DB_PASSWORD="${FT_DB_PASSWORD:-werds}"
export FT_DB_NAME="${FT_DB_NAME:-facttrack}"
export PGPASSWORD="$FT_DB_PASSWORD"

mkdir -p logs state
RUN_TAG="${RUN_TAG:-$(date +%Y%m%d)}"
CKPT="state/scan_mini_${RUN_TAG}.checkpoint"
touch "$CKPT"

# Mini's slice of the fleet
COUNTIES=(
  "48161:Freestone"
  "48347:Nacogdoches"
  "48471:Walker"
  "48313:Madison"
)

for entry in "${COUNTIES[@]}"; do
  FIPS="${entry%%:*}"
  NAME="${entry##*:}"
  if grep -q "^${FIPS}$" "$CKPT" 2>/dev/null; then
    echo "═════ SKIP ${NAME} (${FIPS}) — already completed ═════"
    continue
  fi
  LOG="logs/mini_scan_${FIPS}_$(date +%Y%m%d_%H%M%S).log"
  echo "═════ Mini starting ${NAME} County (${FIPS}) — log: ${LOG} ═════"
  if bash "scripts/scan_county.sh" "$FIPS" 2>&1 | tee "$LOG"; then
    echo "${FIPS}" >> "$CKPT"
    echo "═════ ${NAME} complete (mini) ═════"
  else
    echo "═════ ${NAME} FAILED on mini ═════"
  fi
  sleep 3
done

echo
echo "Mini fleet share complete. Roll-up:"
psql -h "$FT_DB_HOST" -U "$FT_DB_USER" -d "$FT_DB_NAME" -P pager=off -c "
SELECT project_id,
       count(*) FILTER (WHERE severity='critical') AS critical,
       count(*) FILTER (WHERE severity='high')     AS high,
       count(*) AS total
  FROM facttrack.curative_item
 WHERE project_id LIKE 'county_research_%'
 GROUP BY project_id
 ORDER BY critical DESC, high DESC;
"
