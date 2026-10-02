#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[EN] Collector of Derive option quotes (public API, no keys). Collection,
not measurement: raw tickers are stored as returned, field mapping is
left to the evaluator. Writes gzip JSON to artifacts/defi/.

--- Ukrainian original below ---
analytics/defi_collect.py — ЗБІР котирувань Derive. НЕ ВИМІРЮВАННЯ.

Пише в artifacts/defi/. Жоден модуль цього не читає і читати не мусить,
доки немає оцінювача d_iv. Висновків із цих полів НЕ РОБИТИ до
накопичення ряду.

--------------------------------------------------------------------
КЛЮЧОВЕ РІШЕННЯ: ТІКЕР ЗБЕРІГАЄТЬСЯ СИРИМ, БЕЗ ІНТЕРПРЕТАЦІЇ ПОЛІВ.

Пробник 2 показав, що get_ticker повертає стиснуті однолітерні ключі
('A','B','I','M','a','b','f','maxp' + stats з 'c','h','l','n','oi',
'p','pr','v'). Яке з них bid, а яке ask — НЕ ОЧЕВИДНО, і здогад тут
коштував би цілого ряду: помилка в мапінгу виявилась би через місяці,
коли оцінювач видав би від'ємні спреди.

Тому колектор НІЧОГО не мапить. Він кладе об'єкт як є. Мапінг — робота
оцінювача, і вона робиться ОДИН РАЗ, з очима на реальні значення, які
цей колектор і надрукує в preflight.

Це рівно та сама логіка, що й у schema 2 ланцюга: спершу поле в
артефакті, читачі потім.
--------------------------------------------------------------------

СЕРІАЛІЗОВАНИЙ ЗБІР САМ Є ЕКСПОЗИЦІЄЮ (клас 4). Прохід опитує
інструменти по черзі, тож останній має на десятки секунд більше часу
"зрушити ціну", ніж перший. Тому в КОЖНОМУ записі лежать `seq`
(порядок опитування) і `ts_fetch_ms` (коли саме його взяли), а в корені
— `elapsed_s`. Без цих полів майбутній читач порівнював би величини,
зняті в різні моменти, і не мав би як це побачити.

ДВІ СТЕЛІ, і це не надмірність: бюджет запитів захищає від несподівано
довгого лістингу (нова експірація додає десятки інструментів), бюджет
часу — від повільної мережі при нормальній довжині. ОДНА стеля ловить
рівно ОДИН із двох випадків.

