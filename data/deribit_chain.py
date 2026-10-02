# -*- coding: utf-8 -*-
"""
[EN] Deribit option-chain snapshot (public REST, no API keys) and the
full surface pipeline: instrument parsing -> quote filters -> synthetic
forward from put-call parity -> bid/mid/ask IV via the in-house Black-76
solver -> per-expiry SVI fit -> RR25/BF25 in premium-adjusted deltas ->
calendar check -> JSON artifact in artifacts/chain/.
Run order: offline self-tests, then live snapshot (VOLEDGE_OFFLINE=1
runs self-tests only).

--- Ukrainian original below ---
Vol-Edge / data / deribit_chain.py

Знімок опціонного ланцюга Deribit (публічний REST, БЕЗ ключів) і повний
пайплайн: парсинг інструментів -> фільтри котирувань -> synthetic-форварди
з put-call parity -> IV(bid/mid/ask) через власний солвер -> SVI-фіт по
кожній експірації (з каскадом fixed_s_hint від довших до коротших) ->
RR25/BF25 у premium-adjusted дельтах -> calendar-перевірка -> [OVERALL]/
[SUMMARY]-логи + JSON-артефакт.

Порядок запуску (подвійний клік):
  1) Офлайн-самотести (без мережі): парсинг, tau, синтетичний ланцюг
     наскрізь через пайплайн з відновленням форварда та ATM IV.
  2) Live-знімок по валютах з VOLEDGE_CURRENCIES (default "BTC,ETH").

Environment flags (обробка явна, значення логуються):
  VOLEDGE_CURRENCIES         - "BTC,ETH" | "BTC" | "ETH"
  VOLEDGE_OFFLINE            - "1" -> пропустити live-секцію (самотести)
  VOLEDGE_CHAIN_DEPTH        - "1" (default) -> другий прохід /public/ticker
                               по кожному використаному інструменту заради
                               best_bid_amount/best_ask_amount; "0" -> лише
                               рівень 1 (bid/ask/mark/oi з book summary,
                               НУЛЬ додаткових HTTP-запитів)
  VOLEDGE_CHAIN_DEPTH_BUDGET - стеля запитів на знімок (default 1200)
  VOLEDGE_CHAIN_DEPTH_MAX_S  - стеля часу на прохід глибини, с (default 240;
                               стелі рахуються НА ВАЛЮТУ, не на знімок)
  VOLEDGE_CHAIN_DEPTH_SLEEP_MS - пауза між запитами, мс (default 0)

ЗБІР, НЕ ВИМІРЮВАННЯ (schema 2, 27 сер)
---------------------------------------
Котирування пишуться в артефакт і НЕ читаються жодним аналітичним
модулем. Жодне з нових полів не гейтить фіт, не дропає інструмент і не
змінює IV. Причина: історію стакана безкоштовно не купити, тому збір
починається зараз, а вимірювальні претензії — після накопичення ряду.
Порожній прохід глибини (мережа лягла, бюджет вичерпано) НЕ валить
знімок: поля лишаються None, depth_status каже причину, лічильники
друкуються. Це не fail-open, бо нічого не гейтиться.

ЧОГО ТУТ НЕМАЄ І ЧОМУ: ts_quote (час останнього оновлення котирування).
І book_summary.creation_timestamp, і ticker.timestamp — час ГЕНЕРАЦІЇ
відповіді сервером, а не час котирування. Записати їх як «свіжість»
означало б завести показник, який завжди говорить «свіжо». Замість
цього — px_moved: скільки інструментів зрушили ціну між проходом
summary і проходом ticker. Це міряє, наскільки живий був ринок під час
збору, і робить це чесно.

Залежності: black76_greeks.py, svi_fit.py у ../core. Лише stdlib.
Жодного silent fail-open: кожен відкинутий інструмент/зріз -> лічильник
з причиною.
"""

import json
import math
import os
import sys
import time
import traceback
import urllib.request
import urllib.error
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "core"))
for _p in (_CORE_DIR, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from black76_greeks import implied_vol, vega_usd, price_usd  # noqa: E402
from svi_fit import fit_slice, calendar_check, rr_bf, atm_dns_pa_k  # noqa: E402
from env_flags import merge_known, warn_unknown_env  # noqa: E402

# Змінні, які читає САМЕ ЦЯ точка входу (обидві — у main()). Імпортовані
# black76_greeks/svi_fit оточення не читають.
KNOWN_ENV = merge_known((
    "VOLEDGE_CURRENCIES",
    "VOLEDGE_CHAIN_DEPTH",
    "VOLEDGE_CHAIN_DEPTH_BUDGET",
    "VOLEDGE_CHAIN_DEPTH_MAX_S",
    "VOLEDGE_CHAIN_DEPTH_SLEEP_MS",
))

# ----------------------------------------------------------------------------
# Конфігурація (явна, в одному місці)
# ----------------------------------------------------------------------------

API_BASE = "https://www.deribit.com/api/v2/public"
HTTP_TIMEOUT_S = 15
HTTP_RETRIES = 3
HTTP_RETRY_SLEEP_S = 2.0

MIN_TAU_HOURS = 4.0        # експірації, що вмирають, не фітимо
MIN_BID_COIN = 1e-4        # котирування-пил відкидаємо
MAX_REL_SPREAD = 4.0       # (ask-bid)/bid; ширші крила відкидаємо
MIN_PARITY_PAIRS = 1       # мінімум C/P-пар для форварда
DELTA_LEVEL = 0.25         # RR/BF рівень

# Версія схеми артефакту. 1 = без котирувань (до 27 сер 2026).
# 2 = quote-поля рівня 1 + (опційно) розміри рівня 2.
# БЕЗ ЦЬОГО ПОЛЯ читач через два місяці отримав би None на половині
# ряду і не мав би способу відрізнити «не збирали» від «не було».
SCHEMA_VERSION = 2

# Прохід глибини: власні таймаути, бо 879 запитів х 3 ретраї х 2 с
# паузи = півгодини на одному лежачому інструменті.
DEPTH_TIMEOUT_S = 8
DEPTH_RETRIES = 1
DEPTH_BUDGET_DEFAULT = 1200
DEPTH_MAX_S_DEFAULT = 240.0
DEPTH_SLEEP_MS_DEFAULT = 0
# Відносний зсув ціни між проходами, який рахуємо за «ринок рухався».
DEPTH_PX_MOVE_REL = 0.005

ARTIFACTS_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "artifacts",
                                              "chain"))

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


