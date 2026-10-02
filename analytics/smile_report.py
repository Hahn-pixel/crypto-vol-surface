# -*- coding: utf-8 -*-
"""
[EN] HTML report with fitted smiles against market quotes, used for
visual quality control of the SVI fits.

--- Ukrainian original below ---
Vol-Edge :: analytics/smile_report.py

HTML-звіт зі смайлами (візуальний QA фітів).

Для кожної експірації малює inline-SVG: зфітована SVI-крива поверх точок
bid/mid/ask IV, підсвічені відкинуті котирування, позначки 25D(pa)/ATM-DNS.
Один самодостатній HTML-файл, чистий stdlib, без мережі.

Джерело даних: останній артефакт artifacts/chain/chain_*.json(.gz)
(rows там уже збережені; svi_params збережені лише для SVI-режимів).

ЯВНІ ПРИНЦИПИ (успадковані від проєкту):
  * Модель валідна ЛИШЕ в діапазоні даних: крива малюється тільки на
    [k_min, k_max] + 10% падінг, як у svi_fit.
  * Зрізи PARABOLA/FLAT: коефіцієнти в артефакті НЕ збережені -> кривої
    немає, лише точки + бейдж NO_MODEL_IN_ARTIFACT; лічильник явний.
  * 25D недосяжна в межах даних -> позначки немає, причина в підписі;
    лічильник явний. Жодного silent fail-open.

Env-флаги:
  VOLEDGE_OFFLINE=1     -> лише самотести, без читання артефактів.
  VOLEDGE_ARTIFACT=path -> явний шлях до артефакту замість "останнього".
  VOLEDGE_REPORT_DIR    -> тека для HTML (default: <root>/reports).

Запуск подвійним кліком; завжди чекає input() у кінці.
"""

import glob
import gzip
import html
import json
import math
import os
import sys
import traceback
from datetime import datetime, timezone

# --- шляхи проєкту: analytics/ -> корінь -> core/ ---------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_ROOT, os.path.join(_ROOT, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from svi_fit import SliceModel, atm_dns_pa_k, solve_k_for_delta
except ImportError:
    from core.svi_fit import SliceModel, atm_dns_pa_k, solve_k_for_delta
try:
    from env_flags import merge_known, warn_unknown_env
except ImportError:
    from core.env_flags import merge_known, warn_unknown_env

# Змінні, які читає САМЕ ЦЯ точка входу (усі — у main()).
KNOWN_ENV = merge_known((
    "VOLEDGE_ARTIFACT",
    "VOLEDGE_REPORT_DIR",
))

DELTA_LEVEL = 0.25

# ----------------------------------------------------------------------------
# Пошук і читання артефакту
# ----------------------------------------------------------------------------


def find_latest_artifact(chain_dir):
    """Останній chain_*.json(.gz) за іменем (ім'я = timestamp)."""
    pats = [os.path.join(chain_dir, "chain_*.json"),
            os.path.join(chain_dir, "chain_*.json.gz")]
    files = []
    for p in pats:
        files.extend(glob.glob(p))
    if not files:
        return None

    def keyf(path):
        base = os.path.basename(path)
        return base[:-3] if base.endswith(".gz") else base

    files.sort(key=keyf)
    return files[-1]