Запуск: подвійний клік або `python3 defi_collect.py`.
Самотести без мережі: VOLEDGE_OFFLINE=1.
Лише preflight (один тікер, без проходу): VOLEDGE_DEFI_C_PREFLIGHT_ONLY=1.
"""

import gzip
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_force_utf8_stdio()

# Корінь репозиторію від РОЗТАШУВАННЯ ФАЙЛА, не від CWD.
# Заміряно 26 вер: обгортка cron не робить cd, тож при відносному
# дефолті артефакт ліг у ~/artifacts/defi замість
# vol-edge/artifacts/defi. Лог показав відносний шлях і exit=0 —
# успішний запис НЕ ТУДИ виглядає точно як успішний запис (клас 10).
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
DEFAULT_ART_DIR = os.path.join(_REPO_ROOT, "artifacts", "defi")

# --------------------------------------------------------------------------
# КАНОН [ENV-WARN]
# --------------------------------------------------------------------------

KNOWN_ENV = (
    "VOLEDGE_OFFLINE",
    "VOLEDGE_DEFI_C_CURRENCIES",
    "VOLEDGE_DEFI_C_BASE_URL",
    "VOLEDGE_DEFI_C_TIMEOUT_S",
    "VOLEDGE_DEFI_C_BUDGET",
    "VOLEDGE_DEFI_C_MAX_S",
    "VOLEDGE_DEFI_C_SLEEP_MS",
    "VOLEDGE_DEFI_C_ARTIFACT_DIR",
    "VOLEDGE_DEFI_C_GZIP",
    "VOLEDGE_DEFI_C_NO_WRITE",
    "VOLEDGE_DEFI_C_PREFLIGHT_ONLY",
    "VOLEDGE_DEFI_C_MODE",
)


def env_warn_scan():
    return [n for n in sorted(os.environ)
            if n.startswith("VOLEDGE_") and n not in KNOWN_ENV]


def env_str(name, default):
    if name not in KNOWN_ENV:
        raise RuntimeError("env_str: %s не оголошено в KNOWN_ENV" % name)
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def env_int(name, default, minimum=None, maximum=None):
    raw = env_str(name, None)
    if raw is None:
        return default
    try:
        val = int(raw)
    except ValueError:
        # Тихий дефолт на стелі означав би прохід іншої тривалості,
        # ніж просили. Падаємо до початку збору.
        raise RuntimeError("%s: не число: %r" % (name, raw))
    if minimum is not None and val < minimum:
        raise RuntimeError("%s: %d < мінімуму %d" % (name, val, minimum))
    if maximum is not None and val > maximum:
        raise RuntimeError("%s: %d > максимуму %d" % (name, val, maximum))
    return val


def new_counters():
    return {
        "instruments_listed": 0,
        "drop_bad_name": 0,
        "drop_suffix": 0,
        "drop_currency": 0,
        "drop_inactive": 0,
        "candidates": 0,
        "drop_duplicate": 0,
        "ticker_ok": 0,
        "ticker_failed": 0,
        "budget_hit": 0,
        "time_hit": 0,
        "http_calls": 0,
        "http_retries": 0,
    }


# --------------------------------------------------------------------------
# БІЛИЙ СПИСОК ІМЕНІ (клас 8)
# --------------------------------------------------------------------------

def parse_derive_name(name):
    if not isinstance(name, str) or not name or name != name.upper():
        return None
    parts = name.split("-")
    if len(parts) == 4:
        cur, ymd, strike_s, kind = parts
        ru = None
    elif len(parts) == 5:
        cur, ymd, strike_s, kind, ru_s = parts
        if not ru_s.isdigit():
            return None
        ru = int(ru_s)
    else:
        return None
    if not cur.isalnum() or not cur.isupper():
        return None
    if len(ymd) != 8 or not ymd.isdigit():
        return None
    try:
        datetime.strptime(ymd, "%Y%m%d")
    except ValueError:
        return None
    if kind not in ("C", "P"):
        return None
    if "_" in strike_s:
        head, _, tail = strike_s.partition("_")
        if not head.isdigit() or not tail.isdigit() or tail == "":
            return None
        strike = float(head + "." + tail)
    else:
        if not strike_s.isdigit():
            return None
        strike = float(strike_s)
    if not (strike > 0.0):
        return None
    return (cur, ymd, strike, kind, ru)


# --------------------------------------------------------------------------
# МЕРЕЖА
# --------------------------------------------------------------------------

class ApiError(RuntimeError):
    """Детермінована помилка від сервера."""


class TransportError(RuntimeError):
    """Мережевий збій — має сенс повторити."""


def http_post_json(url, payload, timeout_s, counters):
    counters["http_calls"] += 1
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": "vol-edge-defi-collect/1"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        if exc.code == 429 or exc.code >= 500:
            raise TransportError("HTTP %s: %r" % (exc.code, raw[:120]))
        raise ApiError("HTTP %s: %r" % (exc.code, raw[:120]))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TransportError("%s: %s" % (type(exc).__name__, exc))
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TransportError("невалідний JSON: %s" % exc)


def unwrap(doc):
    if not isinstance(doc, dict):
        raise RuntimeError("відповідь не об'єкт")
    if doc.get("error"):
        raise RuntimeError("API error: %r" % (doc["error"],))
    if "result" in doc:
        return doc["result"]
    return doc


def make_list_fn(base_url, timeout_s, counters):
    url = base_url.rstrip("/") + "/public/get_all_instruments"

    def list_fn(currency):
        out = []
        page = 1
        declared = None
        while True:
            if page > 100:
                raise RuntimeError("лістинг: понад 100 сторінок — межа явна")
            res = unwrap(http_post_json(url, {
                "currency": currency, "instrument_type": "option",
                "expired": False, "page": page, "page_size": 1000},
                timeout_s, counters))
            if not isinstance(res, dict):
                raise RuntimeError("лістинг: result не об'єкт")
            arr = res.get("instruments")
            if not isinstance(arr, list):
                raise RuntimeError("лістинг: 'instruments' не список, ключі=%s"
                                   % (sorted(res.keys())[:8],))
            pag = res.get("pagination") or {}
            if page == 1 and isinstance(pag.get("count"), int):
                declared = pag["count"]
            out.extend(arr)
            npages = pag.get("num_pages")
            if not arr:
                break
            if isinstance(npages, int) and page >= npages:
                break
            if not isinstance(npages, int) and len(arr) < 1000:
                break
            page += 1
        if declared is not None and declared != len(out):
            raise RuntimeError("лістинг: зібрано %d, заявлено %d — "
                               "сторінкування НЕ повне" % (len(out), declared))
        return out

    return list_fn


def make_ticker_fn(base_url, timeout_s, counters):
    url = base_url.rstrip("/") + "/public/get_ticker"

    def ticker_fn(instrument_name):
        last = None
        for attempt in range(2):
            try:
                return unwrap(http_post_json(
                    url, {"instrument_name": instrument_name},
                    timeout_s, counters))
            except TransportError as exc:
                last = exc
                counters["http_retries"] += 1
                time.sleep(0.8 * (attempt + 1))
            except (ApiError, RuntimeError) as exc:
                raise
        raise TransportError("тікер %s: транспорт двічі: %s"
                             % (instrument_name, last))

    return ticker_fn


# --------------------------------------------------------------------------
# ПРОХІД
# --------------------------------------------------------------------------

def collect_currency(currency, list_fn, ticker_fn, budget, max_s, sleep_ms,
                     counters, log):
    """-> (records, meta). Провал ОКРЕМОГО тікера знімок НЕ валить."""
    rows = list_fn(currency)
    candidates = []
    for row in rows:
        counters["instruments_listed"] += 1
        if not isinstance(row, dict):
            counters["drop_bad_name"] += 1
            continue
        p = parse_derive_name(row.get("instrument_name"))
        if p is None:
            counters["drop_bad_name"] += 1
            continue
        cur, ymd, strike, kind, ru = p
        if ru is not None:
            counters["drop_suffix"] += 1
            continue
        if cur != currency:
            counters["drop_currency"] += 1
            continue
        if row.get("is_active") is False:
            counters["drop_inactive"] += 1
            continue
        candidates.append((row["instrument_name"], ymd, strike, kind))

    candidates.sort()
    counters["candidates"] = len(candidates)
    log("  [%s] кандидатів %d (з %d у лістингу)"
        % (currency, len(candidates), counters["instruments_listed"]))

    records = []
    t0 = time.time()
    status = "ok"
    for seq, (name, ymd, strike, kind) in enumerate(candidates):
        if counters["http_calls"] >= budget:
            counters["budget_hit"] = 1
            status = "budget_hit"
            break
        if (time.time() - t0) >= max_s:
            counters["time_hit"] = 1
            status = "time_hit"
            break

        rec = {
            "instrument_name": name,
            "expiry_ymd": ymd,
            "strike": strike,
            "kind": kind,
            "seq": seq,
            "ts_fetch_ms": int(time.time() * 1000),
        }
        try:
            rec["ticker_raw"] = ticker_fn(name)
            rec["status"] = "ok"
            counters["ticker_ok"] += 1
        except Exception as exc:
            rec["ticker_raw"] = None
            rec["status"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
            counters["ticker_failed"] += 1
        records.append(rec)

        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)
        if (seq + 1) % 100 == 0:
            log("    ... %d/%d, %.0f с" % (seq + 1, len(candidates),
                                           time.time() - t0))

    elapsed = time.time() - t0
    meta = {
        "status": status,
        "elapsed_s": round(elapsed, 2),
        "n_candidates": len(candidates),
        "n_records": len(records),
    }
    return records, meta


def _num(x):
    """Поля приходять десятковими РЯДКАМИ. -> float або None."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        v = float(x)
    elif isinstance(x, str):
        s = x.strip()
        if s == "":
            return None
        try:
            v = float(s)
        except ValueError:
            return None
    else:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


# Розшифровано 2026-09-25. ДВІ РІЗНІ СХЕМИ, і плутати їх не можна:
#
# v3 (api.derive.xyz/v3) — стиснуті однолітерні ключі. Мапінг
#   підтверджено перевіркою delta на BTC-20270924-95000-P:
#   d = -0.50137 при f=88015, K=95000, i=0.38738, tau~1р відтворюється
#   як N(d1)-1 = -0.5015. Збіг до 4-го знака -> не здогад.
#   АЛЕ: заміряно, що v3 віддає oi=0 там, де v2 показує 10046 ->
#   дані v3 НЕПОВНІ. Джерелом істини вважаємо v2.
#
# v2 (api.lyra.finance) — людські імена, плюс глибина на 5% і
#   open_interest з розбивкою по менеджерах ризику (PM/PM2/SM).
#
# СХЕМА ВИЗНАЧАЄТЬСЯ ЯВНО. Невідома схема -> окремий лічильник, а не
# тихі нулі: мовчазний нуль тут виглядав би як "ринку немає".
SCHEMA_V3 = {"bid_px": "b", "ask_px": "a", "bid_sz": "B", "ask_sz": "A"}
SCHEMA_V2 = {"bid_px": "best_bid_price", "ask_px": "best_ask_price",
             "bid_sz": "best_bid_amount", "ask_sz": "best_ask_amount"}


def detect_schema(tk):
    """-> 'v2' | 'v3' | None. Без евристик: за наявністю ключа."""
    if not isinstance(tk, dict):
        return None
    if "best_bid_price" in tk:
        return "v2"
    if "b" in tk and "a" in tk:
        return "v3"
    return None


def _oi_v2(tk):
    """open_interest у v2 — словник менеджерів зі списками. Сумуємо."""
    oi = tk.get("open_interest")
    if not isinstance(oi, dict):
        return None
    total = 0.0
    seen = False
    for _mgr, arr in oi.items():
        if not isinstance(arr, list):
            continue
        for item in arr:
            if not isinstance(item, dict):
                continue
            v = _num(item.get("current_open_interest"))
            if v is not None:
                total += v
                seen = True
    return total if seen else None


