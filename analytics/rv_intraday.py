# -*- coding: utf-8 -*-
"""
[EN] Daily realized volatility from 5-minute returns on UTC days, with
subsampling on 5/15/30-minute grids and a HAR-RV forecast evaluated
walk-forward out of sample.

--- Ukrainian original below ---
Vol-Edge :: analytics/rv_intraday.py

5-хвилинна realized variance + HAR-RV прогноз.

Мотивація: розбіжність cc 27% проти Parkinson 33% на BTC RV30 — денна
дискретизація груба. Тут:
  1) 5-хв свічки PERPETUAL (той самий публічний endpoint, resolution=5),
     чанками по 7 діб, дедуплікація по ticks.
  2) Добова RV = сума квадратів 5-хв лог-ретернів у межах UTC-доби;
     неповні доби (< порогу барів) відкидаються з лічильником — явно.
  3) RV30_5m проти RV30_cc та RV30_pk, порахованих З ТИХ САМИХ 5-хв даних
     (денна агрегація: close=останній, high=max, low=min) — порівняння
     один-в-один на ідентичному вікні.
  4) HAR-RV: пряма регресія середньої добової варіації наступних 30 діб
     на (var_d, var_w(5д), var_m(22д)) — чесний прогноз майбутньої RV;
     діагностична 1-денна HAR з R^2. Проти HAR-прогнозу, а не trailing,
     коректно міряти VRP: IV30/RV_HAR30 і IV30^2 - E[var] (variance units).
  5) IV30 — з останнього артефакту chain через interp_tenor
     (analytics/surface_report.py).
  6) JSON-артефакт у artifacts/rv/rv_YYYYMMDD_HHMMSS.json.

Усі об'єми — у частках; ануалізація sqrt(365). Жодного silent fail-open:
кожен дроп/пропуск — з лічильником або [SKIP]-рядком з причиною.

Env-флаги:
  VOLEDGE_OFFLINE=1       -> лише самотести, без мережі.
  VOLEDGE_CURRENCIES      -> "BTC,ETH" (default).
  VOLEDGE_RV5_DAYS        -> вікно 5-хв історії, default 140, мінімум 90
                             (HAR-30d: вибірка = N-22-30+1; 90 -> 39 точок).
  VOLEDGE_RV5_MIN_BARS    -> мінімум 5-хв ретернів на повну добу,
                             default 276 (з 288; ~4% дірок допустимо).

Запуск подвійним кліком; завжди чекає input() у кінці.
"""

import glob
import gzip
import json
import math
import os
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone

# --- шляхи проєкту: analytics/ -> корінь ------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_ROOT, _THIS_DIR, os.path.join(_ROOT, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from surface_report import interp_tenor
except ImportError:
    from analytics.surface_report import interp_tenor
try:
    from env_flags import merge_known, warn_unknown_env
except ImportError:
    from core.env_flags import merge_known, warn_unknown_env

# Змінні, які читає САМЕ ЦЯ точка входу (усі — у main()). Імпортований
# surface_report дає лише чисту interp_tenor і оточення під час прогону
# rv_intraday не читає, тож його змінні сюди НЕ входять.
KNOWN_ENV = merge_known((
    "VOLEDGE_CURRENCIES",
    "VOLEDGE_RV5_DAYS",
    "VOLEDGE_RV5_MIN_BARS",
))

API_BASE = "https://www.deribit.com/api/v2/public"
HTTP_TIMEOUT_S = 15
HTTP_RETRIES = 3
HTTP_RETRY_SLEEP_S = 2.0

ANNUALIZE = 365.0
PERP_NAME = {"BTC": "BTC-PERPETUAL", "ETH": "ETH-PERPETUAL"}
BARS_PER_DAY = 288          # 24*60/5
CHUNK_DAYS = 7              # запит чанками по 7 діб
HAR_W_D = 5                 # тижнева компонента
HAR_M_D = 22                # місячна компонента
HAR_HORIZON_D = 30          # горизонт прогнозу
MIN_DIRECT_SAMPLES = 20     # мінімум точок для прямої HAR-30d регресії

CHAIN_DIR = os.path.join(_ROOT, "artifacts", "chain")
RV_DIR = os.path.join(_ROOT, "artifacts", "rv")


# ----------------------------------------------------------------------------
# HTTP (та сама конвенція, що в surface_report)
# ----------------------------------------------------------------------------

