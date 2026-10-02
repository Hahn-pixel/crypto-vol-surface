# -*- coding: utf-8 -*-
"""
[EN] Conversion between raw SVI and jump-wings (JW) parameters
(Gatheral-Jacquier 2013). v_t and psi are local and robust features;
p and c are global asymptotes, used only as a quality flag. Includes a
small CLI.

--- Ukrainian original below ---
Vol-Edge :: core/svi_jw.py

SVI jump-wings (JW) параметризація (Gatheral-Jacquier 2013).

Перерахунок raw (a, b, rho, m, s) <-> JW (v_t, psi, p, c, v_min) для tau:
  v_t   — ATM total variance / tau (ATM-варіація, річна);
  psi   — ATM skew (похідна усмішки в k=0, нормована);
  p     — нахил лівого (put) крила;
  c     — нахил правого (call) крила;
  v_min — мінімальна варіація усмішки.

Навіщо: інтерпретовані ряди для детектора аномалій. z-score по psi —
чистіший сигнал скосу, ніж RR25 (не залежить від дельта-солвера і меж
даних); p/c дають нахили крил напряму.

ВАЖЛИВО (конвенція проєкту): JW описує ГЛОБАЛЬНУ форму raw-SVI, але сам
фіт валідний лише в k_range даних. psi/v_t — це властивості в k=0, що
всередині даних для всіх наших зрізів; p/c — асимптоти, їх трактуємо як
параметри форми, не як прогноз IV поза даними.

Формули (w0 = total variance в k=0):
  w0    = a + b*(-rho*m + sqrt(m^2 + s^2))
  v_t   = w0 / tau
  psi   = (b/2) * (rho - m/sqrt(m^2+s^2)) / sqrt(w0)
  p     = b*(1-rho)/sqrt(w0)
  c     = b*(1+rho)/sqrt(w0)
  v_min = (a + b*s*sqrt(1-rho^2)) / tau
Обернений перерахунок — за статтею, з явною відмовою на виродженнях.

Чистий stdlib. Запуск подвійним кліком; без мережі (читає лише локальний
артефакт). Env-флаги:
  VOLEDGE_OFFLINE=1     -> лише самотести, артефакт не читається.
  VOLEDGE_ARTIFACT=path -> явний шлях до chain-артефакту.
"""

import glob
import gzip
import json
import math
import os
import sys
import traceback

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)

CHAIN_DIR = os.path.join(_ROOT, "artifacts", "chain")
_EPS = 1e-12


# ----------------------------------------------------------------------------
# raw -> JW
# ----------------------------------------------------------------------------

def raw_to_jw(params, tau: float) -> dict:
    """(a,b,rho,m,s), tau -> dict JW. Явна відмова на невалідних входах."""
    a, b, rho, m, s = params
    if tau <= 0:
        raise ValueError(f"raw_to_jw: tau={tau} <= 0")
    if b < 0 or s <= 0 or abs(rho) >= 1:
        raise ValueError(f"raw_to_jw: невалідні raw: b={b} s={s} rho={rho}")
    hyp0 = math.sqrt(m * m + s * s)
    w0 = a + b * (-rho * m + hyp0)
    if w0 <= 0:
        raise ValueError(f"raw_to_jw: w0={w0} <= 0 (усмішка невалідна в k=0)")
    sq = math.sqrt(w0)
    return {
        "v_t": w0 / tau,
        "psi": (b / 2.0) * (rho - m / hyp0) / sq,
        "p": b * (1.0 - rho) / sq,
        "c": b * (1.0 + rho) / sq,
        "v_min": (a + b * s * math.sqrt(1.0 - rho * rho)) / tau,
        "w0": w0,
        "atm_iv_from_w0": math.sqrt(w0 / tau),
    }


# ----------------------------------------------------------------------------
# JW -> raw
# ----------------------------------------------------------------------------