def quote_tally(records):
    """Скільки інструментів РЕАЛЬНО котирується.

    Критично, бо mark і mark-IV існують і БЕЗ котирувань (модельні).
    d_iv проти mark порівнював би дві моделі, а не ціни.

    Нічого не дропає. Лічильники, не фільтр.
    """
    t = {"n": 0, "two_sided": 0, "one_sided": 0, "empty": 0,
         "crossed": 0, "oi_positive": 0, "no_ticker": 0,
         "schema_v2": 0, "schema_v3": 0, "schema_unknown": 0,
         "has_their_iv": 0, "has_their_fwd": 0, "has_depth5": 0}
    for r in records:
        t["n"] += 1
        tk = r.get("ticker_raw")
        sch = detect_schema(tk)
        if sch is None:
            t["no_ticker" if not isinstance(tk, dict)
              else "schema_unknown"] += 1
            continue
        t["schema_" + sch] += 1
        fields = SCHEMA_V2 if sch == "v2" else SCHEMA_V3

        bid = _num(tk.get(fields["bid_px"]))
        ask = _num(tk.get(fields["ask_px"]))
        has_bid = bid is not None and bid > 0.0
        has_ask = ask is not None and ask > 0.0
        if has_bid and has_ask:
            t["two_sided"] += 1
            if bid > ask:
                t["crossed"] += 1
        elif has_bid or has_ask:
            t["one_sided"] += 1
        else:
            t["empty"] += 1

        if sch == "v2":
            oi = _oi_v2(tk)
            if _num(tk.get("five_percent_bid_depth")) is not None:
                t["has_depth5"] += 1
            op = tk.get("option_pricing")
            iv_key, fwd_key = "iv", "forward_price"
        else:
            stats = tk.get("stats")
            oi = _num(stats.get("oi")) if isinstance(stats, dict) else None
            op = tk.get("option_pricing")
            iv_key, fwd_key = "i", "f"
        if oi is not None and oi > 0.0:
            t["oi_positive"] += 1
        if isinstance(op, dict):
            if _num(op.get(iv_key)) is not None:
                t["has_their_iv"] += 1
            if _num(op.get(fwd_key)) is not None:
                t["has_their_fwd"] += 1
    return t


def ticker_key_inventory(records):
    """Які ключі взагалі трапились. Діагностика, не інтерпретація."""
    top = {}
    nested = {}
    for r in records:
        t = r.get("ticker_raw")
        if not isinstance(t, dict):
            continue
        for k, v in t.items():
            top[k] = top.get(k, 0) + 1
            if isinstance(v, dict):
                for k2 in v:
                    nested.setdefault(k, {})
                    nested[k][k2] = nested[k].get(k2, 0) + 1
    return top, nested


# --------------------------------------------------------------------------
# САМОТЕСТИ
# --------------------------------------------------------------------------

