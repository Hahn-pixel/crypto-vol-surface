#!/usr/bin/env bash
# Vol-Edge / deploy / defi_snapshot.sh
# Cron-обгортка для знімка котирувань Derive (гіпотеза DEFI).
# - flock: захист від накладання прогонів (пропуск логується явно);
# - логи: logs/defi_snapshot_YYYYMMDD.log, ретенція 30 днів;
# - артефакти: колектор пише ВЖЕ стиснутими (.json.gz), ретенція 90 днів;
# - exit-код python прокидається у лог явно.
#
# 26 вер 2026: ЗБІР, НЕ ВИМІРЮВАННЯ. Жоден аналітичний модуль
# artifacts/defi/ не читає і читати не мусить, доки немає оцінювача
# d_iv. Знімок нічого не гейтить і ні на що не впливає.
#
# ЧОМУ :12, А НЕ :10. Пара (chain, defi) має сенс лише при малій
# різниці МОМЕНТІВ зняття котирувань: на тижневих опціонах дельта
# велика, і кілька хвилин розбіжності дають vol-пункти на рівному
# місці. Порядок той самий, що rvroll перед ланцюгом: спершу ЕТАЛОН
# (snapshot.sh о :10), потім те, що з ним порівнюється.
# АЛЕ :10 інколи розтягується до 400 с, тож перетин з :12 можливий.
# Синхронність НЕ припускається, а МІРЯЄТЬСЯ: у кожному записі є
# ts_fetch_ms, і оцінювач мусить відкидати пари поза явним вікном з
# лічильником (клас 8, білий список).
#
# ЧОМУ BASE_URL ЕКСПОРТУЄТЬСЯ ЯВНО І НЕ ДОРІВНЮЄ ДЕФОЛТУ МОДУЛЯ.
# Дефолт defi_collect.py — api.derive.xyz/v3. Заміряно 25-26 вер:
# на v3 підсистема історії віддає -32603 на всіх маршрутах, а тікери
# показують oi=0 там, де v2 дає 10046. Дані v3 НЕПОВНІ. Робочий хост
# — api.lyra.finance. Це ЄДИНЕ місце, де значення тут НАВМИСНО
# відрізняється від дефолту модуля; решта збігається, і розбіжність
# означала б помилку в одному з двох.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
ART_DIR="$ROOT/artifacts/defi"
LOCK_FILE="/tmp/voledge_defi_snapshot.lock"
STAMP_DAY="$(date -u +%Y%m%d)"
LOG_FILE="$LOG_DIR/defi_snapshot_${STAMP_DAY}.log"

# Жорстка стеля тривалості. Заміряно на живих прогонах: 33-34 с на
# обидві валюти (14 запитів на валюту, пакетний режим). Внутрішні
# межі Python дають ~480 с у найгіршому разі (240 с на валюту), але
# вони зібрані з окремих констант. Тут — ОДНЕ число, менше за
# годинний слот cron, тож наступний прогін не наздожене цей.
SNAPSHOT_TIMEOUT_S=600

mkdir -p "$LOG_DIR" "$ART_DIR"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')Z] $*" >> "$LOG_FILE"; }

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "[SKIP] Попередній прогін ще працює (flock зайнятий) — пропуск явний."
    exit 0
fi

