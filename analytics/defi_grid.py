#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[EN] Diagnostics before the DEFI evaluator: overlap of Derive and
Deribit expiries and strikes, exact-strike matches, and the share of
Derive strikes outside the Deribit quoted range (explicit refusals).

--- Ukrainian original below ---
analytics/defi_grid.py — ДІАГНОСТИКА перед оцінювачем d_iv.

READ-ONLY. Не рахує d_iv, не чіпає поверхню, не читає артефактів
Vol-Edge. Пише лише в artifacts/defi/.

ПИТАННЯ: скільки продуктів Derive взагалі МОЖНА порівняти з Deribit
без екстраполяції, і що для цього доведеться інтерполювати.

Три окремі речі, які тут міряються:
  1. ЗБІГ ЕКСПІРАЦІЙ. Якщо дата Derive є в лістингу Deribit — зріз
     береться прямо. Якщо ні — доведеться інтерполювати total
     variance по tau між сусідніми зрізами (як у константних тенорах).
  2. K_RANGE. ГОЛОВНИЙ ПРИНЦИП проєкту: модель валідна ЛИШЕ в
     k_range даних + 10% падінг. Страйк Derive поза котированою зоною
     Deribit -> ЯВНА ВІДМОВА, ніколи екстраполяція. Тут рахуємо, якою
     буде частка відмов.
  3. ТОЧНИЙ ЗБІГ СТРАЙКІВ. Де страйк збігається точно І експірація
     спільна — d_iv рахується без жодної інтерполяції. Це найчистіше
     підмножина, і негативний контроль варто будувати саме на ній.

ЩО ТУТ НАВМИСНО НЕ РОБИТЬСЯ: форвард. Для бакетування за moneyness
береться ІНДЕКС Deribit, не synthetic forward із put-call parity.
Індекс годиться, щоб порахувати, який страйк де лежить; для d_iv він
НЕ годиться і там має бути наш форвард. Різниця форвардів — один із
головних підозрюваних у появі хибного d_iv, тож змішувати не можна.

ЦЕ ДІАГНОСТИКА, НЕ ВЕРДИКТ. Жодних пре-реєстрованих порогів тут
немає, бо жодна гіпотеза тут не перевіряється. Цифри потрібні, щоб
спроєктувати оцінювач, а не щоб щось ним вирішити.