def selftests(log):
    n_ok = 0
    n_fail = 0

    def check(label, cond):
        nonlocal n_ok, n_fail
        if cond:
            n_ok += 1
        else:
            n_fail += 1
            log("  [FAIL] %s" % label)

    # [1] парсер
    check("1.1 базове",
          parse_derive_name("BTC-20260926-82000-P")[2] == 82000.0)
    check("1.2 дріб", parse_derive_name("BTC-20260626-65000_5-P")[2] == 65000.5)
    check("1.3 ru", parse_derive_name("ETH-20240914-2400-C-42")[4] == 42)
    check("1.4 PERP", parse_derive_name("BTC-PERP") is None)
    check("1.5 регістр", parse_derive_name("btc-20260926-82000-P") is None)
    check("1.6 None", parse_derive_name(None) is None)

    def mk_rows(n, cur="BTC"):
        return [{"instrument_name": "%s-20260926-%d-C" % (cur, 80000 + i * 1000),
                 "is_active": True} for i in range(n)]

    # [2] нормальний прохід
    c = new_counters()
    recs, meta = collect_currency(
        "BTC", lambda cur: mk_rows(5),
        lambda nm: {"a": 1, "b": 2, "stats": {"v": 3}},
        1000, 60, 0, c, lambda s: None)
    check("2.1 п'ять записів", len(recs) == 5)
    check("2.2 усі ok", c["ticker_ok"] == 5 and c["ticker_failed"] == 0)
    check("2.3 статус ok", meta["status"] == "ok")
    check("2.4 seq зростає", [r["seq"] for r in recs] == [0, 1, 2, 3, 4])
    check("2.5 ts_fetch_ms є", all(isinstance(r["ts_fetch_ms"], int)
                                   for r in recs))
    check("2.6 сирий тікер збережено",
          recs[0]["ticker_raw"] == {"a": 1, "b": 2, "stats": {"v": 3}})

    # [3] СТЕЛЯ БЮДЖЕТУ
    # Фейк МУСИТЬ витрачати http_calls, інакше тест перевіряє не стелю,
    # а власну бездіяльність.
    c = new_counters()
    c["http_calls"] = 990

    def counting(nm):
        c["http_calls"] += 1
        return {"a": 1}

    recs, meta = collect_currency(
        "BTC", lambda cur: mk_rows(50), counting, 1000, 60, 0, c,
        lambda s: None)
    check("3.1 бюджет спрацював", c["budget_hit"] == 1)
    check("3.2 статус budget_hit", meta["status"] == "budget_hit")
    check("3.3 зібрано менше за кандидатів",
          meta["n_records"] < meta["n_candidates"])
    check("3.4 зібрано рівно до стелі", meta["n_records"] == 10)

    # [4] СТЕЛЯ ЧАСУ
    c = new_counters()
    slow_calls = {"n": 0}

    def slow(nm):
        slow_calls["n"] += 1
        time.sleep(0.02)
        return {"a": 1}

    recs, meta = collect_currency(
        "BTC", lambda cur: mk_rows(200), slow, 100000, 0.05, 0, c,
        lambda s: None)
    check("4.1 час спрацював", c["time_hit"] == 1)
    check("4.2 статус time_hit", meta["status"] == "time_hit")

    # [5] ПРОВАЛ ОКРЕМОГО ТІКЕРА знімок не валить
    c = new_counters()

    def flaky(nm):
        if nm.endswith("81000-C"):
            raise TransportError("симульований збій")
        return {"a": 1}

    recs, meta = collect_currency(
        "BTC", lambda cur: mk_rows(4), flaky, 1000, 60, 0, c, lambda s: None)
    check("5.1 усі записи на місці", len(recs) == 4)
    check("5.2 один провал порахований", c["ticker_failed"] == 1)
    check("5.3 решта ok", c["ticker_ok"] == 3)
    bad = [r for r in recs if r["ticker_raw"] is None]
    check("5.4 причина записана", len(bad) == 1
          and "TransportError" in bad[0]["status"])

    # [6] ПОВНИЙ ПРОВАЛ проходу
    c = new_counters()

    def always_fail(nm):
        raise TransportError("усе погано")

    recs, meta = collect_currency(
        "BTC", lambda cur: mk_rows(3), always_fail, 1000, 60, 0, c,
        lambda s: None)
    check("6.1 записи є, тікери None", len(recs) == 3
          and all(r["ticker_raw"] is None for r in recs))
    check("6.2 status проходу лишився ok (стелі ні до чого)",
          meta["status"] == "ok")
    check("6.3 лічильник провалів", c["ticker_failed"] == 3)

    # [7] фільтрація лістингу
    c = new_counters()
    rows = [
        {"instrument_name": "BTC-20260926-80000-C", "is_active": True},
        {"instrument_name": "BTC-PERP", "is_active": True},
        {"instrument_name": "ETH-20260926-2400-C", "is_active": True},
        {"instrument_name": "BTC-20260926-81000-C-7", "is_active": True},
        {"instrument_name": "BTC-20260926-82000-C", "is_active": False},
    ]
    recs, meta = collect_currency(
        "BTC", lambda cur: rows, lambda nm: {"a": 1}, 1000, 60, 0, c,
        lambda s: None)
    check("7.1 один кандидат", meta["n_candidates"] == 1)
    check("7.2 не опціон", c["drop_bad_name"] == 1)
    check("7.3 чужа валюта", c["drop_currency"] == 1)
    check("7.4 суфікс", c["drop_suffix"] == 1)
    check("7.5 неактивний", c["drop_inactive"] == 1)
    check("7.6 сума дропів + кандидати = лістинг",
          c["drop_bad_name"] + c["drop_currency"] + c["drop_suffix"]
          + c["drop_inactive"] + c["candidates"] == c["instruments_listed"])

    # [8] ПОРОЖНІЙ лістинг не падає
    c = new_counters()
    recs, meta = collect_currency(
        "BTC", lambda cur: [], lambda nm: {"a": 1}, 1000, 60, 0, c,
        lambda s: None)
    check("8.1 нуль записів", recs == [] and meta["n_records"] == 0)

    # [9] інвентар ключів
    top, nested = ticker_key_inventory([
        {"ticker_raw": {"a": 1, "b": 2, "stats": {"v": 1, "oi": 2}}},
        {"ticker_raw": {"a": 1, "stats": {"v": 1}}},
        {"ticker_raw": None},
    ])
    check("9.1 top рахує", top["a"] == 2 and top["b"] == 1)
    check("9.2 nested рахує", nested["stats"]["v"] == 2
          and nested["stats"]["oi"] == 1)
    check("9.3 None ігнорується", "None" not in top)

    # [9a] підрахунок котирувань — схема v3
    tal = quote_tally([
        {"ticker_raw": {"b": "10", "a": "12", "stats": {"oi": "5"},
                        "option_pricing": {"i": "0.4", "f": "88000"}}},
        {"ticker_raw": {"b": "0", "a": "0", "stats": {"oi": "0"},
                        "option_pricing": {"i": "0.4", "f": "88000"}}},
        {"ticker_raw": {"b": "10", "a": "0"}},
        {"ticker_raw": {"b": "13", "a": "12"}},
        {"ticker_raw": None},
    ])
    check("9a.1 всього", tal["n"] == 5)
    check("9a.2 двосторонніх", tal["two_sided"] == 2)
    check("9a.3 односторонніх", tal["one_sided"] == 1)
    check("9a.4 порожніх", tal["empty"] == 1)
    check("9a.5 схрещена книга", tal["crossed"] == 1)
    check("9a.6 без тікера", tal["no_ticker"] == 1)
    check("9a.7 oi>0", tal["oi_positive"] == 1)
    check("9a.8 їхня IV є і БЕЗ котирувань — модельна",
          tal["has_their_iv"] == 2)
    check("9a.9 схема v3 розпізнана", tal["schema_v3"] == 4)

    # [9b] схема v2 — ІНШІ імена полів
    v2row = {
        "best_bid_price": "100", "best_ask_price": "110",
        "best_bid_amount": "1", "best_ask_amount": "2",
        "five_percent_bid_depth": "5",
        "open_interest": {
            "PM": [{"current_open_interest": "0"}],
            "PM2": [{"current_open_interest": "10046.67"}],
            "SM": [{"current_open_interest": "1929.55"}],
        },
        "option_pricing": {"iv": "0.387", "forward_price": "88183"},
    }
    tal2 = quote_tally([{"ticker_raw": v2row}])
    check("9b.1 схема v2 розпізнана", tal2["schema_v2"] == 1)
    check("9b.2 двостороння книга", tal2["two_sided"] == 1)
    check("9b.3 oi просумовано по менеджерах", tal2["oi_positive"] == 1)
    check("9b.4 глибина 5% помічена", tal2["has_depth5"] == 1)
    check("9b.5 їхня IV", tal2["has_their_iv"] == 1)
    check("9b.6 їхній форвард", tal2["has_their_fwd"] == 1)

    # [9c] v2 з НУЛЬОВИМИ котируваннями, але НЕНУЛЬОВИМ oi —
    # реальний випадок BTC-20270924-95000-P
    v2empty = dict(v2row, best_bid_price="0", best_ask_price="0")
    tal3 = quote_tally([{"ticker_raw": v2empty}])
    check("9c.1 книга порожня", tal3["empty"] == 1)
    check("9c.2 але oi є", tal3["oi_positive"] == 1)

    # [9d] НЕВІДОМА схема -> окремий лічильник, НЕ тихий нуль
    tal4 = quote_tally([{"ticker_raw": {"щось": 1}}])
    check("9d.1 невідома схема порахована", tal4["schema_unknown"] == 1)
    check("9d.2 не зарахована як порожня книга", tal4["empty"] == 0)

    # [9e] detect_schema
    check("9e.1 v2", detect_schema({"best_bid_price": "0"}) == "v2")
    check("9e.2 v3", detect_schema({"a": "0", "b": "0"}) == "v3")
    check("9e.3 None", detect_schema(None) is None)
    check("9e.4 чуже", detect_schema({"x": 1}) is None)

    # [9f] лістинг із котируваннями / без
    ok_carry, sch = listing_carries_quotes([{"best_bid_price": "1"}])
    check("9f.1 лістинг несе котирування", ok_carry and sch == "v2")
    ok_carry, sch = listing_carries_quotes([{"instrument_name": "X"}])
    check("9f.2 лістинг без котирувань", not ok_carry)
    ok_carry, sch = listing_carries_quotes([])
    check("9f.3 порожній лістинг", not ok_carry)

    # [9g] _oi_v2
    check("9g.1 сума по менеджерах",
          abs(_oi_v2(v2row) - 11976.22) < 1e-6)
    check("9g.2 немає поля", _oi_v2({}) is None)

    # [9h] _num
    check("9h.1 рядок", _num("1.5") == 1.5)
    check("9h.2 bool", _num(True) is None)
    check("9h.3 None", _num(None) is None)
    check("9h.4 порожній", _num("") is None)

    # [9i] normalize_bulk: форми відповіді
    check("9i.1 список", normalize_bulk([{"a": 1}]) == [{"a": 1}])
    check("9i.2 ключ tickers зі СПИСКОМ",
          normalize_bulk({"tickers": [{"a": 1}]}) == [{"a": 1}])
    check("9i.2b ключ tickers зі СЛОВНИКОМ (реальна форма v2)",
          normalize_bulk({"tickers": {"BTC-1": {"instrument_name": "BTC-1"},
                                      "BTC-2": {"instrument_name": "BTC-2"}}})
          == [{"instrument_name": "BTC-1"}, {"instrument_name": "BTC-2"}])
    _nb = normalize_bulk({"tickers": {"BTC-20260926-80000-C": {"a": "1"}}})
    check("9i.2b1 ім'я взято з КЛЮЧА, коли в записі його немає",
          _nb == [{"a": "1",
                   "instrument_name": "BTC-20260926-80000-C"}])
    _src = {"a": "1"}
    normalize_bulk({"tickers": {"BTC-X": _src}})
    check("9i.2b2 вихідний запис не змінено", _src == {"a": "1"})
    check("9i.2b3 наявне ім'я не перезаписується",
          normalize_bulk({"tickers": {"КЛЮЧ": {"instrument_name": "СПРАВЖНЄ"}}})
          == [{"instrument_name": "СПРАВЖНЄ"}])
    check("9i.2c порожній словник tickers",
          normalize_bulk({"tickers": {}}) == [])
    _c = False
    try:
        normalize_bulk({"tickers": "рядок"})
    except RuntimeError:
        _c = True
    check("9i.2d tickers не контейнер -> помилка", _c)
    check("9i.3 словник name->тікер",
          len(normalize_bulk({"X": {"instrument_name": "X"},
                              "Y": {"instrument_name": "Y"}})) == 2)
    caught = False
    try:
        normalize_bulk({"zzz": 1})
    except RuntimeError:
        caught = True
    check("9i.4 невідома форма -> помилка, НЕ порожній список", caught)
    caught = False
    try:
        normalize_bulk("рядок")
    except RuntimeError:
        caught = True
    check("9i.5 скаляр -> помилка", caught)
    check("9i.6 порожній список", normalize_bulk([]) == [])

    # [9j] collect_bulk по експіраціях
    c = new_counters()
    bulk_rows = [
        {"instrument_name": "BTC-20260926-80000-C", "best_bid_price": "1",
         "best_ask_price": "2", "is_active": True},
        {"instrument_name": "BTC-20260926-79000-C", "best_bid_price": "0",
         "best_ask_price": "0", "is_active": True},
        {"instrument_name": "BTC-PERP", "best_bid_price": "1"},
        {"instrument_name": "ETH-20260926-2400-C", "best_bid_price": "1"},
        {"instrument_name": "BTC-20260926-81000-C-7", "best_bid_price": "1"},
        {"instrument_name": "BTC-20260926-82000-C", "is_active": False},
    ]
    recs, meta = collect_bulk("BTC", lambda cur: bulk_rows,
                              lambda cur, exp: bulk_rows, [1790000000],
                              0, c, lambda s: None)
    check("9j.1 два кандидати", meta["n_candidates"] == 2)
    check("9j.2 режим bulk", meta["mode"] == "bulk")
    check("9j.3 дропи", c["drop_bad_name"] == 1 and c["drop_currency"] == 1
          and c["drop_suffix"] == 1 and c["drop_inactive"] == 1)
    check("9j.4 ОДНА мітка часу на всіх — немає серіалізованої експозиції",
          len({r["ts_fetch_ms"] for r in recs}) == 1)
    check("9j.5 seq послідовний", [r["seq"] for r in recs] == [0, 1])
    check("9j.6 відсортовано за іменем",
          recs[0]["instrument_name"] < recs[1]["instrument_name"])
    check("9j.7 expiry_sec записано", recs[0]["expiry_sec"] == 1790000000)
    tal_b = quote_tally(recs)
    check("9j.8 книга рахується на пакетних записах",
          tal_b["two_sided"] == 1 and tal_b["empty"] == 1)

    # [9k] ДУБЛІКАТИ між експіраціями не рахуються двічі
    c2b = new_counters()
    recs2, meta2 = collect_bulk("BTC", lambda cur: bulk_rows,
                                lambda cur, exp: bulk_rows,
                                [1790000000, 1790600000], 0, c2b,
                                lambda s: None)
    check("9k.1 дублі відкинуто", meta2["n_records"] == 2)
    check("9k.2 лічильник дублів", c2b["drop_duplicate"] == 2)
    check("9k.3 експірацій у meta", meta2["n_expiries"] == 2)

    # [9k2] ГЕЙТ [ALL-DROPPED]: усе задроплено -> помилка, не тихий 0
    c_all = new_counters()
    _caught = False
    try:
        collect_bulk("BTC", lambda cur: [],
                     lambda cur, exp: [{"instrument_name": "BTC-PERP"},
                                       {"instrument_name": "ETH-1"}],
                     [1790000000], 0, c_all, lambda s: None)
    except RuntimeError as exc:
        _caught = "ALL-DROPPED" in str(exc)
    check("9k2.1 повний дроп піднімає [ALL-DROPPED]", _caught)

    # ПОРОЖНЯ відповідь — НЕ те саме: дропати нічого, гейт мовчить
    c_emp = new_counters()
    recs_e, meta_e = collect_bulk("BTC", lambda cur: [],
                                  lambda cur, exp: [], [1790000000], 0,
                                  c_emp, lambda s: None)
    check("9k2.2 порожня відповідь гейт НЕ піднімає", recs_e == [])
    check("9k2.3 n_dropped у meta", meta_e["n_dropped"] == 0)

    # [9l] експірації з лістингу — з ПОЛЯ, не з імені
    cl = new_counters()
    exps = expiries_from_listing([
        {"instrument_name": "BTC-20260926-80000-C",
         "option_details": {"expiry": 1790409600}},
        {"instrument_name": "BTC-20260926-81000-P",
         "option_details": {"expiry": 1790409600}},
        {"instrument_name": "BTC-20261030-90000-C",
         "option_details": {"expiry": 1792396800}},
        {"instrument_name": "BTC-PERP", "option_details": {"expiry": 1}},
        {"instrument_name": "ETH-20260926-2400-C",
         "option_details": {"expiry": 999}},
        {"instrument_name": "BTC-20260926-82000-C", "is_active": False,
         "option_details": {"expiry": 555}},
        {"instrument_name": "BTC-20261127-90000-C"},
    ], "BTC", cl)
    check("9l.1 дві унікальні експірації", exps == [1790409600, 1792396800])
    check("9l.2 відсортовано", exps == sorted(exps))
    check("9l.3 порожній лістинг", expiries_from_listing([], "BTC", cl) == [])

    # [9m] _expiry_value — чотири форми однієї дати
    _e = int(datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc).timestamp())
    check("9m.1 секунди", _expiry_value(_e, "sec") == _e)
    check("9m.2 мілісекунди", _expiry_value(_e, "ms") == _e * 1000)
    check("9m.3 ymd_int", _expiry_value(_e, "ymd_int") == 20260926)
    check("9m.4 ymd_str", _expiry_value(_e, "ymd_str") == "20260926")
    _caught = False
    try:
        _expiry_value(_e, "казна-що")
    except RuntimeError:
        _caught = True
    check("9m.5 невідома одиниця -> помилка", _caught)
    check("9m.6 expiry_date перший кандидат",
          BULK_PARAM_CANDIDATES[0][0] == "expiry_date")

    # [9n] дефолтна тека артефактів — АБСОЛЮТНА і в репозиторії
    check("9n.1 абсолютний шлях", os.path.isabs(DEFAULT_ART_DIR))
    check("9n.2 закінчується artifacts/defi",
          DEFAULT_ART_DIR.replace("\\", "/").endswith("artifacts/defi"))
    check("9n.3 не залежить від CWD",
          DEFAULT_ART_DIR == os.path.join(
              os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
              "artifacts", "defi"))

    # [10] класи помилок
    check("10.1 ApiError не TransportError",
          not issubclass(ApiError, TransportError))
    check("10.2 обидва RuntimeError",
          issubclass(ApiError, RuntimeError)
          and issubclass(TransportError, RuntimeError))

    # [11] KNOWN_ENV
    check("11.1 без дублів", len(KNOWN_ENV) == len(set(KNOWN_ENV)))
    check("11.2 усі VOLEDGE_*",
          all(n.startswith("VOLEDGE_") for n in KNOWN_ENV))

    log("Самотести: %d/%d" % (n_ok, n_ok + n_fail))
    return n_fail == 0


