# -*- coding: utf-8 -*-
"""
[EN] One-off probe: reads the Derive fee schedule (maker/taker rates,
base fee, cap) from the public instruments endpoint. The server response
is printed in full, without truncation.

--- Ukrainian original below ---
defi_fee_probe.py — РАЗОВА діагностика: тарифи Derive з довідника API.

Питання: чи несе public/get_instruments ставки комісій (maker/taker,
base_fee, стеля). Відповідь сервера друкується ПОВНІСТЮ, без обрізання
(клас 10). Нічого не пише в artifacts/, нічого не гейтить.

Змінних оточення НЕ читає. Будь-яка VOLEDGE_* у середовищі -> [ENV-WARN].
Запуск: python defi_fee_probe.py (подвійний клік теж працює).
"""
import json
import os
import sys
import urllib.request
import urllib.error

BASE_URL = "https://api.lyra.finance"
CURRENCIES = ("BTC", "ETH")
TIMEOUT_S = 20
SHOW_FULL = 2          # скільки записів на валюту друкувати повністю
KNOWN_ENV = ()         # точка входу не читає жодної VOLEDGE_*
FEE_HINTS = ("fee", "cap")


def env_warn():
    n = 0
    for k in sorted(os.environ):
        if k.startswith("VOLEDGE_") and k not in KNOWN_ENV:
            print("[ENV-WARN] %s задано, але цей скрипт його НЕ читає" % k)
            n += 1
    return n


def post(method, params):
    url = BASE_URL + "/public/" + method
    body = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "vol-edge-fee-probe/1",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as _rfh:
            raw = _rfh.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        print("[HTTP-ERROR] %s %s" % (e.code, url))
        print(raw)
        raise
    return json.loads(raw)


def probe_currency(cur, counters):
    print("\n=== %s ===" % cur)
    resp = post("get_instruments", {
        "currency": cur, "instrument_type": "option", "expired": False})
    if "result" not in resp:
        print("[NO-RESULT] повна відповідь:")
        print(json.dumps(resp, ensure_ascii=False, indent=1))
        counters["no_result"] += 1
        return
    items = resp["result"]
    if isinstance(items, dict):
        items = items.get("instruments", items.get("data", []))
    counters["instruments"] += len(items)
    print("інструментів: %d" % len(items))
    if not items:
        counters["empty"] += 1
        return

    keys = sorted(items[0].keys())
    print("полів у записі: %d" % len(keys))
    print("поля: " + ", ".join(keys))

    for it in items[:SHOW_FULL]:
        print("\n-- повний запис --")
        print(json.dumps(it, ensure_ascii=False, indent=1))

    fee_keys = [k for k in keys if any(h in k.lower() for h in FEE_HINTS)]
    if not fee_keys:
        print("\n[NO-FEE-FIELDS] жодного поля з 'fee'/'cap' у довіднику %s" % cur)
        counters["no_fee_fields"] += 1
        return
    counters["fee_fields"] += len(fee_keys)
    print("\nполя комісій: " + ", ".join(fee_keys))
    for k in fee_keys:
        vals = {}
        for it in items:
            v = json.dumps(it.get(k), ensure_ascii=False)
            vals[v] = vals.get(v, 0) + 1
        print("  %s: %d різних значень" % (k, len(vals)))
        for v, c in sorted(vals.items(), key=lambda x: -x[1]):
            print("    %s  x%d" % (v, c))


def main():
    counters = {"instruments": 0, "empty": 0, "no_result": 0,
                "no_fee_fields": 0, "fee_fields": 0, "failed": 0}
    env_warn()
    print("хост: %s" % BASE_URL)
    for cur in CURRENCIES:
        try:
            probe_currency(cur, counters)
        except Exception as e:  # явний лічильник, не тиша
            counters["failed"] += 1
            print("[FAIL] %s: %r" % (cur, e))
    print("\n[COUNTERS] " + json.dumps(counters, ensure_ascii=False))
    if counters["failed"] or counters["no_result"]:
        print("[STATUS] ПОМИЛКА: є валюти без відповіді")
        return 2
    if counters["instruments"] == 0:
        print("[STATUS] ПОМИЛКА: довідник порожній по всіх валютах")
        return 2
    if counters["fee_fields"] == 0:
        print("[STATUS] ПОЛІВ КОМІСІЙ НЕМАЄ — тариф лише з документації (верхня межа)")
        return 1
    print("[STATUS] поля комісій знайдено — переписати в STATE з датою")
    return 0


if __name__ == "__main__":
    rc = 3
    try:
        rc = main()
    except Exception as e:
        print("[FATAL] %r" % (e,))
        rc = 3
    finally:
        print("exit code: %d" % rc)
        try:
            input("Натисніть Enter для виходу...")
        except EOFError:
            pass
    sys.exit(rc)