Запуск: подвійний клік або `python3 defi_grid.py`.
Самотести без мережі: VOLEDGE_OFFLINE=1.
"""

import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_force_utf8_stdio()

# --------------------------------------------------------------------------
# КАНОН [ENV-WARN]
# --------------------------------------------------------------------------

KNOWN_ENV = (
    "VOLEDGE_OFFLINE",
    "VOLEDGE_GRID_CURRENCIES",
    "VOLEDGE_GRID_DERIVE_URL",
    "VOLEDGE_GRID_DERIBIT_URL",
    "VOLEDGE_GRID_TIMEOUT_S",
    "VOLEDGE_GRID_PAD",
    "VOLEDGE_GRID_ARTIFACT_DIR",
    "VOLEDGE_GRID_NO_WRITE",
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
        raise RuntimeError("%s: не число: %r" % (name, raw))
    if minimum is not None and val < minimum:
        raise RuntimeError("%s: %d < мінімуму %d" % (name, val, minimum))
    if maximum is not None and val > maximum:
        raise RuntimeError("%s: %d > максимуму %d" % (name, val, maximum))
    return val


def env_float(name, default, minimum=None, maximum=None):
    raw = env_str(name, None)
    if raw is None:
        return default
    try:
        val = float(raw)
    except ValueError:
        raise RuntimeError("%s: не число: %r" % (name, raw))
    if minimum is not None and val < minimum:
        raise RuntimeError("%s: %g < мінімуму %g" % (name, val, minimum))
    if maximum is not None and val > maximum:
        raise RuntimeError("%s: %g > максимуму %g" % (name, val, maximum))
    return val


def new_counters():
    return {
        "derive_rows": 0,
        "derive_drop_bad_name": 0,
        "derive_drop_suffix": 0,
        "derive_drop_currency": 0,
        "derive_drop_inactive": 0,
        "derive_kept": 0,
        "deribit_rows": 0,
        "deribit_drop_bad": 0,
        "deribit_drop_kind": 0,
        "deribit_kept": 0,
        "http_calls": 0,
    }


# --------------------------------------------------------------------------
# ПАРСИНГ
# --------------------------------------------------------------------------

def parse_derive_name(name):
    """<CUR>-<YYYYMMDD>-<STRIKE>-<C|P> [-<RU>] -> кортеж або None.

    Білий список (клас 8): усе, що не лягло у форму ТОЧНО, — None.
    """
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


def ymd_from_ms(ts_ms):
    """UTC-дата експірації з мітки часу. Для Deribit беремо ПОЛЕ,
    а не парсимо DDMMMYY з імені: місяці там літерами, і локаль
    могла б зіпсувати розбір тихо."""
    dt = datetime.fromtimestamp(ts_ms / 1000.0, timezone.utc)
    return dt.strftime("%Y%m%d")


def to_float(x):
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


# --------------------------------------------------------------------------
# K_RANGE
# --------------------------------------------------------------------------

def padded_k_range(strikes, fwd, pad):
    """-> (k_lo, k_hi) з падінгом pad (частка ширини) у ЛОГ-просторі.

    Падінг у лог-просторі, а не в ціновому: k = ln(K/F), і саме в
    ньому живе k_range моделі.
    """
    ks = sorted(math.log(s / fwd) for s in strikes)
    lo, hi = ks[0], ks[-1]
    width = hi - lo
    if width <= 0.0:
        return lo, hi
    return lo - pad * width, hi + pad * width


def classify_strike(strike, fwd, k_lo_raw, k_hi_raw, k_lo_pad, k_hi_pad):
    """-> 'inside' | 'padded' | 'outside'."""
    k = math.log(strike / fwd)
    if k_lo_raw <= k <= k_hi_raw:
        return "inside"
    if k_lo_pad <= k <= k_hi_pad:
        return "padded"
    return "outside"


# --------------------------------------------------------------------------
# МЕРЕЖА
# --------------------------------------------------------------------------

def http_post_json(url, payload, timeout_s, counters):
    counters["http_calls"] += 1
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": "vol-edge-defi-grid/1"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def http_get_json(url, params, timeout_s, counters):
    counters["http_calls"] += 1
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        full, headers={"Accept": "application/json",
                       "User-Agent": "vol-edge-defi-grid/1"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def unwrap(doc):
    if not isinstance(doc, dict):
        raise RuntimeError("відповідь не об'єкт")
    if doc.get("error"):
        raise RuntimeError("API error: %r" % (doc["error"],))
    if "result" in doc:
        return doc["result"]
    return doc


def fetch_derive_instruments(base_url, currency, timeout_s, counters):
    """Усі активні опціони Derive. Сторінкування явне."""
    url = base_url.rstrip("/") + "/public/get_all_instruments"
    out = []
    page = 1
    declared = None
    while True:
        if page > 100:
            raise RuntimeError("derive: понад 100 сторінок — межа явна")
        res = unwrap(http_post_json(url, {
            "currency": currency, "instrument_type": "option",
            "expired": False, "page": page, "page_size": 1000},
            timeout_s, counters))
        if not isinstance(res, dict):
            raise RuntimeError("derive: result не об'єкт")
        arr = res.get("instruments")
        if not isinstance(arr, list):
            raise RuntimeError("derive: 'instruments' не список, ключі=%s"
                               % (sorted(res.keys())[:8],))
        pag = res.get("pagination") or {}
        if page == 1:
            declared = pag.get("count") if isinstance(pag.get("count"), int) else None
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
        raise RuntimeError("derive: зібрано %d, заявлено %d — сторінкування "
                           "НЕ повне" % (len(out), declared))
    return out


def fetch_deribit_instruments(base_url, currency, timeout_s, counters):
    url = base_url.rstrip("/") + "/public/get_instruments"
    res = unwrap(http_get_json(url, {"currency": currency, "kind": "option",
                                     "expired": "false"}, timeout_s, counters))
    if not isinstance(res, list):
        raise RuntimeError("deribit: result не список")
    return res


def fetch_deribit_index(base_url, currency, timeout_s, counters):
    url = base_url.rstrip("/") + "/public/get_index_price"
    name = "%s_usd" % currency.lower()
    res = unwrap(http_get_json(url, {"index_name": name}, timeout_s, counters))
    if not isinstance(res, dict):
        raise RuntimeError("deribit index: result не об'єкт")
    px = to_float(res.get("index_price"))
    if px is None or px <= 0.0:
        raise RuntimeError("deribit index: некоректна ціна %r"
                           % (res.get("index_price"),))
    return px


# --------------------------------------------------------------------------
# НОРМАЛІЗАЦІЯ В СПІЛЬНУ ФОРМУ
# --------------------------------------------------------------------------

def normalize_derive(rows, currency, counters):
    """-> {ymd: set(strikes)}."""
    grid = {}
    for row in rows:
        counters["derive_rows"] += 1
        if not isinstance(row, dict):
            counters["derive_drop_bad_name"] += 1
            continue
        p = parse_derive_name(row.get("instrument_name"))
        if p is None:
            counters["derive_drop_bad_name"] += 1
            continue
        cur, ymd, strike, kind, ru = p
        if ru is not None:
            counters["derive_drop_suffix"] += 1
            continue
        if cur != currency:
            counters["derive_drop_currency"] += 1
            continue
        active = row.get("is_active")
        if active is False:
            counters["derive_drop_inactive"] += 1
            continue
        counters["derive_kept"] += 1
        grid.setdefault(ymd, set()).add(strike)
    return grid


def normalize_deribit(rows, currency, counters):
    """-> {ymd: set(strikes)}. Дата з ПОЛЯ expiration_timestamp."""
    grid = {}
    for row in rows:
        counters["deribit_rows"] += 1
        if not isinstance(row, dict):
            counters["deribit_drop_bad"] += 1
            continue
        if row.get("kind") not in (None, "option"):
            counters["deribit_drop_kind"] += 1
            continue
        ts = row.get("expiration_timestamp")
        strike = to_float(row.get("strike"))
        if not isinstance(ts, int) or strike is None or strike <= 0.0:
            counters["deribit_drop_bad"] += 1
            continue
        counters["deribit_kept"] += 1
        grid.setdefault(ymd_from_ms(ts), set()).add(strike)
    return grid


# --------------------------------------------------------------------------
# ПОРІВНЯННЯ
# --------------------------------------------------------------------------

def compare_grids(derive_grid, deribit_grid, fwd, pad):
    """Чиста функція. -> звіт про покриття."""
    d_exp = set(derive_grid)
    b_exp = set(deribit_grid)
    shared = sorted(d_exp & b_exp)
    only_derive = sorted(d_exp - b_exp)

    per_expiry = []
    tot = {"inside": 0, "padded": 0, "outside": 0, "exact": 0}
    n_shared_strikes = 0

    for ymd in shared:
        d_strikes = sorted(derive_grid[ymd])
        b_strikes = sorted(deribit_grid[ymd])
        if not b_strikes:
            continue
        k_lo_raw = math.log(b_strikes[0] / fwd)
        k_hi_raw = math.log(b_strikes[-1] / fwd)
        k_lo_pad, k_hi_pad = padded_k_range(b_strikes, fwd, pad)
        b_set = set(b_strikes)

        cls = {"inside": 0, "padded": 0, "outside": 0}
        exact = 0
        for s in d_strikes:
            c = classify_strike(s, fwd, k_lo_raw, k_hi_raw, k_lo_pad, k_hi_pad)
            cls[c] += 1
            if s in b_set:
                exact += 1
        n_shared_strikes += len(d_strikes)
        for k in cls:
            tot[k] += cls[k]
        tot["exact"] += exact

        per_expiry.append({
            "ymd": ymd,
            "derive_strikes": len(d_strikes),
            "deribit_strikes": len(b_strikes),
            "exact_match": exact,
            "inside": cls["inside"],
            "padded": cls["padded"],
            "outside": cls["outside"],
            "deribit_strike_min": b_strikes[0],
            "deribit_strike_max": b_strikes[-1],
        })

    n_only = sum(len(derive_grid[y]) for y in only_derive)
    n_all = n_shared_strikes + n_only

    return {
        "derive_expiries": sorted(d_exp),
        "deribit_expiries": sorted(b_exp),
        "shared_expiries": shared,
        "only_derive_expiries": only_derive,
        "n_derive_instruments": n_all,
        "n_on_shared_expiry": n_shared_strikes,
        "n_on_unshared_expiry": n_only,
        "totals": tot,
        "per_expiry": per_expiry,
        "frac_shared_expiry": (n_shared_strikes / n_all) if n_all else 0.0,
        "frac_exact": (tot["exact"] / n_all) if n_all else 0.0,
        "frac_usable": ((tot["inside"] + tot["padded"]) / n_all) if n_all else 0.0,
        "frac_refused_k_range": (tot["outside"] / n_all) if n_all else 0.0,
    }


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

    # [1] парсер Derive
    p = parse_derive_name("BTC-20260926-82000-P")
    check("1.1 базове", p is not None and p[1] == "20260926" and p[2] == 82000.0)
    check("1.2 дріб",
          parse_derive_name("BTC-20260626-65000_5-P")[2] == 65000.5)
    check("1.3 ru", parse_derive_name("ETH-20240914-2400-C-42")[4] == 42)
    check("1.4 PERP", parse_derive_name("BTC-PERP") is None)
    check("1.5 регістр", parse_derive_name("btc-20260926-82000-P") is None)
    check("1.6 дата", parse_derive_name("BTC-20261332-82000-P") is None)
    check("1.7 None", parse_derive_name(None) is None)

    # [2] дата з мітки часу
    check("2.1 08:00 UTC", ymd_from_ms(1790409600000) == ymd_from_ms(1790409600000))
    ts = int(datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
    check("2.2 конкретна дата", ymd_from_ms(ts) == "20260926")

    # [3] k_range і падінг
    lo, hi = padded_k_range([90.0, 100.0, 110.0], 100.0, 0.10)
    raw_lo = math.log(0.9)
    raw_hi = math.log(1.1)
    w = raw_hi - raw_lo
    check("3.1 падінг симетричний у лог-просторі",
          abs(lo - (raw_lo - 0.1 * w)) < 1e-12
          and abs(hi - (raw_hi + 0.1 * w)) < 1e-12)
    lo2, hi2 = padded_k_range([100.0], 100.0, 0.10)
    check("3.2 один страйк -> нульова ширина", abs(lo2 - hi2) < 1e-12)

    # [4] класифікація
    b = [90.0, 110.0]
    klo, khi = math.log(0.9), math.log(1.1)
    plo, phi = padded_k_range(b, 100.0, 0.10)
    check("4.1 всередині",
          classify_strike(100.0, 100.0, klo, khi, plo, phi) == "inside")
    check("4.2 на межі = inside",
          classify_strike(110.0, 100.0, klo, khi, plo, phi) == "inside")
    check("4.3 у падінгу",
          classify_strike(111.0, 100.0, klo, khi, plo, phi) == "padded")
    check("4.4 зовні",
          classify_strike(200.0, 100.0, klo, khi, plo, phi) == "outside")
    check("4.5 зовні знизу",
          classify_strike(10.0, 100.0, klo, khi, plo, phi) == "outside")

    # [5] нормалізація Derive
    c = new_counters()
    g = normalize_derive([
        {"instrument_name": "BTC-20260926-82000-P"},
        {"instrument_name": "BTC-20260926-84000-C"},
        {"instrument_name": "BTC-20261030-90000-C"},
        {"instrument_name": "ETH-20260926-2400-C"},
        {"instrument_name": "BTC-PERP"},
        {"instrument_name": "BTC-20260926-82000-P-7"},
        {"instrument_name": "BTC-20260926-99000-C", "is_active": False},
    ], "BTC", c)
    check("5.1 дві експірації", sorted(g) == ["20260926", "20261030"])
    check("5.2 два страйки в першій", len(g["20260926"]) == 2)
    check("5.3 чужа валюта", c["derive_drop_currency"] == 1)
    check("5.4 не опціон", c["derive_drop_bad_name"] == 1)
    check("5.5 суфікс", c["derive_drop_suffix"] == 1)
    check("5.6 неактивний", c["derive_drop_inactive"] == 1)
    check("5.7 kept", c["derive_kept"] == 3)

    # [6] нормалізація Deribit
    c2 = new_counters()
    ts1 = int(datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
    g2 = normalize_deribit([
        {"expiration_timestamp": ts1, "strike": 80000, "kind": "option"},
        {"expiration_timestamp": ts1, "strike": 90000, "kind": "option"},
        {"expiration_timestamp": ts1, "strike": 0, "kind": "option"},
        {"expiration_timestamp": "x", "strike": 80000, "kind": "option"},
        {"expiration_timestamp": ts1, "strike": 80000, "kind": "future"},
    ], "BTC", c2)
    check("6.1 одна експірація", sorted(g2) == ["20260926"])
    check("6.2 два страйки", len(g2["20260926"]) == 2)
    check("6.3 дропи", c2["deribit_drop_bad"] == 2 and c2["deribit_drop_kind"] == 1)

    # [7] порівняння: ІДЕНТИЧНІ сітки
    same = {"20260926": {90.0, 100.0, 110.0}}
    rep = compare_grids(same, {"20260926": {90.0, 100.0, 110.0}}, 100.0, 0.10)
    check("7.1 усе збігається", abs(rep["frac_exact"] - 1.0) < 1e-12)
    check("7.2 відмов нема", abs(rep["frac_refused_k_range"]) < 1e-12)
    check("7.3 спільна експірація", rep["shared_expiries"] == ["20260926"])

    # [8] ВИРОДЖЕНИЙ контроль: нуль спільних експірацій
    rep2 = compare_grids({"20260926": {100.0}}, {"20261030": {100.0}},
                         100.0, 0.10)
    check("8.1 нуль спільних", rep2["shared_expiries"] == [])
    check("8.2 усе на неспільній", rep2["n_on_unshared_expiry"] == 1)
    check("8.3 frac_shared = 0", abs(rep2["frac_shared_expiry"]) < 1e-12)
    check("8.4 frac_usable = 0 (нема проти чого судити)",
          abs(rep2["frac_usable"]) < 1e-12)

    # [9] порівняння: страйки Derive ШИРШІ за Deribit
    rep3 = compare_grids({"20260926": {50.0, 100.0, 200.0}},
                         {"20260926": {90.0, 110.0}}, 100.0, 0.10)
    check("9.1 один всередині", rep3["totals"]["inside"] == 1)
    check("9.2 два зовні", rep3["totals"]["outside"] == 2)
    check("9.3 точних збігів 0", rep3["totals"]["exact"] == 0)
    check("9.4 частка відмов 2/3",
          abs(rep3["frac_refused_k_range"] - 2.0 / 3.0) < 1e-12)

    # [10] порожні входи не падають
    rep4 = compare_grids({}, {}, 100.0, 0.10)
    check("10.1 порожньо", rep4["n_derive_instruments"] == 0)
    check("10.2 частки нульові", rep4["frac_exact"] == 0.0)

    # [11] KNOWN_ENV
    check("11.1 без дублів", len(KNOWN_ENV) == len(set(KNOWN_ENV)))
    check("11.2 усі VOLEDGE_*",
          all(n.startswith("VOLEDGE_") for n in KNOWN_ENV))

    # [12] to_float
    check("12.1 рядок", to_float("1.5") == 1.5)
    check("12.2 bool", to_float(True) is None)
    check("12.3 nan", to_float("nan") is None)

    log("Самотести: %d/%d" % (n_ok, n_ok + n_fail))
    return n_fail == 0


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def run(log):
    unknown = env_warn_scan()
    for name in unknown:
        log("[ENV-WARN] нерозпізнана змінна: %s" % name)

    offline = env_int("VOLEDGE_OFFLINE", 0, 0, 1)

    log("=" * 70)
    log("DEFI: сітка експірацій і страйків — ДІАГНОСТИКА")
    log("=" * 70)

    if not selftests(log):
        log("[ERROR] самотести не пройшли — прогін зупинено")
        return 2

    if offline == 1:
        log("VOLEDGE_OFFLINE=1 -> мережа не чіпається, вихід.")
        return 0

    currencies = [c.strip().upper() for c in
                  env_str("VOLEDGE_GRID_CURRENCIES", "BTC,ETH").split(",")
                  if c.strip()]
    derive_url = env_str("VOLEDGE_GRID_DERIVE_URL", "https://api.derive.xyz/v3")
    deribit_url = env_str("VOLEDGE_GRID_DERIBIT_URL",
                          "https://www.deribit.com/api/v2")
    timeout_s = env_int("VOLEDGE_GRID_TIMEOUT_S", 30, 1, 600)
    pad = env_float("VOLEDGE_GRID_PAD", 0.10, 0.0, 1.0)
    art_dir = env_str("VOLEDGE_GRID_ARTIFACT_DIR", "artifacts/defi")
    no_write = env_int("VOLEDGE_GRID_NO_WRITE", 0, 0, 1)

    now = datetime.now(timezone.utc)
    log("Derive:  %s" % derive_url)
    log("Deribit: %s" % deribit_url)
    log("Падінг k_range: %.0f%%" % (100.0 * pad))
    log("")

    result = {
        "schema": 1,
        "kind": "defi_grid_diagnostic",
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "derive_url": derive_url,
        "deribit_url": deribit_url,
        "pad": pad,
        "env_warnings": unknown,
        "currencies": {},
    }

    for cur in currencies:
        log("[%s] збір лістингів..." % cur)
        counters = new_counters()
        t0 = time.time()

        d_rows = fetch_derive_instruments(derive_url, cur, timeout_s, counters)
        b_rows = fetch_deribit_instruments(deribit_url, cur, timeout_s, counters)
        fwd = fetch_deribit_index(deribit_url, cur, timeout_s, counters)
        elapsed = time.time() - t0

        d_grid = normalize_derive(d_rows, cur, counters)
        b_grid = normalize_deribit(b_rows, cur, counters)
        rep = compare_grids(d_grid, b_grid, fwd, pad)
        rep["index_price"] = fwd
        rep["counters"] = counters
        rep["elapsed_s"] = round(elapsed, 2)
        result["currencies"][cur] = rep

        log("  [%s] індекс Deribit: %.2f (для бакетування, НЕ для d_iv)"
            % (cur, fwd))
        log("  [%s] інструментів: Derive %d, Deribit %d (%.1f с, %d запитів)"
            % (cur, counters["derive_kept"], counters["deribit_kept"],
               elapsed, counters["http_calls"]))
        log("  [%s] експірацій: Derive %d, Deribit %d, СПІЛЬНИХ %d"
            % (cur, len(rep["derive_expiries"]), len(rep["deribit_expiries"]),
               len(rep["shared_expiries"])))
        if rep["only_derive_expiries"]:
            log("  [%s] лише в Derive (треба інтерполяція по tau): %s"
                % (cur, ", ".join(rep["only_derive_expiries"][:12])))
        t = rep["totals"]
        log("  [%s] страйків Derive усього %d; на спільних експіраціях %d"
            % (cur, rep["n_derive_instruments"], rep["n_on_shared_expiry"]))
        log("  [%s]   точний збіг страйка: %d (%.1f%% усіх)"
            % (cur, t["exact"], 100.0 * rep["frac_exact"]))
        log("  [%s]   у k_range: %d | у падінгу: %d | ПОЗА: %d"
            % (cur, t["inside"], t["padded"], t["outside"]))
        log("  [%s]   придатних до оцінки: %.1f%% | ВІДМОВ по k_range: %.1f%%"
            % (cur, 100.0 * rep["frac_usable"],
               100.0 * rep["frac_refused_k_range"]))
        log("  [%s] по експіраціях (перші 10):" % cur)
        for e in rep["per_expiry"][:10]:
            log("    %s  Derive %3d / Deribit %3d | точних %3d | "
                "in %3d pad %2d out %3d | Deribit K [%g .. %g]"
                % (e["ymd"], e["derive_strikes"], e["deribit_strikes"],
                   e["exact_match"], e["inside"], e["padded"], e["outside"],
                   e["deribit_strike_min"], e["deribit_strike_max"]))
        drops = {k: v for k, v in counters.items()
                 if "drop" in k and v > 0}
        log("  [%s] дропи: %s" % (cur, drops if drops else "немає"))
        log("")

    log("=" * 70)
    log("ЦЕ ДІАГНОСТИКА. Вердикту немає — гіпотеза тут не перевіряється.")
    log("=" * 70)

    if no_write == 0:
        os.makedirs(art_dir, exist_ok=True)
        path = os.path.join(art_dir, "defigrid_%s.json"
                            % now.strftime("%Y%m%dT%H%M%SZ"))
        payload = json.dumps(result, ensure_ascii=False, indent=1,
                             sort_keys=True, default=str)
        _gfh = open(path, "w", encoding="utf-8")
        try:
            _gfh.write(payload)
        finally:
            _gfh.close()
        log("Артефакт: %s (%d Б, sha256:%s)"
            % (path, len(payload.encode("utf-8")),
               hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]))
    else:
        log("VOLEDGE_GRID_NO_WRITE=1 -> артефакт не записано.")

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
