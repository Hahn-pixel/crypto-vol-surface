#!/usr/bin/env bash
# Vol-Edge / deploy / anomaly_snapshot.sh
# Cron-обгортка для analytics/anomaly_detect.py (Модуль 5) на Ubuntu VPS.
# Конвенції ідентичні snapshot.sh / rv_snapshot.sh:
# - flock (окремий lock): захист від накладань, пропуск логується явно;
# - логи: logs/anomaly_YYYYMMDD.log, ретенція 30 днів;
# - артефакти artifacts/anomaly: gzip старших 1 доби, ретенція .gz 90д;
# - у лог додатково дублюються [ALERT]-рядки прогону (швидкий grep);
# - exit-код python прокидається у лог явно.
# Рекомендований cron: 25 */4 * * * (через 15 хв після chain-знімка о :10).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
ART_DIR="$ROOT/artifacts/anomaly"
LOCK_FILE="/tmp/voledge_anomaly.lock"
STAMP_DAY="$(date -u +%Y%m%d)"
LOG_FILE="$LOG_DIR/anomaly_${STAMP_DAY}.log"

mkdir -p "$LOG_DIR" "$ART_DIR"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')Z] $*" >> "$LOG_FILE"; }

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "[SKIP] Попередній anomaly-прогін ще працює (flock зайнятий) — пропуск явний."
    exit 0
fi

log "[START] anomaly_detect."
export VOLEDGE_OFFLINE="0"
export VOLEDGE_Z_THRESH="3.0"
export VOLEDGE_MIN_HISTORY="12"
export VOLEDGE_VRP_MAX_AGE_H="5"
export VOLEDGE_VRP_SOURCE="rvroll"

RUN_OUT="$(python3 "$ROOT/analytics/anomaly_detect.py" </dev/null 2>&1)"
RC=$?
echo "$RUN_OUT" >> "$LOG_FILE"
if [ "$RC" -ne 0 ]; then
    log "[ERROR] anomaly_detect.py завершився з кодом $RC."
else
    N_ALERTS=$(echo "$RUN_OUT" | grep -c '^\[ALERT\]' || true)
    log "[OK] Детекція завершена (exit=0), алертів: $N_ALERTS."
fi

# Компресія артефактів старших 1 доби
N_GZ=$(find "$ART_DIR" -name 'anomaly_*.json' -mmin +1440 -print | wc -l)
if [ "$N_GZ" -gt 0 ]; then
    find "$ART_DIR" -name 'anomaly_*.json' -mmin +1440 -exec gzip -f {} \;
    log "[GZIP] Стиснуто anomaly-артефактів: $N_GZ."
fi

# Ретенція: артефакти 90 днів, логи 30 днів (видалення логується)
N_DEL_ART=$(find "$ART_DIR" -name 'anomaly_*.json.gz' -mtime +90 -print | wc -l)
if [ "$N_DEL_ART" -gt 0 ]; then
    find "$ART_DIR" -name 'anomaly_*.json.gz' -mtime +90 -delete
    log "[RETENTION] Видалено anomaly-артефактів старших 90д: $N_DEL_ART."
fi
N_DEL_LOG=$(find "$LOG_DIR" -name 'anomaly_*.log' -mtime +30 -print | wc -l)
if [ "$N_DEL_LOG" -gt 0 ]; then
    find "$LOG_DIR" -name 'anomaly_*.log' -mtime +30 -delete
    log "[RETENTION] Видалено anomaly-логів старших 30д: $N_DEL_LOG."
fi

DISK_MB=$(du -sm "$ART_DIR" | cut -f1)
log "[END] Розмір artifacts/anomaly: ${DISK_MB} MB. Exit=$RC."
exit "$RC"
