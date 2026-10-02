#!/usr/bin/env python3
"""
[EN] Status summary of collected data: artifact counts and time ranges,
latest anomaly-detector output and recent cron log lines. Local files
only, no network.

--- Ukrainian original below ---
vol_edge_status.py

Зведення стану зібраних даних Vol-Edge на VPS: кількість артефактів,
часовий діапазон, статус детектора аномалій, останні алерти.

Запуск на VPS:
    cd <корінь репозиторію>
    python3 vol_edge_status.py

Лише stdlib. Без мережі, без залежностей.
"""

import gzip
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
ARTIFACT_DIRS = {
    "chain": os.path.join(ROOT, "artifacts", "chain"),
    "rv": os.path.join(ROOT, "artifacts", "rv"),
    "anomaly": os.path.join(ROOT, "artifacts", "anomaly"),
}
LOG_DIR = os.path.join(ROOT, "logs")
MIN_HISTORY_DEFAULT = 12


def read_json_maybe_gz(path):
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)
        else:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        return {"__error__": str(e)}


def list_artifacts(dir_path):
    if not os.path.isdir(dir_path):
        return []
    files = [
        os.path.join(dir_path, fn)
        for fn in os.listdir(dir_path)
        if fn.endswith(".json") or fn.endswith(".json.gz")
    ]
    files.sort(key=lambda p: os.path.getmtime(p))
    return files


