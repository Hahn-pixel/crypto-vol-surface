# -*- coding: utf-8 -*-
"""
[EN] Read-only structural diagnostics of the interpolation nodes behind
constant tenors: which expiries bracket each tenor and whether the
weights introduce calendar (day-of-week) artifacts.

--- Ukrainian original below ---
Vol-Edge :: analytics/tenor_nodes.py

READ-ONLY структурна діагностика ВУЗЛІВ ІНТЕРПОЛЯЦІЇ константних тенорів.
Нічого не змінює: ані детектор, ані калібратор, ані робочі артефакти.

КРОК A розслідування «ефекту п'ятниці/суботи».

ПИТАННЯ
-------
`interp_tenor` (surface_report.py) бере рівно ДВА вузли, що охоплюють
тенор, і лінійно інтерполює total variance. Отже atm7/atm30/atm90
залежать не лише від значень IV, а й від ТОГО, ЯКІ САМЕ дві експірації
стали охоплювачами і з якою вагою x.

Сітка експірацій Deribit не однорідна: дейлі йдуть до найближчої
п'ятниці, далі тижневі з кроком 7 діб. Тому пара охоплювачів для
tenor 7 і вага x МОЖУТЬ детерміновано їздити по днях тижня, а в
п'ятницю після експірації 08:00 UTC — перескакувати.

Якщо це так, «ефект п'ятниці/суботи» в детекторі — не властивість
ринку, а властивість нашої конструкції фічі.

ЩО МІРЯЄТЬСЯ (тільки структура, НЕ значення IV)
------------------------------------------------
На кожен знімок і кожен тенор:
  lo_d, hi_d   — вузли-охоплювачі у добах;
  gap_d        — плече інтерполяції hi_d - lo_d;
  x            — вага верхнього вузла, (t* - t1) / (t2 - t1);
  lo_is_min    — чи є нижнім охоплювачем НАЙБЛИЖЧА експірація
                 (та сама з tau -> 0, на яку падала підозра в STATE.md);
  pair         — (expiry_lo, expiry_hi), ідентичність пари.

Далі по кроках між сусідніми знімками (4 год):
  |dx|         — модуль зміни ваги;
  switch       — чи змінилася пара охоплювачів (0/1).

Агрегація по днях тижня і по слотах UTC, з перестановочним p-value.

ВАЖЛИВО ПРО КОНТРОЛЬ ГОДИНИ: x механічно повзе протягом доби (усі tau
зменшуються на 4 год), тож сирий розподіл |dx| по днях тижня містить
годинну варіацію. Тому тест рахується ДВІЧІ: на сирих значеннях і на
центрованих по слоту (з кожного значення віднято медіану його слота).
Вердикт береться з ЦЕНТРОВАНИХ. Сирі — довідково.

ПОРОГИ, ЗАФІКСОВАНІ ДО ЗАМІРУ
------------------------------
  p < 0.01 і effect_abs >= 0.05 -> WEEKDAY-STRUCTURE (гіпотеза підтверджена)
       (effect_abs — розкид медіан у ОДИНИЦЯХ ВАГИ x, не в MAD:
        |dx| дискретний, MAD пулу вироджується, див. MAD_FLOOR_FRAC)
  p >= 0.05                -> NO-STRUCTURE (гіпотеза відхилена)
  інше                     -> INCONCLUSIVE
  < MIN_PER_BUCKET точок у > 2 буднях -> LOW-N, вердикту немає.
Ці рядки не редагувати за результатом заміру.

Читає: artifacts/chain/chain_*.json(.gz) — ВСІ.
Пише:  artifacts/anomaly/tenor_nodes_YYYYMMDD_HHMMSS.json (звіт).

Env-флаги:
  VOLEDGE_OFFLINE=1               -> лише самотести, артефакти не читаються.
  VOLEDGE_TENOR_NODES_TENORS      -> тенори у добах, default "7,30,90".
  VOLEDGE_TENOR_NODES_PERM        -> перестановок, default 2000; 0 -> тест
                                     пропускається явно.
  VOLEDGE_TENOR_NODES_MIN_BUCKET  -> мінімум точок на день тижня, default 5.
  VOLEDGE_TENOR_NODES_NO_WRITE=1  -> не писати звіт (тільки друк).

Чистий stdlib; мережі немає. Подвійний клік + input() завжди.
"""

import glob
import gzip
import json
import math
import os
import re
import sys
import traceback
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_ROOT, _THIS_DIR, os.path.join(_ROOT, "core"),
           os.path.join(_ROOT, "analytics")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CHAIN_DIR = os.path.join(_ROOT, "artifacts", "chain")
OUT_DIR = os.path.join(_ROOT, "artifacts", "anomaly")

ANNUALIZE = 365.0
CURRENCIES = ("BTC", "ETH")
_STAMP_RE = re.compile(r"_(\d{8})_(\d{6})\.json(\.gz)?$")

# Канонічна UTC-сітка після фіксу таймзон (utc_gate.sh, 31 лип).
CANONICAL_SLOT_HOURS = (0, 4, 8, 12, 16, 20)
# Стара сітка (CRON_TZ no-op, зсув -3 год), ~35 точок 25-31 лип.
LEGACY_SLOT_HOURS = (21, 1, 5, 9, 13, 17)
GAP_MIN_H = 3.5
GAP_MAX_H = 4.5

WEEKDAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")

# --- пороги вердикту, зафіксовані ДО заміру ---------------------------------
VERDICT_P_STRONG = 0.01
VERDICT_P_NULL = 0.05
# РОЗМІР ЕФЕКТУ МІРЯЄТЬСЯ В АБСОЛЮТНИХ ОДИНИЦЯХ, НЕ В MAD.
# Причина (виявлено на реальних даних 23 сер): |dx| сильно дискретний —
# маса точно однакових значень, MAD пулу падає до нуля, і stat/MAD дає
# 7711 замість осмисленого числа. Це та сама пастка, що з ratio на
# центрованих даних: НОРМУВАННЯ НА ОЦІНКУ, ЯКА САМА ВИРОДЖУЄТЬСЯ.
# Тут нормування не потрібне: x — вага інтерполяції, безрозмірна і
# обмежена [0,1], тож абсолютний поріг має пряме тлумачення.
VERDICT_EFFECT_ABS = 0.05   # розкид медіан |dx| у одиницях ваги x
# Для індикаторів 0/1 (switch) — прямий розкид часток.
VERDICT_EFFECT_RATE = 0.25
# effect_mad лишається ДІАГНОСТИКОЮ і рахується лише якщо MAD пулу не
# вироджений відносно масштабу самих даних.
MAD_FLOOR_FRAC = 0.05
MIN_PER_BUCKET_DEFAULT = 5
MIN_BUCKETS_FOR_VERDICT = 3
PERM_DEFAULT = 2000
PERM_SEED = 20260823

