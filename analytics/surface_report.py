# -*- coding: utf-8 -*-
"""
[EN] Report over accumulated surface snapshots: constant-tenor ATM IV
(linear interpolation of total variance in tau), RR/BF, realized
volatility and the Deribit DVOL index for comparison.

--- Ukrainian original below ---
Vol-Edge / analytics / surface_report.py

Аналітичний звіт по накопичених знімках поверхні (artifacts/chain/*.json):
- Константні тенори ATM IV (7/30/90/180д): лінійна інтерполяція TOTAL
  VARIANCE по tau (єдина інтерполяція, що не ламає calendar-умову).
- RR25pa/BF25pa на тенорі 30д: лінійна інтерполяція по tau.
- Term slope: ATM90 - ATM30.
- Realized vol по денних свічках Deribit PERPETUAL (проксі індексу):
  close-to-close та Parkinson, ануалізація sqrt(365).
- IV30/RV30 (variance risk premium proxy) та IV7/RV7.
- Крос-перевірка: наш ATM30 проти офіційного DVOL.
- Кросс-перекоси: ATM30(ETH) - ATM30(BTC), відношення, спред RR.
- Історична таблиця по всіх знімках (коли їх накопичиться більше одного).

Environment flags (явна обробка, значення логуються):
  VOLEDGE_OFFLINE  - "1" -> лише самотести, без мережі та без артефактів
  VOLEDGE_RV_DAYS  - вікно історії свічок для RV (default 35)

Потрібні файли: artifacts/chain/*.json від deribit_chain.py.
Мережа: лише публічні endpoint-и (свічки, DVOL), без ключів.
Жодного silent fail-open: кожен пропуск тенора/блоку — з причиною.
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

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_THIS_DIR, ".."))
for _p in (_ROOT, _THIS_DIR, os.path.join(_ROOT, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
ARTIFACTS_DIR = os.path.join(_ROOT, "artifacts", "chain")

try:
    from env_flags import merge_known, warn_unknown_env
except ImportError:
    from core.env_flags import merge_known, warn_unknown_env

# Змінні, які читає САМЕ ЦЯ точка входу (обидві — у main()).
KNOWN_ENV = merge_known((
    "VOLEDGE_RV_DAYS",
))

API_BASE = "https://www.deribit.com/api/v2/public"
HTTP_TIMEOUT_S = 15
HTTP_RETRIES = 3
HTTP_RETRY_SLEEP_S = 2.0

TENORS_D = (7.0, 30.0, 90.0, 180.0)
RR_TENOR_D = 30.0
ANNUALIZE = 365.0
PERP_NAME = {"BTC": "BTC-PERPETUAL", "ETH": "ETH-PERPETUAL"}


# ----------------------------------------------------------------------------
# HTTP
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


# ----------------------------------------------------------------------------
# Константні тенори з одного знімка
# ----------------------------------------------------------------------------

def interp_tenor(slices: list, tenor_d: float) -> dict:
    """
    slices: список dict зі status/tau/atm_dns_pa_iv/rr25_pa/bf25_pa
    (схема артефакту deribit_chain).
    ATM: лінійно по total variance w = iv^2 * tau.
    RR/BF: лінійно по tau (None у вузлі -> None у результаті з причиною).
    Поза діапазоном експірацій -> {'status': 'out_of_range'} (явно).
    """
    pts = sorted(
        [(s["tau"], s["atm_dns_pa_iv"], s.get("rr25_pa"), s.get("bf25_pa"))
         for s in slices if s.get("status") == "OK"
         and s.get("atm_dns_pa_iv") is not None],
        key=lambda x: x[0])
    if len(pts) < 2:
        return {"status": "not_enough_slices", "n": len(pts)}
    t_star = tenor_d / ANNUALIZE
    taus = [p[0] for p in pts]
    if not (taus[0] <= t_star <= taus[-1]):
        return {"status": "out_of_range",
                "range_d": (taus[0] * ANNUALIZE, taus[-1] * ANNUALIZE)}
    hi = next(i for i, t in enumerate(taus) if t >= t_star)
    if taus[hi] == t_star:
        t, iv, rr, bf = pts[hi]
        return {"status": "OK", "atm_iv": iv, "rr": rr, "bf": bf,
                "exact_node": True}
    lo = hi - 1
    t1, iv1, rr1, bf1 = pts[lo]
    t2, iv2, rr2, bf2 = pts[hi]
    x = (t_star - t1) / (t2 - t1)
    w1, w2 = iv1 * iv1 * t1, iv2 * iv2 * t2
    w_star = w1 + x * (w2 - w1)
    if w_star <= 0.0:
        return {"status": "bad_variance", "w": w_star}
    atm = math.sqrt(w_star / t_star)
    rr = rr1 + x * (rr2 - rr1) if (rr1 is not None and rr2 is not None) \
        else None
    bf = bf1 + x * (bf2 - bf1) if (bf1 is not None and bf2 is not None) \
        else None
    return {"status": "OK", "atm_iv": atm, "rr": rr, "bf": bf,
            "exact_node": False, "nodes_d": (t1 * ANNUALIZE, t2 * ANNUALIZE)}


def snapshot_metrics(cur_block: dict) -> dict:
    """Метрики одного знімка однієї валюти."""
    out = {"currency": cur_block["currency"],
           "snapshot_utc": cur_block["snapshot_utc"],
           "index_price": cur_block["index_price"],
           "tenors": {}}
    for td in TENORS_D:
        out["tenors"][td] = interp_tenor(cur_block["slices"], td)
    t30 = out["tenors"].get(30.0, {})
    t90 = out["tenors"].get(90.0, {})
    if t30.get("status") == "OK" and t90.get("status") == "OK":
        out["slope_30_90"] = t90["atm_iv"] - t30["atm_iv"]
    else:
        out["slope_30_90"] = None
    return out


# ----------------------------------------------------------------------------
# Realized volatility
# ----------------------------------------------------------------------------

def rv_close_to_close(closes: list, window: int) -> float:
    """Ануалізована RV по останніх `window` лог-ретернах."""
    if len(closes) < window + 1:
        raise ValueError(f"rv_cc: треба {window + 1} цін, є {len(closes)}")
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - window, len(closes))]
    var = sum(r * r for r in rets) / window
    return math.sqrt(var * ANNUALIZE)


def rv_parkinson(highs: list, lows: list, window: int) -> float:
    """Parkinson по останніх `window` барах."""
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


def fetch_daily_candles(currency: str, days: int) -> dict:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int((days + 2) * 24 * 3600 * 1000)
    inst = PERP_NAME[currency]
    r = http_get_json(
        f"{API_BASE}/get_tradingview_chart_data?instrument_name={inst}"
        f"&start_timestamp={start_ms}&end_timestamp={end_ms}&resolution=1D")
    if r.get("status") != "ok" or not r.get("close"):
        raise RuntimeError(f"candles {inst}: status={r.get('status')}")
    return {"close": [float(x) for x in r["close"]],
            "high": [float(x) for x in r["high"]],
            "low": [float(x) for x in r["low"]]}


def fetch_dvol_last(currency: str) -> float:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - 6 * 3600 * 1000
    r = http_get_json(
        f"{API_BASE}/get_volatility_index_data?currency={currency}"
        f"&start_timestamp={start_ms}&end_timestamp={end_ms}"
        f"&resolution=3600")
    data = r.get("data")
    if not data:
        raise RuntimeError(f"DVOL {currency}: порожні дані")
    return float(data[-1][4]) / 100.0  # close останньої години, у частках


# ----------------------------------------------------------------------------
# Артефакти
# ----------------------------------------------------------------------------

def load_artifacts(art_dir: str) -> list:
    """Всі знімки (.json та .json.gz), відсортовані за часом.
    Биті файли — явний лог і skip."""
    files = sorted(glob.glob(os.path.join(art_dir, "chain_*.json"))
                   + glob.glob(os.path.join(art_dir, "chain_*.json.gz")))
    out = []
    n_bad = 0
    for path in files:
        try:
            if path.endswith(".gz"):
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    out.append({"path": path, "data": json.load(f)})
            else:
                with open(path, "r", encoding="utf-8") as f:
                    out.append({"path": path, "data": json.load(f)})
        except (json.JSONDecodeError, OSError) as e:
            n_bad += 1
            print(f"[ARTIFACT] Битий файл пропущено: {path} :: {e}")
    print(f"[ARTIFACT] Завантажено {len(out)} знімків, битих: {n_bad}")
    return out


# ----------------------------------------------------------------------------
# Форматування звіту
# ----------------------------------------------------------------------------

def _fmt_pct(x, digits=2):
    return f"{x * 100:.{digits}f}%" if x is not None else "  n/a "


def _fmt_vp(x):
    return f"{x * 100:+.2f}vp" if x is not None else "   n/a"


def print_currency_report(m: dict, rv: dict, dvol) -> None:
    cur = m["currency"]
    print(f"\n[REPORT] {cur}  index={m['index_price']:.2f}  "
          f"snapshot={m['snapshot_utc']}")
    for td in TENORS_D:
        t = m["tenors"][td]
        if t["status"] != "OK":
            print(f"  ATM{int(td):>3}d: SKIP ({t['status']})")
            continue
        extra = ""
        if td == RR_TENOR_D:
            extra = f"  RR25pa={_fmt_vp(t['rr'])}  BF25pa={_fmt_vp(t['bf'])}"
        print(f"  ATM{int(td):>3}d: {_fmt_pct(t['atm_iv'])}{extra}")
    if m["slope_30_90"] is not None:
        print(f"  term slope 30->90: {_fmt_vp(m['slope_30_90'])}")
    if rv is not None:
        print(f"  RV7  cc={_fmt_pct(rv['rv7_cc'])}  "
              f"park={_fmt_pct(rv['rv7_park'])}")
        print(f"  RV30 cc={_fmt_pct(rv['rv30_cc'])}  "
              f"park={_fmt_pct(rv['rv30_park'])}")
        t7 = m["tenors"][7.0]
        t30 = m["tenors"][30.0]
        if t7.get("status") == "OK" and rv["rv7_cc"] > 0:
            print(f"  IV7/RV7   = {t7['atm_iv'] / rv['rv7_cc']:.3f}")
        if t30.get("status") == "OK" and rv["rv30_cc"] > 0:
            print(f"  IV30/RV30 = {t30['atm_iv'] / rv['rv30_cc']:.3f}")
    if dvol is not None:
        t30 = m["tenors"][30.0]
        if t30.get("status") == "OK":
            print(f"  DVOL={_fmt_pct(dvol)}  наш ATM30={_fmt_pct(t30['atm_iv'])}"
                  f"  diff={_fmt_vp(t30['atm_iv'] - dvol)}")


def print_cross(metrics: dict) -> None:
    if "BTC" not in metrics or "ETH" not in metrics:
        return
    b30 = metrics["BTC"]["tenors"][30.0]
    e30 = metrics["ETH"]["tenors"][30.0]
    if b30.get("status") != "OK" or e30.get("status") != "OK":
        print("\n[CROSS] SKIP: немає ATM30 для обох валют")
        return
    print("\n[CROSS] ETH vs BTC (тенор 30д)")
    print(f"  ATM: ETH-BTC = {_fmt_vp(e30['atm_iv'] - b30['atm_iv'])}  "
          f"ratio = {e30['atm_iv'] / b30['atm_iv']:.3f}")
    if b30.get("rr") is not None and e30.get("rr") is not None:
        print(f"  RR25pa spread (ETH-BTC) = {_fmt_vp(e30['rr'] - b30['rr'])}")


def print_history(artifacts: list) -> None:
    if len(artifacts) < 2:
        print("\n[HISTORY] Знімок один — таблиця часових рядів з'явиться "
              "з накопиченням артефактів.")
        return
    print("\n[HISTORY] Останні знімки (до 12):")
    print("  {:<20s} {:>9s} {:>9s} {:>9s} {:>10s}".format(
        "snapshot_utc", "BTC ATM30", "BTC RR30", "ETH ATM30", "ETH-BTC"))
    for art in artifacts[-12:]:
        row = {"ts": None, "b": None, "brr": None, "e": None}
        for blk in art["data"]:
            mm = snapshot_metrics(blk)
            row["ts"] = mm["snapshot_utc"][:19]
            t30 = mm["tenors"][30.0]
            if t30.get("status") != "OK":
                continue
            if blk["currency"] == "BTC":
                row["b"], row["brr"] = t30["atm_iv"], t30.get("rr")
            elif blk["currency"] == "ETH":
                row["e"] = t30["atm_iv"]
        spread = (row["e"] - row["b"]) if (row["e"] is not None
                                           and row["b"] is not None) else None
        print("  {:<20s} {:>9s} {:>9s} {:>9s} {:>10s}".format(
            row["ts"] or "?", _fmt_pct(row["b"]), _fmt_vp(row["brr"]),
            _fmt_pct(row["e"]), _fmt_vp(spread)))


# ----------------------------------------------------------------------------
# Самотести (офлайн, детерміновано)
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


def run_self_tests() -> TestCounters:
    tc = TestCounters()

    print("[1] interp_tenor: total variance лінійна")
    s1 = {"status": "OK", "tau": 10.0 / 365.0, "atm_dns_pa_iv": 0.30,
          "rr25_pa": -0.02, "bf25_pa": 0.01}
    s2 = {"status": "OK", "tau": 50.0 / 365.0, "atm_dns_pa_iv": 0.40,
          "rr25_pa": -0.06, "bf25_pa": 0.02}
    r = interp_tenor([s1, s2], 30.0)
    tc.check("interp: status OK", r["status"] == "OK", str(r))
    w1 = 0.30 ** 2 * 10 / 365
    w2 = 0.40 ** 2 * 50 / 365
    w_star = w1 + 0.5 * (w2 - w1)
    atm_exp = math.sqrt(w_star / (30 / 365))
    tc.check("interp: ATM по total variance",
             abs(r["atm_iv"] - atm_exp) < 1e-12,
             f"got={r['atm_iv']:.6f} exp={atm_exp:.6f}")
    tc.check("interp: RR лінійний", abs(r["rr"] - (-0.04)) < 1e-12,
             f"rr={r['rr']}")
    r_edge = interp_tenor([s1, s2], 10.0)
    tc.check("interp: точний вузол", r_edge["status"] == "OK"
             and r_edge.get("exact_node") is True
             and abs(r_edge["atm_iv"] - 0.30) < 1e-12, str(r_edge))
    r_out = interp_tenor([s1, s2], 90.0)
    tc.check("interp: out_of_range явний",
             r_out["status"] == "out_of_range", str(r_out))
    s_rr_none = dict(s2)
    s_rr_none["rr25_pa"] = None
    r_none = interp_tenor([s1, s_rr_none], 30.0)
    tc.check("interp: None у вузлі RR -> None (без вигадування)",
             r_none["status"] == "OK" and r_none["rr"] is None, str(r_none))

    print("[2] RV-оцінювачі на детермінованих рядах")
    r0 = 0.02
    closes = [100.0 * math.exp(r0 * i) for i in range(31)]
    rv = rv_close_to_close(closes, 30)
    tc.check("rv_cc: константний ретерн",
             abs(rv - r0 * math.sqrt(ANNUALIZE)) < 1e-12,
             f"got={rv:.6f} exp={r0 * math.sqrt(ANNUALIZE):.6f}")
    ratio = 1.03
    highs = [c * ratio for c in closes[:30]]
    lows = list(closes[:30])
    rvp = rv_parkinson(highs, lows, 30)
    exp_p = math.sqrt(math.log(ratio) ** 2 / (4 * math.log(2)) * ANNUALIZE)
    tc.check("rv_park: константний H/L",
             abs(rvp - exp_p) < 1e-12, f"got={rvp:.6f} exp={exp_p:.6f}")
    try:
        rv_close_to_close(closes[:5], 30)
        tc.check("rv_cc: замало даних -> виняток", False, "не кинув")
    except ValueError:
        tc.check("rv_cc: замало даних -> виняток", True)

    print("[3] snapshot_metrics + історія на синтетичному артефакті")
    blk = {"currency": "BTC", "snapshot_utc": "2026-07-25T12:00:00+00:00",
           "index_price": 64000.0,
           "slices": [
               {"status": "OK", "tau": 5.0 / 365.0, "atm_dns_pa_iv": 0.28,
                "rr25_pa": -0.02, "bf25_pa": 0.008},
               {"status": "OK", "tau": 40.0 / 365.0, "atm_dns_pa_iv": 0.36,
                "rr25_pa": -0.05, "bf25_pa": 0.011},
               {"status": "OK", "tau": 120.0 / 365.0, "atm_dns_pa_iv": 0.41,
                "rr25_pa": -0.06, "bf25_pa": 0.010},
               {"status": "no_forward", "tau": 1.0 / 365.0},
           ]}
    m = snapshot_metrics(blk)
    tc.check("metrics: 7/30/90 OK, 180 out_of_range",
             m["tenors"][7.0]["status"] == "OK"
             and m["tenors"][30.0]["status"] == "OK"
             and m["tenors"][90.0]["status"] == "OK"
             and m["tenors"][180.0]["status"] == "out_of_range",
             str({k: v["status"] for k, v in m["tenors"].items()}))
    tc.check("metrics: slope порахований", m["slope_30_90"] is not None
             and m["slope_30_90"] > 0, str(m["slope_30_90"]))
    tc.check("metrics: битий зріз проігноровано без падіння", True)

    print("[4] load_artifacts: .json + .json.gz + битий файл")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "chain_20260101_000000.json")
        with open(p1, "w", encoding="utf-8") as f:
            json.dump([blk], f)
        p2 = os.path.join(td, "chain_20260102_000000.json.gz")
        with gzip.open(p2, "wt", encoding="utf-8") as f:
            json.dump([blk], f)
        p3 = os.path.join(td, "chain_20260103_000000.json")
        with open(p3, "w", encoding="utf-8") as f:
            f.write("{битий json")
        arts = load_artifacts(td)
        tc.check("artifacts: 2 валідних (json+gz), битий пропущено",
                 len(arts) == 2, f"n={len(arts)}")
        tc.check("artifacts: gz прочитано коректно",
                 arts[1]["data"][0]["currency"] == "BTC", str(arts[1]["path"]))

    # --- Перевірка змінних оточення (quiet=True: самотест не сміє
    # смітити в живий лог) ---
    import re as _envre
    _unk = warn_unknown_env(KNOWN_ENV, environ={"VOLEDGE_RV_DAYS": "35", "VOLEDGE_RV_DAY": "35"}, quiet=True)
    tc.check("env_warn_flags_typo", _unk == ["VOLEDGE_RV_DAY"], str(_unk))
    tc.check("env_warn_ignores_known", "VOLEDGE_RV_DAYS" not in _unk)
    _envsrc = ""
    try:
        with open(os.path.abspath(__file__), "r",
                  encoding="utf-8") as _envfh:
            _envsrc = _envfh.read()
    except OSError:
        _envsrc = ""
    _read = set(_envre.findall(
        r'(?:os\.environ\.get|_env_int)\(\s*"(VOLEDGE_[A-Z0-9_]+)"',
        _envsrc))
    tc.check("known_env_covers_every_read",
             bool(_envsrc) and _read and _read <= set(KNOWN_ENV),
             f"не оголошені: {sorted(_read - set(KNOWN_ENV))}")
    tc.check("known_env_has_no_dead_names",
             bool(_envsrc) and set(KNOWN_ENV) <= _read | {"VOLEDGE_OFFLINE"},
             f"зайві: {sorted(set(KNOWN_ENV) - _read - {'VOLEDGE_OFFLINE'})}")

    return tc


# ----------------------------------------------------------------------------
# Live
# ----------------------------------------------------------------------------

def run_live(rv_days: int) -> None:
    artifacts = load_artifacts(ARTIFACTS_DIR)
    if not artifacts:
        raise RuntimeError(
            f"Немає артефактів у {ARTIFACTS_DIR} — спершу запустіть "
            f"data/deribit_chain.py")
    latest = artifacts[-1]
    print(f"[LATEST] {latest['path']}")
    metrics = {}
    for blk in latest["data"]:
        metrics[blk["currency"]] = snapshot_metrics(blk)

    for cur, m in metrics.items():
        rv = None
        try:
            cd = fetch_daily_candles(cur, rv_days)
            rv = {"rv7_cc": rv_close_to_close(cd["close"], 7),
                  "rv30_cc": rv_close_to_close(cd["close"], 30),
                  "rv7_park": rv_parkinson(cd["high"], cd["low"], 7),
                  "rv30_park": rv_parkinson(cd["high"], cd["low"], 30)}
        except (RuntimeError, ValueError) as e:
            print(f"[RV] {cur}: SKIP ({e})")
        dvol = None
        try:
            dvol = fetch_dvol_last(cur)
        except (RuntimeError, ValueError) as e:
            print(f"[DVOL] {cur}: SKIP ({e})")
        print_currency_report(m, rv, dvol)

    print_cross(metrics)
    print_history(artifacts)
    print("\n[SUMMARY] Звіт завершено.")


def main() -> int:
    print("=" * 72)
    print("Vol-Edge :: surface_report")
    print("=" * 72)
    # ПЕРЕД розбором значень: нерозпізнана змінна = тихий дефолт.
    warn_unknown_env(KNOWN_ENV, label="surface_report")
    offline_raw = os.environ.get("VOLEDGE_OFFLINE", "0")
    rv_days_raw = os.environ.get("VOLEDGE_RV_DAYS", "35")
    offline = offline_raw.strip() == "1"
    try:
        rv_days = int(rv_days_raw)
    except ValueError:
        raise RuntimeError(f"VOLEDGE_RV_DAYS={rv_days_raw!r} не ціле")
    if rv_days < 31:
        raise RuntimeError(f"VOLEDGE_RV_DAYS={rv_days} < 31 — RV30 "
                           f"не порахується (потрібен 31 бар)")
    print(f"[ENV] VOLEDGE_OFFLINE={offline_raw!r} -> {offline}")
    print(f"[ENV] VOLEDGE_RV_DAYS={rv_days_raw!r} -> {rv_days}")

    print("\n--- ОФЛАЙН-САМОТЕСТИ " + "-" * 50)
    tc = run_self_tests()
    print("-" * 72)
    print(f"[SUMMARY] самотести: {tc.run}, пройдено: {tc.passed}, "
          f"провалено: {len(tc.failures)}")
    if tc.failures:
        for f in tc.failures:
            print(f"  - {f}")
        return 1
    if offline:
        print("\n[SUMMARY] VOLEDGE_OFFLINE=1 -> live-звіт пропущено явно.")
        return 0

    print("\n--- LIVE-ЗВІТ " + "-" * 57)
    try:
        run_live(rv_days)
    except Exception as e:
        print(f"\n[ERROR] Live-звіт провалився: {e}")
        return 2
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