def jw_to_raw(jw: dict, tau: float):
    """JW -> (a,b,rho,m,s). Явні відмови на виродженнях (|beta|>=1 тощо)."""
    v_t, psi, p, c, v_min = (jw["v_t"], jw["psi"], jw["p"], jw["c"],
                             jw["v_min"])
    if tau <= 0:
        raise ValueError(f"jw_to_raw: tau={tau} <= 0")
    if v_t <= 0 or p <= 0 or c <= 0:
        raise ValueError(f"jw_to_raw: v_t={v_t} p={p} c={c} мають бути > 0")
    w0 = v_t * tau
    sq = math.sqrt(w0)
    b = 0.5 * sq * (c + p)
    if b <= 0:
        raise ValueError(f"jw_to_raw: b={b} <= 0")
    rho = 1.0 - p * sq / b
    if abs(rho) >= 1.0:
        raise ValueError(f"jw_to_raw: |rho|={abs(rho)} >= 1 (p,c несумісні)")
    beta = rho - 2.0 * psi * sq / b
    if abs(beta) >= 1.0:
        raise ValueError(
            f"jw_to_raw: |beta|={abs(beta):.6f} >= 1 — psi поза досяжним "
            f"діапазоном для цих (p,c); JW-набір не відповідає жодній "
            f"raw-SVI")
    dvw = (v_t - v_min) * tau
    if dvw < -_EPS:
        raise ValueError(f"jw_to_raw: v_min={v_min} > v_t={v_t}")
    sqrt1r2 = math.sqrt(1.0 - rho * rho)
    if abs(beta) < _EPS:
        # симетричний випадок: m = 0
        m = 0.0
        denom = b * (1.0 - sqrt1r2)
        if denom < _EPS:
            raise ValueError(
                "jw_to_raw: вироджений симетричний випадок rho~0 — JW не "
                "ідентифікує (a,s) окремо (v_min==v_t); зворотний "
                f"перерахунок неможливий (rho={rho}, denom={denom})")
        s = max(dvw, 0.0) / denom
    else:
        alpha = math.copysign(math.sqrt(1.0 / (beta * beta) - 1.0), beta)
        denom = b * (-rho + math.copysign(math.sqrt(1.0 + alpha * alpha),
                                          alpha) - alpha * sqrt1r2)
        if abs(denom) < _EPS:
            raise ValueError(f"jw_to_raw: вироджений denom={denom}")
        m = max(dvw, 0.0) / denom
        if m == 0.0:
            raise ValueError("jw_to_raw: m=0 при beta!=0 — суперечність "
                             f"(v_t==v_min={v_t}, psi={psi})")
        s = alpha * m
    if s <= 0:
        raise ValueError(f"jw_to_raw: s={s} <= 0 (перевірте знак psi/beta)")
    a = v_min * tau - b * s * sqrt1r2
    return (a, b, rho, m, s)


# ----------------------------------------------------------------------------
# Читання артефакту (та сама конвенція, що в smile_report)
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


def jw_table_for_artifact(chain_data) -> dict:
    """
    {currency: {"slices": [...], "counters": {...}}}; кожен зріз або JW,
    або явний skip з причиною. Жодного тихого пропуску.
    """
    out = {}
    for blk in chain_data:
        cur = blk.get("currency", "?")
        rows = []
        c = {"slices_total": 0, "jw_ok": 0, "skip_no_params": 0,
             "skip_slice_bad": 0, "skip_convert_fail": 0}
        for sl in blk.get("slices", []):
            c["slices_total"] += 1
            exp = sl.get("expiry")
            if sl.get("status") != "OK":
                c["skip_slice_bad"] += 1
                rows.append({"expiry": exp, "status":
                             f"SKIP: slice status={sl.get('status')}"})
                continue
            params = sl.get("svi_params")
            if not params or len(params) != 5:
                c["skip_no_params"] += 1
                rows.append({"expiry": exp, "status":
                             f"SKIP: без svi_params "
                             f"(fit_mode={sl.get('fit_mode')})"})
                continue
            try:
                jw = raw_to_jw(tuple(params), sl["tau"])
            except ValueError as e:
                c["skip_convert_fail"] += 1
                rows.append({"expiry": exp, "status": f"SKIP: {e}"})
                continue
            c["jw_ok"] += 1
            jw["expiry"] = exp
            jw["tau"] = sl["tau"]
            jw["status"] = "OK"
            jw["atm_dns_pa_iv"] = sl.get("atm_dns_pa_iv")
            rows.append(jw)
        out[cur] = {"slices": rows, "counters": c}
    return out