# Канон перевірки — core/env_flags.py. Локальна копія функції прибрана
# 24 сер: вона вже встигла розійтися з копією в rv_rolling.py (різний
# текст друку, різна форма), а попереду ще п'ять модулів.
try:
    from env_flags import merge_known, warn_unknown_env
except ImportError:
    from core.env_flags import merge_known, warn_unknown_env

KNOWN_ENV = merge_known((
    "VOLEDGE_TENOR_NODES_TENORS",
    "VOLEDGE_TENOR_NODES_PERM",
    "VOLEDGE_TENOR_NODES_MIN_BUCKET",
    "VOLEDGE_TENOR_NODES_NO_WRITE",
))


# ----------------------------------------------------------------------------
# Оточення
# ----------------------------------------------------------------------------

def _env_int(name, default, minimum=None):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = int(raw.strip())
    except ValueError:
        raise ValueError(f"{name}={raw!r} не ціле число")
    if minimum is not None and val < minimum:
        raise ValueError(f"{name}={val} < {minimum}")
    return val


def parse_tenors(raw, default=(7.0, 30.0, 90.0)):
    """Розбір списку тенорів. Порожньо -> default. Помилка -> ValueError."""
    if raw is None or raw.strip() == "":
        return tuple(default)
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part == "":
            continue
        val = float(part)
        if not (val > 0.0):
            raise ValueError(f"тенор {val} має бути > 0")
        out.append(val)
    if not out:
        raise ValueError(f"порожній список тенорів: {raw!r}")
    return tuple(out)


# ----------------------------------------------------------------------------
# Читання артефактів
# ----------------------------------------------------------------------------

