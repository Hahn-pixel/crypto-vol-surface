#!/usr/bin/env bash
# Vol-Edge / deploy / utc_gate.sh
# Вартовий UTC-розкладу для cron.
#
# ПРИЧИНА ІСНУВАННЯ: Debian-збірка cron 3.0pl1 (Ubuntu) НЕ застосовує
# змінну CRON_TZ — рядки завжди інтерпретуються в локальній TZ сервера
# (тут EEST/EET, UTC+3/+2). Через це розклад vol-edge зсувався на -3 год
# і захисний зсув ":10 повз експірацію 08:00 UTC" не працював.
#
# РІШЕННЯ: cron стріляє ЩОГОДИНИ за локальним часом, а фактичний розклад
# тримає цей gate за `date -u`. DST-переходи (EEST<->EET) не впливають.
#
# Використання:
#   utc_gate.sh "0,4,8,12,16,20" /path/to/vol-edge/deploy/snapshot.sh
#   utc_gate.sh "0"              /path/to/vol-edge/deploy/rv_snapshot.sh
#   utc_gate.sh "3"              /path/to/script.sh arg1 arg2
#
# Аргументи після цілі прокидаються цілі без змін.
# Пропуск НЕ мовчазний: друкує [GATE-SKIP] з поточною UTC-годиною.
set -uo pipefail

ALLOWED_HOURS="${1:?[GATE-FAIL] потрібен список годин UTC через кому}"
TARGET="${2:?[GATE-FAIL] потрібен шлях до цільового скрипта}"
shift 2   # решта ($@) — аргументи цілі

if [ ! -x "$TARGET" ]; then
    echo "[GATE-FAIL] Ціль не існує або не виконувана: $TARGET"
    exit 1
fi

# %-H прибирає провідний нуль (інакше "08" трактується як вісімкове число).
UTC_HOUR="$(date -u +%-H)"

IFS=',' read -ra HOURS <<< "$ALLOWED_HOURS"
for h in "${HOURS[@]}"; do
    if [ "$UTC_HOUR" -eq "$h" ] 2>/dev/null; then
        exec "$TARGET" "$@"
    fi
done

echo "[GATE-SKIP] UTC-година $UTC_HOUR не входить у [$ALLOWED_HOURS] — пропуск явний."
exit 0