# --------------------------------------------------------------------------
# PREFLIGHT
# --------------------------------------------------------------------------

# get_tickers вимагає експірацію: заміряно 2026-09-25,
# -32602 "Expiry date is required for options". Отже пакет — це ОДНА
# експірація, не вся валюта. 13 експірацій -> 13 запитів на валюту
# замість 686 поштучних.
#
# Ім'я параметра з повідомлення не випливає, тож кандидати
# перебираються ЯВНО і той, що спрацював, друкується та лягає в
# артефакт. Мовчазний підбір тут був би тихим дефолтом.
# Заміряно 2026-09-25: ім'я параметра — expiry_date. Воно єдине дало
# ІНШУ помилку ("Expiry date must be in ...", а не "is required"),
# тобто ім'я прийнято і не підійшов ФОРМАТ. Тому кандидати
# expiry_date йдуть першими і перебираються за форматом.
BULK_PARAM_CANDIDATES = (
    ("expiry_date", "ymd_int"),
    ("expiry_date", "ymd_str"),
    ("expiry_date", "sec"),
    ("expiry_date", "ms"),
    ("expiry", "ymd_int"),
    ("expiry", "sec"),
    ("expiry_timestamp", "sec"),
    ("expiry_timestamp", "ms"),
)


def _expiry_value(expiry_sec, unit):
    """Одне й те саме в чотирьох формах. Дата береться в UTC."""
    e = int(expiry_sec)
    if unit == "sec":
        return e
    if unit == "ms":
        return e * 1000
    ymd = datetime.fromtimestamp(e, timezone.utc).strftime("%Y%m%d")
    if unit == "ymd_int":
        return int(ymd)
    if unit == "ymd_str":
        return ymd
    raise RuntimeError("_expiry_value: невідома одиниця %r" % unit)