def http_get_json(url: str) -> dict:
    last_err = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Vol-Edge/1.0"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if "result" not in payload:
                raise RuntimeError(f"Відповідь без 'result': {payload}")
            return payload["result"]
        except Exception as e:
            last_err = e
            print(f"  [HTTP] спроба {attempt}/{HTTP_RETRIES}: {e}")
            if attempt < HTTP_RETRIES:
                time.sleep(HTTP_RETRY_SLEEP_S)
    raise RuntimeError(f"HTTP остаточно провалився: {url} :: {last_err}")


def fetch_5m_candles(currency: str, days: int) -> dict:
    """
    5-хв свічки PERPETUAL за останні `days` діб чанками по CHUNK_DAYS.
    Повертає {"ticks": [ms], "close": [...], "high": [...], "low": [...],
              "counters": {...}} — відсортовано, дедупліковано по ticks.
    """
    inst = PERP_NAME[currency]
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int((days + 1) * 24 * 3600 * 1000)
    chunk_ms = CHUNK_DAYS * 24 * 3600 * 1000
    by_tick = {}
    c = {"chunks": 0, "bars_raw": 0, "bars_dup": 0, "bars_bad": 0}
    lo = start_ms
    while lo < end_ms:
        hi = min(lo + chunk_ms, end_ms)
        r = http_get_json(
            f"{API_BASE}/get_tradingview_chart_data?instrument_name={inst}"
            f"&start_timestamp={lo}&end_timestamp={hi}&resolution=5")
        c["chunks"] += 1
        if r.get("status") != "ok":
            raise RuntimeError(f"candles5m {inst}: status={r.get('status')} "
                               f"на чанку [{lo},{hi}]")
        ticks = r.get("ticks") or []
        closes = r.get("close") or []
        highs = r.get("high") or []
        lows = r.get("low") or []
        if not (len(ticks) == len(closes) == len(highs) == len(lows)):
            raise RuntimeError(
                f"candles5m {inst}: розбіжні довжини масивів у чанку "
                f"[{lo},{hi}]: {len(ticks)}/{len(closes)}/"
                f"{len(highs)}/{len(lows)}")
        for t, cl, h, l in zip(ticks, closes, highs, lows):
            c["bars_raw"] += 1
            t = int(t)
            cl, h, l = float(cl), float(h), float(l)
            if not (cl > 0 and h > 0 and l > 0 and h >= l
                    and math.isfinite(cl)):
                c["bars_bad"] += 1
                continue
            if t in by_tick:
                c["bars_dup"] += 1
            by_tick[t] = (cl, h, l)
        lo = hi
    ticks_sorted = sorted(by_tick)
    c["bars_kept"] = len(ticks_sorted)
    return {"ticks": ticks_sorted,
            "close": [by_tick[t][0] for t in ticks_sorted],
            "high": [by_tick[t][1] for t in ticks_sorted],
            "low": [by_tick[t][2] for t in ticks_sorted],
            "counters": c}


# ----------------------------------------------------------------------------
# Добова агрегація 5-хв даних
# ----------------------------------------------------------------------------

def _utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0,
                                  tz=timezone.utc).strftime("%Y-%m-%d")


def daily_from_5m(bars: dict, min_bars: int) -> dict:
    """
    Групує 5-хв дані по UTC-добах (ретерн належить добі бару-закриття).
    Повна доба: >= min_bars ретернів. Остання (сьогоднішня, незавершена)
    доба відкидається завжди — з лічильником.

    Повертає:
      days: [YYYY-MM-DD] лише повних діб, зростання;
      var_5m: добова варіація (сума квадратів 5-хв лог-ретернів);
      close/high/low: денна агрегація для cc/Parkinson;
      counters: {...}
    """
    ticks, closes = bars["ticks"], bars["close"]
    highs, lows = bars["high"], bars["low"]
    n = len(ticks)
    c = {"days_seen": 0, "days_full": 0, "days_incomplete": 0,
         "day_today_dropped": 0, "rets_used": 0}
    if n < 2:
        raise RuntimeError(f"daily_from_5m: замало барів ({n})")

    acc = {}  # day -> {"var":..,"n_rets":..,"close":..,"high":..,"low":..}
    for i in range(1, n):
        day = _utc_day(ticks[i])
        a = acc.get(day)
        if a is None:
            a = {"var": 0.0, "n_rets": 0, "close": closes[i],
                 "high": highs[i], "low": lows[i]}
            acc[day] = a
        r = math.log(closes[i] / closes[i - 1])
        a["var"] += r * r
        a["n_rets"] += 1
        a["close"] = closes[i]
        a["high"] = max(a["high"], highs[i])
        a["low"] = min(a["low"], lows[i])

    today = _utc_day(int(time.time() * 1000))
    days_sorted = sorted(acc)
    out_days, out_var, out_cl, out_hi, out_lo = [], [], [], [], []
    for day in days_sorted:
        c["days_seen"] += 1
        if day == today:
            c["day_today_dropped"] += 1
            continue
        a = acc[day]
        if a["n_rets"] < min_bars:
            c["days_incomplete"] += 1
            print(f"  [DROP] доба {day}: {a['n_rets']}/{BARS_PER_DAY} "
                  f"ретернів < min_bars={min_bars}")
            continue
        c["days_full"] += 1
        c["rets_used"] += a["n_rets"]
        out_days.append(day)
        out_var.append(a["var"])
        out_cl.append(a["close"])
        out_hi.append(a["high"])
        out_lo.append(a["low"])
    return {"days": out_days, "var_5m": out_var, "close": out_cl,
            "high": out_hi, "low": out_lo, "counters": c}