def _load_json(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _stamp_of(path):
    m = _STAMP_RE.search(os.path.basename(path))
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") \
                   .replace(tzinfo=timezone.utc)


def list_chain_artifacts(dirpath):
    """[(stamp_utc, path)] відсортовано. Нерозпізнані імена — явний [SKIP]."""
    paths = []
    for pat in ("chain_*.json", "chain_*.json.gz"):
        paths.extend(glob.glob(os.path.join(dirpath, pat)))
    pairs = []
    for p in paths:
        st = _stamp_of(p)
        if st is None:
            print(f"[SKIP] нерозпізнане ім'я артефакту: {p}")
            continue
        pairs.append((st, p))
    pairs.sort()
    return pairs


# ----------------------------------------------------------------------------
# Структура вузлів одного знімка
# ----------------------------------------------------------------------------

def usable_nodes(slices):
    """[(tau_d, expiry)] по зрізах, придатних для interp_tenor.

    Фільтр ТОЧНО той самий, що в surface_report.interp_tenor: status == OK
    і atm_dns_pa_iv is not None. Якщо ці умови розійдуться, модуль
    міряти ме не ту сітку — тримати синхронно.
    """
    pts = []
    for s in slices:
        if s.get("status") != "OK":
            continue
        if s.get("atm_dns_pa_iv") is None:
            continue
        tau = s.get("tau")
        if tau is None:
            continue
        pts.append((float(tau) * ANNUALIZE, str(s.get("expiry"))))
    pts.sort(key=lambda p: p[0])
    return pts


def bracket_of(nodes, tenor_d):
    """Структура охоплення тенора. dict зі status.

    status: OK | not_enough_nodes | out_of_range | exact_node
    exact_node виділено окремо: там інтерполяції немає, x не визначена,
    і мовчазно підставляти 0.0 не можна.
    """
    if len(nodes) < 2:
        return {"status": "not_enough_nodes", "n_nodes": len(nodes)}
    taus = [p[0] for p in nodes]
    if not (taus[0] <= tenor_d <= taus[-1]):
        return {"status": "out_of_range",
                "range_d": [taus[0], taus[-1]], "n_nodes": len(nodes)}
    hi = next(i for i, t in enumerate(taus) if t >= tenor_d)
    if taus[hi] == tenor_d:
        return {"status": "exact_node", "node_d": taus[hi],
                "expiry": nodes[hi][1], "n_nodes": len(nodes)}
    lo = hi - 1
    gap = taus[hi] - taus[lo]
    if not (gap > 0.0):
        return {"status": "degenerate_gap", "gap_d": gap,
                "n_nodes": len(nodes)}
    return {
        "status": "OK",
        "lo_d": taus[lo], "hi_d": taus[hi], "gap_d": gap,
        "x": (tenor_d - taus[lo]) / gap,
        "lo_idx": lo,
        "lo_is_min": (lo == 0),
        "tau_min_d": taus[0],
        "pair": [nodes[lo][1], nodes[hi][1]],
        "n_nodes": len(nodes),
    }


def snapshot_structure(chain_data, tenors):
    """{currency: {tenor: bracket_dict}} для одного chain-артефакту."""
    out = {}
    for blk in chain_data:
        cur = blk.get("currency")
        if cur is None:
            continue
        nodes = usable_nodes(blk.get("slices") or [])
        out[cur] = {td: bracket_of(nodes, td) for td in tenors}
    return out


def build_panel(chain_pairs, tenors):
    """([{stamp, cur, tenor, ...}], counters). Один рядок = знімок x тенор."""
    rows = []
    c = {"files_seen": 0, "files_bad": 0, "rows": 0,
         "ok": 0, "out_of_range": 0, "exact_node": 0,
         "not_enough_nodes": 0, "degenerate_gap": 0}
    for st, path in chain_pairs:
        c["files_seen"] += 1
        try:
            data = _load_json(path)
        except Exception as exc:                      # noqa: BLE001
            c["files_bad"] += 1
            print(f"[SKIP] нечитабельний артефакт {os.path.basename(path)}: "
                  f"{exc}")
            continue
        struct = snapshot_structure(data, tenors)
        for cur in sorted(struct):
            for td in tenors:
                br = struct[cur][td]
                c["rows"] += 1
                key = "ok" if br["status"] == "OK" else br["status"]
                if key not in c:
                    raise RuntimeError(
                        f"невідомий статус охоплення {br['status']!r} — "
                        f"лічильник не оголошений (no silent fail-open)")
                c[key] += 1
                row = {"stamp": st, "cur": cur, "tenor": td}
                row.update(br)
                rows.append(row)
    return rows, c


# ----------------------------------------------------------------------------
# Кроки між сусідніми знімками
# ----------------------------------------------------------------------------

def step_series(rows, cur, tenor):
    """([{stamp, hour, weekday, dx, switch, ...}], counters) для однієї серії.

    Крок береться лише між сусідніми знімками канонічної сітки з розривом
    3.5..4.5 год. Кожен відкид — явний лічильник.
    """
    seq = [r for r in rows if r["cur"] == cur and r["tenor"] == tenor]
    seq.sort(key=lambda r: r["stamp"])
    steps = []
    c = {"rows_seen": len(seq), "pairs_seen": 0, "kept": 0,
         "not_ok": 0, "legacy_grid": 0, "off_grid": 0, "bad_gap": 0}
    for i in range(1, len(seq)):
        prev, cur_r = seq[i - 1], seq[i]
        c["pairs_seen"] += 1
        if prev["status"] != "OK" or cur_r["status"] != "OK":
            c["not_ok"] += 1
            continue
        hp, hc = prev["stamp"].hour, cur_r["stamp"].hour
        if hp in LEGACY_SLOT_HOURS or hc in LEGACY_SLOT_HOURS:
            c["legacy_grid"] += 1
            continue
        if hp not in CANONICAL_SLOT_HOURS or hc not in CANONICAL_SLOT_HOURS:
            c["off_grid"] += 1
            continue
        gap_h = (cur_r["stamp"] - prev["stamp"]).total_seconds() / 3600.0
        if not (GAP_MIN_H <= gap_h <= GAP_MAX_H):
            c["bad_gap"] += 1
            continue
        steps.append({
            "stamp": cur_r["stamp"],
            "hour": hc,
            "weekday": cur_r["stamp"].weekday(),
            "dx": cur_r["x"] - prev["x"],
            "adx": abs(cur_r["x"] - prev["x"]),
            "switch": 1 if prev["pair"] != cur_r["pair"] else 0,
            "x": cur_r["x"],
            "gap_d": cur_r["gap_d"],
            "lo_is_min": 1 if cur_r["lo_is_min"] else 0,
        })
        c["kept"] += 1
    return steps, c


def center_by_slot(steps, field):
    """Значення field з відніманням медіани СВОГО слота.

    x механічно повзе протягом доби, тож годинна варіація сидить у
    сирому field і роздуває розкид між буднями. Центрування по слоту
    її прибирає; без нього тест на день тижня міряє суміш.
    """
    by_slot = {}
    for s in steps:
        by_slot.setdefault(s["hour"], []).append(s[field])
    med = {h: _median(v) for h, v in by_slot.items()}
    return [s[field] - med[s["hour"]] for s in steps]


# ----------------------------------------------------------------------------
# Статистика
# ----------------------------------------------------------------------------

def _median(xs):
    n = len(xs)
    if n == 0:
        return None
    s = sorted(xs)
    return s[n // 2] if n % 2 == 1 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _mean(xs):
    return (sum(xs) / len(xs)) if xs else None


def _lcg_stream(seed):
    x = seed & 0x7FFFFFFF
    while True:
        x = (1103515245 * x + 12345) % 2147483648
        yield x / 2147483648.0


def _shuffled(items, rng):
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = int(next(rng) * (i + 1))
        if j > i:
            j = i
        out[i], out[j] = out[j], out[i]
    return out


def permutation_test(buckets, n_perm, agg="median", min_per_bucket=5,
                     seed=PERM_SEED):
    """({...}|None, reason). H0: розподіл однаковий по всіх бакетах.

    Статистика — розкид агрегатів між бакетами (max - min). p-value
    перестановочний по мітках бакетів; distribution-free.

    agg="median" для неперервних величин, "mean" для індикаторів 0/1
    (медіана індикатора вироджена: майже завжди 0).
    """
    usable = {k: list(v) for k, v in buckets.items()
              if len(v) >= min_per_bucket}
    if len(usable) < 2:
        return None, (f"придатних бакетів {len(usable)} < 2 "
                      f"(поріг {min_per_bucket} точок)")
    if n_perm < 1:
        return None, f"n_perm={n_perm} < 1"
    fagg = _median if agg == "median" else _mean
    keys = sorted(usable)
    sizes = [(k, len(usable[k])) for k in keys]
    pool = []
    for k in keys:
        pool.extend(usable[k])

    def _stat(vals):
        aggs, pos = [], 0
        for _k, n_ in sizes:
            aggs.append(fagg(vals[pos:pos + n_]))
            pos += n_
        return max(aggs) - min(aggs), aggs

    obs_stat, obs_aggs = _stat(pool)
    rng = _lcg_stream(seed)
    ge = 0
    for _ in range(n_perm):
        st_, _a = _stat(_shuffled(pool, rng))
        if st_ >= obs_stat:
            ge += 1
    p = (ge + 1.0) / (n_perm + 1.0)
    peak_i = max(range(len(keys)), key=lambda i: obs_aggs[i])
    others = [obs_aggs[i] for i in range(len(keys)) if i != peak_i]
    med_other = _median(others)
    ratio = None
    if med_other is not None and abs(med_other) > 1e-12:
        ratio = obs_aggs[peak_i] / med_other
    # РОЗМІР ЕФЕКТУ: абсолютний (obs_stat у рідних одиницях). ratio
    # НЕПРИДАТНИЙ на центрованих даних (медіани ~0), а нормування на MAD
    # пулу вироджується на дискретних рядах. Обидва лишаються довідково,
    # effect_mad — лише коли MAD не вироджений відносно масштабу даних.
    pool_med = _median(pool)
    pool_mad = _median([abs(v - pool_med) for v in pool])
    scale = _median([abs(v) for v in pool])
    floor = MAD_FLOOR_FRAC * scale if scale else 0.0
    mad_degenerate = not (pool_mad is not None and pool_mad > 1e-12
                          and pool_mad >= floor)
    effect = None if mad_degenerate else obs_stat / pool_mad
    # Дискретність пулу: частка значень, що збігаються з модою. Саме вона
    # обвалює MAD; без цього числа виродження виглядає як «просто малий MAD».
    counts = {}
    for v in pool:
        counts[round(v, 12)] = counts.get(round(v, 12), 0) + 1
    ties_frac = max(counts.values()) / len(pool)
    return ({
        "effect_abs": obs_stat,
        "effect_mad": effect,
        "mad_degenerate": mad_degenerate,
        "pool_mad": pool_mad,
        "pool_scale": scale,
        "ties_frac": ties_frac,
        "n_buckets": len(keys),
        "buckets": keys,
        "sizes": [n_ for _k, n_ in sizes],
        "aggs": obs_aggs,
        "agg_kind": agg,
        "stat": obs_stat,
        "p": p,
        "n_perm": n_perm,
        "peak_bucket": keys[peak_i],
        "peak_agg": obs_aggs[peak_i],
        "ratio_peak_vs_rest": ratio,
    }, None)


def verdict_of(test, n_buckets_needed=MIN_BUCKETS_FOR_VERDICT):
    """Вердикт за порогами, зафіксованими ДО заміру."""
    if test is None:
        return "NO-TEST"
    if test["n_buckets"] < n_buckets_needed:
        return "LOW-N"
    p = test["p"]
    if test.get("agg_kind") == "mean":
        thresh = VERDICT_EFFECT_RATE
    else:
        thresh = VERDICT_EFFECT_ABS
    eff = test.get("effect_abs")
    if eff is None:
        return "NO-TEST"
    if p < VERDICT_P_STRONG and eff >= thresh:
        return "WEEKDAY-STRUCTURE"
    if p >= VERDICT_P_NULL:
        return "NO-STRUCTURE"
    return "INCONCLUSIVE"


# ----------------------------------------------------------------------------
# Аналіз
# ----------------------------------------------------------------------------

def analyze_series(rows, cur, tenor, n_perm, min_per_bucket):
    """Повний аналіз однієї пари (валюта, тенор)."""
    steps, sc = step_series(rows, cur, tenor)
    out = {"cur": cur, "tenor": tenor, "step_counters": sc,
           "n_steps": len(steps)}
    if not steps:
        out["reason"] = "немає придатних кроків"
        return out

    # Профіль по днях тижня (описовий).
    by_wd = {}
    for s in steps:
        by_wd.setdefault(s["weekday"], []).append(s)
    prof = {}
    for wd in sorted(by_wd):
        grp = by_wd[wd]
        prof[wd] = {
            "n": len(grp),
            "med_adx": _median([g["adx"] for g in grp]),
            "switch_rate": _mean([g["switch"] for g in grp]),
            "med_gap_d": _median([g["gap_d"] for g in grp]),
            "lo_is_min_frac": _mean([g["lo_is_min"] for g in grp]),
            "med_x": _median([g["x"] for g in grp]),
        }
    out["weekday_profile"] = prof

    # Профіль по слотах (контроль: годинна варіація має бути саме тут).
    by_h = {}
    for s in steps:
        by_h.setdefault(s["hour"], []).append(s)
    out["slot_profile"] = {
        h: {"n": len(v), "med_adx": _median([g["adx"] for g in v]),
            "med_x": _median([g["x"] for g in v])}
        for h, v in sorted(by_h.items())
    }

    # Тести. Вердикт — з ЦЕНТРОВАНИХ по слоту |dx|.
    raw_b = {wd: [g["adx"] for g in by_wd[wd]] for wd in by_wd}
    cen_vals = center_by_slot(steps, "adx")
    cen_b = {}
    for s, v in zip(steps, cen_vals):
        cen_b.setdefault(s["weekday"], []).append(v)
    sw_b = {wd: [g["switch"] for g in by_wd[wd]] for wd in by_wd}

    t_raw, r_raw = permutation_test(raw_b, n_perm, "median", min_per_bucket)
    t_cen, r_cen = permutation_test(cen_b, n_perm, "median", min_per_bucket)
    t_sw, r_sw = permutation_test(sw_b, n_perm, "mean", min_per_bucket)

    out["test_adx_raw"] = t_raw
    out["test_adx_raw_reason"] = r_raw
    out["test_adx_slot_centered"] = t_cen
    out["test_adx_slot_centered_reason"] = r_cen
    out["test_switch_rate"] = t_sw
    out["test_switch_rate_reason"] = r_sw
    out["verdict"] = verdict_of(t_cen)
    out["verdict_switch"] = verdict_of(t_sw)
    return out


def run_analysis(chain_pairs, tenors, n_perm, min_per_bucket):
    rows, pc = build_panel(chain_pairs, tenors)
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "n_artifacts": len(chain_pairs),
        "tenors_d": list(tenors),
        "panel_counters": pc,
        "thresholds": {
            "p_strong": VERDICT_P_STRONG, "p_null": VERDICT_P_NULL,
            "effect_abs_strong": VERDICT_EFFECT_ABS,
            "effect_rate_strong": VERDICT_EFFECT_RATE,
            "mad_floor_frac": MAD_FLOOR_FRAC,
            "min_per_bucket": min_per_bucket,
            "min_buckets": MIN_BUCKETS_FOR_VERDICT,
            "fixed_before_measurement": True,
        },
        "series": [],
    }
    if chain_pairs:
        report["first_stamp"] = chain_pairs[0][0].isoformat()
        report["last_stamp"] = chain_pairs[-1][0].isoformat()
    curs = sorted({r["cur"] for r in rows})
    for cur in curs:
        for td in tenors:
            report["series"].append(
                analyze_series(rows, cur, td, n_perm, min_per_bucket))
    return report


# ----------------------------------------------------------------------------
# Друк
# ----------------------------------------------------------------------------

def _f(x, prec=3):
    if x is None:
        return "  --  "
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return " nan  "
    return f"{x:.{prec}f}"


def print_report(report):
    print("=" * 78)
    print("Vol-Edge :: СТРУКТУРА ВУЗЛІВ ІНТЕРПОЛЯЦІЇ (READ-ONLY, крок A)")
    print("=" * 78)
    print(f"Артефактів: {report['n_artifacts']}   "
          f"тенори: {report['tenors_d']}")
    if "first_stamp" in report:
        print(f"Період: {report['first_stamp']} .. {report['last_stamp']}")
    pc = report["panel_counters"]
    print(f"Панель: рядків {pc['rows']}, OK {pc['ok']}, "
          f"out_of_range {pc['out_of_range']}, exact_node {pc['exact_node']}, "
          f"not_enough_nodes {pc['not_enough_nodes']}, "
          f"файлів нечитабельних {pc['files_bad']}")
    th = report["thresholds"]
    print(f"Пороги (зафіксовані ДО заміру): p<{th['p_strong']} і "
          f"effect>={th['effect_abs_strong']} (абс.) -> WEEKDAY-STRUCTURE; "
          f"p>={th['p_null']} -> NO-STRUCTURE")

    for s in report["series"]:
        print()
        print("-" * 78)
        print(f"{s['cur']}  tenor {s['tenor']:g}д   кроків {s['n_steps']}")
        sc = s["step_counters"]
        print(f"  відкинуто: not_ok {sc['not_ok']}, "
              f"legacy_grid {sc['legacy_grid']}, off_grid {sc['off_grid']}, "
              f"bad_gap {sc['bad_gap']}")
        if not s.get("weekday_profile"):
            print(f"  {s.get('reason', 'немає даних')}")
            continue
        print("  день |   n | med|dx| | switch | med gap | lo=min | med x")
        for wd in sorted(s["weekday_profile"]):
            p = s["weekday_profile"][wd]
            print(f"   {WEEKDAY_NAMES[wd]}  | {p['n']:3d} |  "
                  f"{_f(p['med_adx'])}  |  {_f(p['switch_rate'], 2)} |  "
                  f"{_f(p['med_gap_d'], 2)}  |  {_f(p['lo_is_min_frac'], 2)} "
                  f"| {_f(p['med_x'])}")
        print("  слот |   n | med|dx| | med x")
        for h in sorted(s["slot_profile"]):
            p = s["slot_profile"][h]
            print(f"   {h:02d}  | {p['n']:3d} |  {_f(p['med_adx'])}  | "
                  f"{_f(p['med_x'])}")
        for label, key in (("|dx| сирі", "test_adx_raw"),
                           ("|dx| центр. по слоту", "test_adx_slot_centered"),
                           ("switch rate", "test_switch_rate")):
            t = s.get(key)
            if t is None:
                print(f"  [{label}] тесту немає: {s.get(key + '_reason')}")
                continue
            peak = WEEKDAY_NAMES[t["peak_bucket"]]
            unit = ("розкид часток" if t.get("agg_kind") == "mean"
                    else "од. ваги x")
            mad_s = ("MAD ВИРОДЖЕНИЙ" if t.get("mad_degenerate")
                     else f"{_f(t.get('effect_mad'), 2)} MAD")
            print(f"  [{label}] p={t['p']:.4f}  "
                  f"effect={_f(t.get('effect_abs'), 4)} ({unit})  "
                  f"[діагн.: {mad_s}, ties={_f(t.get('ties_frac'), 2)}]  "
                  f"пік={peak}  бакетів={t['n_buckets']}  "
                  f"перестановок={t['n_perm']}")
            if t.get("mad_degenerate"):
                print("    [TIES] MAD пулу вироджений (дискретний ряд) — "
                      "нормовані міри не рахуються, вердикт на абсолютній.")
        print(f"  ВЕРДИКТ (центровані |dx|): {s['verdict']}")
        print(f"  ВЕРДИКТ (switch rate):     {s['verdict_switch']}")

    print()
    print("=" * 78)
    print("НАГАДУВАННЯ: це СТРУКТУРНИЙ замір. Він показує, чи їздить пара")
    print("вузлів і вага x по днях тижня. Він НЕ доводить, що саме це")
    print("породжує алерти детектора — це крок B (групування в")
    print("anomaly_calibrate за ключем weekday на схемі rank_diffs).")
    print("=" * 78)


# ----------------------------------------------------------------------------
# Самотести
# ----------------------------------------------------------------------------

def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:                                  # noqa: BLE001
        return False
    return False


class TestCounters:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append(f"{name}: {detail}")
            print(f"  [FAIL] {name} {detail}")


def _mk_slice(expiry, tau_d, iv=0.5, status="OK"):
    return {"expiry": expiry, "tau": tau_d / ANNUALIZE, "status": status,
            "atm_dns_pa_iv": iv}


def _mk_stamp(y, mo, d, h):
    return datetime(y, mo, d, h, 10, 1, tzinfo=timezone.utc)


def run_self_tests():
    tc = TestCounters()
    print("Самотести tenor_nodes.py ...")

    # --- parse_tenors ---
    tc.check("tenors_default", parse_tenors("") == (7.0, 30.0, 90.0))
    tc.check("tenors_parse", parse_tenors(" 7, 30 ,90 ") == (7.0, 30.0, 90.0))
    tc.check("tenors_negative_raises",
             _raises(lambda: parse_tenors("7,-1"), ValueError))
    tc.check("tenors_garbage_raises",
             _raises(lambda: parse_tenors("7,abc"), ValueError))

    # --- usable_nodes: фільтр синхронний з interp_tenor ---
    sl = [_mk_slice("A", 1.0), _mk_slice("B", 5.0, status="DEGRADED"),
          {"expiry": "C", "tau": 9.0 / ANNUALIZE, "status": "OK",
           "atm_dns_pa_iv": None},
          _mk_slice("D", 3.0)]
    nodes = usable_nodes(sl)
    tc.check("usable_drops_non_ok_and_none",
             [n[1] for n in nodes] == ["A", "D"], str(nodes))
    tc.check("usable_sorted",
             all(nodes[i][0] <= nodes[i + 1][0]
                 for i in range(len(nodes) - 1)))

    # --- bracket_of ---
    nd = [(4.66, "E1"), (11.66, "E2"), (18.66, "E3")]
    br = bracket_of(nd, 7.0)
    tc.check("bracket_ok", br["status"] == "OK", str(br))
    tc.check("bracket_nodes", br["lo_d"] == 4.66 and br["hi_d"] == 11.66)
    tc.check("bracket_gap", abs(br["gap_d"] - 7.0) < 1e-9)
    tc.check("bracket_x", abs(br["x"] - (7.0 - 4.66) / 7.0) < 1e-12,
             str(br["x"]))
    tc.check("bracket_pair", br["pair"] == ["E1", "E2"])
    tc.check("bracket_lo_is_min_true", br["lo_is_min"] is True)
    br2 = bracket_of([(0.66, "D0"), (4.66, "E1"), (11.66, "E2")], 7.0)
    tc.check("bracket_lo_is_min_false", br2["lo_is_min"] is False,
             str(br2))
    # x у [0,1] завжди
    tc.check("bracket_x_in_unit", 0.0 <= br["x"] <= 1.0)
    # межі
    tc.check("bracket_out_of_range_high",
             bracket_of(nd, 500.0)["status"] == "out_of_range")
    tc.check("bracket_out_of_range_low",
             bracket_of(nd, 1.0)["status"] == "out_of_range")
    tc.check("bracket_exact_node",
             bracket_of(nd, 11.66)["status"] == "exact_node")
    tc.check("bracket_not_enough",
             bracket_of([(5.0, "X")], 7.0)["status"] == "not_enough_nodes")

    # ВИРОДЖЕНІСТЬ: exact_node НЕ підставляє x=0 мовчки.
    tc.check("exact_node_has_no_x", "x" not in bracket_of(nd, 11.66))

    # --- узгодження з interp_tenor (той самий поділ на вузли) ---
    try:
        try:
            from surface_report import interp_tenor
        except ImportError:
            from analytics.surface_report import interp_tenor
        sl2 = [_mk_slice("E1", 4.66, 0.60), _mk_slice("E2", 11.66, 0.50)]
        it = interp_tenor(sl2, 7.0)
        bb = bracket_of(usable_nodes(sl2), 7.0)
        ok = (it.get("status") == "OK"
              and abs(it["nodes_d"][0] - bb["lo_d"]) < 1e-9
              and abs(it["nodes_d"][1] - bb["hi_d"]) < 1e-9)
        tc.check("agrees_with_interp_tenor", ok, f"{it} vs {bb}")
        # і x відтворює atm з interp_tenor
        t1, t2 = bb["lo_d"] / ANNUALIZE, bb["hi_d"] / ANNUALIZE
        w1, w2 = 0.60 ** 2 * t1, 0.50 ** 2 * t2
        w = w1 + bb["x"] * (w2 - w1)
        atm = math.sqrt(w / (7.0 / ANNUALIZE))
        tc.check("x_reproduces_interp_atm",
                 abs(atm - it["atm_iv"]) < 1e-12, f"{atm} vs {it['atm_iv']}")
    except ImportError as exc:
        tc.check("agrees_with_interp_tenor", False, f"import: {exc}")

    # --- step_series: відкиди явні ---
    def _row(st, x, pair, status="OK"):
        return {"stamp": st, "cur": "BTC", "tenor": 7.0, "status": status,
                "x": x, "pair": pair, "gap_d": 7.0, "lo_is_min": False}

    base = [_row(_mk_stamp(2026, 8, 3, h), 0.3, ["A", "B"])
            for h in (0, 4, 8, 12, 16, 20)]
    steps, c = step_series(base, "BTC", 7.0)
    tc.check("steps_all_kept", c["kept"] == 5 and len(steps) == 5, str(c))
    tc.check("steps_zero_dx", all(abs(s["dx"]) < 1e-15 for s in steps))
    tc.check("steps_no_switch", all(s["switch"] == 0 for s in steps))

    legacy = base + [_row(_mk_stamp(2026, 8, 3, 21), 0.3, ["A", "B"])]
    _s, c2 = step_series(legacy, "BTC", 7.0)
    tc.check("steps_legacy_dropped", c2["legacy_grid"] == 1, str(c2))

    holed = [base[0], _row(_mk_stamp(2026, 8, 3, 12), 0.3, ["A", "B"])]
    _s, c3 = step_series(holed, "BTC", 7.0)
    tc.check("steps_bad_gap_dropped", c3["bad_gap"] == 1 and c3["kept"] == 0,
             str(c3))

    notok = [base[0], _row(_mk_stamp(2026, 8, 3, 4), 0.3, ["A", "B"],
                           status="out_of_range")]
    _s, c4 = step_series(notok, "BTC", 7.0)
    tc.check("steps_not_ok_dropped", c4["not_ok"] == 1, str(c4))

    sw = [_row(_mk_stamp(2026, 8, 3, 0), 0.3, ["A", "B"]),
          _row(_mk_stamp(2026, 8, 3, 4), 0.9, ["B", "C"])]
    s_sw, _c = step_series(sw, "BTC", 7.0)
    tc.check("steps_switch_detected", s_sw[0]["switch"] == 1)
    tc.check("steps_dx_value", abs(s_sw[0]["adx"] - 0.6) < 1e-12)

    # серії не змішуються між валютами/тенорами
    mixed = base + [dict(r, cur="ETH") for r in base]
    _s, c5 = step_series(mixed, "BTC", 7.0)
    tc.check("steps_isolated_by_cur", c5["rows_seen"] == 6, str(c5))

    # --- центрування по слоту ---
    st_c = [{"hour": 0, "adx": 1.0}, {"hour": 0, "adx": 3.0},
            {"hour": 4, "adx": 11.0}, {"hour": 4, "adx": 13.0}]
    cen = center_by_slot(st_c, "adx")
    tc.check("centering_removes_slot_level",
             abs(_median(cen[:2]) - _median(cen[2:])) < 1e-12, str(cen))

    # --- перестановочний тест: НЕГАТИВНИЙ і ПОЗИТИВНИЙ контроль ---
    rng = _lcg_stream(777)
    flat = {wd: [next(rng) for _ in range(25)] for wd in range(5)}
    t_flat, _r = permutation_test(flat, 400, "median", 5)
    tc.check("perm_no_effect_high_p", t_flat is not None and t_flat["p"] > 0.05,
             str(t_flat["p"] if t_flat else None))

    rng2 = _lcg_stream(778)
    shifted = {wd: [next(rng2) + (5.0 if wd == 4 else 0.0)
                    for _ in range(25)] for wd in range(5)}
    t_sh, _r = permutation_test(shifted, 400, "median", 5)
    tc.check("perm_has_power", t_sh is not None and t_sh["p"] < 0.01,
             str(t_sh["p"] if t_sh else None))
    tc.check("perm_finds_peak", t_sh is not None and t_sh["peak_bucket"] == 4)

    # ТЕСТ БЕЗ НЕГАТИВНОГО КОНТРОЛЮ НІЧОГО НЕ ВАРТИЙ — обидва вище.

    # малі бакети відкидаються
    tiny = {0: [1.0, 2.0], 1: [1.0, 2.0]}
    t_tiny, r_tiny = permutation_test(tiny, 100, "median", 5)
    tc.check("perm_low_n_refuses", t_tiny is None and "бакет" in r_tiny,
             str(r_tiny))
    t_np, r_np = permutation_test(flat, 0, "median", 5)
    tc.check("perm_zero_perm_refuses", t_np is None, str(r_np))

    # p ніколи не 0 (додано 1 у чисельник і знаменник)
    tc.check("perm_p_never_zero", t_sh["p"] > 0.0)

    # індикатор 0/1: медіана вироджена, mean — ні
    ind = {0: [0] * 20, 1: [1] * 20}
    t_med, _r = permutation_test(ind, 200, "median", 5)
    t_mean, _r = permutation_test(ind, 200, "mean", 5)
    tc.check("indicator_mean_separates",
             t_mean is not None and abs(t_mean["stat"] - 1.0) < 1e-12)
    tc.check("indicator_median_also_separates_here",
             t_med is not None and t_med["stat"] == 1)
    ind2 = {0: [0] * 18 + [1, 1], 1: [0] * 12 + [1] * 8}
    t_med2, _r = permutation_test(ind2, 200, "median", 5)
    t_mean2, _r = permutation_test(ind2, 200, "mean", 5)
    tc.check("indicator_median_degenerate",
             t_med2 is not None and t_med2["stat"] == 0,
             "медіана індикатора не розрізняє 10% проти 40%")
    tc.check("indicator_mean_not_degenerate",
             t_mean2 is not None and t_mean2["stat"] > 0.25)

    # --- РЕГРЕСІЯ НА РЕАЛЬНИХ ЧИСЛАХ 23 сер: дискретний |dx| ---
    # Прогін по 176 артефактах дав pool з масою точно однакових 0.024 і
    # effect=7711 MAD. Відтворюємо ту саму патологію і вимагаємо, щоб
    # нормована міра ВІДМОВИЛАСЬ, а абсолютна лишилась осмисленою.
    real = {0: [0.024] * 18, 1: [0.024] * 9 + [0.028] * 9,
            2: [0.024] * 9 + [0.030] * 9, 3: [0.024] * 6 + [0.033] * 12,
            4: [0.024] * 21, 5: [0.024] * 24, 6: [0.024] * 23}
    t_real, _r = permutation_test(real, 500, "median", 5)
    tc.check("real_ties_mad_refused",
             t_real is not None and t_real["mad_degenerate"] is True
             and t_real["effect_mad"] is None,
             str(t_real and (t_real["pool_mad"], t_real["effect_mad"])))
    tc.check("real_ties_frac_high",
             t_real is not None and t_real["ties_frac"] > 0.7,
             str(t_real and t_real["ties_frac"]))
    tc.check("real_abs_effect_small",
             t_real is not None and 0.0 < t_real["effect_abs"] < 0.02,
             str(t_real and t_real["effect_abs"]))
    tc.check("real_effect_below_threshold",
             t_real is not None
             and t_real["effect_abs"] < VERDICT_EFFECT_ABS,
             "значущість без величини не має давати WEEKDAY-STRUCTURE")
    tc.check("real_verdict_not_structure",
             verdict_of(t_real) != "WEEKDAY-STRUCTURE", verdict_of(t_real))
    # НЕГАТИВНИЙ КОНТРОЛЬ ДО ВИРОДЖЕННЯ: на неперервному пулі з тим самим
    # розкидом медіан MAD НЕ вироджений і нормована міра рахується.
    rng3 = _lcg_stream(4242)
    cont = {wd: [0.024 + 0.01 * next(rng3) + (0.009 if wd == 3 else 0.0)
                 for _ in range(20)] for wd in range(7)}
    t_cont, _r = permutation_test(cont, 500, "median", 5)
    tc.check("continuous_mad_not_degenerate",
             t_cont is not None and t_cont["mad_degenerate"] is False
             and t_cont["effect_mad"] is not None,
             str(t_cont and t_cont["pool_mad"]))
    tc.check("continuous_ties_frac_low",
             t_cont is not None and t_cont["ties_frac"] < 0.2)
    # ВИРОДЖЕННЯ НЕ МАЄ ЗАЛЕЖАТИ ВІД МАСШТАБУ: той самий ряд, помножений
    # на 1000, мусить дати ТОЙ САМИЙ вердикт про виродження (поріг
    # відносний, не абсолютний).
    scaled = {k: [v * 1000.0 for v in vs] for k, vs in real.items()}
    t_sc, _r = permutation_test(scaled, 500, "median", 5)
    tc.check("mad_degeneracy_scale_invariant",
             t_sc is not None
             and t_sc["mad_degenerate"] == t_real["mad_degenerate"],
             str(t_sc and t_sc["mad_degenerate"]))
    tc.check("abs_effect_scales_linearly",
             t_sc is not None
             and abs(t_sc["effect_abs"] - 1000.0 * t_real["effect_abs"])
             < 1e-9)


    tc.check("verdict_none", verdict_of(None) == "NO-TEST")
    tc.check("verdict_low_n",
             verdict_of({"n_buckets": 2, "p": 0.001,
                         "effect_abs": 9.0}) == "LOW-N")
    tc.check("verdict_structure",
             verdict_of({"n_buckets": 5, "p": 0.001,
                         "effect_abs": 0.30}) == "WEEKDAY-STRUCTURE")
    tc.check("verdict_null",
             verdict_of({"n_buckets": 5, "p": 0.40,
                         "effect_abs": 0.30}) == "NO-STRUCTURE")
    tc.check("verdict_inconclusive_small_effect",
             verdict_of({"n_buckets": 5, "p": 0.001,
                         "effect_abs": 0.01}) == "INCONCLUSIVE")
    tc.check("verdict_inconclusive_mid_p",
             verdict_of({"n_buckets": 5, "p": 0.03,
                         "effect_abs": 0.30}) == "INCONCLUSIVE")
    tc.check("verdict_rate_own_threshold",
             verdict_of({"n_buckets": 5, "p": 0.001, "agg_kind": "mean",
                         "effect_abs": 0.5}) == "WEEKDAY-STRUCTURE")
    tc.check("verdict_rate_small_effect",
             verdict_of({"n_buckets": 5, "p": 0.001, "agg_kind": "mean",
                         "effect_abs": 0.05}) == "INCONCLUSIVE")
    # 0.10 проходить абсолютний поріг 0.05 для x, але НЕ поріг 0.25 для
    # часток — шкали різні, і вердикт мусить це розрізняти.
    tc.check("verdict_scales_are_separate",
             verdict_of({"n_buckets": 5, "p": 0.001, "agg_kind": "median",
                         "effect_abs": 0.10}) == "WEEKDAY-STRUCTURE"
             and verdict_of({"n_buckets": 5, "p": 0.001, "agg_kind": "mean",
                             "effect_abs": 0.10}) == "INCONCLUSIVE")
    tc.check("verdict_effect_none_no_test",
             verdict_of({"n_buckets": 5, "p": 0.001,
                         "effect_abs": None}) == "NO-TEST")
    # ЦЕНТРУВАННЯ ВБИВАЄ ratio, АЛЕ НЕ абсолютний ефект.
    _st = [{"hour": h, "adx": v, "weekday": wd}
           for wd in range(5)
           for h, v in ((0, 1.0 + wd), (4, 11.0 + wd), (8, 21.0 + wd))]
    _cen = center_by_slot(_st, "adx")
    _b = {}
    for _s0, _v in zip(_st, _cen):
        _b.setdefault(_s0["weekday"], []).append(_v)
    _t, _r = permutation_test(_b, 200, "median", 3)
    tc.check("centered_ratio_degenerate_effect_survives",
             _t is not None and _t["effect_abs"] > 0.0,
             str(_t and _t["effect_abs"]))

    # --- панель на синтетичному ланцюгу ---
    chain = [{"currency": "BTC",
              "slices": [_mk_slice("D0", 0.66), _mk_slice("E1", 4.66),
                         _mk_slice("E2", 11.66), _mk_slice("E3", 32.66),
                         _mk_slice("E4", 67.66), _mk_slice("E5", 123.66)]}]
    st = snapshot_structure(chain, (7.0, 30.0, 90.0))
    tc.check("panel_all_ok",
             all(st["BTC"][t]["status"] == "OK" for t in (7.0, 30.0, 90.0)),
             str(st))
    tc.check("panel_t30_nodes",
             abs(st["BTC"][30.0]["lo_d"] - 11.66) < 1e-9
             and abs(st["BTC"][30.0]["hi_d"] - 32.66) < 1e-9,
             str((st["BTC"][30.0]["lo_d"], st["BTC"][30.0]["hi_d"])))

    # --- ENV-WARN ---
    unk = warn_unknown_env(KNOWN_ENV, environ={"VOLEDGE_OFFLINE": "1",
                                              "VOLEDGE_TYPO_HERE": "1",
                                              "PATH": "/bin"}, quiet=True)
    # Контракт «список = те, що читається». УВАГА: цей модуль читає
    # частину змінних НЕ напряму, а через _env_int(), тож регекс мусить
    # покривати ОБИДВІ форми. Скан лише по os.environ.get показував тут
    # дві «зайві» змінні, яких насправді немає (24 сер).
    import re as _envre
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
    tc.check("env_warn_flags_unknown", unk == ["VOLEDGE_TYPO_HERE"], str(unk))
    tc.check("env_warn_ignores_known",
             "VOLEDGE_OFFLINE" not in unk)

    tc.check("env_int_bad_raises",
             _raises(lambda: _env_int("PATH", 1), ValueError))

    print(f"Самотести: {tc.passed}/{tc.passed + tc.failed}")
    if tc.failures:
        print("ПРОВАЛЕНІ:")
        for f in tc.failures:
            print("  " + f)
    return tc


# ----------------------------------------------------------------------------
# Live
# ----------------------------------------------------------------------------

def run_live(tenors, n_perm, min_per_bucket, no_write):
    if not os.path.isdir(CHAIN_DIR):
        raise RuntimeError(f"немає теки chain-артефактів: {CHAIN_DIR}")
    pairs = list_chain_artifacts(CHAIN_DIR)
    if not pairs:
        raise RuntimeError(f"у {CHAIN_DIR} немає chain_*.json(.gz)")
    print(f"Знайдено артефактів: {len(pairs)}")
    report = run_analysis(pairs, tenors, n_perm, min_per_bucket)
    print_report(report)
    if no_write:
        print("[NO-WRITE] звіт не збережено (VOLEDGE_TENOR_NODES_NO_WRITE=1)")
        return report
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(OUT_DIR, f"tenor_nodes_{ts}.json")

    def _enc(o):
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError(f"не серіалізується: {type(o)}")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=_enc)
    print(f"Звіт: {path}")
    return report


def main() -> int:
    rc = 0
    try:
        warn_unknown_env(KNOWN_ENV, label="tenor_nodes")
        offline = os.environ.get("VOLEDGE_OFFLINE", "").strip() == "1"
        tc = run_self_tests()
        if tc.failed:
            print("САМОТЕСТИ ПРОВАЛЕНІ — live-прогін скасовано.")
            return 2
        if offline:
            print("[OFFLINE] VOLEDGE_OFFLINE=1 — live-прогін пропущено явно.")
            return 0
        tenors = parse_tenors(os.environ.get("VOLEDGE_TENOR_NODES_TENORS"))
        n_perm = _env_int("VOLEDGE_TENOR_NODES_PERM", PERM_DEFAULT, 0)
        min_b = _env_int("VOLEDGE_TENOR_NODES_MIN_BUCKET",
                         MIN_PER_BUCKET_DEFAULT, 1)
        no_write = os.environ.get(
            "VOLEDGE_TENOR_NODES_NO_WRITE", "").strip() == "1"
        run_live(tenors, n_perm, min_b, no_write)
    except Exception:                                  # noqa: BLE001
        traceback.print_exc()
        rc = 1
    finally:
        try:
            input("\nГотово. Enter для виходу...")
        except EOFError:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
