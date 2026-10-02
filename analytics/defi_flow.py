#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[EN] DEFI hypothesis, step 0: is there real taker flow in Derive
options? Unit of analysis is the wallet, not the trade; pre-registered
thresholds on daily notional and number of wallets.

--- Ukrainian original below ---
analytics/defi_flow.py — ГІПОТЕЗА DEFI, КРОК 0: інвентаризація потоку.

READ-ONLY щодо проєкту: не читає жодного артефакту Vol-Edge, не читає
поверхню, не чіпає ланцюг. Пише ЛИШЕ в artifacts/defi/.

ПИТАННЯ КРОКУ 0: чи торгується це взагалі. Не "чи є котировки" —
котировки на тонкій книзі є завжди. Міряємо ТЕЙКЕРСЬКИЙ потік і число
унікальних контрагентів.

ЩО ЦЕ НЕ МІРЯЄ: d_iv. Жодного разу. Тут немає ані солвера, ані
поверхні, ані форварда. Ціна використовується лише для нотіоналу.

--------------------------------------------------------------------
ДЖЕРЕЛО: api.lyra.finance (v2), а НЕ api.derive.xyz/v3.
Заміряно 2026-09-25 пробником 2: у v3 підсистема історії віддає
-32603 Internal error на ВСІХ маршрутах (get_trade_history,
get_option_settlement_prices, get_liquidation_history,
get_funding_rate_history) при живих get_time / get_all_instruments /
get_ticker. Валідація параметрів при цьому проходить (page=0 дає
чесний -32602), тобто падає обробник, не наш запит.
Хост v2 відповідає нормально: 316583 BTC-опціонних угод за весь час,
722 за добу.
КОЛИ v3 ПОЛАГОДЯТЬ — перемкнути VOLEDGE_DEFI_BASE_URL і перевірити
preflight: схема полів у v2 і v3 НЕ зобов'язана збігатися.
--------------------------------------------------------------------

PREFLIGHT: перед збором робиться ОДИН запит, друкуються фактичні поля
запису і перевіряється наявність обов'язкових. Брак поля зупиняє
прогін ЯВНО. Мовчазна деградація (рахувати без wallet, без розділення
maker/taker) дала б цифри, які виглядають як вимір, але ним не є.

ПОРОГИ ПРЕ-РЕЄСТРОВАНІ (див. THRESHOLDS). Не рухати після першого
прогону — це рух правила після даних.