# ----------------------------------------------------------------------------
# RV-оцінки на спільному вікні
# ----------------------------------------------------------------------------

def rv_from_daily_var(var_daily: list, window: int) -> float:
    """Ануалізована RV з добових варіацій за останні `window` діб."""
    if len(var_daily) < window:
        raise ValueError(f"rv_5m: треба {window} діб, є {len(var_daily)}")
    mean_var = sum(var_daily[-window:]) / window
    return math.sqrt(mean_var * ANNUALIZE)


def rv_cc_from_closes(closes: list, window: int) -> float:
    if len(closes) < window + 1:
        raise ValueError(f"rv_cc: треба {window + 1} цін, є {len(closes)}")
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - window, len(closes))]
    var = sum(r * r for r in rets) / window
    return math.sqrt(var * ANNUALIZE)


def rv_park_from_hl(highs: list, lows: list, window: int) -> float:
    n = len(highs)
    if n != len(lows):
        raise ValueError("rv_park: highs/lows різної довжини")
    if n < window:
        raise ValueError(f"rv_park: треба {window} барів, є {n}")
    coef = 1.0 / (4.0 * math.log(2.0))
    acc = 0.0
    for i in range(n - window, n):
        if not (highs[i] > 0 and lows[i] > 0 and highs[i] >= lows[i]):
            raise ValueError(f"rv_park: бар {i}: H={highs[i]} L={lows[i]}")
        hl = math.log(highs[i] / lows[i])
        acc += coef * hl * hl
    return math.sqrt(acc / window * ANNUALIZE)


# ----------------------------------------------------------------------------
# OLS (Гаус з частковим вибором головного елемента), HAR-RV
# ----------------------------------------------------------------------------

def solve_linear(A: list, b: list) -> list:
    n = len(A)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r_: abs(M[r_][col]))
        if abs(M[piv][col]) < 1e-14:
            raise RuntimeError(f"solve_linear: вироджена матриця (col={col})")
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for j in range(col, n + 1):
            M[col][j] /= pv
        for r_ in range(n):
            if r_ != col and M[r_][col] != 0.0:
                f = M[r_][col]
                for j in range(col, n + 1):
                    M[r_][j] -= f * M[col][j]
    return [M[i][n] for i in range(n)]


def ols(X: list, y: list) -> dict:
    """X: список рядків-фіч (без константи — додається). R^2 in-sample."""
    n = len(X)
    if n != len(y) or n == 0:
        raise ValueError(f"ols: n_X={len(X)} n_y={len(y)}")
    p = len(X[0]) + 1
    rows = [[1.0] + list(x) for x in X]
    A = [[0.0] * p for _ in range(p)]
    b = [0.0] * p
    for r_, yy in zip(rows, y):
        for i in range(p):
            b[i] += r_[i] * yy
            for j in range(p):
                A[i][j] += r_[i] * r_[j]
    for i in range(p):
        A[i][i] += 1e-16
    beta = solve_linear(A, b)
    y_mean = sum(y) / n
    ss_tot = sum((v - y_mean) ** 2 for v in y)
    ss_res = 0.0
    for r_, yy in zip(rows, y):
        pred = sum(bb * xx for bb, xx in zip(beta, r_))
        ss_res += (yy - pred) ** 2
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {"beta": beta, "r2": r2, "n": n}


# ----------------------------------------------------------------------------
# Subsampling-тест мікроструктури: RV30 на 5/15/30-хв сітках
# ----------------------------------------------------------------------------

SUBSAMPLE_STEPS = (1, 3, 6)  # 5, 15, 30 хв