def resolve_bulk_param(base_url, currency, expiry_sec, timeout_s, counters,
                       log):
    """-> (назва, одиниця). Перебір кандидатів з логуванням."""
    url = base_url.rstrip("/") + "/public/get_tickers"
    errors = []
    for name, unit in BULK_PARAM_CANDIDATES:
        payload = {"currency": currency, "instrument_type": "option",
                   name: _expiry_value(expiry_sec, unit)}
        try:
            res = unwrap(http_post_json(url, payload, timeout_s, counters))
        except Exception as exc:
            errors.append("%s/%s -> %s" % (name, unit, str(exc)[:300]))
            continue
        try:
            rows = normalize_bulk(res)
        except RuntimeError as exc:
            errors.append("%s/%s -> форма: %s" % (name, unit, str(exc)[:300]))
            continue
        if rows:
            log("  параметр експірації: %r (%s), тікерів у відповіді: %d"
                % (name, unit, len(rows)))
            return name, unit
        errors.append("%s/%s -> прийнято, але 0 тікерів" % (name, unit))
    raise RuntimeError("get_tickers: жоден кандидат не підійшов:\n    "
                       + "\n    ".join(errors))


def make_bulk_fn(base_url, timeout_s, counters, param_name, param_unit):
    """public/get_tickers по ОДНІЙ експірації.

    get_ticker задепрекейчено на користь get_tickers з 01.12.2025.
    """
    url = base_url.rstrip("/") + "/public/get_tickers"

    def bulk_fn(currency, expiry_sec):
        payload = {"currency": currency, "instrument_type": "option",
                   param_name: _expiry_value(expiry_sec, param_unit)}
        last = None
        for attempt in range(3):
            try:
                return unwrap(http_post_json(url, payload, timeout_s,
                                             counters))
            except TransportError as exc:
                last = exc
                counters["http_retries"] += 1
                time.sleep(1.0 * (attempt + 1))
            except (ApiError, RuntimeError):
                raise
        raise TransportError("get_tickers: транспорт тричі: %s" % last)

    return bulk_fn


def expiries_from_listing(rows, currency, counters):
    """-> відсортований список експірацій у СЕКУНДАХ.

    Береться ПОЛЕ option_details.expiry, а не парсинг дати з імені:
    точний час розрахунку лежить у полі, і припускати 08:00 не треба.
    """
    out = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        p = parse_derive_name(row.get("instrument_name"))
        if p is None or p[0] != currency or p[4] is not None:
            continue
        if row.get("is_active") is False:
            continue
        od = row.get("option_details")
        if not isinstance(od, dict):
            continue
        exp = od.get("expiry")
        if isinstance(exp, int) and exp > 0:
            out.add(exp)
    return sorted(out)


def normalize_bulk(res):
    """-> список тікерів. Форми перебираються ЯВНО, без евристик.

    Невідома форма -> помилка, а не порожній список: порожнеча тут
    читалась би як "ринку немає".
    """
    if isinstance(res, list):
        return [r for r in res if isinstance(r, dict)]
    if isinstance(res, dict):
        for key in ("tickers", "instruments", "data"):
            if key not in res:
                continue
            arr = res[key]
            if isinstance(arr, list):
                return [r for r in arr if isinstance(r, dict)]
            # Заміряно 2026-09-26: v2 віддає {"tickers": {ім'я: тікер}} —
            # СЛОВНИК усередині контейнера, не список.
            if isinstance(arr, dict):
                # Заміряно 2026-09-26: v2 віддає {"tickers": {ІМ'Я: тікер}},
                # причому в стиснутій формі запису поля instrument_name
                # НЕМАЄ — ім'я несе тільки КЛЮЧ. Перенести його в запис
                # обов'язково, інакше весь пакет іде в drop_bad_name.
                out = []
                for k, v in arr.items():
                    if not isinstance(v, dict):
                        continue
                    if "instrument_name" not in v and isinstance(k, str):
                        v = dict(v)
                        v["instrument_name"] = k
                    out.append(v)
                return out
            raise RuntimeError("get_tickers: %r не список і не словник: %s"
                               % (key, type(arr).__name__))
        vals = list(res.values())
        if vals and all(isinstance(v, dict) for v in vals):
            named = [v for v in vals if "instrument_name" in v]
            if len(named) == len(vals):
                return named
        raise RuntimeError("get_tickers: невідома форма, ключі=%s"
                           % (sorted(res.keys())[:10],))
    raise RuntimeError("get_tickers: result не список і не об'єкт: %s"
                       % type(res).__name__)


