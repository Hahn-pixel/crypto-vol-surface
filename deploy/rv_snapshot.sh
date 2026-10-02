#!/usr/bin/env bash
# Vol-Edge / deploy / rv_snapshot.sh
# Cron-обгортка для добового прогону analytics/rv_intraday.py на Ubuntu VPS.
# Конвенції ідентичні snapshot.sh:
# - flock (окремий lock-файл): захист від накладань, пропуск логується явно;
# - логи: logs/rv_YYYYMMDD.log, ретенція 30 днів;
# - артефакти artifacts/rv: gzip для файлів старших 1 доби, ретенція .gz 90д;
# - exit-код python прокидається у лог явно.
# Рекомендований cron: 40 0 * * * (00:40 UTC — UTC-доба закрита, не
# перетинається зі знімками ланцюга о :10).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
ART_DIR="$ROOT/artifacts/rv"
LOCK_FILE="/tmp/voledge_rv.lock"
STAMP_DAY="$(date -u +%Y%m%d)"
LOG_FILE="$LOG_DIR/rv_${STAMP_DAY}.log"

mkdir -p "$LOG_DIR" "$ART_DIR"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')Z] $*" >> "$LOG_FILE"; }

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "[SKIP] Попередній rv-прогін ще працює (flock зайнятий) — пропуск явний."
    exit 0
fi

log "[START] rv_intraday (5-хв RV + HAR)."
export VOLEDGE_CURRENCIES="BTC,ETH"
export VOLEDGE_OFFLINE="0"
export VOLEDGE_RV5_DAYS="140"
export VOLEDGE_RV5_MIN_BARS="276"

python3 "$ROOT/analytics/rv_intraday.py" </dev/null >> "$LOG_FILE" 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
    log "[ERROR] rv_intraday.py завершився з кодом $RC."
else
    log "[OK] rv-артефакт збережено (exit=0)."
fi

# Компресія rv-артефактів старших 1 доби (читачі мають підтримувати .gz)
N_GZ=$(find "$ART_DIR" -name 'rv_*.json' -mmin +1440 -print | wc -l)
if [ "$N_GZ" -gt 0 ]; then
    find "$ART_DIR" -name 'rv_*.json' -mmin +1440 -exec gzip -f {} \;
    log "[GZIP] Стиснуто rv-артефактів: $N_GZ."
fi

# Ретенція: артефакти 90 днів, логи 30 днів (видалення логується)
N_DEL_ART=$(find "$ART_DIR" -name 'rv_*.json.gz' -mtime +90 -print | wc -l)
if [ "$N_DEL_ART" -gt 0 ]; then
    find "$ART_DIR" -name 'rv_*.json.gz' -mtime +90 -delete
    log "[RETENTION] Видалено rv-артефактів старших 90д: $N_DEL_ART."
fi
N_DEL_LOG=$(find "$LOG_DIR" -name 'rv_*.log' -mtime +30 -print | wc -l)
if [ "$N_DEL_LOG" -gt 0 ]; then
    find "$LOG_DIR" -name 'rv_*.log' -mtime +30 -delete
    log "[RETENTION] Видалено rv-логів старших 30д: $N_DEL_LOG."
fi

DISK_MB=$(du -sm "$ART_DIR" | cut -f1)
log "[END] Розмір artifacts/rv: ${DISK_MB} MB. Exit=$RC."
exit "$RC"