Запуск: подвійний клік або `python3 defi_flow.py`.
Самотести без мережі: VOLEDGE_OFFLINE=1.
"""

import hashlib
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ENV_WARNINGS = []


def _force_utf8_stdio():
    """Windows-консоль за замовчуванням cp866 -> кирилиця стає сміттям."""
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
    "VOLEDGE_DEFI_CURRENCIES",
    "VOLEDGE_DEFI_DAYS",
    "VOLEDGE_DEFI_PAGE_SIZE",
    "VOLEDGE_DEFI_MAX_PAGES",
    "VOLEDGE_DEFI_SLEEP_MS",
    "VOLEDGE_DEFI_TIMEOUT_S",
    "VOLEDGE_DEFI_BASE_URL",
    "VOLEDGE_DEFI_ARTIFACT_DIR",
    "VOLEDGE_DEFI_NO_WRITE",
    "VOLEDGE_DEFI_PREFLIGHT_ONLY",
)


def env_warn_scan():
    """НЕРОЗПІЗНАНА VOLEDGE_* = ТИХИЙ ДЕФОЛТ. Гасимо явно."""
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


# --------------------------------------------------------------------------
# ПРЕ-РЕЄСТРОВАНІ ПОРОГИ (крок 0)
# --------------------------------------------------------------------------

THRESHOLDS = {
    "fail_median_daily_taker_notional_usd": 100000.0,
    "fail_min_unique_taker_wallets": 10,
    "pass_min_days_with_flow_frac": 0.80,
    "pass_min_unique_taker_wallets": 30,
}

# Поля, без яких крок 0 не є кроком 0.
REQUIRED_FIELDS = ("instrument_name", "timestamp", "trade_amount",
                   "index_price", "wallet", "liquidity_role")

TENOR_BUCKETS = (
    ("0-2d", 0.0, 2.0),
    ("2-8d", 2.0, 8.0),
    ("8-31d", 8.0, 31.0),
    ("31d+", 31.0, float("inf")),
)

MONEYNESS_BUCKETS = (
    ("atm|k|<0.05", 0.0, 0.05),
    ("mid0.05-0.15", 0.05, 0.15),
    ("wing|k|>0.15", 0.15, float("inf")),
)


def new_counters():
    return {
        "rows_seen": 0,
        "drop_bad_name": 0,
        "drop_unknown_suffix": 0,
        "drop_wrong_currency": 0,
        "drop_bad_number": 0,
        "drop_no_wallet": 0,
        "drop_bad_role": 0,
        "drop_out_of_window": 0,
        "drop_nonpositive_index": 0,
        "drop_expiry_before_trade": 0,
        "kept_taker": 0,
        "kept_maker": 0,
        "pages_fetched": 0,
        "http_retries": 0,
    }


# --------------------------------------------------------------------------
# БІЛИЙ СПИСОК ІМЕНІ ІНСТРУМЕНТА (клас 8)
# --------------------------------------------------------------------------

def parse_instrument(name):
    """-> (currency, expiry_date_utc, strike, kind, risk_universe) або None."""
    if not isinstance(name, str) or not name:
        return None
    if name != name.upper():
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
        exp = datetime.strptime(ymd, "%Y%m%d").replace(tzinfo=timezone.utc)
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

    return (cur, exp, strike, kind, ru)


def expiry_timestamp_ms(expiry_date_utc):
    """08:00 UTC — конвенція розрахунку для крипти.

    ТОЧНИЙ час лежить в option_details.expiry інструмента; потік угод
    його не несе, тож це ПРИПУЩЕННЯ. Впливає лише на бакетування за
    тенором, НЕ на нотіонал і НЕ на вердикт. Оцінювач d_iv мусить
    брати поле, а не це.
    """
    dt = expiry_date_utc.replace(hour=8, minute=0, second=0, microsecond=0)
    return int(dt.timestamp() * 1000)


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


def bucket_of(value, buckets):
    for label, lo, hi in buckets:
        if lo <= value < hi:
            return label
    return buckets[-1][0]


# --------------------------------------------------------------------------
# СХЕМА ВІДПОВІДІ
# --------------------------------------------------------------------------

def check_schema(sample_row, required=REQUIRED_FIELDS):
    """-> (ok, missing, present). Без здогадів про синоніми полів.

    Якщо поле зветься інакше — це має побачити людина і вирішити, а не
    евристика, що тихо підставить mark_price замість index_price.
    """
    if not isinstance(sample_row, dict):
        return False, list(required), []
    present = sorted(sample_row.keys())
    missing = [f for f in required if f not in sample_row]
    return (len(missing) == 0), missing, present


def unwrap_result(doc):
    """JSON-RPC кладе корисне в 'result'. Форму фіксуємо ЯВНО."""
    if not isinstance(doc, dict):
        raise RuntimeError("відповідь не є об'єктом")
    if doc.get("error"):
        raise RuntimeError("JSON-RPC error: %r" % (doc["error"],))
    if "result" in doc and isinstance(doc["result"], dict):
        return doc["result"], "result"
    if "trades" in doc:
        return doc, "flat"
    raise RuntimeError("немає ані 'result', ані 'trades': ключі %r"
                       % (sorted(doc.keys())[:8],))


# --------------------------------------------------------------------------
# МЕРЕЖА
# --------------------------------------------------------------------------

class ApiError(RuntimeError):
    """Помилка ВІД СЕРВЕРА на валідному запиті. Детермінована."""


class TransportError(RuntimeError):
    """Мережевий збій. Має сенс повторити."""


def http_post_json(url, payload, timeout_s):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "vol-edge-defi-flow/2",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        # 4xx детермінований, 5xx і 429 варті повтору.
        if exc.code in (429,) or exc.code >= 500:
            raise TransportError("HTTP %s: %r" % (exc.code, raw[:160]))
        raise ApiError("HTTP %s: %r" % (exc.code, raw[:160]))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TransportError("%s: %s" % (type(exc).__name__, exc))

    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TransportError("невалідний JSON: %s" % exc)


def make_fetch_fn(base_url, timeout_s, counters):
    """Ретрай ЛИШЕ на транспорті.

    JSON-RPC error на детермінованому payload повернеться тричі
    поспіль — це вже коштувало 12 секунд і трьох однакових відповідей.
    """
    url = base_url.rstrip("/") + "/public/get_trade_history"

    def fetch(currency, from_ms, to_ms, page, page_size):
        payload = {
            "currency": currency,
            "instrument_type": "option",
            "page": page,
            "page_size": page_size,
        }
        if from_ms is not None:
            payload["from_timestamp"] = from_ms
        if to_ms is not None:
            payload["to_timestamp"] = to_ms

        last = None
        for attempt in range(3):
            try:
                return unwrap_result(http_post_json(url, payload, timeout_s))[0]
            except TransportError as exc:
                last = exc
                counters["http_retries"] += 1
                time.sleep(1.5 * (attempt + 1))
            except ApiError:
                raise
            except RuntimeError:
                raise
        raise TransportError("транспорт провалився 3 рази: %s" % last)

    return fetch


def fetch_all_pages(fetch_fn, currency, from_ms, to_ms, page_size,
                    max_pages, sleep_ms, counters, log):
    """Збір усіх сторінок ВІКНА, ЗАФІКСОВАНОГО В МИНУЛОМУ."""
    trades = []
    page = 1
    declared_count = None
    declared_pages = None

    while True:
        if page > max_pages:
            raise RuntimeError(
                "%s: перевищено MAX_PAGES=%d — межа НЕ обходиться тихо"
                % (currency, max_pages))
        res = fetch_fn(currency, from_ms, to_ms, page, page_size)
        counters["pages_fetched"] += 1

        batch = res.get("trades")
        if not isinstance(batch, list):
            raise RuntimeError("%s стор.%d: 'trades' не список" % (currency, page))
        pag = res.get("pagination") or {}
        c = pag.get("count")
        np_ = pag.get("num_pages")
        if page == 1:
            declared_count = c if isinstance(c, int) else None
            declared_pages = np_ if isinstance(np_, int) else None
            log("  [%s] заявлено: count=%s num_pages=%s"
                % (currency, declared_count, declared_pages))

        trades.extend(batch)
        if len(batch) == 0:
            break
        if declared_pages is not None and page >= declared_pages:
            break
        if declared_pages is None and len(batch) < page_size:
            break
        page += 1
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)
        if page % 25 == 0:
            log("    ... стор. %d/%s, зібрано %d"
                % (page, declared_pages, len(trades)))

    if declared_count is not None and declared_count != len(trades):
        raise RuntimeError(
            "%s: зібрано %d, заявлено %d — сторінкування НЕ повне"
            % (currency, len(trades), declared_count))
    return trades


# --------------------------------------------------------------------------
# АГРЕГАЦІЯ
# --------------------------------------------------------------------------

def aggregate(trades, currency, from_ms, to_ms, counters):
    days = {}
    taker_wallets = {}
    tenor = {lab: {"n": 0, "usd": 0.0} for lab, _, _ in TENOR_BUCKETS}
    money = {lab: {"n": 0, "usd": 0.0} for lab, _, _ in MONEYNESS_BUCKETS}
    total_taker_usd = 0.0
    total_taker_n = 0

    for row in trades:
        counters["rows_seen"] += 1
        if not isinstance(row, dict):
            counters["drop_bad_number"] += 1
            continue

        parsed = parse_instrument(row.get("instrument_name"))
        if parsed is None:
            counters["drop_bad_name"] += 1
            continue
        cur, exp_date, strike, kind, ru = parsed
        if ru is not None:
            counters["drop_unknown_suffix"] += 1
            continue
        if cur != currency:
            counters["drop_wrong_currency"] += 1
            continue

        role = row.get("liquidity_role")
        if role not in ("maker", "taker"):
            counters["drop_bad_role"] += 1
            continue

        ts = row.get("timestamp")
        if not isinstance(ts, int):
            counters["drop_bad_number"] += 1
            continue
        if ts < from_ms or ts > to_ms:
            counters["drop_out_of_window"] += 1
            continue

        amt = to_float(row.get("trade_amount"))
        idx = to_float(row.get("index_price"))
        if amt is None or idx is None:
            counters["drop_bad_number"] += 1
            continue
        if idx <= 0.0:
            counters["drop_nonpositive_index"] += 1
            continue

        wallet = row.get("wallet")
        if not isinstance(wallet, str) or wallet.strip() == "":
            counters["drop_no_wallet"] += 1
            continue

        tau_d = (expiry_timestamp_ms(exp_date) - ts) / 86400000.0
        if tau_d <= 0.0:
            counters["drop_expiry_before_trade"] += 1
            continue

        if role == "maker":
            counters["kept_maker"] += 1
            continue

        counters["kept_taker"] += 1
        usd = abs(amt) * idx
        total_taker_usd += usd
        total_taker_n += 1

        day = datetime.fromtimestamp(ts / 1000.0, timezone.utc).strftime("%Y-%m-%d")
        d = days.setdefault(day, {"n": 0, "usd": 0.0, "wallets": set()})
        d["n"] += 1
        d["usd"] += usd
        d["wallets"].add(wallet)

        w = taker_wallets.setdefault(wallet, {"n": 0, "usd": 0.0})
        w["n"] += 1
        w["usd"] += usd

        tb = bucket_of(tau_d, TENOR_BUCKETS)
        tenor[tb]["n"] += 1
        tenor[tb]["usd"] += usd

        k = abs(math.log(strike / idx))
        mb = bucket_of(k, MONEYNESS_BUCKETS)
        money[mb]["n"] += 1
        money[mb]["usd"] += usd

    n_days_window = max(1, int(round((to_ms - from_ms) / 86400000.0)))
    daily_usd = [days[d]["usd"] for d in sorted(days)]
    padded = daily_usd + [0.0] * (n_days_window - len(daily_usd))
    median_daily = statistics.median(padded) if padded else 0.0

    # Одиниця спостереження — гаманець, не угода. Сто угод одного
    # маркетмейкера = одна точка.
    wallet_usd = sorted((v["usd"] for v in taker_wallets.values()), reverse=True)
    tot = sum(wallet_usd)
    top1 = (wallet_usd[0] / tot) if tot > 0 and wallet_usd else 0.0
    hhi = sum((u / tot) ** 2 for u in wallet_usd) if tot > 0 else 0.0
    n_eff_wallets = (1.0 / hhi) if hhi > 0 else 0.0

    return {
        "currency": currency,
        "window_days": n_days_window,
        "days_with_flow": len(days),
        "days_with_flow_frac": len(days) / float(n_days_window),
        "taker_trades": total_taker_n,
        "taker_notional_usd": total_taker_usd,
        "median_daily_taker_notional_usd": median_daily,
        "unique_taker_wallets": len(taker_wallets),
        "top1_wallet_share": top1,
        "n_eff_wallets_hhi": n_eff_wallets,
        "by_tenor": tenor,
        "by_moneyness": money,
        "daily": {d: {"n": days[d]["n"], "usd": days[d]["usd"],
                      "wallets": len(days[d]["wallets"])} for d in sorted(days)},
    }


# --------------------------------------------------------------------------
# ВЕРДИКТ — ЧИСТА ФУНКЦІЯ (клас 2)
# --------------------------------------------------------------------------

def final_verdict(median_daily_usd, unique_wallets, days_frac, th):
    """-> (вердикт, причина). FAIL має пріоритет над PASS ЗАВЖДИ."""
    reasons = []
    if median_daily_usd < th["fail_median_daily_taker_notional_usd"]:
        reasons.append("медіанний денний тейкерський нотіонал %.0f < %.0f"
                       % (median_daily_usd,
                          th["fail_median_daily_taker_notional_usd"]))
    if unique_wallets < th["fail_min_unique_taker_wallets"]:
        reasons.append("унікальних тейкерських гаманців %d < %d"
                       % (unique_wallets, th["fail_min_unique_taker_wallets"]))
    if reasons:
        return "FAIL", "; ".join(reasons)

    if (days_frac >= th["pass_min_days_with_flow_frac"]
            and unique_wallets >= th["pass_min_unique_taker_wallets"]):
        return "PASS", ("потік у %.1f%% діб, %d гаманців"
                        % (100.0 * days_frac, unique_wallets))

    miss = []
    if days_frac < th["pass_min_days_with_flow_frac"]:
        miss.append("діб з потоком %.1f%% < %.1f%%"
                    % (100.0 * days_frac,
                       100.0 * th["pass_min_days_with_flow_frac"]))
    if unique_wallets < th["pass_min_unique_taker_wallets"]:
        miss.append("гаманців %d < %d"
                    % (unique_wallets, th["pass_min_unique_taker_wallets"]))
    return "INCONCLUSIVE", "; ".join(miss)


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

    # --- [1] білий список імені
    p = parse_instrument("ETH-20240914-2400-C")
    check("1.1 базове ім'я", p is not None and p[0] == "ETH" and p[2] == 2400.0
          and p[3] == "C" and p[4] is None)
    p = parse_instrument("BTC-20260626-65000_5-P")
    check("1.2 дробовий страйк", p is not None and abs(p[2] - 65000.5) < 1e-9)
    p = parse_instrument("ETH-20240914-2400-C-42")
    check("1.3 risk universe розпізнано", p is not None and p[4] == 42)
    check("1.4 нижній регістр відкинуто",
          parse_instrument("eth-20240914-2400-C") is None)
    check("1.5 PERP відкинуто", parse_instrument("ETH-PERP") is None)
    check("1.6 спот відкинуто", parse_instrument("ETH-USDC") is None)
    check("1.7 хибний тип", parse_instrument("ETH-20240914-2400-X") is None)
    check("1.8 хибна дата", parse_instrument("ETH-20241332-2400-C") is None)
    check("1.9 нульовий страйк", parse_instrument("ETH-20240914-0-C") is None)
    check("1.10 зайвий сегмент",
          parse_instrument("ETH-20240914-2400-C-42-9") is None)
    check("1.11 нечисловий ru",
          parse_instrument("ETH-20240914-2400-C-abc") is None)
    check("1.12 порожній дріб", parse_instrument("ETH-20240914-2400_-C") is None)
    check("1.13 None", parse_instrument(None) is None)

    # --- [2] to_float
    check("2.1 рядок", to_float("1.5") == 1.5)
    check("2.2 число", to_float(2) == 2.0)
    check("2.3 сміття", to_float("abc") is None)
    check("2.4 nan", to_float("nan") is None)
    check("2.5 bool не число", to_float(True) is None)
    check("2.6 порожній рядок", to_float("") is None)

    # --- [3] вердикт: ПРІОРИТЕТ ГІЛОК
    th = THRESHOLDS
    check("3.1 FAIL по нотіоналу б'є PASS",
          final_verdict(50000.0, 100, 1.0, th)[0] == "FAIL")
    check("3.2 FAIL по гаманцях б'є PASS",
          final_verdict(1e9, 5, 1.0, th)[0] == "FAIL")
    check("3.3 PASS", final_verdict(200000.0, 40, 0.9, th)[0] == "PASS")
    check("3.4 мало діб -> INCONCLUSIVE",
          final_verdict(200000.0, 40, 0.5, th)[0] == "INCONCLUSIVE")
    check("3.5 мало гаманців -> INCONCLUSIVE",
          final_verdict(200000.0, 20, 0.95, th)[0] == "INCONCLUSIVE")
    check("3.6 порожнеча -> FAIL",
          final_verdict(0.0, 0, 0.0, th)[0] == "FAIL")
    check("3.7 рівно на порозі -> не FAIL",
          final_verdict(100000.0, 10, 0.80, th)[0] != "FAIL")

    # --- [4] агрегація на синтетиці
    base = 1700000000000
    trades = []
    for i in range(10):
        trades.append({
            "instrument_name": "BTC-20991231-50000-C",
            "liquidity_role": "taker",
            "timestamp": base + i * 3600000,
            "trade_amount": "1",
            "index_price": "50000",
            "wallet": "0xA" if i < 5 else "0xB",
        })
    trades.append({
        "instrument_name": "BTC-20991231-50000-C",
        "liquidity_role": "maker",
        "timestamp": base,
        "trade_amount": "1000",
        "index_price": "50000",
        "wallet": "0xMM",
    })
    c = new_counters()
    agg = aggregate(trades, "BTC", base - 1, base + 20 * 3600000, c)
    check("4.1 тейкерів 10", agg["taker_trades"] == 10)
    check("4.2 мейкер не в нотіоналі",
          abs(agg["taker_notional_usd"] - 500000.0) < 1e-6)
    check("4.3 мейкер порахований", c["kept_maker"] == 1)
    check("4.4 гаманців 2", agg["unique_taker_wallets"] == 2)
    check("4.5 top1 = 0.5", abs(agg["top1_wallet_share"] - 0.5) < 1e-9)
    check("4.6 n_eff гаманців = 2", abs(agg["n_eff_wallets_hhi"] - 2.0) < 1e-9)

    # --- [5] ВИРОДЖЕНИЙ контроль: один гаманець робить усе
    trades2 = [dict(t, wallet="0xONE") for t in trades
               if t["liquidity_role"] == "taker"]
    c2 = new_counters()
    agg2 = aggregate(trades2, "BTC", base - 1, base + 20 * 3600000, c2)
    check("5.1 монополія: гаманець один", agg2["unique_taker_wallets"] == 1)
    check("5.2 монополія: n_eff = 1",
          abs(agg2["n_eff_wallets_hhi"] - 1.0) < 1e-9)
    check("5.3 монополія -> FAIL попри нотіонал",
          final_verdict(agg2["median_daily_taker_notional_usd"],
                        agg2["unique_taker_wallets"],
                        agg2["days_with_flow_frac"], th)[0] == "FAIL")

    # --- [6] дропи рахуються
    bad = [
        {"instrument_name": "ETH-PERP", "liquidity_role": "taker",
         "timestamp": base, "trade_amount": "1", "index_price": "1",
         "wallet": "0xA"},
        {"instrument_name": "BTC-20991231-50000-C", "liquidity_role": "x",
         "timestamp": base, "trade_amount": "1", "index_price": "1",
         "wallet": "0xA"},
        {"instrument_name": "BTC-20991231-50000-C", "liquidity_role": "taker",
         "timestamp": base, "trade_amount": "1", "index_price": "0",
         "wallet": "0xA"},
        {"instrument_name": "BTC-20991231-50000-C", "liquidity_role": "taker",
         "timestamp": base, "trade_amount": "1", "index_price": "1",
         "wallet": ""},
        {"instrument_name": "ETH-20991231-2400-C", "liquidity_role": "taker",
         "timestamp": base, "trade_amount": "1", "index_price": "1",
         "wallet": "0xA"},
        {"instrument_name": "BTC-20991231-50000-C-7", "liquidity_role": "taker",
         "timestamp": base, "trade_amount": "1", "index_price": "1",
         "wallet": "0xA"},
        {"instrument_name": "BTC-20000101-50000-C", "liquidity_role": "taker",
         "timestamp": base, "trade_amount": "1", "index_price": "1",
         "wallet": "0xA"},
    ]
    c3 = new_counters()
    agg3 = aggregate(bad, "BTC", base - 1, base + 1, c3)
    check("6.1 нічого не пройшло", agg3["taker_trades"] == 0)
    check("6.2 drop_bad_name", c3["drop_bad_name"] == 1)
    check("6.3 drop_bad_role", c3["drop_bad_role"] == 1)
    check("6.4 drop_nonpositive_index", c3["drop_nonpositive_index"] == 1)
    check("6.5 drop_no_wallet", c3["drop_no_wallet"] == 1)
    check("6.6 drop_wrong_currency", c3["drop_wrong_currency"] == 1)
    check("6.7 drop_unknown_suffix", c3["drop_unknown_suffix"] == 1)
    check("6.8 drop_expiry_before_trade", c3["drop_expiry_before_trade"] == 1)
    check("6.9 сума дропів = rows_seen",
          sum(v for k, v in c3.items() if k.startswith("drop_"))
          == c3["rows_seen"])

    # --- [7] сторінкування через ІН'ЄКЦІЮ
    def fake_ok(currency, f, t, page, size):
        total = 7
        start = (page - 1) * size
        rows = [{"i": i} for i in range(start, min(start + size, total))]
        return {"trades": rows,
                "pagination": {"count": total,
                               "num_pages": (total + size - 1) // size}}

    c4 = new_counters()
    got = fetch_all_pages(fake_ok, "BTC", 0, 1, 3, 100, 0, c4, lambda s: None)
    check("7.1 усі сторінки зібрані", len(got) == 7)
    check("7.2 сторінок 3", c4["pages_fetched"] == 3)

    def fake_truncated(currency, f, t, page, size):
        return {"trades": [{"i": 0}, {"i": 1}],
                "pagination": {"count": 100, "num_pages": 1}}

    caught = False
    try:
        fetch_all_pages(fake_truncated, "BTC", 0, 1, 10, 100, 0,
                        new_counters(), lambda s: None)
    except RuntimeError:
        caught = True
    check("7.3 неповне сторінкування -> RuntimeError", caught)

    def fake_endless(currency, f, t, page, size):
        return {"trades": [{"i": page}] * size, "pagination": {}}

    caught = False
    try:
        fetch_all_pages(fake_endless, "BTC", 0, 1, 2, 5, 0,
                        new_counters(), lambda s: None)
    except RuntimeError:
        caught = True
    check("7.4 MAX_PAGES не обходиться тихо", caught)

    def fake_empty(currency, f, t, page, size):
        return {"trades": [], "pagination": {"count": 0, "num_pages": 0}}

    got = fetch_all_pages(fake_empty, "BTC", 0, 1, 10, 100, 0,
                          new_counters(), lambda s: None)
    check("7.5 порожнє вікно не падає", got == [])

    # --- [8] unwrap форми відповіді
    check("8.1 форма result", unwrap_result({"result": {"trades": []}})[1] == "result")
    check("8.2 форма flat", unwrap_result({"trades": []})[1] == "flat")
    caught = False
    try:
        unwrap_result({"error": {"code": -1}})
    except RuntimeError:
        caught = True
    check("8.3 JSON-RPC error піднімається", caught)
    caught = False
    try:
        unwrap_result({"zzz": 1})
    except RuntimeError:
        caught = True
    check("8.4 невідома форма піднімається", caught)

    # --- [9] бакети
    check("9.1 тенор 1д", bucket_of(1.0, TENOR_BUCKETS) == "0-2d")
    check("9.2 тенор 30д", bucket_of(30.0, TENOR_BUCKETS) == "8-31d")
    check("9.3 тенор 200д", bucket_of(200.0, TENOR_BUCKETS) == "31d+")
    check("9.4 ATM", bucket_of(0.01, MONEYNESS_BUCKETS) == "atm|k|<0.05")
    check("9.5 крило", bucket_of(0.9, MONEYNESS_BUCKETS) == "wing|k|>0.15")

    # --- [10] KNOWN_ENV
    check("10.1 KNOWN_ENV без дублів", len(KNOWN_ENV) == len(set(KNOWN_ENV)))
    check("10.2 усі імена VOLEDGE_*",
          all(n.startswith("VOLEDGE_") for n in KNOWN_ENV))

    # --- [11] PREFLIGHT: схема
    full = {f: 1 for f in REQUIRED_FIELDS}
    ok, missing, present = check_schema(full)
    check("11.1 повна схема проходить", ok and missing == [])
    partial = dict(full)
    del partial["wallet"]
    ok, missing, present = check_schema(partial)
    check("11.2 брак wallet ловиться", (not ok) and missing == ["wallet"])
    partial2 = dict(full)
    del partial2["liquidity_role"]
    del partial2["index_price"]
    ok, missing, present = check_schema(partial2)
    check("11.3 кілька полів у missing", set(missing) ==
          {"liquidity_role", "index_price"})
    ok, missing, present = check_schema(None)
    check("11.4 не-словник -> усе missing", (not ok) and
          len(missing) == len(REQUIRED_FIELDS))
    ok, missing, present = check_schema({"a": 1, "b": 2})
    check("11.5 present повертається", present == ["a", "b"])

    # --- [12] класи помилок мережі розрізняються
    check("12.1 ApiError не TransportError",
          not issubclass(ApiError, TransportError))
    check("12.2 обидва RuntimeError",
          issubclass(ApiError, RuntimeError)
          and issubclass(TransportError, RuntimeError))

    log("Самотести: %d/%d" % (n_ok, n_ok + n_fail))
    return n_fail == 0


# --------------------------------------------------------------------------
# PREFLIGHT
# --------------------------------------------------------------------------

def preflight(fetch_fn, currency, log):
    """ОДИН запит. Друкує фактичну схему, перевіряє обов'язкові поля."""
    log("PREFLIGHT: один запит, перевірка схеми відповіді")
    res = fetch_fn(currency, None, None, 1, 5)
    trades = res.get("trades")
    if not isinstance(trades, list):
        raise RuntimeError("PREFLIGHT: 'trades' не список, ключі=%s"
                           % (sorted(res.keys())[:8],))
    pag = res.get("pagination") or {}
    log("  заявлено всього угод: count=%s num_pages=%s"
        % (pag.get("count"), pag.get("num_pages")))
    if not trades:
        raise RuntimeError("PREFLIGHT: порожній список без фільтрів — "
                           "схему перевірити нічим")

    row = trades[0]
    ok, missing, present = check_schema(row)
    log("  ФАКТИЧНІ ПОЛЯ ЗАПИСУ (%d):" % len(present))
    for k in present:
        v = row.get(k)
        s = repr(v)
        if len(s) > 60:
            s = s[:57] + "..."
        log("    %-24s = %s" % (k, s))

    if not ok:
        log("")
        log("  БРАКУЄ ОБОВ'ЯЗКОВИХ ПОЛІВ: %s" % ", ".join(missing))
        log("  Без них крок 0 не є кроком 0:")
        log("    wallet         -> немає контрагентів, немає n_eff")
        log("    liquidity_role -> не відділити тейкера від маркетмейкера")
        log("    index_price    -> немає нотіоналу в USD")
        raise RuntimeError("PREFLIGHT: схема не містить %s — прогін зупинено"
                           % ", ".join(missing))

    log("  Схема ПОВНА: усі обов'язкові поля на місці.")
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
    log("DEFI КРОК 0 — інвентаризація потоку (READ-ONLY)")
    log("=" * 70)

    if not selftests(log):
        log("[ERROR] самотести не пройшли — прогін зупинено")
        return 2

    if offline == 1:
        log("VOLEDGE_OFFLINE=1 -> мережа не чіпається, вихід.")
        return 0

    currencies = [c.strip().upper() for c in
                  env_str("VOLEDGE_DEFI_CURRENCIES", "BTC,ETH").split(",")
                  if c.strip()]
    if not currencies:
        raise RuntimeError("VOLEDGE_DEFI_CURRENCIES порожній")
    days = env_int("VOLEDGE_DEFI_DAYS", 90, 1, 3650)
    page_size = env_int("VOLEDGE_DEFI_PAGE_SIZE", 1000, 1, 1000)
    max_pages = env_int("VOLEDGE_DEFI_MAX_PAGES", 2000, 1, 100000)
    sleep_ms = env_int("VOLEDGE_DEFI_SLEEP_MS", 200, 0, 60000)
    timeout_s = env_int("VOLEDGE_DEFI_TIMEOUT_S", 30, 1, 600)
    base_url = env_str("VOLEDGE_DEFI_BASE_URL", "https://api.lyra.finance")
    art_dir = env_str("VOLEDGE_DEFI_ARTIFACT_DIR", "artifacts/defi")
    no_write = env_int("VOLEDGE_DEFI_NO_WRITE", 0, 0, 1)
    pre_only = env_int("VOLEDGE_DEFI_PREFLIGHT_ONLY", 0, 0, 1)

    # Вікно ЗАКРИТЕ і закінчується в МИНУЛОМУ: інакше остання доба
    # неповна, а набір угод рухається під час збору.
    now = datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    from_ms = int(start.timestamp() * 1000)
    to_ms = int(end.timestamp() * 1000)

    log("Вікно UTC: %s .. %s (%d діб)"
        % (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), days))
    log("Джерело: %s" % base_url)
    log("Пороги (пре-реєстровані): %s" % json.dumps(THRESHOLDS, sort_keys=True))
    log("")

    counters0 = new_counters()
    fetch0 = make_fetch_fn(base_url, timeout_s, counters0)
    preflight(fetch0, currencies[0], log)
    log("")

    if pre_only == 1:
        log("VOLEDGE_DEFI_PREFLIGHT_ONLY=1 -> збір не запускається.")
        return 0

    result = {
        "schema": 1,
        "kind": "defi_flow_step0",
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_from_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_to_utc": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": base_url,
        "thresholds": dict(THRESHOLDS),
        "env_warnings": unknown,
        "currencies": {},
    }

    for cur in currencies:
        log("[%s] збір..." % cur)
        counters = new_counters()
        fetch_fn = make_fetch_fn(base_url, timeout_s, counters)
        t0 = time.time()
        trades = fetch_all_pages(fetch_fn, cur, from_ms, to_ms, page_size,
                                 max_pages, sleep_ms, counters, log)
        elapsed = time.time() - t0
        log("  [%s] сирих рядків %d за %.1f с (%d сторінок, %d ретраїв)"
            % (cur, len(trades), elapsed, counters["pages_fetched"],
               counters["http_retries"]))

        agg = aggregate(trades, cur, from_ms, to_ms, counters)
        verdict, reason = final_verdict(
            agg["median_daily_taker_notional_usd"],
            agg["unique_taker_wallets"],
            agg["days_with_flow_frac"],
            THRESHOLDS)
        agg["verdict"] = verdict
        agg["verdict_reason"] = reason
        agg["counters"] = counters
        agg["elapsed_s"] = round(elapsed, 2)
        result["currencies"][cur] = agg

        log("  [%s] тейкерських угод %d, нотіонал $%.0f"
            % (cur, agg["taker_trades"], agg["taker_notional_usd"]))
        log("  [%s] медіана на добу $%.0f | діб з потоком %d/%d (%.1f%%)"
            % (cur, agg["median_daily_taker_notional_usd"],
               agg["days_with_flow"], agg["window_days"],
               100.0 * agg["days_with_flow_frac"]))
        log("  [%s] гаманців %d | top1 %.1f%% | n_eff(HHI) %.1f"
            % (cur, agg["unique_taker_wallets"],
               100.0 * agg["top1_wallet_share"], agg["n_eff_wallets_hhi"]))
        log("  [%s] за тенором: %s" % (cur, ", ".join(
            "%s n=%d $%.0f" % (lab, agg["by_tenor"][lab]["n"],
                               agg["by_tenor"][lab]["usd"])
            for lab, _, _ in TENOR_BUCKETS)))
        log("  [%s] за moneyness: %s" % (cur, ", ".join(
            "%s n=%d $%.0f" % (lab, agg["by_moneyness"][lab]["n"],
                               agg["by_moneyness"][lab]["usd"])
            for lab, _, _ in MONEYNESS_BUCKETS)))
        drops = {k: v for k, v in counters.items()
                 if k.startswith("drop_") and v > 0}
        log("  [%s] мейкерських рядків (не в метриці): %d"
            % (cur, counters["kept_maker"]))
        log("  [%s] дропи: %s" % (cur, drops if drops else "немає"))
        log("  [%s] ВЕРДИКТ: %s — %s" % (cur, verdict, reason))
        log("")

    verdicts = {c: result["currencies"][c]["verdict"] for c in currencies}
    if all(v == "FAIL" for v in verdicts.values()):
        overall = "FAIL"
    elif any(v == "PASS" for v in verdicts.values()):
        overall = "PASS"
    else:
        overall = "INCONCLUSIVE"
    result["overall_verdict"] = overall

    log("=" * 70)
    log("ЗАГАЛЬНИЙ ВЕРДИКТ КРОКУ 0: %s   (%s)"
        % (overall, ", ".join("%s=%s" % kv for kv in sorted(verdicts.items()))))
    if overall == "FAIL":
        log("Напрям де-фі закрито. Оцінювач d_iv НЕ пишемо.")
    elif overall == "PASS":
        log("Далі: колектор котировок у artifacts/defi/, потім оцінювач.")
    else:
        log("Лише форвардний збір. Історичних претензій НЕ висувати.")
    log("=" * 70)

    if no_write == 0:
        os.makedirs(art_dir, exist_ok=True)
        fname = "defiflow_%s.json" % now.strftime("%Y%m%dT%H%M%SZ")
        path = os.path.join(art_dir, fname)
        payload = json.dumps(result, ensure_ascii=False, indent=1,
                             sort_keys=True, default=str)
        _afh = open(path, "w", encoding="utf-8")
        try:
            _afh.write(payload)
        finally:
            _afh.close()
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        log("Артефакт: %s (%d Б, sha256:%s)"
            % (path, len(payload.encode("utf-8")), digest))
    else:
        log("VOLEDGE_DEFI_NO_WRITE=1 -> артефакт не записано.")

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
