#!/usr/bin/env bash
# Vol-Edge / deploy / snapshot.sh
# Cron-обгортка для періодичного знімка ланцюга Deribit на Ubuntu VPS.
# - flock: захист від накладання прогонів (пропуск логується явно);
# - логи: logs/snapshot_YYYYMMDD.log, ретенція 30 днів;
# - артефакти: gzip для файлів старших 1 доби, ретенція .gz — 90 днів;
# - exit-код python прокидається у лог явно.
#
# 27 сер 2026 (schema 2): знімок збирає котирування — bid/ask/mark/oi
# рівня 1 з book summary (нуль додаткових запитів) і розміри рівня 2
# через /public/ticker (один запит на інструмент, ~878 на знімок).
# Заміряно на живому прогоні: 47 с BTC + 39 с ETH, разом real 1m30s,
# нуль провалів. Артефакт 527 КБ сирий / 92 КБ після gzip.
#
# ЧОМУ ПРАПОРЦІ ГЛИБИНИ ЕКСПОРТУЮТЬСЯ ЯВНО, хоч модуль має ті самі
# дефолти: інакше факт «знімок робить 878 HTTP-запитів» живе тільки в
# коді Python, і при читанні crontab його не видно. Це той самий клас,
# що VOLEDGE_RVROLL_MODE проти VOLEDGE_ROLL_MODE — тихий дефолт, який
# помічають через тижні. Значення тут = дефолти модуля; розбіжність
# між цим файлом і deribit_chain.py означає помилку в одному з них.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
ART_DIR="$ROOT/artifacts/chain"
LOCK_FILE="/tmp/voledge_snapshot.lock"
STAMP_DAY="$(date -u +%Y%m%d)"
LOG_FILE="$LOG_DIR/snapshot_${STAMP_DAY}.log"

# Жорстка стеля тривалості знімка, с. Внутрішні межі Python дають
# ~700 с у найгіршому випадку (240 с глибини на валюту + ретраї на
# index/book summary), але вони зібрані з чотирьох окремих констант.
# Тут — ОДНЕ число, менше за крок сітки 4 год і за годинний слот cron,
# тож наступний прогін гарантовано не наздожене цей.
# timeout шле TERM; python має шанс дописати лог і вийти.
SNAPSHOT_TIMEOUT_S=900

mkdir -p "$LOG_DIR" "$ART_DIR"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')Z] $*" >> "$LOG_FILE"; }

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "[SKIP] Попередній прогін ще працює (flock зайнятий) — пропуск явний."
    exit 0
fi

log "[START] Знімок ланцюга."
export VOLEDGE_CURRENCIES="BTC,ETH"
export VOLEDGE_OFFLINE="0"

# --- schema 2: збір котирувань (збір, НЕ вимірювання) ---------------
# Жоден аналітичний модуль ці поля поки не читає. Вимкнення глибини
# (VOLEDGE_CHAIN_DEPTH=0) залишає рівень 1 і не ламає нічого — це
# аварійний важіль, якщо Deribit почне різати rate limit.
export VOLEDGE_CHAIN_DEPTH="1"
export VOLEDGE_CHAIN_DEPTH_BUDGET="1200"
export VOLEDGE_CHAIN_DEPTH_MAX_S="240"
export VOLEDGE_CHAIN_DEPTH_SLEEP_MS="0"
log "[ENV] depth=${VOLEDGE_CHAIN_DEPTH} budget=${VOLEDGE_CHAIN_DEPTH_BUDGET} max_s=${VOLEDGE_CHAIN_DEPTH_MAX_S} sleep_ms=${VOLEDGE_CHAIN_DEPTH_SLEEP_MS} timeout=${SNAPSHOT_TIMEOUT_S}s"

SEC_START=$(date -u +%s)
timeout --signal=TERM --kill-after=30s "${SNAPSHOT_TIMEOUT_S}s" \
    python3 "$ROOT/data/deribit_chain.py" </dev/null >> "$LOG_FILE" 2>&1
RC=$?
SEC_ELAPSED=$(( $(date -u +%s) - SEC_START ))

# timeout повертає 124 (TERM) або 137 (KILL після --kill-after).
# Розрізняти обов'язково: 124 — знімок не встиг, це подія розкладу;
# будь-який інший ненульовий код — помилка самого модуля. Злиття цих
# двох в одне [ERROR] сховало б деградацію мережі за шумом.
if [ "$RC" -eq 124 ] || [ "$RC" -eq 137 ]; then
    log "[TIMEOUT] deribit_chain.py перевищив ${SNAPSHOT_TIMEOUT_S}s (rc=$RC, ${SEC_ELAPSED}s). Артефакт цього слота НЕ створено."
elif [ "$RC" -ne 0 ]; then
    log "[ERROR] deribit_chain.py завершився з кодом $RC (${SEC_ELAPSED}s)."
else
    log "[OK] Знімок збережено (exit=0, ${SEC_ELAPSED}s)."
fi

# Компресія артефактів старших 1 доби (surface_report читає .gz нативно)
N_GZ=$(find "$ART_DIR" -name 'chain_*.json' -mmin +1440 -print | wc -l)
if [ "$N_GZ" -gt 0 ]; then
    find "$ART_DIR" -name 'chain_*.json' -mmin +1440 -exec gzip -f {} \;
    log "[GZIP] Стиснуто артефактів: $N_GZ."
fi

# Ретенція: артефакти 90 днів, логи 30 днів (видалення логується).
# Перерахунок 27 сер під schema 2: 92 КБ/знімок після gzip х 6/добу х
# 90 діб = ~49 МБ. Вільно 3.3 ГБ. Ретенція НЕ змінюється.
N_DEL_ART=$(find "$ART_DIR" -name 'chain_*.json.gz' -mtime +90 -print | wc -l)
if [ "$N_DEL_ART" -gt 0 ]; then
    find "$ART_DIR" -name 'chain_*.json.gz' -mtime +90 -delete
    log "[RETENTION] Видалено артефактів старших 90д: $N_DEL_ART."
fi
N_DEL_LOG=$(find "$LOG_DIR" -name 'snapshot_*.log' -mtime +30 -print | wc -l)
if [ "$N_DEL_LOG" -gt 0 ]; then
    find "$LOG_DIR" -name 'snapshot_*.log' -mtime +30 -delete
    log "[RETENTION] Видалено логів старших 30д: $N_DEL_LOG."
fi

DISK_MB=$(du -sm "$ART_DIR" | cut -f1)
DISK_FREE_MB=$(df -Pm "$ART_DIR" | tail -1 | awk '{print $4}')
log "[END] Розмір artifacts/chain: ${DISK_MB} MB, вільно ${DISK_FREE_MB} MB. Exit=$RC."
exit "$RC"