# ----------------------------------------------------------------------------
# HTTP (stdlib) з ретраями
# ----------------------------------------------------------------------------

def http_get_json(url: str, timeout: float = None, retries: int = None,
                  quiet: bool = False) -> dict:
    """GET -> result. timeout/retries=None -> глобальні дефолти.

    quiet=True потрібен ЛИШЕ проходу глибини: 879 запитів, і кожен
    ретрай, надрукований у лог, зробив би лог cron нечитабельним, а
    grep по ньому — брехливим. Мовчазним стає ДРУК, не облік: провал
    повертається винятком і потрапляє в лічильник depth_failed.
    """
    tmo = HTTP_TIMEOUT_S if timeout is None else timeout
    tries = HTTP_RETRIES if retries is None else retries
    last_err = None
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Vol-Edge/1.0"})
            with urllib.request.urlopen(req, timeout=tmo) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if "result" not in payload:
                raise RuntimeError(f"Відповідь без 'result': {payload}")
            return payload["result"]
        except Exception as e:  # явний ретрай з логом
            last_err = e
            if not quiet:
                print(f"  [HTTP] спроба {attempt}/{tries} провалилась: {e}")
            if attempt < tries:
                time.sleep(HTTP_RETRY_SLEEP_S)
    raise RuntimeError(f"HTTP остаточно провалився: {url} :: {last_err}")


