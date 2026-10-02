#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[EN] Hypothesis A, step 1 (read-only): does the ETH-BTC ATM30
volatility spread carry more independent information (effective sample
size n_eff) than each leg? Judged against an empirical null, with
negative and positive controls; thresholds pre-registered.

--- Ukrainian original below ---
Vol-Edge :: analytics/spread_neff.py — КРОК 1 гіпотези A. READ-ONLY.

ЩО САМЕ МІРЯЄМО (і що НЕ міряємо)
----------------------------------
Гіпотеза A: спред ethbtc_atm30 = ETH.atm30 - BTC.atm30 містить БІЛЬШЕ
незалежної інформації на одиницю календарного часу, ніж кожна нога
окремо, бо спільний крипто-фактор скорочується.

Цей модуль міряє РІВНО ЦЕ і нічого більше. Про mean reversion,
напрямок, торгованість — жодного слова у вердикті. Якщо ratio не
проходить, гіпотеза A закривається тут, дешево, і кроку 2 не буде.

ПОРОГИ ЗАФІКСОВАНІ ДО ПРОГОНУ (STATE, дисципліна вимірювання):
  raw   = n_eff(spread) / max(n_eff(BTC.atm30), n_eff(ETH.atm30))
  norm  = raw / медіана raw на НУЛЬОВІЙ моделі (див. поправку нижче)
    norm >= 1.5               -> PASS
    norm <= 1.1               -> FAIL
    між ними                  -> INCONCLUSIVE
  спільних якорів < 60        -> LOW-N, вердикту НЕМАЄ
  валідаційний негативний контроль поза [0.90, 1.10] -> CONTROL-FAIL, VOID
  позитивний контроль <= 1.5                         -> CONTROL-FAIL, VOID

ПОПРАВКА ДО ПРЕ-РЕЄСТРАЦІЇ (внесена ДО першого живого прогону,
підставою є САМОТЕСТ, не дані)
------------------------------------------------------------------
Спершу пороги 1.5/1.1 були зафіксовані на СИРОМУ ratio, з мовчазним
припущенням «під нулем ratio = 1.0». Негативний контроль це припущення
СПРОСТУВАВ: дві НЕЗАЛЕЖНІ AR(1) з r1=0.95 при n=150 дають медіанний
сирий ratio 0.71, а не 1.00.

Причина не в даних і не в спреді. n_eff = n(1-r)/(1+r) — сильно
опукла функція r, і r оцінюється з шумом. У ЗНАМЕННИКУ стоїть max()
двох шумних оцінок; максимум зміщений угору, отже ratio зміщений униз
СИСТЕМАТИЧНО, ще до будь-якої фізики. На сирому ratio поріг FAIL <= 1.1
відхиляв би гіпотезу навіть там, де ефект насправді є.

Тому вердикт виноситься по НОРМОВАНІЙ величині: сирий ratio ділиться на
медіану сирого ratio під нульовою моделлю, підігнаною під НАШІ n, r1 і
sd ніг. Пороги 1.5/1.1 НЕ рухаються — рухається лише те, від чого вони
відлічуються, і рухається у бік СУВОРІШИЙ до гіпотези.

max() у знаменнику лишається навмисно: гіпотеза стверджує «більше, ніж
у КОЖНОЇ ноги», і це найконсервативніше прочитання.

Наслідок для контролю: нормований негативний контроль на тих самих
реалізаціях дорівнював би 1.0 ТОТОЖНО і нічого б не перевіряв. Тому
негативних наборів ДВА, з різними зернами: калібрувальний задає
нормувальник, ВАЛІДАЦІЙНИЙ його перевіряє. Перевірка [0.90, 1.10] іде
саме по валідаційному.

ТРИ СМУГИ, А НЕ ДВІ — свідомо. Жорсткий поріг на неперервній величині
двічі давав вердикт, протилежний до даних (0.9/0.1 у decompose;
безнапрямкова гілка базової лінії у dvol_drift). Проміжна смуга
називається своїм ім'ям.

ЧОМУ n_eff ІМПОРТУЄТЬСЯ, А НЕ ПЕРЕПИСУЄТЬСЯ
--------------------------------------------
Вимірюється ВІДНОШЕННЯ двох n_eff. Якби формула тут відрізнялась від
робочої хоч на епсилон, ми б порівнювали два різні оцінювачі, а не два
ряди. `autocorr` і `effective_n` беруться з anomaly_calibrate (де вони
тотожні детекторним), `features_of_snapshot` — з anomaly_detect.
Самотест ідентичності оцінювача обов'язковий.

n_eff рахується на РІВНЯХ (як rep["n_eff_ar1"] калібратора), не на
приростах. Змішувати не можна: у калібраторі поруч живуть обидві
величини і сплутати їх легко.

ЧОМУ ДВА КОНТРОЛІ, А НЕ ОДИН
-----------------------------
- НЕГАТИВНИЙ: дві НЕЗАЛЕЖНІ AR(1) з r1 нашими ж ніг. Спільного фактора
  немає -> скорочувати нічого -> ratio мусить лягти на 1.0. Якщо не
  лягає, вимірювач має зсув і будь-яке число з нього недійсне.
- ПОЗИТИВНИЙ: спільний персистентний фактор + слабко корельовані
  ідіосинкразії. Ratio мусить бути ВЕЛИКИМ. Без цього тесту ми не
  знаємо, чи інструмент узагалі здатен побачити те, що шукає:
  «тест без негативного контролю нічого не вартий», але контроль лише
  негативний не відрізняє «ефекту немає» від «прилад сліпий».

Контролі проганяються REPS разів; вердикт по МЕДІАНІ (одна реалізація
шумна). Додатково друкується частка контрольних реалізацій >= observed
— це ДІАГНОСТИКА, вердикт дає зафіксований поріг.

ЩО МОДУЛЬ НЕ РОБИТЬ
-------------------
Не ходить у мережу. Не чіпає rv/rvroll (vrp тут не потрібен зовсім,
тому VOLEDGE_VRP_SOURCE НЕ читається — і саме тому його немає в
KNOWN_ENV). Не пише нічого, крім свого звіту в reports/ і власного
кешу фіч.