def subsample_bars(bars: dict, step: int) -> dict:
    """Проріджує до кожного step-го 5-хв бару, вирівняно по часу
    (tick кратний step*5хв) — стійко до дірок у стрічці."""
    period = step * 300000
    idx = [i for i, t in enumerate(bars["ticks"]) if t % period == 0]
    return {k: [bars[k][i] for i in idx]
            for k in ("ticks", "close", "high", "low")}


def rv30_by_grid(bars: dict, min_bars_5m: int) -> dict:
    """RV30 по сітках SUBSAMPLE_STEPS з тих самих даних. Явні лічильники;
    сітка без 30 повних діб -> status із причиною, не тихий пропуск."""
    out = {}
    for step in SUBSAMPLE_STEPS:
        mb = max(1, min_bars_5m // step)
        d = daily_from_5m(subsample_bars(bars, step), mb)
        nd = len(d["days"])
        key = f"{step * 5}m"
        if nd < HAR_HORIZON_D:
            out[key] = {"status": f"not_enough_days: {nd}",
                        "counters": d["counters"]}
        else:
            out[key] = {"status": "OK",
                        "rv30": rv_from_daily_var(d["var_5m"],
                                                  HAR_HORIZON_D),
                        "n_days": nd, "counters": d["counters"]}
    return out


def har_features(var_daily: list, t: int) -> list:
    """Фічі на кінець доби t (індекс): var_d, var_w(5), var_m(22)."""
    var_d = var_daily[t]
    var_w = sum(var_daily[t - HAR_W_D + 1:t + 1]) / HAR_W_D
    var_m = sum(var_daily[t - HAR_M_D + 1:t + 1]) / HAR_M_D
    return [var_d, var_w, var_m]


def har_fit_and_forecast(var_daily: list) -> dict:
    """
    1) Діагностика: HAR-1d, y = var(t+1).
    2) Прогноз: пряма HAR-30d, y = mean(var(t+1..t+30)).
    Прогноз з останньої доби; кламп знизу нулем — явний, з прапорцем.
    """
    n = len(var_daily)
    out = {"n_days": n}

    # --- 1d діагностика ---
    X1, y1 = [], []
    for t in range(HAR_M_D - 1, n - 1):
        X1.append(har_features(var_daily, t))
        y1.append(var_daily[t + 1])
    if len(y1) < MIN_DIRECT_SAMPLES:
        raise RuntimeError(
            f"HAR-1d: вибірка {len(y1)} < {MIN_DIRECT_SAMPLES} "
            f"(n_days={n}) — замало історії")
    out["har1d"] = ols(X1, y1)
    out["har1d"]["n_samples"] = len(y1)

    # --- 30d прямий прогноз ---
    Xh, yh = [], []
    for t in range(HAR_M_D - 1, n - HAR_HORIZON_D):
        Xh.append(har_features(var_daily, t))
        yh.append(sum(var_daily[t + 1:t + 1 + HAR_HORIZON_D])
                  / HAR_HORIZON_D)
    if len(yh) < MIN_DIRECT_SAMPLES:
        raise RuntimeError(
            f"HAR-30d: вибірка {len(yh)} < {MIN_DIRECT_SAMPLES} "
            f"(n_days={n}; потрібно >= "
            f"{HAR_M_D + HAR_HORIZON_D + MIN_DIRECT_SAMPLES - 1} діб) "
            f"— замало історії")
    fit = ols(Xh, yh)
    fit["n_samples"] = len(yh)
    feats_last = har_features(var_daily, n - 1)
    pred_var = fit["beta"][0] + sum(
        b * x for b, x in zip(fit["beta"][1:], feats_last))
    clamped = False
    if pred_var <= 0.0:
        clamped = True
        pred_var = min(v for v in var_daily if v > 0.0)
    fit["forecast_daily_var"] = pred_var
    fit["forecast_clamped"] = clamped
    fit["rv_har30"] = math.sqrt(pred_var * ANNUALIZE)
    out["har30d"] = fit
    return out


def har_walkforward_oos(var_daily: list, min_train: int = 40) -> dict:
    """
    Walk-forward out-of-sample для HAR-30d проти наївних бенчмарків:
      naive_trail30 = середня варіація останніх 30 діб на момент t;
      naive_rv7     = середня варіація останніх 7 діб.
    Розширюване вікно: рефіт на кожному кроці на даних до t включно.
    MSE у variance-просторі. ratio<1 -> HAR б'є бенчмарк.
    """
    n = len(var_daily)
    ts = list(range(HAR_M_D + 29, n - HAR_HORIZON_D))  # trailing30 доступний
    if len(ts) < min_train + 10:
        raise RuntimeError(
            f"har_oos: точок {len(ts)} < min_train+10={min_train + 10} "
            f"— замало історії")
    se = {"har": 0.0, "trail30": 0.0, "rv7": 0.0}
    n_oos = 0
    for t in ts[min_train:]:
        Xh, yh = [], []
        for u in range(HAR_M_D - 1, t - HAR_HORIZON_D + 1):
            Xh.append(har_features(var_daily, u))
            yh.append(sum(var_daily[u + 1:u + 1 + HAR_HORIZON_D])
                      / HAR_HORIZON_D)
        beta = ols(Xh, yh)["beta"]
        fx = har_features(var_daily, t)
        pred = beta[0] + sum(b * x for b, x in zip(beta[1:], fx))
        y_true = sum(var_daily[t + 1:t + 1 + HAR_HORIZON_D]) / HAR_HORIZON_D
        se["har"] += (pred - y_true) ** 2
        se["trail30"] += (sum(var_daily[t - 29:t + 1]) / 30.0 - y_true) ** 2
        se["rv7"] += (sum(var_daily[t - 6:t + 1]) / 7.0 - y_true) ** 2
        n_oos += 1
    mse = {k: v / n_oos for k, v in se.items()}
    return {"n_oos": n_oos, "mse": mse,
            "ratio_vs_trail30": mse["har"] / mse["trail30"]
            if mse["trail30"] > 0 else float("inf"),
            "ratio_vs_rv7": mse["har"] / mse["rv7"]
            if mse["rv7"] > 0 else float("inf")}


# ----------------------------------------------------------------------------
# IV30 з останнього chain-артефакту
# ----------------------------------------------------------------------------

def find_latest_chain(chain_dir: str):
    files = []
    for pat in ("chain_*.json", "chain_*.json.gz"):
        files.extend(glob.glob(os.path.join(chain_dir, pat)))
    if not files:
        return None

    def keyf(path):
        base = os.path.basename(path)
        return base[:-3] if base.endswith(".gz") else base

    files.sort(key=keyf)
    return files[-1]


def load_chain(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def iv30_from_chain(chain_data, currency: str):
    """(iv30|None, reason)."""
    for blk in chain_data:
        if blk.get("currency") == currency:
            t = interp_tenor(blk.get("slices", []), 30.0)
            if t.get("status") == "OK":
                return t["atm_iv"], "OK"
            return None, f"interp_tenor: {t}"
    return None, f"валюта {currency} відсутня в артефакті"


# ----------------------------------------------------------------------------
# Самотести (детерміновано, без random і без мережі)
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
        print(f"[TEST] {'OK ' if ok else 'FAIL'} {name} {detail}")


def _synthetic_5m(days: int, sigma_ann: float, start_day="2026-01-01"):
    """Детермінований 5-хв ряд зі сталим |ретерном| = sigma_5m (знак ±)."""
    t0 = int(datetime.strptime(start_day, "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp() * 1000)
    sigma_5m = sigma_ann / math.sqrt(ANNUALIZE * BARS_PER_DAY)
    ticks, closes, highs, lows = [], [], [], []
    price = 100.0
    n = days * BARS_PER_DAY + 1
    for i in range(n):
        ticks.append(t0 + i * 300000)
        closes.append(price)
        highs.append(price * 1.0001)
        lows.append(price * 0.9999)
        r = sigma_5m if (math.sin(i * 12.9898) * 43758.5453) % 1.0 > 0.5 \
            else -sigma_5m
        price *= math.exp(r)
    return {"ticks": ticks, "close": closes, "high": highs, "low": lows,
            "counters": {}}


def run_self_tests() -> TestCounters:
    tc = TestCounters()

    # 1. solve_linear / ols
    x = solve_linear([[2.0, 1.0], [1.0, 3.0]], [5.0, 10.0])
    tc.check("solve_linear", abs(x[0] - 1.0) < 1e-10
             and abs(x[1] - 3.0) < 1e-10, str(x))
    X = [[float(i)] for i in range(10)]
    y = [3.0 + 2.0 * i for i in range(10)]
    f = ols(X, y)
    tc.check("ols_exact", abs(f["beta"][0] - 3.0) < 1e-9
             and abs(f["beta"][1] - 2.0) < 1e-9
             and abs(f["r2"] - 1.0) < 1e-12,
             f"beta={f['beta']} r2={f['r2']}")

    # 2. добова агрегація: RV_5m відтворює закладену sigma
    sig = 0.40
    bars = _synthetic_5m(days=5, sigma_ann=sig)
    d = daily_from_5m(bars, min_bars=276)
    tc.check("daily_days", len(d["days"]) >= 4,
             f"{len(d['days'])} діб, counters={d['counters']}")
    rv = rv_from_daily_var(d["var_5m"], min(4, len(d["days"])))
    tc.check("rv5m_matches_sigma", abs(rv - sig) < 0.01,
             f"rv={rv:.4f} vs sigma={sig}")

    # 3. неповна доба відкидається з лічильником
    bars2 = _synthetic_5m(days=3, sigma_ann=sig)
    cut = []
    day1 = _utc_day(bars2["ticks"][0])
    for i, t in enumerate(bars2["ticks"]):
        if _utc_day(t) == day1 and i % 3 == 0:
            continue  # дірки в першій добі
        cut.append(i)
    bars2c = {k: [bars2[k][i] for i in cut]
              for k in ("ticks", "close", "high", "low")}
    d2 = daily_from_5m(bars2c, min_bars=276)
    tc.check("incomplete_day_dropped",
             d2["counters"]["days_incomplete"] >= 1
             and day1 not in d2["days"], str(d2["counters"]))

    # 4. HAR на синтетиці: два режими волатильності, детерміновано
    var_daily = []
    for i in range(160):
        base = 0.0004 if (i // 40) % 2 == 0 else 0.0016
        wob = 1.0 + 0.2 * math.sin(i * 0.7)
        var_daily.append(base * wob)
    h = har_fit_and_forecast(var_daily)
    tc.check("har_samples",
             h["har30d"]["n_samples"] == 160 - HAR_M_D - HAR_HORIZON_D + 1,
             str(h["har30d"]["n_samples"]))
    tc.check("har_r2_finite", math.isfinite(h["har1d"]["r2"])
             and math.isfinite(h["har30d"]["r2"]),
             f"r2_1d={h['har1d']['r2']:.3f} r2_30d={h['har30d']['r2']:.3f}")
    rv_h = h["har30d"]["rv_har30"]
    tc.check("har_forecast_sane", 0.05 < rv_h < 2.0
             and not h["har30d"]["forecast_clamped"], f"rv_har30={rv_h:.4f}")

    # 5. замало історії -> явна відмова
    try:
        har_fit_and_forecast(var_daily[:60])
        tc.check("har_short_refuses", False, "не кинув виняток")
    except RuntimeError as e:
        tc.check("har_short_refuses", "замало історії" in str(e), str(e))

    # 6. subsampling: на сталій sigma всі сітки дають ту саму RV
    bars_s = _synthetic_5m(days=35, sigma_ann=sig)
    g = rv30_by_grid(bars_s, min_bars_5m=276)
    ok_g = all(g[k]["status"] == "OK"
               and abs(g[k]["rv30"] - sig) < 0.03 for k in g)
    tc.check("subsample_flat_sigma", ok_g,
             str({k: g[k].get("rv30") for k in g}))
    sub = subsample_bars(bars_s, 6)
    tc.check("subsample_alignment",
             all(t % 1800000 == 0 for t in sub["ticks"])
             and len(sub["ticks"]) >= 35 * 48, str(len(sub["ticks"])))

    # 7. HAR walk-forward OOS: рахується, MSE скінченні; замало -> відмова
    oos = har_walkforward_oos(var_daily)
    tc.check("oos_runs", oos["n_oos"] > 0
             and all(math.isfinite(v) for v in oos["mse"].values()),
             f"n={oos['n_oos']} ratios={oos['ratio_vs_trail30']:.3f}/"
             f"{oos['ratio_vs_rv7']:.3f}")
    try:
        har_walkforward_oos(var_daily[:80])
        tc.check("oos_short_refuses", False, "не кинув виняток")
    except RuntimeError as e:
        tc.check("oos_short_refuses", "замало історії" in str(e), str(e))

    # 8. cc/park на денній агрегації працюють
    rv_cc = rv_cc_from_closes(d["close"] + [d["close"][-1] * 1.001],
                              min(4, len(d["close"])))
    tc.check("cc_runs", math.isfinite(rv_cc) and rv_cc >= 0.0,
             f"{rv_cc:.4f}")
    rv_pk = rv_park_from_hl(d["high"], d["low"], min(4, len(d["high"])))
    tc.check("park_runs", math.isfinite(rv_pk) and rv_pk >= 0.0,
             f"{rv_pk:.4f}")

    # --- ENV-WARN (quiet=True: самотест не сміє смітити в живий лог) ---
    import re as _envre
    _env = {"VOLEDGE_RV5_DAYS": "140", "VOLEDGE_RV5_DAY": "140"}
    _unk = warn_unknown_env(KNOWN_ENV, environ=_env, quiet=True)
    tc.check("env_warn_flags_typo", _unk == ["VOLEDGE_RV5_DAY"], str(_unk))
    tc.check("env_warn_ignores_known", "VOLEDGE_RV5_DAYS" not in _unk)
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

    print(f"[TEST] passed {tc.passed}/{tc.run}")
    for f_ in tc.failures:
        print(f"[TEST] FAILURE detail: {f_}")
    return tc


# ----------------------------------------------------------------------------
# Live
# ----------------------------------------------------------------------------

def run_live(currencies, rv5_days, min_bars) -> dict:
    chain_path = find_latest_chain(CHAIN_DIR)
    chain_data = None
    if chain_path is None:
        print(f"[CHAIN] SKIP: нуль артефактів у {CHAIN_DIR} — "
              f"IV30/VRP не буде")
    else:
        print(f"[CHAIN] {chain_path}")
        chain_data = load_chain(chain_path)

    result = {"generated_utc": datetime.now(timezone.utc).isoformat(),
              "rv5_days": rv5_days, "min_bars": min_bars,
              "chain_artifact": chain_path, "currencies": {}}

    for cur in currencies:
        print(f"\n--- {cur} " + "-" * 60)
        bars = fetch_5m_candles(cur, rv5_days)
        print(f"[FETCH] {cur}: {json.dumps(bars['counters'])}")
        d = daily_from_5m(bars, min_bars)
        print(f"[DAILY] {cur}: {json.dumps(d['counters'])}")
        nd = len(d["days"])
        if nd < HAR_HORIZON_D:
            print(f"[RV] {cur}: SKIP (повних діб {nd} < {HAR_HORIZON_D})")
            result["currencies"][cur] = {"status": "not_enough_days",
                                         "n_days": nd}
            continue

        rv30_5m = rv_from_daily_var(d["var_5m"], HAR_HORIZON_D)
        rv7_5m = rv_from_daily_var(d["var_5m"], 7)
        rv30_cc = rv_cc_from_closes(d["close"], HAR_HORIZON_D) \
            if len(d["close"]) >= HAR_HORIZON_D + 1 else None
        rv30_pk = rv_park_from_hl(d["high"], d["low"], HAR_HORIZON_D)

        grids = rv30_by_grid(bars, min_bars)
        gparts = []
        for gk in sorted(grids, key=lambda s: int(s[:-1])):
            gv = grids[gk]
            gparts.append(f"{gk}={gv['rv30']*100:.2f}%"
                          if gv["status"] == "OK"
                          else f"{gk}=SKIP({gv['status']})")
        print(f"[SUBSAMPLE] {cur} RV30 :: " + " ".join(gparts))

        oos = None
        oos_err = ""
        try:
            oos = har_walkforward_oos(d["var_5m"])
            print(f"[HAR-OOS] {cur} :: n={oos['n_oos']} "
                  f"MSE har/trail30={oos['ratio_vs_trail30']:.3f} "
                  f"har/rv7={oos['ratio_vs_rv7']:.3f} "
                  f"({'HAR кращий' if oos['ratio_vs_trail30'] < 1.0 and oos['ratio_vs_rv7'] < 1.0 else 'HAR НЕ б''є наївні'})")
        except RuntimeError as e:
            oos_err = str(e)
            print(f"[HAR-OOS] {cur}: SKIP ({e})")

        har = None
        har_err = ""
        try:
            har = har_fit_and_forecast(d["var_5m"])
        except RuntimeError as e:
            har_err = str(e)
            print(f"[HAR] {cur}: SKIP ({e})")

        iv30 = None
        iv_reason = "chain-артефакт відсутній"
        if chain_data is not None:
            iv30, iv_reason = iv30_from_chain(chain_data, cur)
            if iv30 is None:
                print(f"[IV30] {cur}: SKIP ({iv_reason})")

        cc_s = f"{rv30_cc*100:6.2f}%" if rv30_cc is not None else "   n/a"
        print(f"[OVERALL] {cur} days={nd} :: RV30_5m={rv30_5m*100:6.2f}% "
              f"RV30_cc={cc_s} RV30_pk={rv30_pk*100:6.2f}% "
              f"RV7_5m={rv7_5m*100:6.2f}%")
        blk = {"status": "OK", "n_days": nd,
               "last_day": d["days"][-1],
               "rv30_5m": rv30_5m, "rv7_5m": rv7_5m,
               "rv30_cc": rv30_cc, "rv30_park": rv30_pk,
               "fetch_counters": bars["counters"],
               "daily_counters": d["counters"],
               "rv30_by_grid": grids,
               "har_oos": oos if oos is not None else {"error": oos_err},
               "days": d["days"], "var_5m_daily": d["var_5m"]}
        if har is not None:
            h30 = har["har30d"]
            print(f"[HAR] {cur} :: RV_HAR30={h30['rv_har30']*100:6.2f}% "
                  f"(samples={h30['n_samples']} r2_30d={h30['r2']:.3f} "
                  f"r2_1d={har['har1d']['r2']:.3f}"
                  f"{' CLAMPED' if h30['forecast_clamped'] else ''})")
            blk["har"] = har
        else:
            blk["har"] = None
            blk["har_error"] = har_err

        if iv30 is not None:
            blk["iv30"] = iv30
            vs_trail = iv30 / rv30_5m
            print(f"[VRP] {cur} :: IV30={iv30*100:6.2f}% "
                  f"IV30/RV30_5m(trailing)={vs_trail:.3f}", end="")
            if har is not None:
                rv_h = har["har30d"]["rv_har30"]
                vrp_var = iv30 * iv30 - har["har30d"]["forecast_daily_var"] \
                    * ANNUALIZE
                print(f" IV30/RV_HAR30={iv30/rv_h:.3f} "
                      f"VRP_var={vrp_var:+.4f}")
            else:
                print()
        else:
            blk["iv30"] = None
            blk["iv30_reason"] = iv_reason
        result["currencies"][cur] = blk

    os.makedirs(RV_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(RV_DIR, f"rv_{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"\n[OVERALL] артефакт: {out_path} "
          f"({os.path.getsize(out_path)} байт)")
    return result


def main() -> int:
    print("=" * 72)
    print("Vol-Edge :: rv_intraday (5-хв RV + HAR-RV)")
    print("=" * 72)
    # ПЕРЕД розбором значень: нерозпізнана змінна = тихий дефолт.
    warn_unknown_env(KNOWN_ENV, label="rv_intraday")
    offline_raw = os.environ.get("VOLEDGE_OFFLINE", "0")
    cur_raw = os.environ.get("VOLEDGE_CURRENCIES", "BTC,ETH")
    days_raw = os.environ.get("VOLEDGE_RV5_DAYS", "140")
    mb_raw = os.environ.get("VOLEDGE_RV5_MIN_BARS", "276")
    offline = offline_raw.strip() == "1"
    currencies = [c.strip().upper() for c in cur_raw.split(",") if c.strip()]
    try:
        rv5_days = int(days_raw)
        min_bars = int(mb_raw)
    except ValueError:
        raise RuntimeError(f"VOLEDGE_RV5_DAYS={days_raw!r} або "
                           f"VOLEDGE_RV5_MIN_BARS={mb_raw!r} не ціле")
    if rv5_days < 90:
        raise RuntimeError(f"VOLEDGE_RV5_DAYS={rv5_days} < 90 — HAR-30d "
                           f"не матиме вибірки (потрібно >= 90)")
    if not (1 <= min_bars <= BARS_PER_DAY):
        raise RuntimeError(f"VOLEDGE_RV5_MIN_BARS={min_bars} поза "
                           f"[1,{BARS_PER_DAY}]")
    for cur in currencies:
        if cur not in PERP_NAME:
            raise RuntimeError(f"Невідома валюта {cur!r} у "
                               f"VOLEDGE_CURRENCIES")
    print(f"[ENV] VOLEDGE_OFFLINE={offline_raw!r} -> {offline}")
    print(f"[ENV] VOLEDGE_CURRENCIES={cur_raw!r} -> {currencies}")
    print(f"[ENV] VOLEDGE_RV5_DAYS={days_raw!r} -> {rv5_days}")
    print(f"[ENV] VOLEDGE_RV5_MIN_BARS={mb_raw!r} -> {min_bars}")

    print("\n--- ОФЛАЙН-САМОТЕСТИ " + "-" * 50)
    tc = run_self_tests()
    print("-" * 72)
    print(f"[SUMMARY] самотести: {tc.run}, пройдено: {tc.passed}, "
          f"провалено: {len(tc.failures)}")
    if tc.failures:
        for f_ in tc.failures:
            print(f"  - {f_}")
        return 1
    if offline:
        print("\n[SUMMARY] VOLEDGE_OFFLINE=1 -> live пропущено явно.")
        return 0

    print("\n--- LIVE " + "-" * 62)
    try:
        run_live(currencies, rv5_days, min_bars)
    except Exception as e:
        print(f"\n[ERROR] Live провалився: {e}")
        traceback.print_exc()
        return 2
    print("\n[SUMMARY] rv_intraday завершено.")
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
