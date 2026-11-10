#!/bin/bash
# The whole daily job: sample the window, detect, report, write the digest.
#   tracker/daily.sh window     real run (waits for 07:00 America/Chicago)
#   tracker/daily.sh smoke      2 quick cycles now; results go to smoke/ and are not part of the series
#   tracker/daily.sh report D   re-run reports + digest for date D from saved counts (no sampling)
set -u
MODE="${1:-window}"
R="${CYBERCAB_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export CYBERCAB_ROOT="$R"
PY="${CYBERCAB_PYTHON:-python3}"
cd "$R" || exit 1
DATE="${2:-$(TZ=America/Chicago date +%F)}"
OUT=data; REP=reports
if [ "$MODE" = smoke ]; then
  export CYBERCAB_WINDOW_ALL=1
  OUT=smoke/data; REP=smoke/reports
  mkdir -p "$OUT" "$REP"
  rm -rf frames
  cp -f data/peak_daily*.csv data/peak_by_camera*.csv "$OUT"/ 2>/dev/null
fi
COUNTS="$OUT/counts/$DATE.csv.gz"
mkdir -p "$OUT/counts" "$REP" charts

if [ "$MODE" = window ]; then
  $PY tracker/run_window.py || exit 1
elif [ "$MODE" = smoke ]; then
  $PY tracker/run_window.py --smoke 2 || exit 1
fi

if [ "$MODE" != report ]; then
  echo "== detection"
  $PY tracker/detect_day.py --date "$DATE" --own-counts "$COUNTS" --evidence-dir "evidence/$DATE" || exit 1
fi

rc=0
report() {  # name panel csv
  echo "== $1"
  $PY peak_day_report.py --date "$DATE" --skip-chart --skip-health --panel "$2" --frames frames \
      --csv "$OUT/$3" --own-counts "$COUNTS" 2>&1 | tee "$REP/peak_run_${1}_$DATE.log"
  return "${PIPESTATUS[0]}"
}
if [ "$MODE" = report ]; then
  echo "report mode: rebuilding chart, extra measurements and digest from the saved CSVs"
else
  report expanded sampler/panel_expanded.json peak_daily_expanded.csv || rc=1
  report core     sampler/panel.json          peak_daily.csv          || rc=1
  report newarea  sampler/panel_newarea.json  peak_daily_newarea.csv  || rc=1
fi

export CYBERCAB_DATA_DIR="$OUT" CYBERCAB_REPORTS_DIR="$REP"
$PY charts/make_peak_chart.py || rc=1
echo "== extra measurements"
$PY metrics_extra.py --date "$DATE" || true
echo "== digest"
$PY tracker/make_digest.py --date "$DATE" --mode "$MODE" || rc=1
exit $rc