def collect_bulk(currency, list_fn, bulk_fn, expiries, sleep_ms, counters,
                 log):
    """Пакетний збір: один запит на експірацію."""
    t0 = time.time()
    seen_names = set()
    records = []
    ts = int(time.time() * 1000)

    for i, exp in enumerate(expiries):
        raw = bulk_fn(currency, exp)
        rows = normalize_bulk(raw)
        for row in rows:
            counters["instruments_listed"] += 1
            p = parse_derive_name(row.get("instrument_name"))
            if p is None:
                counters["drop_bad_name"] += 1
                continue
            cur, ymd, strike, kind, ru = p
            if ru is not None:
                counters["drop_suffix"] += 1
                continue
            if cur != currency:
                counters["drop_currency"] += 1
                continue
            if row.get("is_active") is False:
                counters["drop_inactive"] += 1
                continue
            name = row["instrument_name"]
            if name in seen_names:
                counters["drop_duplicate"] += 1
                continue
            seen_names.add(name)
            records.append({
                "instrument_name": name,
                "expiry_ymd": ymd,
                "expiry_sec": exp,
                "strike": strike,
                "kind": kind,
                "seq": 0,
                "ts_fetch_ms": ts,
                "ticker_raw": row,
                "status": "ok",
            })
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)
        if (i + 1) % 5 == 0:
            log("    ... експірацій %d/%d, тікерів %d"
                % (i + 1, len(expiries), len(records)))

    records.sort(key=lambda r: r["instrument_name"])
    for i, r in enumerate(records):
        r["seq"] = i

    counters["candidates"] = len(records)
    counters["ticker_ok"] = len(records)

    # ГЕЙТ [ALL-DROPPED]. Заміряно 2026-09-26: коли normalize_bulk
    # губив ім'я інструмента, усі 572 рядки йшли в drop_bad_name, а
    # лог показував "записів 0/0, провалів 0, status ok" — тобто
    # прохід, що задропав УСЕ, виглядав як порожній ринок. Лічильник
    # дропів існував, але нічого не блокував: той самий fail-open.
    dropped = sum(v for k, v in counters.items() if k.startswith("drop_"))
    if counters["instruments_listed"] > 0 and len(records) == 0:
        raise RuntimeError(
            "[ALL-DROPPED] %s: у відповіді %d рядків, у ряд не пройшов "
            "ЖОДЕН (дропів %d: %s). Це НЕ порожній ринок — це поламаний "
            "розбір." % (currency, counters["instruments_listed"], dropped,
                         {k: v for k, v in counters.items()
                          if k.startswith("drop_") and v}))

    elapsed = time.time() - t0
    meta = {
        "status": "ok",
        "mode": "bulk",
        "n_expiries": len(expiries),
        "n_dropped": dropped,
        "elapsed_s": round(elapsed, 2),
        "n_candidates": len(records),
        "n_records": len(records),
    }
    # Усі тікери однієї експірації зняті одним запитом; між
    # експіраціями розрив у частки секунди, а не хвилини, як було в
    # поштучному проході. Серіалізована експозиція (клас 4) мала.
    return records, meta


def listing_carries_quotes(rows):
    """Чи несе запис лістингу котирування сам по собі.

    Якщо так, 686 запитів замінюються ОДНИМ, і стеля часу перестає
    бути проблемою. Перевірка за наявністю ключа, не за евристикою.
    """
    for row in rows:
        if isinstance(row, dict) and detect_schema(row) is not None:
            return True, detect_schema(row)
    return False, None