log "[START] Знімок котирувань Derive."
export VOLEDGE_OFFLINE="0"
export VOLEDGE_DEFI_C_CURRENCIES="BTC,ETH"
export VOLEDGE_DEFI_C_BASE_URL="https://api.lyra.finance"
export VOLEDGE_DEFI_C_MODE="bulk"
export VOLEDGE_DEFI_C_TIMEOUT_S="20"
export VOLEDGE_DEFI_C_BUDGET="1200"
export VOLEDGE_DEFI_C_MAX_S="240"
export VOLEDGE_DEFI_C_SLEEP_MS="60"
export VOLEDGE_DEFI_C_GZIP="1"
export VOLEDGE_DEFI_C_PREFLIGHT_ONLY="0"
# ART_DIR АБСОЛЮТНИЙ І ЕКСПОРТУЄТЬСЯ ЯВНО. Заміряно 26 вер: cron і
# ssh запускають обгортку з домашньої теки, а не з кореня репо, тож
# відносний шлях поклав артефакт у ~/artifacts/defi. Лог
# при цьому надрукував відносний шлях і exit=0 — успішний запис НЕ
# ТУДИ не відрізнити від успішного (клас 10). Модуль тепер теж
# рахує дефолт від __file__; цей рядок — друга лінія, і розбіжність
# між ними означала б помилку в одному з двох.
export VOLEDGE_DEFI_C_ARTIFACT_DIR="$ART_DIR"
log "[ENV] url=${VOLEDGE_DEFI_C_BASE_URL} mode=${VOLEDGE_DEFI_C_MODE} budget=${VOLEDGE_DEFI_C_BUDGET} max_s=${VOLEDGE_DEFI_C_MAX_S} sleep_ms=${VOLEDGE_DEFI_C_SLEEP_MS} timeout=${SNAPSHOT_TIMEOUT_S}s"
log "[ENV] art_dir=${VOLEDGE_DEFI_C_ARTIFACT_DIR}"

SEC_START=$(date -u +%s)
timeout --signal=TERM --kill-after=30s "${SNAPSHOT_TIMEOUT_S}s" \
    python3 "$ROOT/analytics/defi_collect.py" </dev/null >> "$LOG_FILE" 2>&1
RC=$?
SEC_ELAPSED=$(( $(date -u +%s) - SEC_START ))

# 124 (TERM) / 137 (KILL) — подія розкладу, не помилка модуля.
# Злиття в одну гілку сховало б деградацію мережі за шумом.
if [ "$RC" -eq 124 ] || [ "$RC" -eq 137 ]; then
    log "[TIMEOUT] defi_collect.py перевищив ${SNAPSHOT_TIMEOUT_S}s (rc=$RC, ${SEC_ELAPSED}s). Артефакт цього слота НЕ створено."
elif [ "$RC" -ne 0 ]; then
    log "[ERROR] defi_collect.py завершився з кодом $RC (${SEC_ELAPSED}s)."
else
    log "[OK] Знімок збережено (exit=0, ${SEC_ELAPSED}s)."
fi

# Компресії НЕ РОБИМО: колектор пише .json.gz одразу
# (VOLEDGE_DEFI_C_GZIP=1). Якщо прапорець колись вимкнуть, тут
# з'являться .json — тому рахуємо їх і кажемо вголос, а не мовчимо.
N_RAW=$(find "$ART_DIR" -name 'defiquotes_*.json' -print | wc -l)
if [ "$N_RAW" -gt 0 ]; then
    log "[WARN] Нестиснутих артефактів: $N_RAW — перевірити VOLEDGE_DEFI_C_GZIP."
fi

# Ретенція: артефакти 90 днів, логи 30 днів.
# Розрахунок 26 вер: 117 КБ/знімок після gzip х 6/добу х 90 діб =
# ~63 МБ. Утричі більше за ланцюг (49 МБ). Вільно 3.3 ГБ — вистачає.
N_DEL_ART=$(find "$ART_DIR" -name 'defiquotes_*.json.gz' -mtime +90 -print | wc -l)
if [ "$N_DEL_ART" -gt 0 ]; then
    find "$ART_DIR" -name 'defiquotes_*.json.gz' -mtime +90 -delete
    log "[RETENTION] Видалено артефактів старших 90д: $N_DEL_ART."
fi
N_DEL_LOG=$(find "$LOG_DIR" -name 'defi_snapshot_*.log' -mtime +30 -print | wc -l)
if [ "$N_DEL_LOG" -gt 0 ]; then
    find "$LOG_DIR" -name 'defi_snapshot_*.log' -mtime +30 -delete
    log "[RETENTION] Видалено логів старших 30д: $N_DEL_LOG."
fi

DISK_MB=$(du -sm "$ART_DIR" | cut -f1)
DISK_FREE_MB=$(df -Pm "$ART_DIR" | tail -1 | awk '{print $4}')
log "[END] Розмір artifacts/defi: ${DISK_MB} MB, вільно ${DISK_FREE_MB} MB. Exit=$RC."
exit "$RC"