Змінні оточення:
  VOLEDGE_SPREAD_MIN_N     -> поріг LOW-N, default 60
  VOLEDGE_SPREAD_CTRL_REPS -> реалізацій контролю, default 200;
                              0 -> контролі пропускаються ЯВНО, і
                              вердикт стає CONTROL-SKIPPED
  VOLEDGE_SPREAD_SEED      -> зерно контролів, default 20260826
  VOLEDGE_SPREAD_NO_WRITE  -> 1: не писати звіт
  VOLEDGE_SPREAD_NO_CACHE  -> 1: не читати/не писати кеш фіч

Чистий stdlib. Подвійний клік + input() завжди.
"""

import json
import math
import os
import random
import sys
import traceback
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_ROOT, _THIS_DIR, os.path.join(_ROOT, "core"),
           os.path.join(_ROOT, "analytics")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from anomaly_detect import (CHAIN_DIR, OUT_DIR, _load_json,
                                list_artifacts, features_of_snapshot)
except ImportError:
    from analytics.anomaly_detect import (CHAIN_DIR, OUT_DIR, _load_json,
                                          list_artifacts,
                                          features_of_snapshot)
try:
    from anomaly_calibrate import (autocorr, effective_n, LEGACY_SLOT_HOURS,
                                   CANONICAL_SLOT_HOURS)
except ImportError:
    from analytics.anomaly_calibrate import (autocorr, effective_n,
                                             LEGACY_SLOT_HOURS,
                                             CANONICAL_SLOT_HOURS)
try:
    from env_flags import merge_known, warn_unknown_env
except ImportError:
    from core.env_flags import merge_known, warn_unknown_env

# Читає САМЕ ЦЯ точка входу. Успадкування немає і не потрібне:
# з detect/calibrate імпортуються лише чисті функції, їхні main() не
# виконуються, тому VOLEDGE_CALIB_* тут справді no-op і попередження на
# них ПРАВИЛЬНЕ.
KNOWN_ENV = merge_known((
    "VOLEDGE_SPREAD_MIN_N",
    "VOLEDGE_SPREAD_CTRL_REPS",
    "VOLEDGE_SPREAD_SEED",
    "VOLEDGE_SPREAD_NO_WRITE",
    "VOLEDGE_SPREAD_NO_CACHE",
))

LEG_BTC = "BTC.atm30"
LEG_ETH = "ETH.atm30"
SPREAD = "ethbtc_atm30"
NEEDED = (LEG_BTC, LEG_ETH, SPREAD)

# --- ПОРОГИ. ЗАФІКСОВАНІ ДО ЗАМІРУ. НЕ ЧИТАЮТЬСЯ З ОТОЧЕННЯ НАВМИСНЕ ---
RATIO_PASS = 1.5
RATIO_FAIL = 1.1
MIN_N_DEFAULT = 60
CTRL_NEG_LO = 0.90
CTRL_NEG_HI = 1.10
CTRL_POS_MIN = 1.5
CTRL_REPS_DEFAULT = 200
# Нормувальник — медіана вибірки. При малих REPS вона сама шумить, і
# контроль валиться не через прилад, а через брак реалізацій. Спіймано
# самотестом при REPS=60. Нижче цього — явний [CTRL-WARN].
CTRL_REPS_MIN = 150
CTRL_SEED_DEFAULT = 20260826

# Позитивний контроль: спільний персистентний фактор + швидка ідіосинкразія.
POS_R_COMMON = 0.98
POS_R_IDIO = 0.50
POS_IDIO_W = 0.30

CACHE_PATH = os.path.join(OUT_DIR, "_spread_cache.json")
CACHE_VERSION = 2

# Знімок cron лягає о :10 (utc_gate + snapshot.sh). Вікно хвилин з
# запасом на затримку REST. Усе поза ним — ручний прогін.
GRID_MIN_LO = 5
GRID_MAX_LO = 15


# ----------------------------------------------------------------------------
# Дрібна статистика
# ----------------------------------------------------------------------------

def _median(xs):
    n = len(xs)
    if n == 0:
        return None
    s = sorted(xs)
    return s[n // 2] if n % 2 == 1 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _quantile(xs, q):
    n = len(xs)
    if n == 0:
        return None
    if n == 1:
        return xs[0]
    s = sorted(xs)
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (pos - lo) * (s[hi] - s[lo])


def _sd(xs):
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _fmt(v, nd=3):
    return "n/a" if v is None else f"{v:.{nd}f}"


# ----------------------------------------------------------------------------
# Кеш фіч (три числа на знімок) — читання ланцюгів дороге через JW
# ----------------------------------------------------------------------------

def _load_cache(no_cache):
    if no_cache:
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as _cfh:
            blob = json.load(_cfh)
    except Exception:
        return {}
    if blob.get("version") != CACHE_VERSION:
        return {}
    got = blob.get("items")
    return got if isinstance(got, dict) else {}


def _save_cache(cache, no_cache):
    if no_cache:
        return
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as _cfh:
            json.dump({"version": CACHE_VERSION, "items": cache}, _cfh)
    except Exception as e:
        print(f"[SKIP] кеш не записано: {e}")


# ----------------------------------------------------------------------------
# Побудова спільного ряду
# ----------------------------------------------------------------------------

def on_canonical_grid(st):
    """(ok, reason). БІЛИЙ СПИСОК, не чорний.

    Стара версія відкидала лише LEGACY_SLOT_HOURS, тобто пускала все
    незнайоме. Живий прогін 26 сер показав ціну: чотири ручні знімки о
    18:35/18:41/18:45/19:25 (годин 18-19 немає в жодному списку) і два
    о 16:35/08:55 (година канонічна, хвилина ні) пролізли в ряд. Вони
    ЗЛИПНУТІ — сусідні значення майже тотожні, що завищує r_lag1 і
    занижує n_eff усім трьом рядам. n_eff — величина «на крок»,
    нерівний крок її псує за побудовою.
    """
    if st.hour in LEGACY_SLOT_HOURS:
        return False, "legacy_grid"
    if st.hour not in CANONICAL_SLOT_HOURS:
        return False, "off_grid"
    if not (GRID_MIN_LO <= st.minute <= GRID_MAX_LO):
        return False, "off_grid"
    return True, "OK"


def collect_triplets(chain_pairs, loader=None, no_cache=True):
    """
    ([(stamp, btc, eth, spread), ...], counters).

    ВСІ ТРИ ряди на ОДНИХ І ТИХ САМИХ якорях. Це не зручність, а вимога:
    n_eff залежить від довжини ряду, тож ноги і спред, поміряні на
    різних наборах точок, дали б відношення двох різних n.

    Приймаються ЛИШЕ знімки канонічної сітки (білий список годин +
    вікно хвилин). Стара сітка і ручні прогони рахуються ОКРЕМИМИ
    лічильниками: змішати їх — різні речі з різними причинами.
    """
    counters = {"artifacts": 0, "read_fail": 0, "legacy_grid": 0,
                "off_grid": 0, "missing_feature": 0, "kept": 0,
                "cache_hit": 0, "cache_miss": 0}
    cache = _load_cache(no_cache)
    rows = []
    for st, path in chain_pairs:
        counters["artifacts"] += 1
        ok_grid, why = on_canonical_grid(st)
        if not ok_grid:
            counters[why] += 1
            continue
        key = os.path.basename(path)
        trio = cache.get(key)
        if trio is not None and len(trio) == 3:
            counters["cache_hit"] += 1
        else:
            try:
                data = (loader or _load_json)(path)
                feats, _skips, _q = features_of_snapshot(data)
                counters["cache_miss"] += 1
            except Exception as e:
                counters["read_fail"] += 1
                print(f"[SKIP] знімок {key}: {e}")
                continue
            if any(feats.get(nm) is None for nm in NEEDED):
                counters["missing_feature"] += 1
                cache[key] = None
                continue
            trio = [float(feats[LEG_BTC]), float(feats[LEG_ETH]),
                    float(feats[SPREAD])]
            cache[key] = trio
        if trio is None:
            counters["missing_feature"] += 1
            continue
        rows.append((st, trio[0], trio[1], trio[2]))
        counters["kept"] += 1
    rows.sort(key=lambda r: r[0])
    _save_cache(cache, no_cache)
    return rows, counters


def series_stats(values):
    """{'n','r1','n_eff','sd','median'} для одного ряду. r1/n_eff можуть
    бути None — це ЯВНА виродженість, не нуль."""
    r1, reason = autocorr(values, 1)
    return {"n": len(values), "r1": r1, "r1_reason": reason,
            "n_eff": effective_n(len(values), r1),
            "sd": _sd(values), "median": _median(values)}


def ratio_from_stats(st_btc, st_eth, st_spread):
    """(ratio|None, reason). None -> вимір неможливий, а не «нуль»."""
    legs = [st_btc.get("n_eff"), st_eth.get("n_eff")]
    if any(v is None for v in legs):
        return None, "n_eff однієї з ніг вироджений (|r1| ~ 1)"
    if st_spread.get("n_eff") is None:
        return None, "n_eff спреда вироджений (|r1| ~ 1)"
    den = max(legs)
    if den <= 0.0:
        return None, f"знаменник {den}"
    return st_spread["n_eff"] / den, "OK"


def ratio_verdict(ratio, n_common, min_n):
    """(label, note). Пороги — модульні константи, не з оточення."""
    if n_common < min_n:
        return "LOW-N", (f"спільних якорів {n_common} < {min_n}; "
                         f"вердикту немає")
    if ratio is None:
        return "REFUSE", "ratio не обчислено"
    if ratio >= RATIO_PASS:
        return "PASS", f"ratio {ratio:.3f} >= {RATIO_PASS}"
    if ratio <= RATIO_FAIL:
        return "FAIL", f"ratio {ratio:.3f} <= {RATIO_FAIL}"
    return "INCONCLUSIVE", (f"{RATIO_FAIL} < ratio {ratio:.3f} < "
                            f"{RATIO_PASS} — проміжна смуга")


# ----------------------------------------------------------------------------
# Контролі
# ----------------------------------------------------------------------------

def gen_ar1(rng, n, r1, sd):
    """Стаціонарний AR(1) одиничної дисперсії, помножений на sd."""
    if r1 is None:
        r1 = 0.0
    r1 = max(-0.999, min(0.999, float(r1)))
    inn = math.sqrt(max(0.0, 1.0 - r1 * r1))
    x = rng.gauss(0.0, 1.0)
    out = []
    for _ in range(n):
        x = r1 * x + inn * rng.gauss(0.0, 1.0)
        out.append(x * (sd if sd else 1.0))
    return out


def _ratio_of_pair(a, b):
    """ratio для пари синтетичних ніг; спред = b - a (як ETH - BTC)."""
    sa = series_stats(a)
    sb = series_stats(b)
    ss = series_stats([y - x for x, y in zip(a, b)])
    return ratio_from_stats(sa, sb, ss)[0]


def control_negative(n, r1_btc, r1_eth, sd_btc, sd_eth, reps, seed):
    """Дві НЕЗАЛЕЖНІ AR(1). Спільного фактора немає -> ratio ~ 1.0."""
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        a = gen_ar1(rng, n, r1_btc, sd_btc)
        b = gen_ar1(rng, n, r1_eth, sd_eth)
        r = _ratio_of_pair(a, b)
        if r is not None:
            out.append(r)
    return out


def control_positive(n, sd_btc, sd_eth, reps, seed):
    """Спільний персистентний фактор + швидка ідіосинкразія.
    Спред майже позбавлений фактора -> ratio мусить бути ВЕЛИКИМ."""
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        comm = gen_ar1(rng, n, POS_R_COMMON, 1.0)
        i_a = gen_ar1(rng, n, POS_R_IDIO, POS_IDIO_W)
        i_b = gen_ar1(rng, n, POS_R_IDIO, POS_IDIO_W)
        a = [(c + e) * (sd_btc or 1.0) for c, e in zip(comm, i_a)]
        b = [(c + e) * (sd_eth or 1.0) for c, e in zip(comm, i_b)]
        r = _ratio_of_pair(a, b)
        if r is not None:
            out.append(r)
    return out


def normalize(raw, null_median):
    """(norm|None, reason). Нульова медіана — нормувальник зміщення
    оцінювача, не властивість ринку."""
    if raw is None:
        return None, "сирий ratio відсутній"
    if null_median is None or null_median <= 0.0:
        return None, f"нульова медіана {null_median} непридатна"
    return raw / null_median, "OK"


def control_verdict(neg_valid_norm, pos_norm):
    """(ok, label, note) по НОРМОВАНИХ величинах.

    neg_valid_norm — валідаційний негативний набір (інше зерно, ніж
    калібрувальний), інакше перевірка була б тотожністю.
    Провал АНУЛЮЄ основний вердикт.
    """
    if not neg_valid_norm or not pos_norm:
        return False, "CONTROL-FAIL", "контроль не дав жодної реалізації"
    m_neg = _median(neg_valid_norm)
    m_pos = _median(pos_norm)
    bad = []
    if not (CTRL_NEG_LO <= m_neg <= CTRL_NEG_HI):
        bad.append(f"валідаційний негативний {m_neg:.3f} поза "
                   f"[{CTRL_NEG_LO}, {CTRL_NEG_HI}] — нормувальник "
                   f"нестабільний")
    if m_pos <= CTRL_POS_MIN:
        bad.append(f"позитивний {m_pos:.3f} <= {CTRL_POS_MIN} "
                   f"(прилад не бачить того, що шукає)")
    if bad:
        return False, "CONTROL-FAIL", "; ".join(bad)
    return True, "CONTROL-OK", (f"валідаційний негативний {m_neg:.3f}, "
                                f"позитивний {m_pos:.3f}")


def frac_ge(sample, x):
    """Частка реалізацій >= x. Діагностика, не вердикт."""
    if not sample or x is None:
        return None
    return sum(1 for v in sample if v >= x) / len(sample)


# ----------------------------------------------------------------------------
# Звіт
# ----------------------------------------------------------------------------

def run_measurement(rows, min_n, reps, seed):
    """rows: [(stamp, btc, eth, spread)]. -> dict звіту."""
    btc = [r[1] for r in rows]
    eth = [r[2] for r in rows]
    spr = [r[3] for r in rows]
    n = len(rows)

    st_b = series_stats(btc) if n >= 4 else {"n": n, "r1": None,
                                             "n_eff": None, "sd": None,
                                             "median": None}
    st_e = series_stats(eth) if n >= 4 else dict(st_b)
    st_s = series_stats(spr) if n >= 4 else dict(st_b)

    raw, raw_reason = (ratio_from_stats(st_b, st_e, st_s)
                       if n >= 4 else (None, f"точок {n} < 4"))

    rep = {"n_common": n,
           "first_utc": rows[0][0].isoformat() if rows else None,
           "last_utc": rows[-1][0].isoformat() if rows else None,
           "legs": {LEG_BTC: st_b, LEG_ETH: st_e},
           "spread": {SPREAD: st_s},
           "ratio_raw": raw, "ratio_raw_reason": raw_reason,
           "thresholds": {"pass": RATIO_PASS, "fail": RATIO_FAIL,
                          "min_n": min_n, "neg_lo": CTRL_NEG_LO,
                          "neg_hi": CTRL_NEG_HI,
                          "pos_min": CTRL_POS_MIN}}

    # Без контролів немає нормувальника, а СИРИЙ ratio зміщений. Видати
    # по ньому вердикт = зробити рівно ту помилку, яку контроль спіймав.
    if reps <= 0:
        rep["controls"] = {"skipped": True}
        rep["ratio_norm"] = None
        rep["ratio_verdict"] = None
        rep["final_verdict"] = "CONTROL-SKIPPED"
        rep["final_note"] = ("VOLEDGE_SPREAD_CTRL_REPS=0 — нормувальника "
                             "немає, вердикт по сирому ratio НЕ видається")
        return rep

    if reps < CTRL_REPS_MIN:
        print(f"[CTRL-WARN] реалізацій {reps} < {CTRL_REPS_MIN}: медіана-"
              f"нормувальник шумна, контроль може впасти без вини "
              f"приладу.")
    neg_cal = control_negative(n, st_b.get("r1"), st_e.get("r1"),
                               st_b.get("sd"), st_e.get("sd"), reps, seed)
    neg_val = control_negative(n, st_b.get("r1"), st_e.get("r1"),
                               st_b.get("sd"), st_e.get("sd"), reps,
                               seed + 101)
    pos = control_positive(n, st_b.get("sd"), st_e.get("sd"),
                           reps, seed + 1)

    null_med = _median(neg_cal)
    norm, norm_reason = normalize(raw, null_med)
    neg_val_norm = [v / null_med for v in neg_val] if null_med else []
    pos_norm = [v / null_med for v in pos] if null_med else []

    label, note = ratio_verdict(norm, n, min_n)
    rep["null_median"] = null_med
    rep["ratio_norm"] = norm
    rep["ratio_norm_reason"] = norm_reason
    rep["ratio_verdict"] = label
    rep["ratio_note"] = note
    rep["controls"] = {
        "skipped": False, "reps": reps, "seed": seed,
        "neg_cal_median": null_med,
        "neg_val_norm_median": _median(neg_val_norm),
        "neg_val_norm_q10": _quantile(neg_val_norm, 0.10),
        "neg_val_norm_q90": _quantile(neg_val_norm, 0.90),
        "pos_norm_median": _median(pos_norm),
        "frac_neg_ge_observed": frac_ge(neg_cal, raw),
        "verdict": None, "note": None}

    ok, clabel, cnote = control_verdict(neg_val_norm, pos_norm)
    rep["controls"]["verdict"] = clabel
    rep["controls"]["note"] = cnote

    # LOW-N — твердження про ДАНІ (n < порогу), воно істинне незалежно
    # від справності приладу, тому має пріоритет над VOID. Інакше «мало
    # точок» замаскувалось би під «прилад зламаний».
    if label == "LOW-N":
        rep["final_verdict"] = "LOW-N"
        rep["final_note"] = note + (f"; контроль: {cnote}"
                                    if not ok else "")
    elif not ok:
        rep["final_verdict"] = "VOID"
        rep["final_note"] = f"вердикт анульовано контролем: {cnote}"
    else:
        rep["final_verdict"] = label
        rep["final_note"] = note
    return rep


def print_report(rep, counters):
    print()
    print("-" * 72)
    print("СПІЛЬНІ ЯКОРІ")
    print("-" * 72)
    print(f"  артефактів переглянуто : {counters['artifacts']}")
    print(f"  стара сітка (відкинуто): {counters['legacy_grid']}")
    print(f"  поза сіткою (ручні)    : {counters['off_grid']}")
    print(f"  читання провалено      : {counters['read_fail']}")
    print(f"  фічі відсутні          : {counters['missing_feature']}")
    print(f"  кеш hit/miss           : {counters['cache_hit']}/"
          f"{counters['cache_miss']}")
    print(f"  ЗАЛИШИЛОСЬ             : {counters['kept']}")
    print(f"  вікно UTC              : {rep['first_utc']} .. "
          f"{rep['last_utc']}")

    print()
    print("-" * 72)
    print("АВТОКОРЕЛЯЦІЯ РІВНІВ І n_eff")
    print("-" * 72)
    print(f"{'ряд':<18}{'n':>6}{'r_lag1':>10}{'n_eff':>10}{'sd':>10}")
    for nm, blk in ((LEG_BTC, rep["legs"][LEG_BTC]),
                    (LEG_ETH, rep["legs"][LEG_ETH]),
                    (SPREAD, rep["spread"][SPREAD])):
        print(f"{nm:<18}{blk['n']:>6}{_fmt(blk.get('r1'), 4):>10}"
              f"{_fmt(blk.get('n_eff'), 2):>10}{_fmt(blk.get('sd'), 4):>10}")

    print()
    print("-" * 72)
    print("ВІДНОШЕННЯ")
    print("-" * 72)
    th = rep["thresholds"]
    print(f"  сирий  = n_eff(спред) / max(n_eff ніг) = "
          f"{_fmt(rep['ratio_raw'], 3)}  ({rep['ratio_raw_reason']})")
    print(f"  нульова медіана (зміщення оцінювача) : "
          f"{_fmt(rep.get('null_median'), 3)}")
    print(f"  НОРМОВАНИЙ                            : "
          f"{_fmt(rep.get('ratio_norm'), 3)}")
    print(f"  пороги (зафіксовані до заміру, на НОРМОВАНОМУ): "
          f"PASS >= {th['pass']}, FAIL <= {th['fail']}, "
          f"LOW-N при n < {th['min_n']}")
    if rep.get("ratio_verdict"):
        print(f"  [{rep['ratio_verdict']}] {rep['ratio_note']}")

    print()
    print("-" * 72)
    print("КОНТРОЛІ")
    print("-" * 72)
    c = rep["controls"]
    if c.get("skipped"):
        print("  [SKIP] контролі вимкнено (REPS=0) — вердикт не видається")
    else:
        print(f"  реалізацій: {c['reps']}, зерно: {c['seed']}")
        print(f"  негативний КАЛІБРУВАЛЬНИЙ (сирий): медіана "
              f"{_fmt(c['neg_cal_median'])} — це і є нормувальник; "
              f"відхилення від 1.0 = зміщення max()")
        print(f"  негативний ВАЛІДАЦІЙНИЙ (норм.)  : медіана "
              f"{_fmt(c['neg_val_norm_median'])}, q10..q90 "
              f"{_fmt(c['neg_val_norm_q10'])}.."
              f"{_fmt(c['neg_val_norm_q90'])} (очікується ~1.0)")
        print(f"  позитивний (норм.)               : медіана "
              f"{_fmt(c['pos_norm_median'])} (очікується >> "
              f"{CTRL_POS_MIN})")
        print(f"  [{c['verdict']}] {c['note']}")
        fg = c.get("frac_neg_ge_observed")
        print(f"  ДІАГНОСТИКА: частка негативних реалізацій >= "
              f"спостереженого: {_fmt(fg)} "
              f"(вердикт дає поріг, не ця цифра)")

    print()
    print("=" * 72)
    print(f"  ВЕРДИКТ КРОКУ 1: [{rep['final_verdict']}] {rep['final_note']}")
    print("=" * 72)
    fv = rep["final_verdict"]
    if fv == "PASS":
        print("  -> крок 2 (mean reversion) МАЄ СЕНС. Пороги кроку 2")
        print("     фіксуються ДО його прогону, як завжди.")
    elif fv == "FAIL":
        print("  -> гіпотеза A ЗАКРИТА. Спред не дає виграшу в")
        print("     незалежній інформації; кроку 2 не буде.")
    elif fv == "INCONCLUSIVE":
        print("  -> вердикту немає. Перезамір при помітно більшому n;")
        print("     кроку 2 НЕ починати на проміжній смузі.")
    elif fv == "VOID":
        print("  -> число з приладу недійсне. Спершу лагодити прилад.")
    else:
        print("  -> даних або контролів недостатньо.")


# ----------------------------------------------------------------------------
# Самотести
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


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def _mk_rows(n, fb, fe, start_h=0, legacy=0):
    """[(stamp, btc, eth, spread)] на канонічній 4-годинній сітці."""
    base = datetime(2026, 8, 1, start_h, 10, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        st = base.replace() if i == 0 else base
        st = base + __import__("datetime").timedelta(hours=4 * i)
        if i < legacy:
            st = st - __import__("datetime").timedelta(hours=3)
        b = fb(i)
        e = fe(i)
        out.append((st, b, e, e - b))
    return out


def run_self_tests():
    tc = TestCounters()
    rng = random.Random(11)

    # --- 1. Оцінювач ТОТОЖНИЙ робочому -------------------------------------
    # Якби формула тут відрізнялась, ratio порівнював би два різні
    # оцінювачі, а не два ряди. Перевіряємо саме імпортовані функції.
    seq = [math.sin(i / 3.0) for i in range(60)]
    r_here, _ = autocorr(seq, 1)
    tc.check("neff_matches_formula",
             abs(effective_n(60, r_here)
                 - max(1.0, 60 * (1 - r_here) / (1 + r_here))) < 1e-12)
    tc.check("neff_none_on_degenerate", effective_n(50, 1.0) is None)
    tc.check("neff_floor_is_one", effective_n(10, 0.999998) is None
             or effective_n(10, 0.99) >= 1.0)
    tc.check("autocorr_flat_refuses", autocorr([3.0] * 30, 1)[0] is None)

    # --- 2. Побудова спреда і спільних якорів -------------------------------
    rows = _mk_rows(80, lambda i: 50.0 + 0.1 * i, lambda i: 60.0 + 0.1 * i)
    tc.check("spread_is_difference",
             all(abs(r[3] - (r[2] - r[1])) < 1e-12 for r in rows))
    tc.check("rows_sorted", all(rows[i][0] < rows[i + 1][0]
                                for i in range(len(rows) - 1)))
    tc.check("common_anchor_count", len(rows) == 80, str(len(rows)))

    # Паралельний дрейф: спред КОНСТАНТА -> вироджений, ratio відмовляє.
    st_b = series_stats([r[1] for r in rows])
    st_e = series_stats([r[2] for r in rows])
    st_s = series_stats([r[3] for r in rows])
    rt, why = ratio_from_stats(st_b, st_e, st_s)
    tc.check("constant_spread_refuses_not_zero",
             rt is None and "вироджен" in why, f"{rt} / {why}")

    # --- 3. Вердикт: три смуги, межі включно --------------------------------
    tc.check("verdict_pass", ratio_verdict(1.6, 100, 60)[0] == "PASS")
    tc.check("verdict_pass_on_boundary",
             ratio_verdict(1.5, 100, 60)[0] == "PASS")
    tc.check("verdict_fail", ratio_verdict(1.0, 100, 60)[0] == "FAIL")
    tc.check("verdict_fail_on_boundary",
             ratio_verdict(1.1, 100, 60)[0] == "FAIL")
    tc.check("verdict_middle_named",
             ratio_verdict(1.3, 100, 60)[0] == "INCONCLUSIVE")
    tc.check("verdict_middle_not_silently_pass",
             ratio_verdict(1.49, 100, 60)[0] != "PASS")
    tc.check("verdict_lown_wins_over_ratio",
             ratio_verdict(9.9, 59, 60)[0] == "LOW-N")
    tc.check("verdict_refuse_on_none",
             ratio_verdict(None, 100, 60)[0] == "REFUSE")

    # --- 4. Негативний контроль і ЗМІЩЕННЯ max() ----------------------------
    # ЦЕЙ ТЕСТ ФІКСУЄ ПРИЧИНУ ПОПРАВКИ ДО ПРЕ-РЕЄСТРАЦІЇ. Дві НЕЗАЛЕЖНІ
    # AR(1) з однаковим r1 не мають спільного фактора, тож «фізичного»
    # виграшу спреда бути не може. Проте сирий ratio систематично НИЖЧЕ
    # одиниці: n_eff опукла по r, r шумний, а max() двох шумних оцінок
    # зміщений угору. Якщо колись цей тест почне давати ~1.0, значить
    # знаменник змінили — і нормування треба переглянути.
    neg = control_negative(150, 0.95, 0.95, 1.0, 1.0, 150, 4242)
    m_neg = _median(neg)
    tc.check("raw_neg_control_is_biased_below_one",
             m_neg is not None and m_neg < 0.90, _fmt(m_neg))
    tc.check("neg_control_reps_kept", len(neg) >= 140, str(len(neg)))
    tc.check("neg_control_bias_not_absurd",
             m_neg > 0.30, _fmt(m_neg))
    # Різні r1 ніг: фактора немає — сирий ratio і поготів не сягає PASS.
    neg2 = control_negative(150, 0.95, 0.85, 1.0, 1.0, 120, 777)
    tc.check("neg_control_asym_below_pass",
             _median(neg2) < RATIO_PASS, _fmt(_median(neg2)))

    # --- 5. Нормування ------------------------------------------------------
    nv, _ = normalize(1.4, 0.7)
    tc.check("normalize_divides", abs(nv - 2.0) < 1e-12, _fmt(nv))
    tc.check("normalize_refuses_zero_null",
             normalize(1.0, 0.0)[0] is None)
    tc.check("normalize_refuses_none_null",
             normalize(1.0, None)[0] is None)
    tc.check("normalize_refuses_none_raw",
             normalize(None, 0.7)[0] is None)
    # Нормований негативний на ТИХ САМИХ реалізаціях = 1.0 тотожно.
    # Саме тому валідаційний набір мусить мати ІНШЕ зерно.
    same_norm = [v / m_neg for v in neg]
    tc.check("normalized_self_is_tautological",
             abs(_median(same_norm) - 1.0) < 1e-12, _fmt(_median(same_norm)))
    neg_val = control_negative(150, 0.95, 0.95, 1.0, 1.0, 150, 4242 + 101)
    neg_val_norm = [v / m_neg for v in neg_val]
    tc.check("validation_neg_lands_on_one",
             CTRL_NEG_LO <= _median(neg_val_norm) <= CTRL_NEG_HI,
             _fmt(_median(neg_val_norm)))

    # --- 6. Позитивний контроль ---------------------------------------------
    pos = control_positive(150, 1.0, 1.0, 150, 909)
    pos_norm = [v / m_neg for v in pos]
    m_pos = _median(pos_norm)
    tc.check("pos_control_is_large",
             m_pos is not None and m_pos > CTRL_POS_MIN, _fmt(m_pos))
    tc.check("pos_control_beats_neg", m_pos > _median(neg_val_norm),
             f"{_fmt(m_pos)} vs {_fmt(_median(neg_val_norm))}")
    # Прилад РОЗРІЗНЯЄ два світи — без цього обидва контролі марні.
    tc.check("controls_separate_worlds",
             _quantile(pos_norm, 0.10) > _quantile(neg_val_norm, 0.90),
             f"{_fmt(_quantile(pos_norm, 0.10))} vs "
             f"{_fmt(_quantile(neg_val_norm, 0.90))}")

    # --- 7. Вердикт контролів анулює основний -------------------------------
    ok1, l1, _ = control_verdict(neg_val_norm, pos_norm)
    tc.check("control_verdict_ok", ok1 and l1 == "CONTROL-OK",
             f"{l1}")
    ok2, l2, note2 = control_verdict([2.0] * 50, pos_norm)
    tc.check("control_verdict_catches_bad_normalizer",
             (not ok2) and l2 == "CONTROL-FAIL"
             and "нормувальник" in note2, note2)
    ok3, _l3, note3 = control_verdict(neg_val_norm, [1.0] * 50)
    tc.check("control_verdict_catches_blind_pos",
             (not ok3) and "позитивний" in note3, note3)
    ok4, _, _ = control_verdict([], pos_norm)
    tc.check("control_verdict_empty_is_fail", not ok4)

    # --- 8. run_measurement: наскрізь ---------------------------------------
    # Синтетика зі спільним фактором -> має дати PASS.
    comm = gen_ar1(rng, 120, 0.97, 1.0)
    ia = gen_ar1(rng, 120, 0.4, 0.3)
    ib = gen_ar1(rng, 120, 0.4, 0.3)
    rows_pos = _mk_rows(120, lambda i: 50.0 + comm[i] + ia[i],
                        lambda i: 60.0 + comm[i] + ib[i])
    rep_pos = run_measurement(rows_pos, 60, 200, 20260826)
    tc.check("end_to_end_common_factor_passes",
             rep_pos["final_verdict"] == "PASS",
             f"{rep_pos['final_verdict']} norm="
             f"{_fmt(rep_pos['ratio_norm'])}")
    tc.check("end_to_end_reports_null_median",
             rep_pos.get("null_median") is not None
             and rep_pos["null_median"] > 0.0,
             _fmt(rep_pos.get("null_median")))
    tc.check("end_to_end_norm_exceeds_raw",
             rep_pos["ratio_norm"] > rep_pos["ratio_raw"],
             f"{_fmt(rep_pos['ratio_norm'])} vs "
             f"{_fmt(rep_pos['ratio_raw'])}")
    # Незалежні ноги -> НЕ PASS. Головний негативний контроль модуля.
    ja = gen_ar1(rng, 120, 0.95, 1.0)
    jb = gen_ar1(rng, 120, 0.95, 1.0)
    rows_neg = _mk_rows(120, lambda i: 50.0 + ja[i],
                        lambda i: 60.0 + jb[i])
    rep_neg = run_measurement(rows_neg, 60, 200, 20260826)
    tc.check("end_to_end_independent_not_pass",
             rep_neg["final_verdict"] != "PASS",
             f"{rep_neg['final_verdict']} norm="
             f"{_fmt(rep_neg['ratio_norm'])}")
    # LOW-N має ПЕРЕБИВАТИ навіть красивий ratio.
    rep_low = run_measurement(rows_pos[:40], 60, 200, 1)
    tc.check("end_to_end_lown_blocks",
             rep_low["final_verdict"] == "LOW-N",
             rep_low["final_verdict"])
    # REPS=0 -> явний CONTROL-SKIPPED, а не тихий вердикт без контролю.
    rep_nc = run_measurement(rows_pos, 60, 0, 1)
    tc.check("reps_zero_is_explicit",
             rep_nc["final_verdict"] == "CONTROL-SKIPPED"
             and rep_nc["controls"]["skipped"] is True)
    # Без нормувальника вердикт НЕ видається навіть як «сирий» —
    # інакше повернулись би до зміщеної шкали тихцем.
    tc.check("reps_zero_gives_no_ratio_verdict",
             rep_nc["ratio_norm"] is None
             and rep_nc["ratio_verdict"] is None)
    tc.check("reps_zero_still_reports_raw",
             rep_nc["ratio_raw"] is not None)
    tc.check("report_has_thresholds",
             rep_pos["thresholds"]["pass"] == RATIO_PASS
             and rep_pos["thresholds"]["fail"] == RATIO_FAIL)
    tc.check("report_records_window",
             rep_pos["first_utc"] is not None
             and rep_pos["last_utc"] is not None)

    # --- 8. collect_triplets: лічильники, стара сітка, дірки ----------------
    def _fake_loader_factory(store):
        def _loader(path):
            return store[os.path.basename(path)]
        return _loader

    import datetime as _dt
    store = {}
    pairs = []
    base = datetime(2026, 8, 1, 0, 10, tzinfo=timezone.utc)
    for i in range(30):
        st = base + _dt.timedelta(hours=4 * i)
        if i < 5:                      # стара сітка
            st = st - _dt.timedelta(hours=3)
        nm = f"chain_{st:%Y%m%d_%H%M%S}.json"
        store[nm] = i
        pairs.append((st, "/tmp/" + nm))

    def _feats_ok(data):
        return ({LEG_BTC: 50.0 + data, LEG_ETH: 60.0 + 0.5 * data,
                 SPREAD: 10.0 - 0.5 * data}, {}, [])

    def _feats_hole(data):
        if data % 7 == 0:
            return ({LEG_BTC: 50.0}, {}, [])
        return _feats_ok(data)

    # --- 8b. БІЛИЙ СПИСОК СІТКИ (регресія живого прогону 26 сер) ----------
    # Штампи взяті З РЕАЛЬНОГО каталогу artifacts/chain. Чорний список
    # пускав усі шість: години 18-19 не значились ніде, а 16:35/08:55
    # мали канонічну годину. Тест іменує кожен випадок окремо, щоб
    # регресія показала, ЯКА саме гілка зламалась.
    def _st(h, mi, se=1):
        return datetime(2026, 8, 10, h, mi, se, tzinfo=timezone.utc)

    tc.check("grid_accepts_canonical",
             all(on_canonical_grid(_st(h, 10))[0]
                 for h in CANONICAL_SLOT_HOURS))
    tc.check("grid_accepts_late_rest",
             on_canonical_grid(_st(4, 10, 2))[0]
             and on_canonical_grid(_st(0, 15))[0])
    tc.check("grid_drops_legacy_hour",
             on_canonical_grid(_st(17, 10)) == (False, "legacy_grid"))
    tc.check("grid_drops_unknown_hour_18",
             on_canonical_grid(_st(18, 35, 6)) == (False, "off_grid"))
    tc.check("grid_drops_unknown_hour_19",
             on_canonical_grid(_st(19, 25, 30)) == (False, "off_grid"))
    tc.check("grid_drops_bad_minute_on_good_hour",
             on_canonical_grid(_st(16, 35, 28)) == (False, "off_grid")
             and on_canonical_grid(_st(8, 55, 31)) == (False, "off_grid"))
    # Ключовий тест: години 18-19 НЕ в легасі, тож стара логіка їх
    # пускала. Якщо хтось поверне чорний список, впаде саме цей рядок.
    tc.check("grid_blacklist_would_have_leaked",
             _st(18, 35).hour not in LEGACY_SLOT_HOURS
             and not on_canonical_grid(_st(18, 35))[0])

    _orig = globals()["features_of_snapshot"]
    try:
        globals()["features_of_snapshot"] = _feats_ok
        rws, cnt = collect_triplets(pairs,
                                    loader=_fake_loader_factory(store),
                                    no_cache=True)
        tc.check("collect_drops_legacy_grid", cnt["legacy_grid"] == 5,
                 str(cnt["legacy_grid"]))
        tc.check("collect_kept_matches", cnt["kept"] == 25
                 and len(rws) == 25, f"{cnt['kept']}/{len(rws)}")
        tc.check("collect_counts_add_up",
                 cnt["artifacts"] == cnt["legacy_grid"] + cnt["off_grid"]
                 + cnt["kept"] + cnt["read_fail"]
                 + cnt["missing_feature"], str(cnt))

        globals()["features_of_snapshot"] = _feats_hole
        rws2, cnt2 = collect_triplets(pairs,
                                      loader=_fake_loader_factory(store),
                                      no_cache=True)
        tc.check("collect_counts_missing_feature",
                 cnt2["missing_feature"] > 0
                 and cnt2["kept"] < cnt["kept"], str(cnt2))
        tc.check("collect_no_silent_hole",
                 cnt2["artifacts"] == cnt2["legacy_grid"] + cnt2["off_grid"]
                 + cnt2["kept"] + cnt2["read_fail"]
                 + cnt2["missing_feature"], str(cnt2))

        def _boom(_d):
            raise ValueError("зламаний знімок")
        globals()["features_of_snapshot"] = _boom
        _r3, cnt3 = collect_triplets(pairs,
                                     loader=_fake_loader_factory(store),
                                     no_cache=True)
        tc.check("collect_counts_read_fail", cnt3["read_fail"] == 25,
                 str(cnt3["read_fail"]))
    finally:
        globals()["features_of_snapshot"] = _orig

    # --- 9. Дрібниці, що вже коштували прогонів -----------------------------
    tc.check("gen_ar1_length", len(gen_ar1(rng, 40, 0.5, 1.0)) == 40)
    tc.check("gen_ar1_clips_unit_root",
             len(gen_ar1(rng, 20, 1.5, 1.0)) == 20)
    tc.check("gen_ar1_none_r1_is_white",
             abs(autocorr(gen_ar1(random.Random(5), 400, None, 1.0),
                          1)[0]) < 0.20)
    tc.check("frac_ge_basic", abs(frac_ge([1.0, 2.0, 3.0, 4.0], 3.0)
                                  - 0.5) < 1e-12)
    tc.check("frac_ge_none_on_empty", frac_ge([], 1.0) is None)
    tc.check("sd_none_on_single", _sd([1.0]) is None)
    tc.check("median_none_on_empty", _median([]) is None)
    tc.check("known_env_has_no_vrp_source",
             "VOLEDGE_VRP_SOURCE" not in KNOWN_ENV,
             "vrp тут не читається — імені бути не повинно")
    tc.check("known_env_covers_reads",
             all(nm in KNOWN_ENV for nm in
                 ("VOLEDGE_SPREAD_MIN_N", "VOLEDGE_SPREAD_CTRL_REPS",
                  "VOLEDGE_SPREAD_SEED", "VOLEDGE_SPREAD_NO_WRITE",
                  "VOLEDGE_SPREAD_NO_CACHE")))
    tc.check("known_env_has_universal", "VOLEDGE_OFFLINE" in KNOWN_ENV)
    tc.check("env_warn_catches_typo",
             warn_unknown_env(KNOWN_ENV,
                              environ={"VOLEDGE_SPREAD_MINN": "60"},
                              quiet=True) == ["VOLEDGE_SPREAD_MINN"])
    tc.check("thresholds_ordered", RATIO_FAIL < RATIO_PASS
             and CTRL_NEG_LO < 1.0 < CTRL_NEG_HI)

    print(f"[TEST] passed {tc.passed}/{tc.run}")
    for f_ in tc.failures:
        print(f"[TEST] FAILURE detail: {f_}")
    return tc


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def _env_int(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = int(raw)
    except ValueError:
        raise RuntimeError(f"{name}={raw!r} — очікується ціле")
    print(f"[ENV] {name}={val}")
    return val


def _env_flag(name):
    raw = os.environ.get(name, "0").strip()
    val = raw not in ("", "0", "false", "False")
    if val:
        print(f"[ENV] {name}={raw!r} -> увімкнено")
    return val


def main() -> int:
    print("=" * 72)
    print("Vol-Edge :: spread_neff — крок 1 гіпотези A (READ-ONLY)")
    print("=" * 72)
    warn_unknown_env(KNOWN_ENV, label="spread_neff")

    tc = run_self_tests()
    if tc.failures:
        print("[FAIL] самотести провалено — заміру НЕ буде.")
        return 1

    if _env_flag("VOLEDGE_OFFLINE"):
        print("[OFFLINE] лише самотести, замір пропущено.")
        return 0

    min_n = _env_int("VOLEDGE_SPREAD_MIN_N", MIN_N_DEFAULT)
    reps = _env_int("VOLEDGE_SPREAD_CTRL_REPS", CTRL_REPS_DEFAULT)
    seed = _env_int("VOLEDGE_SPREAD_SEED", CTRL_SEED_DEFAULT)
    no_write = _env_flag("VOLEDGE_SPREAD_NO_WRITE")
    no_cache = _env_flag("VOLEDGE_SPREAD_NO_CACHE")

    chain_pairs = list_artifacts(CHAIN_DIR, "chain")
    print(f"[INFO] chain-артефактів знайдено: {len(chain_pairs)} "
          f"у {CHAIN_DIR}")
    if not chain_pairs:
        raise RuntimeError(f"у {CHAIN_DIR} немає chain-артефактів — "
                           f"міряти нічого")

    rows, counters = collect_triplets(chain_pairs, no_cache=no_cache)
    if len(rows) < 4:
        raise RuntimeError(f"спільних якорів {len(rows)} < 4 — "
                           f"автокореляція не рахується")

    rep = run_measurement(rows, min_n, reps, seed)
    rep["counters"] = counters
    print_report(rep, counters)

    if not no_write:
        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(OUT_DIR, f"spread_neff_{stamp}.json")
        with open(out_path, "w", encoding="utf-8") as _ofh:
            json.dump(rep, _ofh, ensure_ascii=False, indent=2)
        print(f"[OUT] {out_path}")

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