def load_artifact(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------
# Реконструкція моделі зрізу з артефакту
# ----------------------------------------------------------------------------


def rebuild_model(sl):
    """
    (model|None, reason). Модель лише якщо svi_params збережені
    (FULL_SVI/FIXED_S). k_range з рядків зі статусом OK — як у fit_slice.
    """
    params = sl.get("svi_params")
    if not params or len(params) != 5:
        return None, ("NO_MODEL_IN_ARTIFACT: svi_params відсутні "
                      f"(fit_mode={sl.get('fit_mode')})")
    tau = sl["tau"]
    m = SliceModel("svi", tau, params=tuple(params))
    ks = [r["k"] for r in sl.get("rows", []) if r.get("status") == "OK"]
    if not ks:
        return None, "NO_OK_ROWS: нуль рядків OK, k_range невизначений"
    m.k_range = (min(ks), max(ks))
    return m, "OK"


# ----------------------------------------------------------------------------
# SVG-хелпери (без залежностей)
# ----------------------------------------------------------------------------

W, H = 560, 340
ML, MR, MT, MB = 52, 14, 26, 40  # поля: ліво/право/верх/низ


class Scale:
    def __init__(self, k_lo, k_hi, v_lo, v_hi):
        if k_hi <= k_lo:
            k_lo, k_hi = k_lo - 0.05, k_hi + 0.05
        if v_hi <= v_lo:
            v_lo, v_hi = v_lo - 0.01, v_hi + 0.01
        self.k_lo, self.k_hi, self.v_lo, self.v_hi = k_lo, k_hi, v_lo, v_hi

    def x(self, k):
        t = (k - self.k_lo) / (self.k_hi - self.k_lo)
        return ML + t * (W - ML - MR)

    def y(self, iv):
        t = (iv - self.v_lo) / (self.v_hi - self.v_lo)
        return H - MB - t * (H - MT - MB)


def _f(v):
    return f"{v:.2f}"


def _ticks(lo, hi, n=5):
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        step = mag * mult
        if step >= raw:
            break
    t0 = math.ceil(lo / step) * step
    out = []
    t = t0
    while t <= hi + 1e-12:
        out.append(round(t, 10))
        t += step
    return out


def slice_svg(sl, model, model_reason, counters):
    """SVG одного зрізу. Повертає (svg_str, notes:list[str])."""
    notes = []
    rows = sl.get("rows", [])
    ok_rows = [r for r in rows if r.get("status") == "OK"]
    bad_rows = [r for r in rows if r.get("status") != "OK"]
    counters["rows_ok"] += len(ok_rows)
    counters["rows_dropped"] += len(bad_rows)

    F = sl.get("forward")
    # k для відкинутих рядків відновлюємо з K і forward — явно
    bad_ks = []
    for r in bad_rows:
        if F and F > 0 and r.get("K", 0) > 0:
            bad_ks.append((math.log(r["K"] / F), r))
        else:
            counters["dropped_no_k"] += 1

    if not ok_rows and not bad_ks:
        return "<p class='warn'>нуль рядків для відображення</p>", \
               ["EMPTY_SLICE"]

    # діапазони
    ks_all = [r["k"] for r in ok_rows] + [k for k, _ in bad_ks]
    k_lo, k_hi = min(ks_all), max(ks_all)
    pad_k = max(0.02, 0.05 * (k_hi - k_lo))
    k_lo -= pad_k
    k_hi += pad_k

    ivs = []
    for r in ok_rows:
        for f in ("iv_bid", "iv_mid", "iv_ask"):
            v = r.get(f)
            if v is not None and math.isfinite(v):
                ivs.append(v)
    curve_pts = []
    marks = {}
    if model is not None:
        d_lo, d_hi = model.k_range
        pad = max(0.02, 0.10 * (d_hi - d_lo))  # той самий падінг, що у svi_fit
        c_lo, c_hi = d_lo - pad, d_hi + pad
        n = 161
        for i in range(n):
            k = c_lo + (c_hi - c_lo) * i / (n - 1)
            curve_pts.append((k, model.iv(k)))
        ivs.extend(v for _, v in curve_pts)

        k_atm = atm_dns_pa_k(model)
        marks["ATM"] = (k_atm, model.iv(k_atm))
        for name, kind in (("25Dc", "call_pa"), ("25Dp", "put_pa")):
            kx, reason = solve_k_for_delta(model, DELTA_LEVEL, kind)
            if kx is None:
                notes.append(f"{name}: {reason}")
                counters["delta_unreachable"] += 1
            else:
                marks[name] = (kx, model.iv(kx))
    else:
        notes.append(model_reason)
        counters["slices_no_model"] += 1

    if not ivs:
        return "<p class='warn'>нуль скінченних IV</p>", notes + ["NO_IVS"]
    v_lo, v_hi = min(ivs), max(ivs)
    pad_v = max(0.005, 0.07 * (v_hi - v_lo))
    sc = Scale(k_lo, k_hi, v_lo - pad_v, v_hi + pad_v)

    p = []
    p.append(f"<svg viewBox='0 0 {W} {H}' xmlns='http://www.w3.org/2000/svg' "
             f"class='smile'>")
    # осі + сітка
    for tv in _ticks(sc.v_lo, sc.v_hi):
        y = sc.y(tv)
        p.append(f"<line x1='{ML}' y1='{_f(y)}' x2='{W-MR}' y2='{_f(y)}' "
                 f"class='grid'/>")
        p.append(f"<text x='{ML-6}' y='{_f(y+3)}' class='tick' "
                 f"text-anchor='end'>{tv*100:.0f}%</text>")
    for tk in _ticks(sc.k_lo, sc.k_hi):
        x = sc.x(tk)
        p.append(f"<line x1='{_f(x)}' y1='{MT}' x2='{_f(x)}' y2='{H-MB}' "
                 f"class='grid'/>")
        p.append(f"<text x='{_f(x)}' y='{H-MB+14}' class='tick' "
                 f"text-anchor='middle'>{tk:+.2f}</text>")
    p.append(f"<text x='{(ML+W-MR)//2}' y='{H-6}' class='axis' "
             f"text-anchor='middle'>k = ln(K/F)</text>")
    # k=0
    if sc.k_lo < 0.0 < sc.k_hi:
        x0 = sc.x(0.0)
        p.append(f"<line x1='{_f(x0)}' y1='{MT}' x2='{_f(x0)}' "
                 f"y2='{H-MB}' class='zero'/>")

    # bid/ask бари + mid точки
    for r in ok_rows:
        x = sc.x(r["k"])
        b, a, m_ = r.get("iv_bid"), r.get("iv_ask"), r.get("iv_mid")
        if b is not None and a is not None:
            p.append(f"<line x1='{_f(x)}' y1='{_f(sc.y(b))}' x2='{_f(x)}' "
                     f"y2='{_f(sc.y(a))}' class='ba'/>")
        if m_ is not None:
            cls = "mid put" if r.get("cp") == -1 else "mid call"
            p.append(f"<circle cx='{_f(x)}' cy='{_f(sc.y(m_))}' r='2.6' "
                     f"class='{cls}'><title>K={r['K']:.0f} cp={r['cp']} "
                     f"mid={m_*100:.2f}%</title></circle>")

    # відкинуті — трикутники на нижній кромці
    for k, r in bad_ks:
        x = sc.x(k)
        y = H - MB
        st = html.escape(str(r.get("status")))
        p.append(f"<path d='M {_f(x-4)} {y} L {_f(x+4)} {y} L {_f(x)} "
                 f"{y-7} Z' class='drop'><title>K={r['K']:.0f} "
                 f"ВІДКИНУТО: {st}</title></path>")

    # крива
    if curve_pts:
        d = "M " + " L ".join(f"{_f(sc.x(k))} {_f(sc.y(v))}"
                              for k, v in curve_pts)
        p.append(f"<path d='{d}' class='svi'/>")
        d_lo, d_hi = model.k_range
        for kb in (d_lo, d_hi):  # межі даних
            xb = sc.x(kb)
            p.append(f"<line x1='{_f(xb)}' y1='{MT}' x2='{_f(xb)}' "
                     f"y2='{H-MB}' class='krange'/>")

    # позначки
    mark_cls = {"ATM": "matm", "25Dc": "mdel", "25Dp": "mdel"}
    for name, (k, v) in marks.items():
        x, y = sc.x(k), sc.y(v)
        p.append(f"<circle cx='{_f(x)}' cy='{_f(y)}' r='4.5' "
                 f"class='{mark_cls[name]}'/>")
        p.append(f"<text x='{_f(x)}' y='{_f(y-8)}' class='mlab' "
                 f"text-anchor='middle'>{name} {v*100:.1f}%</text>")

    p.append("</svg>")
    return "".join(p), notes


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------

_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;background:#14171c;color:#dde2e8;
 margin:0;padding:18px}