def print_jw_table(table: dict) -> None:
    for cur, blk in table.items():
        print(f"\n--- {cur} " + "-" * 62)
        print(f"{'expiry':>10} {'d':>6} {'v_t':>8} {'ATM(w0)':>8} "
              f"{'psi':>8} {'p':>7} {'c':>7} {'v_min':>8}")
        for r in blk["slices"]:
            if r.get("status") != "OK":
                print(f"{str(r.get('expiry')):>10}  {r['status']}")
                continue
            print(f"{str(r['expiry']):>10} {r['tau']*365:6.1f} "
                  f"{r['v_t']:8.4f} {r['atm_iv_from_w0']*100:7.2f}% "
                  f"{r['psi']:+8.4f} {r['p']:7.3f} {r['c']:7.3f} "
                  f"{r['v_min']:8.4f}")
        print(f"[COUNTERS] {cur}: {json.dumps(blk['counters'])}")


# ----------------------------------------------------------------------------
# Самотести (детерміновано)
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


def _w_raw(params, k):
    a, b, rho, m, s = params
    return a + b * (rho * (k - m) + math.sqrt((k - m) ** 2 + s * s))


def run_self_tests() -> TestCounters:
    tc = TestCounters()
    cases = [
        ("типовий скіс", (0.020, 0.40, -0.30, 0.05, 0.30), 0.25),
        ("майже симетр.", (0.015, 0.25, 0.05, 0.00, 0.20), 0.10),
        ("сильний скіс", (0.010, 0.60, -0.70, 0.10, 0.15), 0.50),
        ("плюс-rho",     (0.030, 0.30,  0.40, -0.08, 0.25), 1.00),
        ("коротка",      (0.002, 0.80, -0.20, 0.01, 0.05), 0.02),
    ]
    for name, raw, tau in cases:
        jw = raw_to_jw(raw, tau)
        back = jw_to_raw(jw, tau)
        errs = [abs(x - y) / max(abs(x), 1e-6)
                for x, y in zip(raw, back)]
        tc.check(f"roundtrip[{name}]", max(errs) < 1e-9,
                 f"max_rel_err={max(errs):.2e}")

    raw, tau = cases[0][1], cases[0][2]
    jw = raw_to_jw(raw, tau)
    # v_t збігається з w(0)/tau прямим обчисленням
    tc.check("v_t_matches_w0",
             abs(jw["v_t"] - _w_raw(raw, 0.0) / tau) < 1e-12,
             f"{jw['v_t']:.6f}")
    # psi збігається з чисельною похідною IV в k=0 (dIV/dk = dw/dk/(2*sqrt(w*tau))... 
    # у JW: psi = d(sqrt(w))/dk / ... перевіряємо через total variance)
    h = 1e-6
    dw = (_w_raw(raw, h) - _w_raw(raw, -h)) / (2 * h)
    psi_num = dw / (2.0 * math.sqrt(_w_raw(raw, 0.0)))
    tc.check("psi_matches_numeric", abs(jw["psi"] - psi_num) < 1e-6,
             f"psi={jw['psi']:.6f} num={psi_num:.6f}")
    # v_min: глобальний мінімум w на сітці
    wmin = min(_w_raw(raw, k * 0.001) for k in range(-3000, 3001))
    tc.check("v_min_matches_grid", abs(jw["v_min"] - wmin / tau) < 1e-4,
             f"v_min={jw['v_min']:.6f} grid={wmin/tau:.6f}")
    # для rho<0: ліве крило крутіше за праве
    tc.check("wings_order_neg_rho", jw["p"] > jw["c"],
             f"p={jw['p']:.4f} c={jw['c']:.4f}")

    # явні відмови
    for nm, bad, btau in (
            ("bad_tau", (0.02, 0.4, -0.3, 0.0, 0.3), 0.0),
            ("bad_rho", (0.02, 0.4, -1.1, 0.0, 0.3), 0.25),
            ("bad_w0",  (-0.5, 0.1, 0.0, 0.0, 0.1), 0.25)):
        try:
            raw_to_jw(bad, btau)
            tc.check(f"refuse[{nm}]", False, "не кинув виняток")
        except ValueError:
            tc.check(f"refuse[{nm}]", True)
    jw_bad = {"v_t": 0.08, "psi": -5.0, "p": 0.5, "c": 0.4, "v_min": 0.07}
    try:
        jw_to_raw(jw_bad, 0.25)
        tc.check("refuse[beta_out]", False, "не кинув виняток")
    except ValueError as e:
        tc.check("refuse[beta_out]", "beta" in str(e), str(e))

    # rho=0: JW вироджена, round-trip мусить явно відмовити
    jw0 = raw_to_jw((0.015, 0.25, 0.0, 0.0, 0.20), 0.10)
    try:
        jw_to_raw(jw0, 0.10)
        tc.check("refuse[rho0_degenerate]", False, "не кинув виняток")
    except ValueError as e:
        tc.check("refuse[rho0_degenerate]", "rho~0" in str(e), str(e))

    # jw_table: skip-гілки з лічильниками
    art = [{"currency": "SYN", "slices": [
        {"expiry": "A", "status": "OK", "tau": 0.25,
         "svi_params": list(cases[0][1]), "atm_dns_pa_iv": 0.30},
        {"expiry": "B", "status": "OK", "tau": 0.1, "svi_params": None,
         "fit_mode": "PARABOLA"},
        {"expiry": "C", "status": "fit_fail"},
    ]}]
    t = jw_table_for_artifact(art)
    c = t["SYN"]["counters"]
    tc.check("table_counters", c["jw_ok"] == 1 and c["skip_no_params"] == 1
             and c["skip_slice_bad"] == 1, str(c))

    print(f"[TEST] passed {tc.passed}/{tc.run}")
    for f_ in tc.failures:
        print(f"[TEST] FAILURE detail: {f_}")
    return tc


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main() -> int:
    print("=" * 72)
    print("Vol-Edge :: svi_jw (jump-wings параметризація)")
    print("=" * 72)
    offline_raw = os.environ.get("VOLEDGE_OFFLINE", "0")
    offline = offline_raw.strip() == "1"
    print(f"[ENV] VOLEDGE_OFFLINE={offline_raw!r} -> {offline}")

    tc = run_self_tests()
    print(f"[SUMMARY] самотести: {tc.run}, пройдено: {tc.passed}, "
          f"провалено: {len(tc.failures)}")
    if tc.failures:
        return 1
    if offline:
        print("[SUMMARY] VOLEDGE_OFFLINE=1 -> читання артефакту пропущено "
              "явно.")
        return 0

    art_env = os.environ.get("VOLEDGE_ARTIFACT", "")
    print(f"[ENV] VOLEDGE_ARTIFACT={art_env or '(auto: останній)'}")
    if art_env:
        path = art_env
        if not os.path.isfile(path):
            print(f"[SUMMARY] артефакт не знайдено: {path}")
            return 1
    else:
        path = find_latest_chain(CHAIN_DIR)
        if path is None:
            print(f"[SUMMARY] нуль артефактів у {CHAIN_DIR}")
            return 1
    print(f"[OVERALL] артефакт: {path}")
    table = jw_table_for_artifact(load_chain(path))
    print_jw_table(table)
    print("\n[SUMMARY] svi_jw завершено.")
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