def _f(x):
    """float або None. Deribit кладе null у порожні поля книги."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN -> None


def fetch_index_price(currency: str) -> float:
    r = http_get_json(f"{API_BASE}/get_index_price?"
                      f"index_name={currency.lower()}_usd")
    px = float(r["index_price"])
    if not (px > 0.0):
        raise RuntimeError(f"index_price={px} для {currency}")
    return px


def fetch_book_summary(currency: str) -> list:
    r = http_get_json(f"{API_BASE}/get_book_summary_by_currency?"
                      f"currency={currency}&kind=option")
    if not isinstance(r, list) or len(r) == 0:
        raise RuntimeError(f"Порожній book summary для {currency}")
    return r


# ----------------------------------------------------------------------------
# Рівень 2: розміри на найкращих рівнях (/public/ticker, 1 інструмент/запит)
# ----------------------------------------------------------------------------

def make_ticker_depth_fn(budget: int = DEPTH_BUDGET_DEFAULT,
                         max_s: float = DEPTH_MAX_S_DEFAULT,
                         sleep_ms: int = DEPTH_SLEEP_MS_DEFAULT):
    """Фабрика depth_fn: список імен -> ({name: depth}, counters).

    ЧОМУ ФАБРИКА, А НЕ ФУНКЦІЯ: process_currency приймає depth_fn
    параметром, тому самотестиганяють увесь шлях заповнення полів і
    всі гілки лічильників ОФЛАЙН, підсовуючи фейкову depth_fn. Без
    ін'єкції гілки budget_hit/time_hit/no_data перевірялись би лише на
    живій мережі, тобто ніколи.

    Стелі — ДВІ, і це не надмірність: бюджет запитів захищає від
    несподівано довгого ланцюга (нова експірація подвоює список),
    бюджет часу — від повільної мережі при нормальній довжині. Одна
    стеля ловить рівно один із двох випадків.
    """
    def _fn(names):
        cnt = {"requested": 0, "ok": 0, "failed": 0, "skipped": 0,
               "budget_hit": 0, "time_hit": 0, "elapsed_s": 0.0}
        out = {}
        t0 = time.monotonic()
        for i, nm in enumerate(names):
            if cnt["requested"] >= budget:
                cnt["budget_hit"] = 1
                cnt["skipped"] = len(names) - i
                break
            if time.monotonic() - t0 > max_s:
                cnt["time_hit"] = 1
                cnt["skipped"] = len(names) - i
                break
            cnt["requested"] += 1
            url = f"{API_BASE}/ticker?instrument_name={nm}"
            try:
                r = http_get_json(url, timeout=DEPTH_TIMEOUT_S,
                                  retries=DEPTH_RETRIES, quiet=True)
            except Exception:
                cnt["failed"] += 1
                continue
            out[nm] = {
                "bid_sz": _f(r.get("best_bid_amount")),
                "ask_sz": _f(r.get("best_ask_amount")),
                "t_bid": _f(r.get("best_bid_price")),
                "t_ask": _f(r.get("best_ask_price")),
                "ts_ms": r.get("timestamp"),
            }
            cnt["ok"] += 1
            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)
        cnt["elapsed_s"] = round(time.monotonic() - t0, 2)
        return out, cnt
    return _fn


def apply_depth(results: list, depth_map: dict) -> dict:
    """Розкладає розміри по рядках зрізів. Нічого не дропає.

    px_moved замінює недоступний ts_quote: ціна в ticker відрізняється
    від ціни в book summary рівно тому, що між проходами минув час.
    Частка таких рядків — міра живості ринку під час збору, і саме її
    треба буде дивитись, коли черга дійде до торгованості.
    """
    c = {"rows": 0, "filled": 0, "no_data": 0, "no_name": 0,
         "one_sided_sz": 0, "px_moved": 0}
    for r in results:
        for row in (r.get("rows") or []):
            if row.get("status") != "OK":
                continue
            c["rows"] += 1
            nm = row.get("name")
            if nm is None:
                row["depth_status"] = "no_name"
                c["no_name"] += 1
                continue
            d = depth_map.get(nm)
            if d is None:
                row["depth_status"] = "no_data"
                c["no_data"] += 1
                continue
            row["bid_sz"] = d.get("bid_sz")
            row["ask_sz"] = d.get("ask_sz")
            row["depth_ts_ms"] = d.get("ts_ms")
            row["depth_status"] = "OK"
            c["filled"] += 1
            if not row["bid_sz"] or not row["ask_sz"]:
                c["one_sided_sz"] += 1
            moved = False
            for tk, sk in (("t_bid", "bid"), ("t_ask", "ask")):
                a, b = d.get(tk), row.get(sk)
                if a is None or not b:
                    continue
                if abs(a - b) / abs(b) > DEPTH_PX_MOVE_REL:
                    moved = True
            if moved:
                c["px_moved"] += 1
    return c


# ----------------------------------------------------------------------------
# Парсинг інструментів
# ----------------------------------------------------------------------------

def parse_instrument(name: str) -> dict:
    """
    'BTC-27MAR26-100000-C' -> {currency, expiry(dt UTC 08:00), strike, cp}.
    Дробові страйки Deribit кодує через 'd': '0d625' -> 0.625.
    Невалідна назва -> ValueError (явно).
    """
    parts = name.split("-")
    if len(parts) != 4:
        raise ValueError(f"parse_instrument: '{name}' — очікую 4 сегменти")
    cur, dstr, kstr, cps = parts
    if cps == "C":
        cp = 1
    elif cps == "P":
        cp = -1
    else:
        raise ValueError(f"parse_instrument: '{name}' — cp='{cps}'")
    day = int(dstr[:-5])
    mon_s = dstr[-5:-2].upper()
    if mon_s not in MONTHS:
        raise ValueError(f"parse_instrument: '{name}' — місяць '{mon_s}'")
    year = 2000 + int(dstr[-2:])
    expiry = datetime(year, MONTHS[mon_s], day, 8, 0, 0,
                      tzinfo=timezone.utc)
    strike = float(kstr.replace("d", "."))
    if not (strike > 0.0):
        raise ValueError(f"parse_instrument: '{name}' — strike={strike}")
    return {"currency": cur, "expiry": expiry, "strike": strike, "cp": cp}


def tau_years(expiry: datetime, now: datetime) -> float:
    return (expiry - now).total_seconds() / (365.0 * 24.0 * 3600.0)


# ----------------------------------------------------------------------------
# Побудова ланцюга: групування + фільтри з лічильниками
# ----------------------------------------------------------------------------

def build_chain(entries: list, now: datetime) -> tuple:
    """
    entries: список dict з ключами instrument_name, bid_price, ask_price
             (обидві в монеті; None якщо котирування немає).
    Повертає (chain, counters):
      chain: {expiry_dt: {strike: {1: quote, -1: quote}}}
      quote: {'bid': float, 'ask': float} у МОНЕТІ (лише ті, що пройшли фільтр)
    """
    c = {"n_total": 0, "drop_parse": 0, "drop_expired": 0,
         "drop_short_tau": 0, "drop_no_bid": 0, "drop_dust": 0,
         "drop_wide": 0, "kept": 0,
         # Діагностика рівня 1 — БЕЗ дропу. Схрещена книга і mark поза
         # [bid, ask] не є підставою викидати інструмент із фіту (фіт
         # іде по mid і працює), але їхня частка — те, що доведеться
         # знати, коли черга дійде до торгованості.
         "q_crossed": 0, "q_mark_outside": 0, "q_no_mark": 0}
    chain = {}
    min_tau = MIN_TAU_HOURS / (365.0 * 24.0)
    for e in entries:
        c["n_total"] += 1
        try:
            meta = parse_instrument(e["instrument_name"])
        except (ValueError, KeyError):
            c["drop_parse"] += 1
            continue
        t = tau_years(meta["expiry"], now)
        if t <= 0.0:
            c["drop_expired"] += 1
            continue
        if t < min_tau:
            c["drop_short_tau"] += 1
            continue
        bid = e.get("bid_price")
        ask = e.get("ask_price")
        if bid is None or ask is None or bid <= 0.0 or ask <= 0.0:
            c["drop_no_bid"] += 1
            continue
        bid, ask = float(bid), float(ask)
        if bid < MIN_BID_COIN:
            c["drop_dust"] += 1
            continue
        if (ask - bid) / bid > MAX_REL_SPREAD:
            c["drop_wide"] += 1
            continue
        c["kept"] += 1
        if bid >= ask:
            c["q_crossed"] += 1
        mark = _f(e.get("mark_price"))
        if mark is None:
            c["q_no_mark"] += 1
        elif mark < min(bid, ask) or mark > max(bid, ask):
            c["q_mark_outside"] += 1
        exp = meta["expiry"]
        chain.setdefault(exp, {}).setdefault(meta["strike"], {})[meta["cp"]] = {
            "bid": bid, "ask": ask,
            "name": e.get("instrument_name"),
            "mark": mark,
            "oi": _f(e.get("open_interest")),
            "vol24h": _f(e.get("volume")),
            "mark_iv": _f(e.get("mark_iv")),
        }
    return chain, c


# ----------------------------------------------------------------------------
# Synthetic-форвард з put-call parity
# ----------------------------------------------------------------------------

def synthetic_forward(strikes_map: dict, index_price: float) -> tuple:
    """
    strikes_map: {strike: {1: quote, -1: quote}} для однієї експірації.
    F = K + (C_mid - P_mid)_USD по парах з обома сторонами; беремо медіану
    трьох пар, найближчих до ATM (|C-P| мінімальний).
    Повертає (F, n_pairs) або кидає RuntimeError (без мовчазного фолбеку).
    """
    cands = []
    for K, sides in strikes_map.items():
        if 1 in sides and -1 in sides:
            c_mid = 0.5 * (sides[1]["bid"] + sides[1]["ask"]) * index_price
            p_mid = 0.5 * (sides[-1]["bid"] + sides[-1]["ask"]) * index_price
            cands.append((abs(c_mid - p_mid), K + c_mid - p_mid))
    if len(cands) < MIN_PARITY_PAIRS:
        raise RuntimeError(
            f"synthetic_forward: {len(cands)} parity-пар "
            f"(< {MIN_PARITY_PAIRS}) — форвард не визначено")
    cands.sort(key=lambda x: x[0])
    top = [f for _, f in cands[:3]]
    top.sort()
    F = top[len(top) // 2]
    if not (F > 0.0):
        raise RuntimeError(f"synthetic_forward: F={F}")
    return F, len(cands)


# ----------------------------------------------------------------------------
# IV по зрізу: OTM-сторона, bid/mid/ask
# ----------------------------------------------------------------------------

def slice_ivs(strikes_map: dict, F: float, tau: float,
              index_price: float) -> tuple:
    """
    Повертає (ks, ivs_mid, weights, rows, counters).
    OTM-конвенція: K >= F -> call, K < F -> put. weights = vega^2 при mid IV.
    Провал IV на bid/ask логується як half-quote (mid все ще використовується,
    якщо сам mid інвертувався) або дроп із причиною.
    """
    c = {"n_strikes": 0, "used": 0, "drop_no_otm_side": 0,
         "drop_iv_fail": 0, "half_quote": 0}
    ks, ivs, wgts, rows = [], [], [], []
    for K in sorted(strikes_map.keys()):
        c["n_strikes"] += 1
        cp = 1 if K >= F else -1
        sides = strikes_map[K]
        if cp not in sides:
            c["drop_no_otm_side"] += 1
            continue
        q = sides[cp]
        mid_usd = 0.5 * (q["bid"] + q["ask"]) * index_price
        try:
            iv_mid = implied_vol(mid_usd, F, K, tau, cp)
        except (ValueError, RuntimeError) as e:
            c["drop_iv_fail"] += 1
            rows.append({"K": K, "cp": cp, "status": f"iv_fail: {e}"})
            continue
        iv_bid = iv_ask = None
        try:
            iv_bid = implied_vol(q["bid"] * index_price, F, K, tau, cp)
            iv_ask = implied_vol(q["ask"] * index_price, F, K, tau, cp)
        except (ValueError, RuntimeError):
            c["half_quote"] += 1
        k = math.log(K / F)
        v = vega_usd(F, K, tau, iv_mid)
        ks.append(k)
        ivs.append(iv_mid)
        wgts.append(v * v)
        mid_coin = 0.5 * (q["bid"] + q["ask"])
        rows.append({
            "K": K, "cp": cp, "k": k, "iv_mid": iv_mid,
            "iv_bid": iv_bid, "iv_ask": iv_ask, "status": "OK",
            # --- рівень 1: з тієї самої відповіді, нуль запитів ---
            "name": q.get("name"),
            "bid": q["bid"], "ask": q["ask"],
            "mark": q.get("mark"),
            "oi": q.get("oi"), "vol24h": q.get("vol24h"),
            "mark_iv_deribit": q.get("mark_iv"),
            "spread_rel": (q["ask"] - q["bid"]) / mid_coin
            if mid_coin > 0.0 else None,
            # iv_bid/iv_ask лічені НАШИМ солвером на НАШОМУ форварді.
            # mark_iv_deribit лежить поруч як чужа величина на чужому
            # форварді — порівнювати можна, змішувати в один ряд ні.
            "iv_spread": (iv_ask - iv_bid)
            if (iv_bid is not None and iv_ask is not None) else None,
            # --- рівень 2: заповнює apply_depth, якщо прохід був ---
            "bid_sz": None, "ask_sz": None, "depth_ts_ms": None,
            "depth_status": "not_requested",
        })
        c["used"] += 1
    return ks, ivs, wgts, rows, c


# ----------------------------------------------------------------------------
# Повний прогін по валюті
# ----------------------------------------------------------------------------

def process_currency(currency: str, entries: list, index_price: float,
                     now: datetime, depth_fn=None) -> dict:
    print(f"\n[{currency}] index={index_price:.2f}")
    chain, cc = build_chain(entries, now)
    print(f"[{currency}] фільтри: total={cc['n_total']} kept={cc['kept']} | "
          f"parse={cc['drop_parse']} expired={cc['drop_expired']} "
          f"short_tau={cc['drop_short_tau']} no_bid={cc['drop_no_bid']} "
          f"dust={cc['drop_dust']} wide={cc['drop_wide']}")
    print(f"[{currency}] книга: crossed={cc['q_crossed']} "
          f"mark_outside={cc['q_mark_outside']} no_mark={cc['q_no_mark']} "
          f"(діагностика, дропу немає)")
    if cc["kept"] == 0:
        raise RuntimeError(f"{currency}: після фільтрів нуль котирувань")

    expiries = sorted(chain.keys(), reverse=True)  # довші першими: каскад s
    results = []
    models = []
    s_hint = None
    for exp in expiries:
        t = tau_years(exp, now)
        label = exp.strftime("%d%b%y").upper()
        try:
            F, n_pairs = synthetic_forward(chain[exp], index_price)
        except RuntimeError as e:
            print(f"[OVERALL] {currency} {label} d={t*365:.1f} :: "
                  f"SKIP (forward): {e}")
            results.append({"expiry": label, "tau": t, "status": "no_forward"})
            continue
        ks, ivs, wgts, rows, sc = slice_ivs(chain[exp], F, t, index_price)
        if sc["used"] == 0:
            print(f"[OVERALL] {currency} {label} d={t*365:.1f} :: "
                  f"SKIP (нуль IV): {sc}")
            results.append({"expiry": label, "tau": t, "status": "no_ivs",
                            "counters": sc})
            continue
        try:
            fr = fit_slice(ks, ivs, t, weights=wgts, fixed_s_hint=s_hint)
        except RuntimeError as e:
            print(f"[OVERALL] {currency} {label} d={t*365:.1f} :: "
                  f"SKIP (fit): {e}")
            results.append({"expiry": label, "tau": t, "status": "fit_fail"})
            continue
        if fr.model.kind == "svi":
            s_hint = fr.model.params[4]  # каскад s до коротших зрізів
        k_atm = atm_dns_pa_k(fr.model)
        atm_iv = fr.model.iv(k_atm)
        rr = bf = None
        rr_note = ""
        try:
            rr, bf, _ = rr_bf(fr.model, DELTA_LEVEL, pa=True)
        except RuntimeError as e:
            rr_note = f" rr_bf_fail: {e}"
        rr_s = f"{rr*100:+.2f}" if rr is not None else "n/a"
        bf_s = f"{bf*100:+.2f}" if bf is not None else "n/a"
        print(f"[OVERALL] {currency} {label} d={t*365:6.1f} F={F:12.2f} "
              f"pairs={n_pairs:2d} used={sc['used']:2d}/"
              f"{sc['n_strikes']:2d} mode={fr.mode:8s} "
              f"ATM={atm_iv*100:6.2f}% RR25pa={rr_s:>7s}vp "
              f"BF25pa={bf_s:>7s}vp durr={len(fr.durrleman_bad)}{rr_note}")
        models.append(fr.model)
        results.append({
            "expiry": label, "tau": t, "status": "OK", "forward": F,
            "n_parity_pairs": n_pairs, "slice_counters": sc,
            "fit_mode": fr.mode, "fit_sse": fr.sse,
            "fit_counters": fr.counters,
            "svi_params": list(fr.model.params) if fr.model.kind == "svi"
            else None,
            "atm_dns_pa_iv": atm_iv, "rr25_pa": rr, "bf25_pa": bf,
            "durrleman_bad_n": len(fr.durrleman_bad),
            "rows": rows,
        })

    # --- прохід глибини (рівень 2) ---
    names = [row["name"] for r in results for row in (r.get("rows") or [])
             if row.get("status") == "OK" and row.get("name")]
    if depth_fn is None:
        depth_c = {"mode": "off", "candidates": len(names)}
        print(f"[{currency}] глибина: ВИМКНЕНО явно "
              f"(кандидатів {len(names)}, запитів 0)")
    else:
        dmap, fetch_c = depth_fn(names)
        fill_c = apply_depth(results, dmap)
        depth_c = {"mode": "ticker", "candidates": len(names)}
        depth_c.update(fetch_c)
        depth_c.update(fill_c)
        print(f"[{currency}] глибина: кандидатів={len(names)} "
              f"запитів={fetch_c['requested']} ok={fetch_c['ok']} "
              f"failed={fetch_c['failed']} skipped={fetch_c['skipped']} "
              f"budget_hit={fetch_c['budget_hit']} "
              f"time_hit={fetch_c['time_hit']} "
              f"{fetch_c['elapsed_s']}с")
        print(f"[{currency}] глибина: заповнено={fill_c['filled']}/"
              f"{fill_c['rows']} no_data={fill_c['no_data']} "
              f"one_sided_sz={fill_c['one_sided_sz']} "
              f"px_moved={fill_c['px_moved']}")

    if len(models) < 2:
        n_cal, worst, cal_info = 0, 0.0, {"details": [], "pairs_skipped": 0}
    else:
        n_cal, worst, cal_info = calendar_check(models)
    ok_n = sum(1 for r in results if r["status"] == "OK")
    print(f"[SUMMARY] {currency}: експірацій={len(expiries)} OK={ok_n} "
          f"calendar_violations={n_cal} worst_gap={worst:.3e} "
          f"calendar_pairs_skipped={cal_info['pairs_skipped']}")
    return {"schema": SCHEMA_VERSION,
            "currency": currency, "index_price": index_price,
            "snapshot_utc": now.isoformat(), "filter_counters": cc,
            "depth_counters": depth_c,
            "calendar_violations": n_cal, "calendar_worst_gap": worst,
            "slices": results}


# ----------------------------------------------------------------------------
# Офлайн-самотести
# ----------------------------------------------------------------------------

class TestCounters:
    def __init__(self):
        self.run = 0
        self.passed = 0
        self.failures = []

    def check(self, name, ok, detail=""):
        self.run += 1
        if ok:
            self.passed += 1
        else:
            self.failures.append(f"{name}: {detail}")
            print(f"  [FAIL] {name}: {detail}")


def _synth_entries(F_true, sigma_fn, tau, expiry_str, index_price, strikes):
    """Генерує канонічні book-записи з відомої воли: mid=теор.ціна, спред 2%."""
    out = []
    for K in strikes:
        for cp, suf in ((1, "C"), (-1, "P")):
            sig = sigma_fn(math.log(K / F_true))
            px_usd = price_usd(F_true, K, tau, sig, cp)
            mid_coin = px_usd / index_price
            half = 0.01 * mid_coin
            out.append({"instrument_name": f"BTC-{expiry_str}-{int(K)}-{suf}",
                        "bid_price": mid_coin - half,
                        "ask_price": mid_coin + half,
                        # Поля рівня 1 приходять у ТІЙ САМІЙ відповіді
                        # book summary — синтетика мусить їх мати, інакше
                        # самотест перевіряв би не той шлях.
                        "mark_price": mid_coin,
                        "open_interest": 100.0 + K / 1000.0,
                        "volume": 1.5,
                        "mark_iv": 100.0 * sig})
    return out


def _fake_depth_fn(names):
    """Повний успіх: усі імена мають розміри."""
    cnt = {"requested": len(names), "ok": len(names), "failed": 0,
           "skipped": 0, "budget_hit": 0, "time_hit": 0, "elapsed_s": 0.0}
    return ({nm: {"bid_sz": 10.0, "ask_sz": 5.0, "t_bid": None,
                  "t_ask": None, "ts_ms": 1700000000000}
             for nm in names}, cnt)


def _fake_depth_fn_partial(names):
    """Бюджет вичерпано на половині — гілка budget_hit + no_data."""
    half = len(names) // 2
    cnt = {"requested": half, "ok": half, "failed": 0,
           "skipped": len(names) - half, "budget_hit": 1, "time_hit": 0,
           "elapsed_s": 0.0}
    return ({nm: {"bid_sz": 0.0, "ask_sz": 5.0, "t_bid": None,
                  "t_ask": None, "ts_ms": 1}
             for nm in names[:half]}, cnt)


def _fake_depth_fn_empty(names):
    """Мережа лягла цілком: жодного розміру, але знімок мусить вижити."""
    cnt = {"requested": len(names), "ok": 0, "failed": len(names),
           "skipped": 0, "budget_hit": 0, "time_hit": 0, "elapsed_s": 0.0}
    return ({}, cnt)


def run_self_tests() -> TestCounters:
    tc = TestCounters()

    print("[1] Парсинг назв інструментів")
    m = parse_instrument("BTC-27MAR26-100000-C")
    tc.check("parse: базовий", m["currency"] == "BTC" and m["cp"] == 1
             and m["strike"] == 100000.0
             and m["expiry"] == datetime(2026, 3, 27, 8, 0,
                                         tzinfo=timezone.utc), str(m))
    m2 = parse_instrument("ETH-4SEP26-0d625-P")
    tc.check("parse: дробовий страйк 'd'", m2["strike"] == 0.625,
             str(m2["strike"]))
    for bad in ("BTC-27MAR26-100000", "BTC-27XXX26-100-C", "BTC-27MAR26-0-C"):
        try:
            parse_instrument(bad)
            tc.check(f"parse: '{bad}' відхилено", False, "не кинув виняток")
        except ValueError:
            tc.check(f"parse: '{bad}' відхилено", True)

    print("[2] tau")
    now = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)
    t = tau_years(datetime(2027, 7, 25, 8, 0, tzinfo=timezone.utc), now)
    tc.check("tau: рівно рік", abs(t - 1.0) < 1e-9, f"t={t}")

    print("[3] Синтетичний ланцюг наскрізь через пайплайн")
    F_true, idx, tau = 100000.0, 99500.0, 30.0 / 365.0
    a, b, rho, mm, ss = 0.012, 0.30, 0.10, -0.02, 0.18

    def sigma_fn(k):
        km = k - mm
        return math.sqrt((a + b * (rho * km + math.sqrt(km * km + ss * ss)))
                         / tau)

    strikes = [70000, 80000, 85000, 90000, 95000, 100000,
               105000, 110000, 120000, 135000, 150000]
    entries = _synth_entries(F_true, sigma_fn, tau, "24AUG26", idx, strikes)
    now3 = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)
    res = process_currency("BTC", entries, idx, now3)
    sl = [r for r in res["slices"] if r["status"] == "OK"]
    tc.check("pipeline: 1 зріз OK", len(sl) == 1, f"OK={len(sl)}")
    if len(sl) == 1:
        r = sl[0]
        tc.check("pipeline: форвард відновлено (<0.05%)",
                 abs(r["forward"] - F_true) / F_true < 5e-4,
                 f"F={r['forward']:.2f} vs {F_true}")
        tc.check("pipeline: mode FULL_SVI", r["fit_mode"] == "FULL_SVI",
                 r["fit_mode"])
        atm_true = sigma_fn(-0.5 * sigma_fn(0.0) ** 2 * tau)
        tc.check("pipeline: ATM IV близько до істини (<0.5vp)",
                 abs(r["atm_dns_pa_iv"] - atm_true) < 0.005,
                 f"fit={r['atm_dns_pa_iv']:.4f} true={atm_true:.4f}")
        tc.check("pipeline: durrleman чистий", r["durrleman_bad_n"] == 0,
                 str(r["durrleman_bad_n"]))
        tc.check("pipeline: RR25pa порахований", r["rr25_pa"] is not None,
                 "None")

    print("[4] Фільтри: лічильники")
    bad_entries = [
        {"instrument_name": "BTC-24AUG26-100000-C",
         "bid_price": None, "ask_price": 0.01},              # no_bid
        {"instrument_name": "BTC-24AUG26-90000-C",
         "bid_price": 1e-5, "ask_price": 2e-5},              # dust
        {"instrument_name": "BTC-24AUG26-80000-C",
         "bid_price": 0.001, "ask_price": 0.05},             # wide
        {"instrument_name": "GARBAGE",
         "bid_price": 0.01, "ask_price": 0.02},              # parse
        {"instrument_name": "BTC-24JUL26-100000-C",
         "bid_price": 0.01, "ask_price": 0.02},              # expired
    ]
    _, cnt = build_chain(bad_entries, now3)
    tc.check("filters: no_bid", cnt["drop_no_bid"] == 1, str(cnt))
    tc.check("filters: dust", cnt["drop_dust"] == 1, str(cnt))
    tc.check("filters: wide", cnt["drop_wide"] == 1, str(cnt))
    tc.check("filters: parse", cnt["drop_parse"] == 1, str(cnt))
    tc.check("filters: expired", cnt["drop_expired"] == 1, str(cnt))
    tc.check("filters: kept=0", cnt["kept"] == 0, str(cnt))

    # Заголовок НЕ містить рядка ENV-WARN: маркер мусить зустрічатися
    # лише там, де він означає подію, інакше grep по логах бреше
    # (спіймано 24 сер — крок перевірки нарахував 1 попередження на
    # заголовку секції самотестів).
    print("[5] Рівень 1: quote-поля потрапляють у рядки")
    ch5, c5 = build_chain(entries, now3)
    _exp5 = sorted(ch5.keys())[0]
    _K5 = sorted(ch5[_exp5].keys())[0]
    _q5 = ch5[_exp5][_K5][sorted(ch5[_exp5][_K5].keys())[0]]
    tc.check("l1: name у quote", isinstance(_q5.get("name"), str),
             str(_q5.get("name")))
    tc.check("l1: mark/oi/vol/mark_iv не None",
             _q5.get("mark") is not None and _q5.get("oi") is not None
             and _q5.get("vol24h") is not None
             and _q5.get("mark_iv") is not None, str(_q5))
    tc.check("l1: діагностичні лічильники присутні і нульові",
             c5["q_crossed"] == 0 and c5["q_mark_outside"] == 0
             and c5["q_no_mark"] == 0, str(c5))
    if len(sl) == 1:
        _r5 = sl[0]["rows"][0]
        tc.check("l1: рядок має bid/ask/spread_rel",
                 _r5["bid"] > 0 and _r5["ask"] > _r5["bid"]
                 and _r5["spread_rel"] is not None, str(_r5.get("spread_rel")))
        tc.check("l1: spread_rel ~ 2% (синтетика)",
                 abs(_r5["spread_rel"] - 0.02) < 1e-9,
                 str(_r5["spread_rel"]))
        tc.check("l1: iv_spread > 0", _r5["iv_spread"] is not None
                 and _r5["iv_spread"] > 0, str(_r5["iv_spread"]))
        tc.check("l1: mark_iv_deribit НЕ змішано з нашим iv_mid",
                 "mark_iv_deribit" in _r5 and "iv_mid" in _r5
                 and _r5["mark_iv_deribit"] != _r5["iv_mid"])
        tc.check("l1: depth_status без проходу = not_requested",
                 _r5["depth_status"] == "not_requested"
                 and _r5["bid_sz"] is None, str(_r5["depth_status"]))
    tc.check("l1: schema у результаті", res.get("schema") == SCHEMA_VERSION,
             str(res.get("schema")))
    tc.check("l1: depth off -> mode off",
             res["depth_counters"]["mode"] == "off",
             str(res["depth_counters"]))

    print("[5a] Діагностика книги: crossed / mark_outside / no_mark")
    diag_entries = [
        {"instrument_name": "BTC-24AUG26-100000-C",
         "bid_price": 0.02, "ask_price": 0.019, "mark_price": 0.0195},
        {"instrument_name": "BTC-24AUG26-90000-C",
         "bid_price": 0.02, "ask_price": 0.03, "mark_price": 0.05},
        {"instrument_name": "BTC-24AUG26-80000-C",
         "bid_price": 0.02, "ask_price": 0.03},
    ]
    _, cd = build_chain(diag_entries, now3)
    tc.check("diag: crossed=1", cd["q_crossed"] == 1, str(cd))
    tc.check("diag: mark_outside=1", cd["q_mark_outside"] == 1, str(cd))
    tc.check("diag: no_mark=1", cd["q_no_mark"] == 1, str(cd))
    tc.check("diag: жодного дропу через діагностику", cd["kept"] == 3,
             str(cd))

    print("[6] Рівень 2: ін'єкція depth_fn, повний успіх")
    res6 = process_currency("BTC", entries, idx, now3,
                            depth_fn=_fake_depth_fn)
    d6 = res6["depth_counters"]
    rows6 = [row for r in res6["slices"] for row in (r.get("rows") or [])
             if row.get("status") == "OK"]
    tc.check("l2: mode ticker", d6["mode"] == "ticker", str(d6["mode"]))
    tc.check("l2: заповнено всі рядки",
             d6["filled"] == d6["rows"] == len(rows6) and d6["rows"] > 0,
             str(d6))
    tc.check("l2: bid_sz/ask_sz у рядку",
             all(r["bid_sz"] == 10.0 and r["ask_sz"] == 5.0
                 and r["depth_status"] == "OK" for r in rows6))
    tc.check("l2: no_data=0 при повному успіху", d6["no_data"] == 0, str(d6))
    tc.check("l2: px_moved=0 коли ціни не рухались", d6["px_moved"] == 0,
             str(d6))
    tc.check("l2: candidates == rows", d6["candidates"] == d6["rows"],
             str(d6))

    print("[7] Рівень 2: бюджет вичерпано на половині")
    res7 = process_currency("BTC", entries, idx, now3,
                            depth_fn=_fake_depth_fn_partial)
    d7 = res7["depth_counters"]
    rows7 = [row for r in res7["slices"] for row in (r.get("rows") or [])
             if row.get("status") == "OK"]
    tc.check("budget: budget_hit=1", d7["budget_hit"] == 1, str(d7))
    tc.check("budget: filled + no_data == rows",
             d7["filled"] + d7["no_data"] == d7["rows"] and d7["no_data"] > 0,
             str(d7))
    tc.check("budget: недоотримані рядки помічені no_data",
             sum(1 for r in rows7 if r["depth_status"] == "no_data")
             == d7["no_data"], str(d7))
    tc.check("budget: bid_sz=0 рахується як one_sided",
             d7["one_sided_sz"] == d7["filled"], str(d7))

    print("[8] Рівень 2: повний провал мережі не валить знімок")
    res8 = process_currency("BTC", entries, idx, now3,
                            depth_fn=_fake_depth_fn_empty)
    d8 = res8["depth_counters"]
    ok8 = [r for r in res8["slices"] if r["status"] == "OK"]
    tc.check("fail: зріз усе одно OK", len(ok8) == 1, str(len(ok8)))
    tc.check("fail: усі рядки no_data", d8["no_data"] == d8["rows"]
             and d8["filled"] == 0, str(d8))
    tc.check("fail: failed порахований", d8["failed"] == d8["requested"]
             and d8["failed"] > 0, str(d8))
    tc.check("fail: ATM IV не постраждала",
             abs(ok8[0]["atm_dns_pa_iv"] - sl[0]["atm_dns_pa_iv"]) < 1e-12,
             "IV змінилась від проходу глибини — цього бути не може")

    print("[8a] px_moved ловить рух ціни між проходами")
    def _fake_depth_moved(names):
        cnt = {"requested": len(names), "ok": len(names), "failed": 0,
               "skipped": 0, "budget_hit": 0, "time_hit": 0,
               "elapsed_s": 0.0}
        return ({nm: {"bid_sz": 1.0, "ask_sz": 1.0, "t_bid": 1.0e9,
                      "t_ask": 1.0e9, "ts_ms": 1} for nm in names}, cnt)
    res8a = process_currency("BTC", entries, idx, now3,
                             depth_fn=_fake_depth_moved)
    d8a = res8a["depth_counters"]
    tc.check("px_moved: усі рядки помічені",
             d8a["px_moved"] == d8a["filled"] and d8a["filled"] > 0,
             str(d8a))

    print("[8b] make_ticker_depth_fn: стелі спрацьовують без мережі")
    fn0 = make_ticker_depth_fn(budget=0, max_s=99.0)
    m0, c0 = fn0(["A", "B", "C"])
    tc.check("stop: budget=0 -> жодного запиту",
             c0["requested"] == 0 and c0["budget_hit"] == 1
             and c0["skipped"] == 3 and m0 == {}, str(c0))
    fn1 = make_ticker_depth_fn(budget=10, max_s=-1.0)
    m1, c1 = fn1(["A", "B"])
    tc.check("stop: час вичерпано -> time_hit",
             c1["time_hit"] == 1 and c1["requested"] == 0
             and c1["skipped"] == 2 and m1 == {}, str(c1))
    m2, c2 = make_ticker_depth_fn()([])
    tc.check("stop: порожній список безпечний",
             c2["requested"] == 0 and m2 == {}, str(c2))

    print("[8c] _f: null/NaN/сміття -> None, без винятку")
    tc.check("_f: None", _f(None) is None)
    tc.check("_f: рядок-сміття", _f("n/a") is None)
    tc.check("_f: NaN", _f(float("nan")) is None)
    tc.check("_f: число", _f("2.5") == 2.5)

    print("[9] Перевірка змінних оточення")
    # quiet=True: самотест не сміє смітити в живий лог.
    import re as _envre
    _env = {"VOLEDGE_CURRENCIES": "BTC", "VOLEDGE_CURRENCY": "BTC"}
    _unk = warn_unknown_env(KNOWN_ENV, environ=_env, quiet=True)
    tc.check("env_warn_flags_typo", _unk == ["VOLEDGE_CURRENCY"], str(_unk))
    tc.check("env_warn_ignores_known", "VOLEDGE_CURRENCIES" not in _unk)
    _src = ""
    try:
        with open(os.path.abspath(__file__), "r",
                  encoding="utf-8") as _envfh:
            _src = _envfh.read()
    except OSError:
        _src = ""
    _read = set(_envre.findall(
        r'os\.environ\.get\(\s*"(VOLEDGE_[A-Z0-9_]+)"', _src))
    tc.check("known_env_covers_every_read",
             bool(_src) and _read and _read <= set(KNOWN_ENV),
             f"не оголошені: {sorted(_read - set(KNOWN_ENV))}")
    tc.check("known_env_has_no_dead_names",
             bool(_src) and set(KNOWN_ENV) <= _read | {"VOLEDGE_OFFLINE"},
             f"зайві: {sorted(set(KNOWN_ENV) - _read - {'VOLEDGE_OFFLINE'})}")
    return tc


# ----------------------------------------------------------------------------
# Live-секція
# ----------------------------------------------------------------------------

def run_live(currencies: list, depth_fn=None) -> list:
    now = datetime.now(timezone.utc)
    all_results = []
    for cur in currencies:
        print(f"\n[LIVE] {cur}: тягну index та book summary...")
        idx = fetch_index_price(cur)
        entries = fetch_book_summary(cur)
        print(f"[LIVE] {cur}: {len(entries)} інструментів у відповіді")
        all_results.append(process_currency(cur, entries, idx, now,
                                            depth_fn=depth_fn))
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(ARTIFACTS_DIR, f"chain_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=1)
    print(f"\n[ARTIFACT] Збережено: {path}")
    return all_results


def main() -> int:
    print("=" * 72)
    print("Vol-Edge :: deribit_chain")
    print("=" * 72)

    # Environment flags — явна обробка.
    # ПЕРЕД розбором значень: нерозпізнана змінна = тихий дефолт.
    warn_unknown_env(KNOWN_ENV, label="deribit_chain")
    cur_raw = os.environ.get("VOLEDGE_CURRENCIES", "BTC,ETH")
    offline_raw = os.environ.get("VOLEDGE_OFFLINE", "0")
    depth_raw = os.environ.get("VOLEDGE_CHAIN_DEPTH", "1")
    budget_raw = os.environ.get("VOLEDGE_CHAIN_DEPTH_BUDGET",
                                str(DEPTH_BUDGET_DEFAULT))
    maxs_raw = os.environ.get("VOLEDGE_CHAIN_DEPTH_MAX_S",
                              str(DEPTH_MAX_S_DEFAULT))
    sleep_raw = os.environ.get("VOLEDGE_CHAIN_DEPTH_SLEEP_MS",
                               str(DEPTH_SLEEP_MS_DEFAULT))
    currencies = [c.strip().upper() for c in cur_raw.split(",") if c.strip()]
    offline = offline_raw.strip() == "1"
    depth_on = depth_raw.strip() == "1"
    try:
        depth_budget = int(float(budget_raw))
        depth_max_s = float(maxs_raw)
        depth_sleep_ms = int(float(sleep_raw))
    except ValueError as e:
        print(f"[ENV] ПОМИЛКА розбору стель глибини: {e}. "
              f"Знімок НЕ запускаю: мовчазний дефолт на бюджеті означав "
              f"би прохід іншої тривалості, ніж просили.")
        return 3
    if depth_budget < 1 or depth_max_s <= 0.0 or depth_sleep_ms < 0:
        print(f"[ENV] ПОМИЛКА: budget={depth_budget} max_s={depth_max_s} "
              f"sleep_ms={depth_sleep_ms} — недопустимі значення.")
        return 3
    print(f"[ENV] VOLEDGE_CURRENCIES={cur_raw!r} -> {currencies}")
    print(f"[ENV] VOLEDGE_OFFLINE={offline_raw!r} -> offline={offline}")
    print(f"[ENV] VOLEDGE_CHAIN_DEPTH={depth_raw!r} -> depth={depth_on} "
          f"(schema={SCHEMA_VERSION})")
    print(f"[ENV] стелі глибини НА ВАЛЮТУ: budget={depth_budget} "
          f"max_s={depth_max_s} sleep_ms={depth_sleep_ms}")
    for c in currencies:
        if c not in ("BTC", "ETH"):
            print(f"[ENV] УВАГА: '{c}' не з (BTC, ETH) — залишаю, але "
                  f"публічний API може його не знати")

    print("\n--- ОФЛАЙН-САМОТЕСТИ " + "-" * 50)
    tc = run_self_tests()
    print("-" * 72)
    print(f"[SUMMARY] самотести: {tc.run}, пройдено: {tc.passed}, "
          f"провалено: {len(tc.failures)}")
    if tc.failures:
        for f in tc.failures:
            print(f"  - {f}")
        print("[SUMMARY] Live-секцію пропущено через провалені самотести.")
        return 1

    if offline:
        print("\n[SUMMARY] VOLEDGE_OFFLINE=1 -> live-секцію пропущено явно.")
        return 0

    print("\n--- LIVE-ЗНІМОК DERIBIT " + "-" * 47)
    depth_fn = None
    if depth_on:
        depth_fn = make_ticker_depth_fn(budget=depth_budget,
                                        max_s=depth_max_s,
                                        sleep_ms=depth_sleep_ms)
    try:
        run_live(currencies, depth_fn=depth_fn)
    except Exception as e:
        print(f"\n[ERROR] Live-знімок провалився: {e}")
        print("[HINT] Перевірте мережу/доступність deribit.com; "
              "самотести при цьому пройдені, код цілий.")
        return 2
    print("\n[SUMMARY] Готово. Артефакт у artifacts/chain/.")
    return 0


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    except Exception:
        print("\n[ERROR] Неперехоплений виняток:")
        traceback.print_exc()
    finally:
        try:
            input("\nНатисніть Enter для виходу...")
        except EOFError:
            pass
    sys.exit(exit_code)