h1{font-size:20px;margin:4px 0 2px}
h2{font-size:17px;margin:26px 0 8px;border-bottom:1px solid #333a44;
 padding-bottom:4px}
.meta{color:#8b96a5;font-size:12px}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(580px,1fr));
 gap:14px}
.card{background:#1b2028;border:1px solid #2a323e;border-radius:8px;
 padding:10px 12px}
.card h3{margin:0 0 4px;font-size:14px}
.badges{font-size:11px;color:#9fb0c3;margin-bottom:6px}
.badge{display:inline-block;background:#242c37;border-radius:4px;
 padding:1px 6px;margin-right:5px}
.badge.bad{background:#5a2430;color:#ffb4c0}
.badge.mode{background:#1f3a2a;color:#9fe0b3}
.badge.mode.degraded{background:#4a3a1a;color:#ffd88a}
svg.smile{width:100%;height:auto;background:#10141a;border-radius:6px}
.grid{stroke:#242b35;stroke-width:1}
.zero{stroke:#3d4a5c;stroke-width:1;stroke-dasharray:4 3}
.krange{stroke:#4a5568;stroke-width:1;stroke-dasharray:2 4}
.tick{fill:#7d8898;font-size:10px}
.axis{fill:#93a0b0;font-size:11px}
.ba{stroke:#5a6a80;stroke-width:1.4}
.mid.call{fill:#6fc3ff}
.mid.put{fill:#ffa14f}
.drop{fill:#ff5470}
.svi{fill:none;stroke:#59e0a0;stroke-width:2}
.matm{fill:none;stroke:#ffe14f;stroke-width:2}
.mdel{fill:none;stroke:#e08cff;stroke-width:2}
.mlab{fill:#c9d3df;font-size:10px}
.notes{font-size:11px;color:#e0a0a8;margin-top:4px;white-space:pre-wrap}
.legend{font-size:12px;color:#9fb0c3;margin:8px 0 14px}
.warn{color:#ffb4c0}
"""


def build_html(art, art_path, counters):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    out = []
    out.append("<!DOCTYPE html><html lang='uk'><head><meta charset='utf-8'>")
    out.append("<title>Vol-Edge smile report</title>")
    out.append(f"<style>{_CSS}</style></head><body>")
    out.append("<h1>Vol-Edge — візуальний QA фітів (smiles)</h1>")
    out.append(f"<div class='meta'>артефакт: {html.escape(art_path)} · "
               f"згенеровано: {now}</div>")
    out.append("<div class='legend'>"
               "<span style='color:#6fc3ff'>&#9679;</span> mid (call) · "
               "<span style='color:#ffa14f'>&#9679;</span> mid (put) · "
               "&#124; bid/ask · "
               "<span style='color:#59e0a0'>&#9472;</span> SVI (лише в межах "
               "даних +10%) · "
               "<span style='color:#ff5470'>&#9650;</span> відкинуте "
               "котирування · "
               "<span style='color:#ffe14f'>&#9675;</span> ATM-DNS(pa) · "
               "<span style='color:#e08cff'>&#9675;</span> 25&#916;(pa) · "
               "пунктирні вертикалі — межі даних k_range</div>")

    for cur in art:
        ccy = cur.get("currency", "?")
        out.append(f"<h2>{html.escape(ccy)} · index="
                   f"{cur.get('index_price')} · "
                   f"знімок {html.escape(str(cur.get('snapshot_utc')))} · "
                   f"calendar_violations={cur.get('calendar_violations')}"
                   f"</h2>")
        out.append("<div class='grid2'>")
        for sl in cur.get("slices", []):
            counters["slices_total"] += 1
            exp = html.escape(str(sl.get("expiry")))
            if sl.get("status") != "OK":
                counters["slices_not_ok"] += 1
                out.append(f"<div class='card'><h3>{exp}</h3>"
                           f"<p class='warn'>status="
                           f"{html.escape(str(sl.get('status')))} — "
                           f"зріз не зфітований в артефакті</p></div>")
                continue
            model, reason = rebuild_model(sl)
            svg, notes = slice_svg(sl, model, reason, counters)
            mode = str(sl.get("fit_mode"))
            mode_cls = "mode" if mode == "FULL_SVI" else "mode degraded"
            fc = sl.get("fit_counters", {})
            durr = sl.get("durrleman_bad_n", 0)
            durr_cls = "badge bad" if durr else "badge"
            drop_n = fc.get("n_dropped", 0)
            drop_cls = "badge bad" if drop_n else "badge"
            atm = sl.get("atm_dns_pa_iv")
            rr = sl.get("rr25_pa")
            bf = sl.get("bf25_pa")
            rr_s = f"{rr*100:+.2f}vp" if rr is not None else "n/a"
            bf_s = f"{bf*100:+.2f}vp" if bf is not None else "n/a"
            out.append(
                f"<div class='card'><h3>{exp} · d={sl['tau']*365:.1f} · "
                f"F={sl['forward']:.2f}</h3>"
                f"<div class='badges'>"
                f"<span class='badge {mode_cls}'>{html.escape(mode)}</span>"
                f"<span class='badge'>sse={sl.get('fit_sse'):.3g}</span>"
                f"<span class='{durr_cls}'>durr={durr}</span>"
                f"<span class='badge'>used={fc.get('n_used')}/"
                f"{fc.get('n_in')}</span>"
                f"<span class='{drop_cls}'>fit_dropped={drop_n}</span>"
                f"<span class='badge'>ATM={atm*100:.2f}%</span>"
                f"<span class='badge'>RR25pa={rr_s}</span>"
                f"<span class='badge'>BF25pa={bf_s}</span>"
                f"</div>{svg}")
            if notes:
                out.append("<div class='notes'>"
                           + html.escape("\n".join(notes)) + "</div>")
            out.append("</div>")
        out.append("</div>")

    out.append("<h2>Лічильники звіту</h2><div class='meta'><pre>"
               + html.escape(json.dumps(counters, ensure_ascii=False,
                                        indent=1))
               + "</pre></div>")
    out.append("</body></html>")
    return "\n".join(out)


def new_counters():
    return {"slices_total": 0, "slices_not_ok": 0, "slices_no_model": 0,
            "rows_ok": 0, "rows_dropped": 0, "dropped_no_k": 0,
            "delta_unreachable": 0}


# ----------------------------------------------------------------------------
# Самотести (синтетика, детерміновано, без мережі та файлів проєкту)
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


def _synthetic_slice():
    tau = 0.25
    params = (0.02, 0.4, -0.3, 0.0, 0.3)
    m = SliceModel("svi", tau, params=params)
    rows = []
    for i in range(9):
        k = -0.4 + 0.1 * i
        iv = m.iv(k)
        rows.append({"K": 60000.0 * math.exp(k), "cp": -1 if k < 0 else 1,
                     "k": k, "iv_mid": iv, "iv_bid": iv - 0.004,
                     "iv_ask": iv + 0.004, "status": "OK"})
    rows.append({"K": 100000.0, "cp": 1, "status": "iv_fail: synthetic"})
    return {"expiry": "TEST", "tau": tau, "status": "OK", "forward": 60000.0,
            "fit_mode": "FULL_SVI", "fit_sse": 0.0,
            "fit_counters": {"n_in": 10, "n_used": 9, "n_dropped": 1},
            "svi_params": list(params), "atm_dns_pa_iv": m.iv(0.0),
            "rr25_pa": -0.01, "bf25_pa": 0.005, "durrleman_bad_n": 0,
            "rows": rows}


def run_self_tests():
    tc = TestCounters()
    sl = _synthetic_slice()

    model, reason = rebuild_model(sl)
    tc.check("rebuild_ok", model is not None and reason == "OK", reason)
    tc.check("k_range", model is not None
             and abs(model.k_range[0] + 0.4) < 1e-12
             and abs(model.k_range[1] - 0.4) < 1e-12,
             str(getattr(model, "k_range", None)))

    sl2 = dict(sl)
    sl2["svi_params"] = None
    sl2["fit_mode"] = "PARABOLA"
    m2, r2 = rebuild_model(sl2)
    tc.check("no_model_explicit", m2 is None and "NO_MODEL" in r2, r2)

    c = new_counters()
    svg, notes = slice_svg(sl, model, "OK", c)
    tc.check("svg_tag", svg.startswith("<svg") and svg.endswith("</svg>"))
    tc.check("svg_curve", "class='svi'" in svg)
    tc.check("svg_dropped_marker", "ВІДКИНУТО" in svg)
    tc.check("svg_atm_mark", "ATM" in svg)
    tc.check("counters_rows", c["rows_ok"] == 9 and c["rows_dropped"] == 1,
             str(c))
    tc.check("counters_no_k", c["dropped_no_k"] == 0, str(c))

    c2 = new_counters()
    svg2, notes2 = slice_svg(sl2, m2, r2, c2)
    tc.check("no_model_note", any("NO_MODEL" in n for n in notes2),
             str(notes2))
    tc.check("no_model_counter", c2["slices_no_model"] == 1, str(c2))
    tc.check("no_model_no_curve", "class='svi'" not in svg2)

    art = [{"currency": "SYN", "index_price": 60000.0,
            "snapshot_utc": "test", "calendar_violations": 0,
            "slices": [sl, {"expiry": "BAD", "tau": 0.1,
                            "status": "fit_fail"}]}]
    c3 = new_counters()
    page = build_html(art, "synthetic", c3)
    tc.check("html_doc", page.startswith("<!DOCTYPE html"))
    tc.check("html_not_ok_slice", c3["slices_not_ok"] == 1, str(c3))
    tc.check("html_escape", "<script" not in page.lower())

    ts = _ticks(0.3, 0.7)
    tc.check("ticks_inside", all(0.3 - 1e-9 <= t <= 0.7 + 1e-9 for t in ts)
             and len(ts) >= 3, str(ts))

    # --- Перевірка змінних оточення (quiet=True: самотест не сміє
    # смітити в живий лог) ---
    import re as _envre
    _unk = warn_unknown_env(KNOWN_ENV, environ={"VOLEDGE_ARTIFACT": "/tmp/a.json", "VOLEDGE_ARTIFACTS": "/tmp"}, quiet=True)
    tc.check("env_warn_flags_typo", _unk == ["VOLEDGE_ARTIFACTS"], str(_unk))
    tc.check("env_warn_ignores_known", "VOLEDGE_ARTIFACT" not in _unk)
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

    print(f"[TEST] passed {tc.passed}/{tc.run}")
    for f_ in tc.failures:
        print(f"[TEST] FAILURE detail: {f_}")
    return tc


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------


def main() -> int:
    # ПЕРЕД розбором значень: нерозпізнана змінна = тихий дефолт.
    warn_unknown_env(KNOWN_ENV, label="smile_report")
    offline = os.environ.get("VOLEDGE_OFFLINE", "0") == "1"
    print(f"[ENV] VOLEDGE_OFFLINE={'1' if offline else '0'}")
    tc = run_self_tests()
    if tc.passed != tc.run:
        print("[SUMMARY] САМОТЕСТИ ПРОВАЛЕНІ — звіт не генерується")
        return 1
    if offline:
        print("[SUMMARY] offline-режим: лише самотести")
        return 0

    art_env = os.environ.get("VOLEDGE_ARTIFACT", "")
    print(f"[ENV] VOLEDGE_ARTIFACT={art_env or '(auto: останній)'}")
    if art_env:
        art_path = art_env
        if not os.path.isfile(art_path):
            print(f"[SUMMARY] артефакт не знайдено: {art_path}")
            return 1
    else:
        chain_dir = os.path.join(_ROOT, "artifacts", "chain")
        art_path = find_latest_artifact(chain_dir)
        if art_path is None:
            print(f"[SUMMARY] нуль артефактів у {chain_dir}")
            return 1
    print(f"[OVERALL] артефакт: {art_path}")
    art = load_artifact(art_path)

    counters = new_counters()
    page = build_html(art, art_path, counters)

    rep_dir = os.environ.get("VOLEDGE_REPORT_DIR",
                             os.path.join(_ROOT, "reports"))
    print(f"[ENV] VOLEDGE_REPORT_DIR={rep_dir}")
    os.makedirs(rep_dir, exist_ok=True)
    base = os.path.basename(art_path)
    stamp = base.replace("chain_", "").replace(".json", "") \
                .replace(".gz", "")
    out_path = os.path.join(rep_dir, f"smile_{stamp}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"[OVERALL] звіт: {out_path} "
          f"({os.path.getsize(out_path)} байт)")
    print(f"[SUMMARY] лічильники: {json.dumps(counters, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    rc = 2
    try:
        rc = main()
    except Exception:
        traceback.print_exc()
        rc = 2
    finally:
        # input() у finally + явний except EOFError — конвенція проєкту.
        # Тут вона була порушена (input поза try): під </dev/null у
        # cron-обгортці модуль друкував traceback EOFError у лог.
        # Спіймано 24 сер; у cron smile_report ще не стоїть, але
        # пункт 7 STATE.md це передбачає.
        try:
            input(f"\n[EXIT rc={rc}] Натисніть Enter для виходу...")
        except EOFError:
            pass
    sys.exit(rc)