def fmt_ts(mtime):
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def section(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def summarize_dir(name, dir_path):
    section(f"{name.upper()} — {dir_path}")
    files = list_artifacts(dir_path)
    if not files:
        print("  [SKIP] Артефактів не знайдено (порожньо або тека відсутня).")
        return files

    first_m = os.path.getmtime(files[0])
    last_m = os.path.getmtime(files[-1])
    span_h = (last_m - first_m) / 3600.0

    print(f"  Кількість файлів: {len(files)}")
    print(f"  Перший:  {fmt_ts(first_m)}  ({os.path.basename(files[0])})")
    print(f"  Останній: {fmt_ts(last_m)}  ({os.path.basename(files[-1])})")
    print(f"  Діапазон: {span_h:.1f} год")

    n_gz = sum(1 for f in files if f.endswith(".gz"))
    n_plain = len(files) - n_gz
    print(f"  Стиснуто (.gz): {n_gz}, нестиснуто (.json): {n_plain}")

    return files


def summarize_chain(files):
    if not files:
        return
    latest = read_json_maybe_gz(files[-1])
    section("ОСТАННІЙ CHAIN-АРТЕФАКТ (деталі)")

    if isinstance(latest, dict) and "__error__" in latest:
        print(f"  [FAIL] Не вдалось прочитати останній chain-артефакт: {latest['__error__']}")
        return

    if isinstance(latest, dict):
        currencies = None
        if isinstance(latest.get("currencies"), list):
            currencies = latest.get("currencies")
        elif isinstance(latest.get("data"), dict):
            currencies = list(latest["data"].keys())
        print(f"  Тип кореня: dict")
        print(f"  Ключі верхнього рівня: {sorted(latest.keys())[:15]}")
        if currencies:
            print(f"  Валюти: {currencies}")

    elif isinstance(latest, list):
        print(f"  Тип кореня: list, елементів: {len(latest)}")
        if latest:
            first_elem = latest[0]
            if isinstance(first_elem, dict):
                print(f"  Ключі першого елемента: {sorted(first_elem.keys())[:15]}")
            else:
                print(f"  Тип першого елемента: {type(first_elem).__name__}")

    else:
        print(f"  [WARN] Неочікуваний тип кореня JSON: {type(latest).__name__}")


def summarize_anomaly(files, min_history):
    if not files:
        print(f"  Очікується накопичення історії до MIN_HISTORY={min_history} перед активним режимом.")
        return

    latest = read_json_maybe_gz(files[-1])
    if "__error__" in latest:
        print(f"  [FAIL] Не вдалось прочитати останній anomaly-артефакт: {latest['__error__']}")
        return

    section("ОСТАННІЙ ANOMALY-АРТЕФАКТ (деталі)")
    print(f"  Файл: {os.path.basename(files[-1])}")
    print(f"  Кількість snapshot-ів у історії на момент прогону: {len(files)}")

    # ВАЖЛИВО: артефакт зберігає прапорець як булеве поле "alert": true,
    # а не як рядок "ALERT". Попередня версія шукала підрядок у строкових
    # значеннях і тому завжди друкувала 0 — тихий фальш-негатив.
    results = latest.get("results")
    if not isinstance(results, dict):
        print("  [FAIL] У артефакті немає dict-поля 'results' — "
              f"схема несподівана (ключі: {sorted(latest.keys())[:15]}).")
        return

    ok_names, skip_names, alert_names = [], [], []
    bad_schema = []
    for name in sorted(results):
        r = results[name]
        if not isinstance(r, dict):
            bad_schema.append(name)
            continue
        status = r.get("status")
        if status == "OK":
            ok_names.append(name)
            if r.get("alert") is True:
                alert_names.append((name, r.get("z"), r.get("value"),
                                    r.get("n_history")))
        elif status == "SKIP":
            skip_names.append((name, r.get("reason", "причина відсутня")))
        else:
            bad_schema.append(name)

    z_thresh = latest.get("z_thresh", "?")
    print(f"  Поріг |z| у артефакті: {z_thresh}, "
          f"min_history: {latest.get('min_history', '?')}")
    print(f"  Фіч у results: {len(results)} "
          f"(OK={len(ok_names)}, SKIP={len(skip_names)}, "
          f"невідомий status={len(bad_schema)})")

    if bad_schema:
        print(f"  [FAIL] Фічі з нерозпізнаною схемою/статусом: {bad_schema}")

    if alert_names:
        print(f"  АЛЕРТІВ: {len(alert_names)} з {len(ok_names)} оцінених "
              f"({100.0 * len(alert_names) / max(1, len(ok_names)):.0f}%)")
        for name, z, value, n in sorted(
                alert_names,
                key=lambda t: -abs(t[1]) if isinstance(t[1], (int, float))
                else 0.0):
            z_s = f"{z:+.2f}" if isinstance(z, (int, float)) else "?"
            v_s = f"{value:+.5f}" if isinstance(value, (int, float)) else "?"
            print(f"    [ALERT] {name:<20} z={z_s:>7}  value={v_s}  n={n}")
    else:
        print(f"  АЛЕРТІВ: 0 з {len(ok_names)} оцінених")

    if skip_names:
        print(f"  SKIP-фічі: {len(skip_names)}")
        for name, reason in skip_names:
            print(f"    [SKIP] {name}: {reason}")
    else:
        print("  SKIP-фічі: 0")

    quality = latest.get("quality_last")
    if isinstance(quality, list) and quality:
        print(f"  Прапорців якості останнього знімка: {len(quality)}")
        for q in quality[:10]:
            print(f"    [FLAG] {q}")
        if len(quality) > 10:
            print(f"    ... ще {len(quality) - 10}")

    skips_last = latest.get("skips_last")
    if isinstance(skips_last, dict) and skips_last:
        print(f"  Пропущених фіч на останньому знімку: {len(skips_last)}")
        for k, v in sorted(skips_last.items()):
            print(f"    [SKIP] {k}: {v}")

    if len(files) < min_history:
        print(f"  [РЕЖИМ] SKIP: історії {len(files)} < MIN_HISTORY={min_history}. "
              f"Ще потрібно ~{min_history - len(files)} знімків.")
    else:
        print(f"  [РЕЖИМ] Достатньо історії ({len(files)} >= {min_history}) — z-оцінки мають бути активні.")


def summarize_logs():
    section(f"ЛОГИ (cron) — {LOG_DIR}")
    if not os.path.isdir(LOG_DIR):
        print("  [SKIP] Тека logs відсутня.")
        return

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    for prefix in ("snapshot", "rv", "anomaly"):
        path = os.path.join(LOG_DIR, f"{prefix}_{today}.log")
        print(f"\n  --- {prefix}_{today}.log ---")
        if not os.path.isfile(path):
            print("  [SKIP] Лог за сьогодні відсутній (можливо, ще не спрацював cron).")
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            tail = lines[-10:] if len(lines) > 10 else lines
            for line in tail:
                print("  " + line.rstrip())
        except Exception as e:
            print(f"  [FAIL] Не вдалось прочитати лог: {e}")


def main():
    min_history = int(os.environ.get("VOLEDGE_MIN_HISTORY", MIN_HISTORY_DEFAULT))

    print("Vol-Edge — статус даних на VPS")
    print(f"Час перевірки (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"ROOT: {ROOT}")

    chain_files = summarize_dir("chain", ARTIFACT_DIRS["chain"])
    summarize_chain(chain_files)

    rv_files = summarize_dir("rv", ARTIFACT_DIRS["rv"])

    anomaly_files = summarize_dir("anomaly", ARTIFACT_DIRS["anomaly"])
    summarize_anomaly(anomaly_files, min_history)

    summarize_logs()

    section("ПІДСУМОК")
    print(f"  chain:   {len(chain_files)} файлів")
    print(f"  rv:      {len(rv_files)} файлів")
    print(f"  anomaly: {len(anomaly_files)} файлів (MIN_HISTORY={min_history})")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FAIL] Непередбачена помилка: {e}")
        sys.exit(1)
