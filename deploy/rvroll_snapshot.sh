#!/usr/bin/env bash
# Vol-Edge / deploy / rvroll_snapshot.sh
# Cron-обгортка для analytics/rv_rolling.py (ковзне вікно RV, режим live).
# Конвенції ідентичні snapshot.sh / rv_snapshot.sh:
# - flock (окремий lock-файл): захист від накладань, пропуск логується явно;
# - логи: logs/rvroll_YYYYMMDD.log, ретенція 30 днів;
# - артефакти artifacts/rv (префікс rvroll_): gzip старших 1 доби,
#   ретенція .gz 90д;
# - exit-код python прокидається у лог явно.
#
# РОЗКЛАД: :05 UTC кожні 4 год, тобто ПЕРЕД знімком ланцюга о :10.
# Так лаг знаменника однаковий для всіх знімків (~5 хв) і слотового
# ефекту не виникає. Якби rvroll йшов ПІСЛЯ chain, кожен знімок брав би
# знаменник попереднього циклу — лаг теж рівномірний, але без потреби
# на 4 год більший.
# Cron (через utc_gate.sh, бо CRON_TZ у Debian cron — no-op):
#   5 * * * * .../utc_gate.sh "0,4,8,12,16,20" .../deploy/rvroll_snapshot.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
ART_DIR="$ROOT/artifacts/rv"
LOCK_FILE="/tmp/voledge_rvroll.lock"
STAMP_DAY="$(date -u +%Y%m%d)"
LOG_FILE="$LOG_DIR/rvroll_${STAMP_DAY}.log"

mkdir -p "$LOG_DIR" "$ART_DIR"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')Z] $*" >> "$LOG_FILE"; }

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "[SKIP] Попередній rvroll-прогін ще працює (flock зайнятий) — пропуск явний."
    exit 0
fi

log "[START] rv_rolling (ковзне вікно, live)."
export VOLEDGE_CURRENCIES="BTC,ETH"
export VOLEDGE_OFFLINE="0"
export VOLEDGE_ROLL_MODE="live"
export VOLEDGE_ROLL_WINDOW_D="30"
export VOLEDGE_ROLL_WINDOW7_D="7"
export VOLEDGE_ROLL_PAD_D="3"
export VOLEDGE_ROLL_MIN_COVER="0.96"

python3 "$ROOT/analytics/rv_rolling.py" </dev/null >> "$LOG_FILE" 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
    log "[ERROR] rv_rolling.py завершився з кодом $RC."
else
    log "[OK] rvroll-артефакт збережено (exit=0)."
fi

# Компресія rvroll-артефактів старших 1 доби (читачі підтримують .gz).
# Префікс rvroll_ НЕ перетинається з глобом rv_* добового rv_snapshot.sh —
# кожна обгортка обслуговує лише свої файли.
N_GZ=$(find "$ART_DIR" -name 'rvroll_*.json' -mmin +1440 -print | wc -l)
if [ "$N_GZ" -gt 0 ]; then
    find "$ART_DIR" -name 'rvroll_*.json' -mmin +1440 -exec gzip -f {} \;
    log "[GZIP] Стиснуто rvroll-артефактів: $N_GZ."
fi

# Ретенція: артефакти 90 днів, логи 30 днів (видалення логується)
N_DEL_ART=$(find "$ART_DIR" -name 'rvroll_*.json.gz' -mtime +90 -print | wc -l)
if [ "$N_DEL_ART" -gt 0 ]; then
    find "$ART_DIR" -name 'rvroll_*.json.gz' -mtime +90 -delete
    log "[RETENTION] Видалено rvroll-артефактів старших 90д: $N_DEL_ART."
fi
N_DEL_LOG=$(find "$LOG_DIR" -name 'rvroll_*.log' -mtime +30 -print | wc -l)
if [ "$N_DEL_LOG" -gt 0 ]; then
    find "$LOG_DIR" -name 'rvroll_*.log' -mtime +30 -delete
    log "[RETENTION] Видалено rvroll-логів старших 30д: $N_DEL_LOG."
fi

N_ART=$(find "$ART_DIR" -name 'rvroll_*.json*' -print | wc -l)
DISK_MB=$(du -sm "$ART_DIR" | cut -f1)
log "[END] rvroll-артефактів: $N_ART. Розмір artifacts/rv: ${DISK_MB} MB. Exit=$RC."
exit "$RC"