def preflight(list_fn, ticker_fn, currency, log):
    """Лістинг + ОДИН тікер. Друкує обидва, нічого не інтерпретує.

    Мапінг полів робиться людиною, з очима на значення, і один раз.
    Здогад тут зіпсував би весь майбутній ряд.
    """
    log("PREFLIGHT: лістинг + один тікер, без інтерпретації полів")
    rows = list_fn(currency)
    log("  інструментів у лістингу: %d" % len(rows))
    if not rows:
        raise RuntimeError("PREFLIGHT: порожній лістинг")

    first = next((r for r in rows if isinstance(r, dict)), None)
    if first is not None:
        log("  КЛЮЧІ ЗАПИСУ ЛІСТИНГУ (%d): %s"
            % (len(first), sorted(first.keys())))
    carries, sch = listing_carries_quotes(rows)
    if carries:
        log("  >>> ЛІСТИНГ УЖЕ НЕСЕ КОТИРУВАННЯ (схема %s)." % sch)
        log("  >>> Поштучний прохід НЕ потрібен: один запит замість сотень.")
        tal = quote_tally([{"ticker_raw": r} for r in rows])
        log("  >>> По всьому лістингу: двосторонніх %d | односторонніх %d"
            " | порожніх %d | oi>0 %d (з %d)"
            % (tal["two_sided"], tal["one_sided"], tal["empty"],
               tal["oi_positive"], tal["n"]))
    else:
        log("  Лістинг котирувань НЕ несе -> потрібен поштучний прохід.")

    name = None
    for row in rows:
        if isinstance(row, dict):
            p = parse_derive_name(row.get("instrument_name"))
            if p is not None and p[0] == currency and p[4] is None:
                name = row["instrument_name"]
                break
    if name is None:
        raise RuntimeError("PREFLIGHT: жодного придатного імені в лістингу")

    log("  тікер для: %s" % name)
    t = ticker_fn(name)
    log("  схема тікера: %s" % detect_schema(t))
    log("  СИРИЙ ТІКЕР (як зберігатиметься):")
    txt = json.dumps(t, ensure_ascii=False, indent=2, sort_keys=True,
                     default=str)
    for line in txt.splitlines()[:80]:
        log("    " + line)
    if len(txt.splitlines()) > 80:
        log("    ... (обрізано, повністю буде в артефакті)")
    return True


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def run(log):
    unknown = env_warn_scan()
    for name in unknown:
        log("[ENV-WARN] нерозпізнана змінна: %s" % name)

    offline = env_int("VOLEDGE_OFFLINE", 0, 0, 1)

    log("=" * 70)
    log("DEFI КОЛЕКТОР КОТИРУВАНЬ — ЗБІР, НЕ ВИМІРЮВАННЯ")
    log("=" * 70)

    if not selftests(log):
        log("[ERROR] самотести не пройшли — прогін зупинено")
        return 2

    if offline == 1:
        log("VOLEDGE_OFFLINE=1 -> мережа не чіпається, вихід.")
        return 0

    currencies = [c.strip().upper() for c in
                  env_str("VOLEDGE_DEFI_C_CURRENCIES", "BTC,ETH").split(",")
                  if c.strip()]
    if not currencies:
        raise RuntimeError("VOLEDGE_DEFI_C_CURRENCIES порожній")
    base_url = env_str("VOLEDGE_DEFI_C_BASE_URL", "https://api.derive.xyz/v3")
    timeout_s = env_int("VOLEDGE_DEFI_C_TIMEOUT_S", 20, 1, 600)
    budget = env_int("VOLEDGE_DEFI_C_BUDGET", 1200, 1, 100000)
    max_s = env_int("VOLEDGE_DEFI_C_MAX_S", 240, 1, 3600)
    sleep_ms = env_int("VOLEDGE_DEFI_C_SLEEP_MS", 60, 0, 60000)
    art_dir = env_str("VOLEDGE_DEFI_C_ARTIFACT_DIR", DEFAULT_ART_DIR)
    do_gzip = env_int("VOLEDGE_DEFI_C_GZIP", 1, 0, 1)
    no_write = env_int("VOLEDGE_DEFI_C_NO_WRITE", 0, 0, 1)
    pre_only = env_int("VOLEDGE_DEFI_C_PREFLIGHT_ONLY", 0, 0, 1)
    mode = env_str("VOLEDGE_DEFI_C_MODE", "bulk")
    if mode not in ("bulk", "ticker"):
        raise RuntimeError("VOLEDGE_DEFI_C_MODE: очікується bulk|ticker, "
                           "отримано %r" % mode)

    now = datetime.now(timezone.utc)
    log("Джерело: %s" % base_url)
    log("Режим: %s" % ("bulk (public/get_tickers, один запит на валюту)"
                       if mode == "bulk"
                       else "ticker (поштучно, застарілий маршрут)"))
    if mode == "ticker":
        log("Стелі НА ВАЛЮТУ: бюджет %d запитів, час %d с, пауза %d мс"
            % (budget, max_s, sleep_ms))
    log("")

    c0 = new_counters()
    bulk_param = None
    if mode == "bulk":
        log("PREFLIGHT: лістинг -> експірації -> один пакетний запит")
        list0 = make_list_fn(base_url, timeout_s, c0)
        rows_l = list0(currencies[0])
        exps0 = expiries_from_listing(rows_l, currencies[0], c0)
        log("  інструментів у лістингу: %d, експірацій: %d"
            % (len(rows_l), len(exps0)))
        if not exps0:
            raise RuntimeError("PREFLIGHT: жодної експірації в лістингу")
        bulk_param = resolve_bulk_param(base_url, currencies[0], exps0[0],
                                        timeout_s, c0, log)
        bulk0 = make_bulk_fn(base_url, timeout_s, c0, bulk_param[0],
                             bulk_param[1])
        rows0 = normalize_bulk(bulk0(currencies[0], exps0[0]))
        if not rows0:
            raise RuntimeError("PREFLIGHT: порожній пакет")
        log("  схема: %s" % detect_schema(rows0[0]))
        log("  ключі запису (%d): %s"
            % (len(rows0[0]), sorted(rows0[0].keys())))
        tal0 = quote_tally([{"ticker_raw": r} for r in rows0])
        log("  КНИГА на найближчій експірації: двосторонніх %d |"
            " односторонніх %d | порожніх %d | oi>0 %d (з %d)"
            % (tal0["two_sided"], tal0["one_sided"], tal0["empty"],
               tal0["oi_positive"], tal0["n"]))
    else:
        preflight(make_list_fn(base_url, timeout_s, c0),
                  make_ticker_fn(base_url, timeout_s, c0), currencies[0], log)
    log("")

    if pre_only == 1:
        log("VOLEDGE_DEFI_C_PREFLIGHT_ONLY=1 -> прохід не запускається.")
        return 0

    result = {
        "schema": 1,
        "kind": "defi_quotes",
        "note": "ticker_raw збережено БЕЗ інтерпретації полів",
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": base_url,
        "env_warnings": unknown,
        "limits": {"budget": budget, "max_s": max_s, "sleep_ms": sleep_ms},
        "mode": mode,
        "bulk_param": list(bulk_param) if bulk_param else None,
        "currencies": {},
    }

    for cur in currencies:
        log("[%s] прохід (%s)..." % (cur, mode))
        counters = new_counters()
        if mode == "bulk":
            list_fn = make_list_fn(base_url, timeout_s, counters)
            exps = expiries_from_listing(list_fn(cur), cur, counters)
            log("  [%s] експірацій: %d" % (cur, len(exps)))
            recs, meta = collect_bulk(
                cur, list_fn,
                make_bulk_fn(base_url, timeout_s, counters,
                             bulk_param[0], bulk_param[1]),
                exps, sleep_ms, counters, log)
        else:
            recs, meta = collect_currency(
                cur,
                make_list_fn(base_url, timeout_s, counters),
                make_ticker_fn(base_url, timeout_s, counters),
                budget, max_s, sleep_ms, counters, log)

        top, nested = ticker_key_inventory(recs)
        tally = quote_tally(recs)
        result["currencies"][cur] = {
            "meta": meta,
            "counters": counters,
            "quote_tally": tally,
            "ticker_keys_top": top,
            "ticker_keys_nested": nested,
            "records": recs,
        }

        log("  [%s] %s | записів %d/%d | ok %d, провалів %d | %.1f с, %d запитів"
            % (cur, meta["status"], meta["n_records"], meta["n_candidates"],
               counters["ticker_ok"], counters["ticker_failed"],
               meta["elapsed_s"], counters["http_calls"]))
        if counters["budget_hit"] or counters["time_hit"]:
            log("  [%s] [CEILING] прохід обірвано стелею — знімок НЕПОВНИЙ"
                % cur)
        log("  [%s] КНИГА: двосторонніх %d | односторонніх %d | порожніх %d"
            " | схрещених %d"
            % (cur, tally["two_sided"], tally["one_sided"], tally["empty"],
               tally["crossed"]))
        log("  [%s] oi>0: %d | їхня IV присутня: %d | схема v2/v3/?: %d/%d/%d"
            % (cur, tally["oi_positive"], tally["has_their_iv"],
               tally["schema_v2"], tally["schema_v3"],
               tally["schema_unknown"]))
        if tally["n"]:
            log("  [%s] КОТИРОВАНА ЧАСТКА: %.1f%% двосторонніх — саме на ній"
                " живе d_iv" % (cur, 100.0 * tally["two_sided"] / tally["n"]))
        log("  [%s] ключі тікера: %s" % (cur, sorted(top)))
        for k in sorted(nested):
            log("  [%s]   %s -> %s" % (cur, k, sorted(nested[k])))
        drops = {k: v for k, v in counters.items()
                 if k.startswith("drop_") and v > 0}
        log("  [%s] дропи: %s" % (cur, drops if drops else "немає"))
        log("")

    log("=" * 70)
    log("ЗБІР ЗАВЕРШЕНО. Читачів у цих полів НЕМАЄ і не мусить бути,")
    log("доки немає оцінювача d_iv. Висновків з одного знімка не робити.")
    log("=" * 70)

    if no_write == 0:
        os.makedirs(art_dir, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        payload = json.dumps(result, ensure_ascii=False, indent=1,
                             sort_keys=True, default=str)
        data = payload.encode("utf-8")
        if do_gzip == 1:
            path = os.path.join(art_dir, "defiquotes_%s.json.gz" % stamp)
            _qfh = gzip.open(path, "wb")
        else:
            path = os.path.join(art_dir, "defiquotes_%s.json" % stamp)
            _qfh = open(path, "wb")
        try:
            _qfh.write(data)
        finally:
            _qfh.close()
        on_disk = os.path.getsize(path)
        log("Артефакт: %s" % os.path.abspath(path))
        log("  сирих %d Б, на диску %d Б, sha256(json):%s"
            % (len(data), on_disk,
               hashlib.sha256(data).hexdigest()[:16]))
    else:
        log("VOLEDGE_DEFI_C_NO_WRITE=1 -> артефакт не записано.")

    return 0


def main():
    rc = 1
    try:
        rc = run(print)
    except Exception as exc:
        rc = 1
        print("")
        print("[ERROR] %s: %s" % (type(exc).__name__, exc))
        import traceback
        traceback.print_exc()
    finally:
        try:
            input("Натисніть Enter для виходу...")
        except EOFError:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
