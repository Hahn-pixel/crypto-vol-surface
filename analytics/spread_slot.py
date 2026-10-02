#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[EN] Hypothesis A, step 3 (read-only): is the reversion an intraday
artifact? Two statistics (slot dependence of beta, lag-6 beta against a
lag-matched null), each with its own positive control, plus a check
that the effect belongs to the spread and not to its legs.

--- Ukrainian original below ---
Vol-Edge :: analytics/spread_slot.py — КРОК 3 гіпотези A. READ-ONLY.

ЩО МІРЯЄМО
----------
Крок 2 (spread_ou.py) дав beta = -0.10511, p_null = 0.024, half-life
1.04 доби. Одна доба — це РІВНО 6 кроків сітки знімків. Проєкт двічі
ловив артефакти саме на цьому масштабі: пилка знаменника vrp30 від
оновлення rv о 00:40 і залишковий пік о 16:xx на краю ковзного вікна.

Реверсія з періодом близько доби ЗОБОВ'ЯЗАНА пройти перевірку на
внутрішньодобову сезонність, перш ніж її можна називати структурою
ринку. Крок 3 — саме ця перевірка. Це НЕ стратегія і не крок до неї.

ЧОТИРИ ПЕРЕВІРКИ (пороги зафіксовані ДО прогону, у STATE.md)
-------------------------------------------------------------
  1) beta по 6 слотах доби, перестановочний тест -> p > 0.05 = ефекту
     немає (тобто сила реверсії однакова в усіх слотах);
  2) beta на лазі 6 (крок = доба) має лишитись ВІД'ЄМНОЮ;
  3) негативний контроль «блукання + добова синусоїда»: артефакт
     відтворюється на лазі 1 і зникає на лазі 6;
  4) той самий тест на ногах BTC.atm30 і ETH.atm30 окремо. Якщо
     реверсія живе і в ногах — вона не має стосунку до гіпотези A.

ДВІ СТАТИСТИКИ, А НЕ ОДНА (з'ясувалось при проєктуванні контролю)
-----------------------------------------------------------------
Наявний `slot_permutation_test` (anomaly_calibrate) міряє розкид МЕДІАН
|приросту| між слотами. Крок 3 просить розкид BETA. Це не одна й та
сама річ, і різниця не косметична:

  beta слота рахується на даних, ЦЕНТРОВАНИХ УСЕРЕДИНІ СЛОТА. Додатковий
  детермінований добовий доданок s[h] зсуває середні x_prev і dx у
  межах слота на константу, а константа з cov/var скорочується. Тобто
  АДИТИВНА СЕЗОННІСТЬ У BETA-СТАТИСТИЦІ НЕ ВИДНА ЗА ПОБУДОВОЮ.

Це добра властивість статистики і погана — для контролю: пре-реєстрований
негативний контроль із синусоїдою перевіряв би те, чого beta-тест і не
мав ловити, і «пройшов» би завжди. Тому:

  (a) |diff|-тест — ІМПОРТУЄТЬСЯ з anomaly_calibrate, не переписується.
      Ловить сезонність рівня/масштабу. Його позитивний контроль —
      синусоїда;
  (b) beta-тест — НОВИЙ. Ловить слотозалежність САМОЇ СИЛИ реверсії.
      Його позитивний контроль — ряд, у якому реверсія локалізована в
      ОДНОМУ слоті, а решта доби — блукання.

Два тести — два позитивні контролі. Один позитивний контроль на дві
статистики означав би, що для однієї з них потужність не міряна взагалі
(клас 3 у STATE: «лише негативного мало»).

ЧОМУ ТУТ НИЗЬКА ПОТУЖНІСТЬ НЕБЕЗПЕЧНІША, НІЖ НА КРОЦІ 2
--------------------------------------------------------
На кроці 2 бажаний висновок був «ефект Є», і слабкий тест лише множив
хибні НЕГАТИВИ — тому UNDERPOWERED там не гасив PASS.

ТУТ НАПРЯМОК ОБЕРНЕНИЙ. Бажаний висновок кроку 3 — «слотового ефекту
НЕМАЄ», тобто підтверджується НУЛЬОВА гіпотеза. Сліпий прилад дає такий
висновок ЗАВЖДИ. Тому p > 0.05 при недоведеній потужності не є PASS: він
є SLOT-INCONCLUSIVE. Це та сама асиметрія, що в класі 2, прочитана в
інший бік, і вона винесена в чисту функцію `step3_verdict`.

ПРАВКА ПРЕ-РЕЄСТРОВАНОГО РЯДКА 2 (внесена ДО дотику до живих даних)
--------------------------------------------------------------------
У STATE рядок 2 записаний як «beta на лазі 6 має лишитись ВІД'ЄМНОЮ».
Нульовий розподіл показав, що цей критерій ПОРОЖНІЙ: медіана beta на
блуканні при лазі 6 дорівнює -0.126 (при -0.021 на лазі 1), бо зміщення
МНК на майже одиничному корені росте з лагом. Заміряно при n = 159:

    ряд                        beta6 < 0    beta6 < 0 І значуще
    блукання                     0.950            0.060
    OU hl = 1.04 доби            1.000            0.850
    блукання + синусоїда         0.950            0.000

Тобто сам ЗНАК проходить на 95% чистих блукань і не відрізняє нічого.
Критерій посилено до «beta < 0 І p_null <= 0.05 проти нуля, узгодженого
з ЛАГОМ». Після посилення він калібрований (0.060), потужний на
оціненому горизонті (0.850) і повністю імунний до сезонності (0.000).

Це третій випадок класу 1 у проєкті і другий поспіль, коли пре-
реєстрований критерій довелося міняти ЧЕРЕЗ НУЛЬОВИЙ РОЗПОДІЛ, а не
через дані. Правка ЗВУЖУЄ, а не розширює множину PASS, і зроблена до
першого погляду на живий ряд — тому вона не є підгонкою.

ЛАГ 6 І ПЕРЕКРИТТЯ
------------------
Пари лага 6 будуються ВСЕРЕДИНІ слота: (x[d, h], x[d+1, h]). Прирости
одного слота не мають спільних точок. Пари РІЗНИХ слотів накривають
зсунуті добові вікна, тож пул по шести слотах не є повністю
неперекривним — beta по кожному слоту окремо друкується поруч із
пуловою саме тому, і вердикт спирається на ЗНАК пулової.

Змінні оточення:
  VOLEDGE_SLOT3_PERM      -> перестановок, default 2000
  VOLEDGE_SLOT3_NULL      -> реалізацій нульового розподілу beta, 500
  VOLEDGE_SLOT3_CTRL_REPS -> реалізацій кожного контролю, default 200;
                             0 -> контролі пропускаються, CONTROL-SKIPPED
  VOLEDGE_SLOT3_CTRL_PERM -> перестановок усередині контролю, default 200
  VOLEDGE_SLOT3_MIN_N     -> поріг LOW-N по якорях, default 60
  VOLEDGE_SLOT3_SEED      -> зерно, default 20260827
  VOLEDGE_SLOT3_NO_WRITE  -> 1: не писати звіт
  VOLEDGE_SLOT3_NO_CACHE  -> 1: не читати/не писати кеш фіч

Чистий stdlib. Подвійний клік + input() завжди.
"""

import json
import math
import os
import random
import sys
import traceback
from datetime import datetime, timedelta, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_ROOT, _THIS_DIR, os.path.join(_ROOT, "core"),
           os.path.join(_ROOT, "analytics")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from spread_neff import (collect_triplets, series_stats, _median,
                             _quantile, _fmt, SPREAD, LEG_BTC, LEG_ETH)
except ImportError:
    from analytics.spread_neff import (collect_triplets, series_stats,
                                       _median, _quantile, _fmt, SPREAD,
                                       LEG_BTC, LEG_ETH)
try:
    from spread_ou import (ou_fit, gen_ou, gen_random_walk, TestCounters,
                           STEPS_PER_DAY, GAP_MIN_H, GAP_MAX_H, MIN_PAIRS,
                           null_beta_distribution, p_null, _tally)
except ImportError:
    from analytics.spread_ou import (ou_fit, gen_ou, gen_random_walk,
                                     TestCounters, STEPS_PER_DAY, GAP_MIN_H,
                                     GAP_MAX_H, MIN_PAIRS,
                                     null_beta_distribution, p_null, _tally)
try:
    from anomaly_calibrate import (slot_diffs, slot_permutation_test,
                                   _lcg_stream, _shuffled,
                                   CANONICAL_SLOT_HOURS, LEGACY_SLOT_HOURS)
except ImportError:
    from analytics.anomaly_calibrate import (slot_diffs,
                                             slot_permutation_test,
                                             _lcg_stream, _shuffled,
                                             CANONICAL_SLOT_HOURS,
                                             LEGACY_SLOT_HOURS)
try:
    from anomaly_detect import CHAIN_DIR, OUT_DIR, list_artifacts
except ImportError:
    from analytics.anomaly_detect import CHAIN_DIR, OUT_DIR, list_artifacts
try:
    from env_flags import merge_known, warn_unknown_env
except ImportError:
    from core.env_flags import merge_known, warn_unknown_env

KNOWN_ENV = merge_known((
    "VOLEDGE_SLOT3_PERM",
    "VOLEDGE_SLOT3_NULL",
    "VOLEDGE_SLOT3_CTRL_REPS",
    "VOLEDGE_SLOT3_CTRL_PERM",
    "VOLEDGE_SLOT3_MIN_N",
    "VOLEDGE_SLOT3_SEED",
    "VOLEDGE_SLOT3_NO_WRITE",
    "VOLEDGE_SLOT3_NO_CACHE",
))

# --- ПОРОГИ. ЗАФІКСОВАНІ ДО ЗАМІРУ. НЕ З ОТОЧЕННЯ НАВМИСНЕ -------------------
P_SLOT_MAX = 0.05          # p <= цього -> слотовий ефект Є
P_NULL_MAX = 0.05          # значущість beta проти блукання
MIN_N_DEFAULT = 60
MIN_SLOT_PAIRS = 12        # менше пар у слоті -> слот не бере участі
MIN_USABLE_SLOTS = 4       # менше придатних слотів -> LOW-N
MIN_PAIRS_LAG6 = 20
PERM_DEFAULT = 2000
PERM_MIN = 500
PERM_DEGEN_MAX_FRAC = 0.10  # більше вироджених перестановок -> REFUSE
# 500 було замало: p_null судиться п'ятим перцентилем нульового
# розподілу, і на 500 реалізаціях сам перцентиль шумний — оцінка
# потужності вирішальної перевірки стрибала на 0.15 між прогонами з
# різними зернами нуля. Це не впливало на вердикт 26 сер (p = 0.116
# далеко від 0.05), але для перезаміру, який очікується БІЛЯ межі,
# такий шум неприйнятний.
NULL_DEFAULT = 5000
NULL_MIN = 1000
SEED_DEFAULT = 20260827

# Контролі
CTRL_REPS_DEFAULT = 200
CTRL_REPS_MIN = 150
CTRL_PERM_DEFAULT = 200
CTRL_FP_MAX = 0.10         # хибний позитив слотового тесту
CTRL_POWER_MIN = 0.80      # потужність слотового тесту
CTRL_ARTIFACT_MIN = 0.50   # синусоїда МУСИТЬ підробити реверсію на лазі 1
CTRL_LAG6_MAX = 0.10       # ...і НЕ мусить на лазі 6
# Потужність ВИРІШАЛЬНОЇ перевірки (лаг 6) на однорідному OU. Поріг
# м'якший за CTRL_POWER_MIN навмисне: горизонт CTRL_OU_HL_D узятий з
# оцінки кроку 2, тобто ПОСТФАКТУМНИЙ (winner's curse). Вимагати від
# нього 0.80 означало б удавати точність, якої в самому горизонті немає.
LAG6_POWER_MIN = 0.70

# Потужність вирішальної перевірки на ОЦІНЕНОМУ горизонті. ДІАГНОСТИКА,
# НЕ ГЕЙТ: гейт лишається на CTRL_OU_HL_D, як пре-реєстровано. Друк
# потрібен тому, що 26 сер гейт дав 0.785 на hl = 1.04 (оцінка лага 1),
# а лаг 6 оцінив hl = 1.51, де потужність ~0.47 — контроль міряв
# сліпоту до ІНШОГО ефекту, ніж спостережений. Клас 6 у STATE.
POWER_REPS = 200
POWER_NULLS = 1000

# Синтетика контролю
SIN_AMP = 3.0              # амплітуда добової синусоїди, у sd кроку
SLOT_LOCAL_BETA = -0.60    # локалізована реверсія в одному слоті
SLOT_LOCAL_HOUR = 8
CTRL_OU_HL_D = 1.04        # однорідний OU з half-life, як у кроці 2

# Нога вважається такою, що сама реверсує
LEG_BETA_SHARE_FRAC = 0.50  # |beta ноги| >= частка від |beta спреда|

STEP_DAYS_LAG1 = 1.0 / STEPS_PER_DAY
STEP_DAYS_LAG6 = 1.0
LAG_DAY = 6

MINUTE_MIN = 5
MINUTE_MAX = 15


# ----------------------------------------------------------------------------
# Сітка і пари по слотах
# ----------------------------------------------------------------------------

def on_canonical_grid(st):
    """Година в каноні І хвилина в [5, 15].

    БІЛИЙ список, не чорний. Крок 1 уже коштував ремонту через чорний:
    шість ручних знімків (18:35, 18:41, 18:45, 19:25, 16:35, 08:55)
    пролізли в ряд, бо їхніх годин не було в жодному ПЕРЕЛІКУ ВИКЛЮЧЕНЬ.
    Дві з них — 16:35 і 08:55 — мають КАНОНІЧНУ годину, тож перевірки
    самої години недостатньо: 16:35 після 12:10 дає розрив 4.42 год і
    проходить фільтр рівномірності.
    """
    return (st.hour in CANONICAL_SLOT_HOURS
            and MINUTE_MIN <= st.minute <= MINUTE_MAX)


def slot_pairs(stamps, values, lag=1):
    """
    ({hour: [(x_prev, dx), ...]}, counters).

    Пара віднесена до UTC-години ПІЗНІШОГО знімка. Розрив мусить лежати
    у [3.5 + 4*(lag-1), 4.5 + 4*(lag-1)] год — при lag=6 це [23.5, 24.5],
    тобто рівно доба.

    Лічильники на кожен drop: legacy_grid / off_grid / bad_gap.
    """
    if lag < 1:
        raise ValueError(f"lag={lag} має бути >= 1")
    gap_min = GAP_MIN_H + 4.0 * (lag - 1)
    gap_max = GAP_MAX_H + 4.0 * (lag - 1)
    buckets = {}
    c = {"lag": lag, "pairs_seen": 0, "kept": 0, "legacy_grid": 0,
         "off_grid": 0, "bad_gap": 0, "gap_min_h": gap_min,
         "gap_max_h": gap_max}
    for i in range(lag, len(stamps)):
        st_prev, st_cur = stamps[i - lag], stamps[i]
        c["pairs_seen"] += 1
        if (st_prev.hour in LEGACY_SLOT_HOURS
                or st_cur.hour in LEGACY_SLOT_HOURS):
            c["legacy_grid"] += 1
            continue
        if not (on_canonical_grid(st_prev) and on_canonical_grid(st_cur)):
            c["off_grid"] += 1
            continue
        gap_h = (st_cur - st_prev).total_seconds() / 3600.0
        if not (gap_min <= gap_h <= gap_max):
            c["bad_gap"] += 1
            continue
        buckets.setdefault(st_cur.hour, []).append(
            (values[i - lag], values[i] - values[i - lag]))
        c["kept"] += 1
    return buckets, c


def pool_pairs(buckets):
    """Усі пари з усіх слотів одним списком (порядок слотів — зростання)."""
    out = []
    for h in sorted(buckets):
        out.extend(buckets[h])
    return out


def usable_slots(buckets, min_pairs=MIN_SLOT_PAIRS):
    """{hour: pairs} лише для слотів із достатньою кількістю пар."""
    return {h: v for h, v in buckets.items() if len(v) >= min_pairs}


def hl_days_from_beta(beta, step_days):
    """half-life у ДОБАХ або None, якщо збіжності немає."""
    if beta is None:
        return None
    decay = abs(1.0 + beta)
    if decay <= 0.0:
        return 0.0
    if decay >= 1.0:
        return None
    return (-math.log(2.0) / math.log(decay)) * step_days


# ----------------------------------------------------------------------------
# Перестановочний тест на BETA по слотах
# ----------------------------------------------------------------------------

def _slot_betas_from_pool(pool, sizes):
    """([beta, ...] | None, degenerate_flag) — beta для кожного бакета."""
    betas = []
    pos = 0
    for _h, n_ in sizes:
        fit = ou_fit(pool[pos:pos + n_])
        pos += n_
        if fit.get("beta") is None:
            return None, True
        betas.append(fit["beta"])
    return betas, False


def slot_beta_permutation(buckets, n_perm, seed=SEED_DEFAULT,
                          min_pairs=MIN_SLOT_PAIRS):
    """
    ({...} | None, reason). H0: сила реверсії однакова в усіх слотах.

    Статистика — розкид beta між слотами (max - min). p-value: частка
    перестановок ПРИНАЛЕЖНОСТІ ПАР до слотів зі статистикою >=
    спостереженої. Distribution-free.

    ПЕРЕСТАВЛЯЮТЬСЯ ПАРИ ЦІЛКОМ, а не значення: (x_prev, dx) — атом
    регресії. Розірвати їх означало б переставляти інший об'єкт, ніж
    той, на якому рахується статистика (той самий урок, що в блоковому
    бутстрепі кроку 2).
    """
    us = usable_slots(buckets, min_pairs)
    if len(us) < 2:
        return None, (f"придатних слотів {len(us)} < 2 "
                      f"(поріг {min_pairs} пар на слот)")
    if n_perm < 1:
        return None, f"n_perm={n_perm} < 1"
    sizes = [(h, len(us[h])) for h in sorted(us)]
    pool = pool_pairs(us)

    obs_betas, degen = _slot_betas_from_pool(pool, sizes)
    if degen:
        return None, "нульова дисперсія рівня в одному зі слотів"
    obs_stat = max(obs_betas) - min(obs_betas)

    rng = _lcg_stream(seed)
    n_ge = 0
    n_valid = 0
    n_degen = 0
    for _ in range(n_perm):
        betas, bad = _slot_betas_from_pool(_shuffled(pool, rng), sizes)
        if bad:
            n_degen += 1
            continue
        n_valid += 1
        if (max(betas) - min(betas)) >= obs_stat:
            n_ge += 1
    if n_valid < 1:
        return None, "усі перестановки вироджені"
    frac_degen = n_degen / float(n_perm)
    if frac_degen > PERM_DEGEN_MAX_FRAC:
        return None, (f"вироджених перестановок {frac_degen:.3f} > "
                      f"{PERM_DEGEN_MAX_FRAC} — p недійсний")
    p_val = (n_ge + 1.0) / (n_valid + 1.0)

    per_slot = {}
    for (h, n_), b in zip(sizes, obs_betas):
        per_slot[str(h)] = {"n_pairs": n_, "beta": b,
                            "hl_days": hl_days_from_beta(b, STEP_DAYS_LAG1)}
    lo_h = min(us, key=lambda h: per_slot[str(h)]["beta"])
    hi_h = max(us, key=lambda h: per_slot[str(h)]["beta"])
    return {"n_perm": n_perm, "n_valid": n_valid, "n_degenerate": n_degen,
            "seed": seed, "stat_spread": obs_stat, "p_value": p_val,
            "per_slot": per_slot, "slots_used": sorted(us),
            "min_beta_slot": lo_h, "max_beta_slot": hi_h,
            "beta_pooled": ou_fit(pool).get("beta")}, "OK"


# ----------------------------------------------------------------------------
# Нульовий розподіл beta, узгоджений із лагом
# ----------------------------------------------------------------------------

_NULL_CACHE = {}


def null_beta_lag(n_anchors, lag, reps, seed):
    """[beta, ...] на блуканнях довжини n_anchors, пари з тим самим лагом.

    Узгоджений із конструкцією: на лазі 6 приріст за добу має ВТРИЧІ
    більшу дисперсію відносно рівня, ніж чотиригодинний, і зміщення МНК
    інше. Брати нуль лага 1 для beta лага 6 означало б судити проти
    чужого розподілу.
    """
    key = (n_anchors, lag, reps, seed)
    if key in _NULL_CACHE:
        return _NULL_CACHE[key]
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        vals = gen_random_walk(rng, n_anchors, 1.0)
        prs = [(vals[i - lag], vals[i] - vals[i - lag])
               for i in range(lag, n_anchors)]
        fit = ou_fit(prs)
        if fit.get("beta") is not None:
            out.append(fit["beta"])
    # Мемоїзація: функція детермінована за (n, lag, reps, seed), а
    # викликається вісім разів за прогін (спред x2 лаги, чотири ноги,
    # плюс контролі). При NULL_DEFAULT = 5000 без кешу це коштувало б
    # сорок тисяч підгонок замість п'яти тисяч.
    _NULL_CACHE[key] = out
    return out


def reversion_at_lag(stamps, values, lag, nulls, seed, step_days,
                     min_pairs):
    """{beta, hl_days, p_null, n_pairs, counters, per_slot_beta}."""
    buckets, c = slot_pairs(stamps, values, lag=lag)
    pool = pool_pairs(buckets)
    fit = ou_fit(pool)
    nb = null_beta_lag(len(pool) + lag, lag, nulls, seed)
    per_slot = {}
    for h in sorted(buckets):
        f_h = ou_fit(buckets[h])
        per_slot[str(h)] = {"n_pairs": len(buckets[h]),
                            "beta": f_h.get("beta")}
    return {"lag": lag, "n_pairs": len(pool), "counters": c,
            "beta": fit.get("beta"), "reason": fit.get("reason"),
            "hl_days": hl_days_from_beta(fit.get("beta"), step_days),
            "p_null": p_null(nb, fit.get("beta")),
            "null_reps": len(nb), "null_median": _median(nb),
            "per_slot_beta": per_slot,
            "min_pairs_required": min_pairs}


def is_reverting(res, p_max=P_NULL_MAX):
    """Ряд «реверсує» = beta < 0 І значуще проти блукання."""
    b = res.get("beta")
    p = res.get("p_null")
    return b is not None and b < 0.0 and p is not None and p <= p_max


# ----------------------------------------------------------------------------
# |diff|-тест: ІМПОРТОВАНИЙ, не переписаний
# ----------------------------------------------------------------------------

def absdiff_slot_test(stamps, values, lag, n_perm, seed):
    """(res|None, reason) через slot_diffs + slot_permutation_test.

    Друга копія збирача і другий оцінювач розкиду медіан були б рівно
    тією помилкою, від якої STATE застерігає: два різні прилади на два
    ряди замість одного приладу на обидва.
    """
    pts = list(zip(stamps, values))
    buckets, c = slot_diffs(pts, lag=lag)
    res, reason = slot_permutation_test(buckets, n_perm, seed=seed)
    if res is not None:
        res["counters"] = c
    return res, reason


# ----------------------------------------------------------------------------
# Синтетика для контролів
# ----------------------------------------------------------------------------

def gen_walk_with_daily_sin(rng, n, amp=SIN_AMP):
    """Блукання + ДЕТЕРМІНОВАНА добова синусоїда.

    Реверсії немає, але є внутрішньодобова сезонність. На лазі 1
    синусоїда підробляє mean reversion (детермінований доданок
    повертається до себе щошість кроків). На лазі 6 обидва кінці пари
    припадають на однакову фазу і доданок скорочується ТОЧНО.
    """
    out = []
    x = 0.0
    period = STEPS_PER_DAY
    for i in range(n):
        x += rng.gauss(0.0, 1.0)
        out.append(x + amp * math.sin(2.0 * math.pi * i / period))
    return out


def gen_slot_local_reversion(rng, n, slot_idx, beta_slot=SLOT_LOCAL_BETA):
    """Блукання, у якому реверсія ЛОКАЛІЗОВАНА в одному слоті доби.

    Позитивний контроль саме для BETA-статистики: тут різниться не
    середнє слота (адитивна сезонність, яку beta скорочує), а сама СИЛА
    повернення. Якщо тест сліпий до цього — він сліпий до того, що
    міряє, і «p > 0.05» не означає нічого.
    """
    out = []
    x = 0.0
    period = int(STEPS_PER_DAY)
    for i in range(n):
        if i % period == slot_idx:
            x += beta_slot * x + rng.gauss(0.0, 1.0)
        else:
            x += rng.gauss(0.0, 1.0)
        out.append(x)
    return out


def synth_stamps(n, start=None):
    """Рівна канонічна сітка 00/04/.../20 UTC, хвилина 10."""
    base = start or datetime(2026, 6, 1, 0, 10, tzinfo=timezone.utc)
    return [base + timedelta(hours=4 * i) for i in range(n)]


# ----------------------------------------------------------------------------
# Контролі
# ----------------------------------------------------------------------------

def power_lag6_at(n_anchors, hl_days, reps=POWER_REPS,
                  nulls=POWER_NULLS, seed=SEED_DEFAULT):
    """Частка реалізацій OU(hl_days), де перевірка 2 дає значущу beta6."""
    if hl_days is None or hl_days <= 0.0:
        return None
    nb = null_beta_lag(n_anchors, LAG_DAY, nulls, seed)
    st = synth_stamps(n_anchors)
    rng = random.Random(seed + 3)
    hits = 0
    for _ in range(reps):
        vals = gen_ou(rng, n_anchors, hl_days, 1.0)
        fit = ou_fit(pool_pairs(slot_pairs(st, vals, lag=LAG_DAY)[0]))
        if is_reverting({"beta": fit.get("beta"),
                         "p_null": p_null(nb, fit.get("beta"))}):
            hits += 1
    return hits / float(reps)


def _control_once(values, stamps, n_perm, seed, nulls1, nulls6,
                  min_pairs, do_absdiff):
    """Один прогін синтетичного ряду через усі три прилади."""
    out = {"p_beta_slot": None, "p_absdiff": None,
           "lag1_reverting": False, "lag6_reverting": False}
    b1, _c1 = slot_pairs(stamps, values, lag=1)
    res, _r = slot_beta_permutation(b1, n_perm, seed=seed,
                                    min_pairs=min_pairs)
    if res is not None:
        out["p_beta_slot"] = res["p_value"]
    pool1 = pool_pairs(b1)
    f1 = ou_fit(pool1)
    out["lag1_reverting"] = is_reverting(
        {"beta": f1.get("beta"), "p_null": p_null(nulls1, f1.get("beta"))})
    b6, _c6 = slot_pairs(stamps, values, lag=LAG_DAY)
    f6 = ou_fit(pool_pairs(b6))
    out["lag6_reverting"] = is_reverting(
        {"beta": f6.get("beta"), "p_null": p_null(nulls6, f6.get("beta"))})
    out["beta1"] = f1.get("beta")
    out["beta6"] = f6.get("beta")
    if do_absdiff:
        ares, _ar = absdiff_slot_test(stamps, values, 1, n_perm, seed + 5)
        if ares is not None:
            out["p_absdiff"] = ares["p_value"]
    return out


def _rate(vals, pred):
    got = [v for v in vals if v is not None]
    if not got:
        return None
    return sum(1 for v in got if pred(v)) / float(len(got))


def run_controls(n, reps, n_perm, seed, nulls1, nulls6, min_pairs,
                 verbose=True):
    """
    Чотири контролі. Повертає {name: {...rates}}.

      neg_walk   блукання           -> обидва тести НЕ мусять спрацювати
      neg_ou     ОДНОРІДНИЙ OU      -> beta-тест НЕ мусить спрацювати
                                       (справжня реверсія, однакова в
                                       усіх слотах, не є слотовим ефектом)
      pos_sin    блукання+синусоїда -> |diff|-тест МУСИТЬ спрацювати;
                                       лаг 1 підробляє реверсію, лаг 6 ні
      pos_slot   локальна реверсія  -> beta-тест МУСИТЬ спрацювати
    """
    out = {}
    # ЯВНІ зсуви зерна. hash(str) у Python рандомізований між процесами
    # (PYTHONHASHSEED), тож зерно з нього зробило б контролі
    # невідтворюваними — і це помітили б лише при спробі повторити прогін.
    tag_offsets = {"neg_walk": 1, "neg_ou": 2, "pos_sin": 3, "pos_slot": 4}

    def _loop(tag, gen, do_absdiff):
        rng = random.Random(seed + 1000 * tag_offsets[tag])
        rows = []
        for i in range(reps):
            vals = gen(rng)
            rows.append(_control_once(vals, synth_stamps(len(vals)),
                                      n_perm, seed + 101 * (i + 1),
                                      nulls1, nulls6, min_pairs,
                                      do_absdiff))
        if verbose:
            print(f"[CTRL] {tag}: {len(rows)} реалізацій")
        return rows

    rw = _loop("neg_walk", lambda r: gen_random_walk(r, n, 1.0), True)
    out["neg_walk"] = {
        "reps": len(rw),
        "slot_beta_fire": _rate([x["p_beta_slot"] for x in rw],
                                lambda p: p <= P_SLOT_MAX),
        "absdiff_fire": _rate([x["p_absdiff"] for x in rw],
                              lambda p: p <= P_SLOT_MAX),
        "lag1_rev": _rate([1.0 if x["lag1_reverting"] else 0.0
                           for x in rw], lambda v: v > 0.5),
        "lag6_rev": _rate([1.0 if x["lag6_reverting"] else 0.0
                           for x in rw], lambda v: v > 0.5)}

    ou = _loop("neg_ou", lambda r: gen_ou(r, n, CTRL_OU_HL_D, 1.0), False)
    out["neg_ou"] = {
        "reps": len(ou),
        "slot_beta_fire": _rate([x["p_beta_slot"] for x in ou],
                                lambda p: p <= P_SLOT_MAX),
        "lag1_rev": _rate([1.0 if x["lag1_reverting"] else 0.0
                           for x in ou], lambda v: v > 0.5),
        "lag6_rev": _rate([1.0 if x["lag6_reverting"] else 0.0
                           for x in ou], lambda v: v > 0.5)}

    sn = _loop("pos_sin", lambda r: gen_walk_with_daily_sin(r, n), True)
    out["pos_sin"] = {
        "reps": len(sn),
        "absdiff_fire": _rate([x["p_absdiff"] for x in sn],
                              lambda p: p <= P_SLOT_MAX),
        "lag1_rev": _rate([1.0 if x["lag1_reverting"] else 0.0
                           for x in sn], lambda v: v > 0.5),
        "lag6_rev": _rate([1.0 if x["lag6_reverting"] else 0.0
                           for x in sn], lambda v: v > 0.5)}

    sl = _loop("pos_slot",
               lambda r: gen_slot_local_reversion(
                   r, n, SLOT_LOCAL_HOUR // 4), False)
    out["pos_slot"] = {
        "reps": len(sl),
        "slot_beta_fire": _rate([x["p_beta_slot"] for x in sl],
                                lambda p: p <= P_SLOT_MAX)}
    return out


def control_verdict(ctrl):
    """
    (ok, label, note). ГІЛКИ ЗА ЗНАКОМ, різні провали — різні ярлики.

      CONTROL-FAIL       прилад бреше: хибний позитив або лаг 6 не
                         імунний до сезонності. Число недійсне.
      UNDERPOWERED-SLOT  прилад справний, але сліпий: «слотового ефекту
                         немає» неможливо відрізнити від «не побачили».
      CONTROL-WEAK       синусоїда не відтворила артефакт на лазі 1 —
                         контроль не перевірив те, заради чого існує.
    """
    bad_fp, bad_pw, weak = [], [], []
    nw = ctrl.get("neg_walk", {})
    no = ctrl.get("neg_ou", {})
    ps = ctrl.get("pos_sin", {})
    pl = ctrl.get("pos_slot", {})

    for tag, key, src in (("блукання/beta", "slot_beta_fire", nw),
                          ("блукання/|diff|", "absdiff_fire", nw),
                          ("однорідний OU/beta", "slot_beta_fire", no)):
        r = src.get(key)
        if r is None:
            bad_fp.append(f"{tag}: реалізацій немає")
        elif r > CTRL_FP_MAX:
            bad_fp.append(f"{tag} спрацьовує у {r:.3f} > {CTRL_FP_MAX}")

    rw6 = nw.get("lag6_rev")
    if rw6 is None:
        bad_fp.append("блукання/лаг6: реалізацій немає")
    elif rw6 > CTRL_FP_MAX:
        bad_fp.append(f"блукання дає значущу реверсію на лазі 6 у "
                      f"{rw6:.3f} > {CTRL_FP_MAX}")

    r6 = ps.get("lag6_rev")
    if r6 is None:
        bad_fp.append("синусоїда/лаг6: реалізацій немає")
    elif r6 > CTRL_LAG6_MAX:
        bad_fp.append(f"синусоїда підробляє реверсію і на ЛАЗІ 6 у "
                      f"{r6:.3f} > {CTRL_LAG6_MAX} — головна перевірка "
                      f"кроку 3 не імунна до сезонності")

    for tag, key, src in (("|diff| на синусоїді", "absdiff_fire", ps),
                          ("beta на локальній реверсії", "slot_beta_fire",
                           pl)):
        r = src.get(key)
        if r is None:
            bad_pw.append(f"{tag}: реалізацій немає")
        elif r < CTRL_POWER_MIN:
            bad_pw.append(f"{tag}: потужність {r:.3f} < {CTRL_POWER_MIN}")

    ro6 = no.get("lag6_rev")
    if ro6 is None:
        bad_pw.append("однорідний OU/лаг6: реалізацій немає")
    elif ro6 < LAG6_POWER_MIN:
        bad_pw.append(f"вирішальна перевірка (лаг 6) бачить справжню "
                      f"реверсію лише у {ro6:.3f} < {LAG6_POWER_MIN}")

    r1 = ps.get("lag1_rev")
    if r1 is None:
        weak.append("синусоїда/лаг1: реалізацій немає")
    elif r1 < CTRL_ARTIFACT_MIN:
        weak.append(f"синусоїда підробляє реверсію на лазі 1 лише у "
                    f"{r1:.3f} < {CTRL_ARTIFACT_MIN}: контроль не "
                    f"відтворив артефакт, якого має боятись крок 3")

    if bad_fp:
        return False, "CONTROL-FAIL", "; ".join(bad_fp)
    if bad_pw:
        return False, "UNDERPOWERED-SLOT", "; ".join(bad_pw)
    if weak:
        return False, "CONTROL-WEAK", "; ".join(weak)
    return True, "CONTROL-OK", (
        f"блукання {nw.get('slot_beta_fire')}/{nw.get('absdiff_fire')}, "
        f"OU {no.get('slot_beta_fire')}, синусоїда лаг1 "
        f"{ps.get('lag1_rev')} / лаг6 {ps.get('lag6_rev')}, "
        f"потужність {ps.get('absdiff_fire')}/{pl.get('slot_beta_fire')}")


# ----------------------------------------------------------------------------
# Вердикт — ЧИСТА функція
# ----------------------------------------------------------------------------

def legs_verdict(leg_btc, leg_eth, beta_spread):
    """(label, note): чи живе реверсія в ногах.

    Нога «реверсує» = beta < 0, значуще проти блукання І за модулем не
    менша за LEG_BETA_SHARE_FRAC від спредової. Остання умова потрібна,
    бо ледь від'ємна значуща beta ноги не пояснює спредову.
    """
    thr = (abs(beta_spread) * LEG_BETA_SHARE_FRAC
           if beta_spread is not None else 0.0)
    flags = []
    for nm, res in (("BTC", leg_btc), ("ETH", leg_eth)):
        b = res.get("beta")
        share = (is_reverting(res) and b is not None and abs(b) >= thr)
        flags.append((nm, share, b, res.get("p_null")))
    n_share = sum(1 for _n, s, _b, _p in flags if s)
    detail = "; ".join(f"{nm}: beta {_fmt(b, 5)}, p {_fmt(p, 4)}"
                       f"{' [РЕВЕРСУЄ]' if s else ''}"
                       for nm, s, b, p in flags)
    if n_share >= 2:
        return "LEGS-SHARE", (f"обидві ноги реверсують самостійно з "
                              f"порівнянною силою -> ефект не належить "
                              f"спреду. {detail}")
    if n_share == 1:
        return "LEG-PARTIAL", (f"одна нога реверсує самостійно: спред "
                               f"успадковує реверсію за побудовою, а не "
                               f"з коінтеграції. {detail}")
    return "LEGS-CLEAN", detail


def step3_verdict(lag1, lag6, slot_beta, absdiff, legs_label, clabel,
                  cnote, n_common, min_n):
    """
    (label, note). ПРІОРИТЕТ І НАПРЯМОК:

      1) LOW-N            твердження про дані, істинне попри стан приладу.
      2) VOID             контроль показав, що прилад бреше.
      3) REFUSE           тест не побудований (мало слотів, вироджені
                          перестановки) — це не «немає ефекту».
      4) SLOT-EFFECT      p <= 0.05: сила реверсії залежить від слота.
      5) LAG6-NOT-CONFIRMED  beta на добовому кроці не пережила
                          перевірки. НАЗВА ОПИСОВА НАВМИСНЕ: 26 сер
                          гілка називалась LAG6-GONE і читалась як
                          «реверсія зникла», тоді як дані показували
                          узгоджену точкову оцінку (hl 1.51 проти 1.04),
                          яка НЕ ДОТЯГНУЛА до порога. Поріг не змінено,
                          змінено лише слово: реверсія
                          лага 1 не пережила прорідження.
      6) LEGS-SHARE       ефект є і в ногах — не про спред.
      7) SLOT-INCONCLUSIVE  потужність недоведена. ТУТ ЦЕ ГАСИТЬ PASS, на
                          відміну від кроку 2: бажаний висновок кроку 3 —
                          НУЛЬОВА гіпотеза, а сліпий прилад підтверджує
                          її завжди.
      8) PASS
    """
    if n_common < min_n:
        return "LOW-N", (f"спільних якорів {n_common} < {min_n}; "
                         f"вердикту немає")
    if lag1.get("n_pairs", 0) < MIN_PAIRS:
        return "LOW-N", (f"пар лага 1: {lag1.get('n_pairs')} < "
                         f"{MIN_PAIRS}")
    if lag6.get("n_pairs", 0) < MIN_PAIRS_LAG6:
        return "LOW-N", (f"пар лага 6: {lag6.get('n_pairs')} < "
                         f"{MIN_PAIRS_LAG6}")
    if clabel == "CONTROL-FAIL":
        return "VOID", f"вердикт анульовано контролем: {cnote}"
    if slot_beta is None:
        return "REFUSE", "перестановочний тест на beta не побудований"
    if len(slot_beta.get("slots_used", [])) < MIN_USABLE_SLOTS:
        return "REFUSE", (f"придатних слотів "
                          f"{len(slot_beta.get('slots_used', []))} < "
                          f"{MIN_USABLE_SLOTS}: слотовий тест не покриває "
                          f"добу")

    p_slot = slot_beta["p_value"]
    if p_slot <= P_SLOT_MAX:
        return "SLOT-EFFECT", (
            f"p = {p_slot:.4f} <= {P_SLOT_MAX}: beta різна по слотах "
            f"(розкид {slot_beta['stat_spread']:+.4f}, мінімум у слоті "
            f"{slot_beta['min_beta_slot']:02d}, максимум у "
            f"{slot_beta['max_beta_slot']:02d}) -> реверсія кроку 2 "
            f"щонайменше частково внутрішньодобова")

    b6 = lag6.get("beta")
    if b6 is None:
        return "REFUSE", f"beta лага 6 не оцінена: {lag6.get('reason')}"
    if not is_reverting(lag6):
        why = (f"{b6:+.5f} >= 0" if b6 >= 0.0 else
               f"{b6:+.5f} < 0, але p_null = "
               f"{_fmt(lag6.get('p_null'), 4)} > {P_NULL_MAX} — блукання "
               f"на цьому лазі дає таке саме не рідше (медіана нуля "
               f"{_fmt(lag6.get('null_median'), 5)})")
        return "LAG6-NOT-CONFIRMED", (
            f"beta на добовому кроці {why}: реверсія лага 1 не пережила "
            f"прорідження до одного знімка на добу — саме так поводиться "
            f"внутрішньодобовий артефакт")

    if legs_label == "LEGS-SHARE":
        return "LEGS-SHARE", ("реверсія живе в обох ногах окремо — "
                              "гіпотеза A не про спред")
    if clabel == "UNDERPOWERED-SLOT":
        return "SLOT-INCONCLUSIVE", (
            f"p = {p_slot:.4f} > {P_SLOT_MAX}, але потужність не "
            f"доведена ({cnote}). «Не побачили» != «немає»: тут це НЕ "
            f"PASS, бо бажаний висновок кроку 3 — нульова гіпотеза")
    if clabel == "CONTROL-WEAK":
        return "SLOT-INCONCLUSIVE", (
            f"p = {p_slot:.4f} > {P_SLOT_MAX}, але контроль неповний: "
            f"{cnote}")

    note = (f"beta однорідна по слотах (p = {p_slot:.4f} > {P_SLOT_MAX}); "
            f"beta лага 6 = {b6:+.5f} < 0 (half-life "
            f"{_fmt(lag6.get('hl_days'), 2)} діб); ноги: {legs_label}")
    if absdiff is not None and absdiff.get("p_value", 1.0) <= P_SLOT_MAX:
        note += (f". [SEASONAL-ABSDIFF] масштаб приросту сезонний "
                 f"(p = {absdiff['p_value']:.4f}, пік у слоті "
                 f"{absdiff.get('peak_slot')}) — на СИЛУ реверсії це не "
                 f"впливає, але для виконання має значення")
    if legs_label == "LEG-PARTIAL":
        note += ". [LEG-PARTIAL] одна нога реверсує сама"
    return "PASS", note


# ----------------------------------------------------------------------------
# Замір
# ----------------------------------------------------------------------------

def run_measurement(rows, min_n, n_perm, nulls, ctrl_reps, ctrl_perm,
                    seed, min_pairs=MIN_SLOT_PAIRS, verbose=True,
                    do_power=True):
    stamps = [r[0] for r in rows]
    btc = [r[1] for r in rows]
    eth = [r[2] for r in rows]
    spread = [r[3] for r in rows]
    n = len(rows)

    nb1 = null_beta_lag(n, 1, nulls, seed + 11)
    nb6 = null_beta_lag(n, LAG_DAY, nulls, seed + 13)

    lag1 = reversion_at_lag(stamps, spread, 1, nulls, seed + 11,
                            STEP_DAYS_LAG1, min_pairs)
    lag6 = reversion_at_lag(stamps, spread, LAG_DAY, nulls, seed + 13,
                            STEP_DAYS_LAG6, min_pairs)

    buckets1, c1 = slot_pairs(stamps, spread, lag=1)
    sb, sb_reason = slot_beta_permutation(buckets1, n_perm, seed=seed,
                                          min_pairs=min_pairs)
    ad, ad_reason = absdiff_slot_test(stamps, spread, 1, n_perm, seed + 3)

    leg_b = reversion_at_lag(stamps, btc, 1, nulls, seed + 11,
                             STEP_DAYS_LAG1, min_pairs)
    leg_e = reversion_at_lag(stamps, eth, 1, nulls, seed + 11,
                             STEP_DAYS_LAG1, min_pairs)
    leg_b6 = reversion_at_lag(stamps, btc, LAG_DAY, nulls, seed + 13,
                              STEP_DAYS_LAG6, min_pairs)
    leg_e6 = reversion_at_lag(stamps, eth, LAG_DAY, nulls, seed + 13,
                              STEP_DAYS_LAG6, min_pairs)
    llabel, lnote = legs_verdict(leg_b, leg_e, lag1.get("beta"))

    rep = {"n_common": n,
           "first_utc": stamps[0].isoformat() if stamps else None,
           "last_utc": stamps[-1].isoformat() if stamps else None,
           "window_days": ((stamps[-1] - stamps[0]).total_seconds()
                           / 86400.0) if n >= 2 else None,
           "spread_stats": series_stats(spread) if n >= 4 else None,
           "pair_counters_lag1": c1,
           "lag1": lag1, "lag6": lag6,
           "slot_beta": sb, "slot_beta_reason": sb_reason,
           "absdiff": ad, "absdiff_reason": ad_reason,
           "legs": {"btc_lag1": leg_b, "eth_lag1": leg_e,
                    "btc_lag6": leg_b6, "eth_lag6": leg_e6,
                    "verdict": llabel, "note": lnote},
           "null_reps": {"lag1": len(nb1), "lag6": len(nb6)},
           "thresholds": {"p_slot_max": P_SLOT_MAX,
                          "p_null_max": P_NULL_MAX,
                          "min_n": min_n,
                          "min_slot_pairs": min_pairs,
                          "min_usable_slots": MIN_USABLE_SLOTS,
                          "ctrl_fp_max": CTRL_FP_MAX,
                          "ctrl_power_min": CTRL_POWER_MIN,
                          "ctrl_artifact_min": CTRL_ARTIFACT_MIN,
                          "ctrl_lag6_max": CTRL_LAG6_MAX,
                          "leg_share_frac": LEG_BETA_SHARE_FRAC}}

    if do_power:
        rep["power_lag6_at_estimated_hl"] = {
            "hl_days": lag6.get("hl_days"),
            "power": power_lag6_at(n, lag6.get("hl_days"), seed=seed + 91),
            "hl_days_lag1": lag1.get("hl_days"),
            "power_at_lag1_hl": power_lag6_at(n, lag1.get("hl_days"),
                                              seed=seed + 93),
            "reps": POWER_REPS, "nulls": POWER_NULLS, "gating": False}
    else:
        rep["power_lag6_at_estimated_hl"] = None

    if ctrl_reps <= 0:
        rep["controls"] = {"skipped": True}
        rep["final_verdict"] = "CONTROL-SKIPPED"
        rep["final_note"] = ("VOLEDGE_SLOT3_CTRL_REPS=0 — без контролів "
                             "вердикт НЕ видається")
        return rep

    if verbose and ctrl_reps < CTRL_REPS_MIN:
        print(f"[CTRL-WARN] реалізацій {ctrl_reps} < {CTRL_REPS_MIN}: "
              f"частки оцінені грубо.")
    ctrl = run_controls(n, ctrl_reps, ctrl_perm, seed + 17, nb1, nb6,
                        min_pairs, verbose=verbose)
    ok, clabel, cnote = control_verdict(ctrl)
    rep["controls"] = {"skipped": False, "reps": ctrl_reps,
                       "perm": ctrl_perm, "seed": seed + 17,
                       "rates": ctrl, "ok": ok,
                       "verdict": clabel, "note": cnote}

    fl, fn = step3_verdict(lag1, lag6, sb, ad, llabel, clabel, cnote,
                           n, min_n)
    rep["final_verdict"] = fl
    rep["final_note"] = fn
    return rep


# ----------------------------------------------------------------------------
# Друк
# ----------------------------------------------------------------------------

def print_report(rep, counters):
    print()
    print("-" * 72)
    print("ЯКОРІ І ПАРИ")
    print("-" * 72)
    print(f"  артефактів переглянуто : {counters['artifacts']}")
    print(f"  стара сітка            : {counters['legacy_grid']}")
    print(f"  поза сіткою (ручні)    : {counters['off_grid']}")
    print(f"  фічі відсутні          : {counters['missing_feature']}")
    print(f"  ЯКОРІВ                 : {counters['kept']}")
    c1 = rep["pair_counters_lag1"]
    print(f"  пар лаг 1: побачено {c1['pairs_seen']}, взято "
          f"{c1['kept']}, дірка {c1['bad_gap']}, стара сітка "
          f"{c1['legacy_grid']}, поза сіткою {c1['off_grid']}")
    c6 = rep["lag6"]["counters"]
    print(f"  пар лаг 6: побачено {c6['pairs_seen']}, взято "
          f"{c6['kept']}, дірка {c6['bad_gap']}  "
          f"(вікно {c6['gap_min_h']:.1f}-{c6['gap_max_h']:.1f} год)")
    print(f"  вікно UTC              : {rep['first_utc']} .. "
          f"{rep['last_utc']}")
    print(f"  довжина вікна, діб     : {_fmt(rep['window_days'], 1)}")
    st = rep.get("spread_stats") or {}
    print(f"  спред: r_lag1 {_fmt(st.get('r1'), 4)}  n_eff "
          f"{_fmt(st.get('n_eff'), 2)}  sd {_fmt(st.get('sd'), 4)}")

    print()
    print("-" * 72)
    print("ПЕРЕВІРКА 1: BETA ПО СЛОТАХ ДОБИ (перестановочний тест)")
    print("-" * 72)
    sb = rep["slot_beta"]
    if sb is None:
        print(f"  [REFUSE] {rep['slot_beta_reason']}")
    else:
        print(f"{'слот':>6}{'пар':>7}{'beta':>12}{'half-life, діб':>18}")
        for h in sb["slots_used"]:
            d = sb["per_slot"][str(h)]
            print(f"{h:>4}:00{d['n_pairs']:>7}{d['beta']:>12.5f}"
                  f"{_fmt(d['hl_days'], 2):>18}")
        print(f"  пулова beta      : {_fmt(sb['beta_pooled'], 5)}")
        print(f"  розкид (max-min) : {sb['stat_spread']:+.5f}  "
              f"(мін у {sb['min_beta_slot']:02d}, макс у "
              f"{sb['max_beta_slot']:02d})")
        print(f"  перестановок     : {sb['n_valid']} придатних, "
              f"{sb['n_degenerate']} вироджених")
        print(f"  p                : {sb['p_value']:.4f}  "
              f"(поріг {P_SLOT_MAX}: p > порога = ефекту НЕМАЄ)")

    print()
    print("-" * 72)
    print("ПЕРЕВІРКА 2: BETA НА ДОБОВОМУ КРОЦІ (лаг 6)")
    print("-" * 72)
    for tag, res in (("лаг 1 (4 год)", rep["lag1"]),
                     ("лаг 6 (доба) ", rep["lag6"])):
        print(f"  {tag}: beta {_fmt(res['beta'], 5)}  half-life "
              f"{_fmt(res['hl_days'], 2)} діб  p_null "
              f"{_fmt(res['p_null'], 4)}  (пар {res['n_pairs']}, "
              f"нуль-медіана {_fmt(res['null_median'], 5)})")
    print("  beta лага 6 по слотах (кожен ряд усередині слота "
          "НЕПЕРЕКРИВНИЙ):")
    for h in sorted(int(k) for k in rep["lag6"]["per_slot_beta"]):
        d = rep["lag6"]["per_slot_beta"][str(h)]
        print(f"    {h:02d}:00  пар {d['n_pairs']:>3}  beta "
              f"{_fmt(d['beta'], 5)}")
    print(f"  КРИТЕРІЙ: beta лага 6 ВІД'ЄМНА І p_null <= {P_NULL_MAX}.")
    print(f"  (сам ЗНАК порожній: на блуканні beta6 < 0 у 95% випадків,")
    print(f"   бо медіана нуля {_fmt(rep['lag6']['null_median'], 5)}, "
          f"а не 0 — правка внесена до заміру)")

    pw = rep.get("power_lag6_at_estimated_hl")
    if pw:
        print()
        print("-" * 72)
        print("ПОТУЖНІСТЬ ПЕРЕВІРКИ 2 НА ОЦІНЕНИХ ГОРИЗОНТАХ "
              "(ДІАГНОСТИКА, НЕ ГЕЙТ)")
        print("-" * 72)
        print(f"  hl лага 6 = {_fmt(pw['hl_days'], 2)} діб -> потужність "
              f"{_fmt(pw['power'], 3)}")
        print(f"  hl лага 1 = {_fmt(pw['hl_days_lag1'], 2)} діб -> "
              f"потужність {_fmt(pw['power_at_lag1_hl'], 3)}")
        print(f"  (гейт контролю рахує сліпоту на hl = {CTRL_OU_HL_D} "
              f"доби — це ТРЕТІЙ горизонт, і саме тому обидва вище")
        print(f"   друкуються: інакше «не підтверджено» неможливо")
        print(f"   відрізнити від «прилад не бачить на цій довжині»)")
        if pw["power"] is not None and pw["power"] < CTRL_POWER_MIN:
            print(f"  [LOW-POWER-AT-ESTIMATE] на власному оціненому "
                  f"горизонті перевірка 2 нижча за {CTRL_POWER_MIN}: "
                  f"негативний результат тут — НЕ спростування")

    print()
    print("-" * 72)
    print("ДІАГНОСТИКА: |ПРИРІСТ| ПО СЛОТАХ (тест із anomaly_calibrate)")
    print("-" * 72)
    ad = rep["absdiff"]
    if ad is None:
        print(f"  [SKIP] {rep['absdiff_reason']}")
    else:
        print(f"  p {ad['p_value']:.4f}, розкид медіан "
              f"{ad['stat_spread']:.6f}, піковий слот "
              f"{ad.get('peak_slot')}, ratio пік/решта "
              f"{_fmt(ad.get('ratio_peak_vs_rest'), 3)}")
        print("  ЦЕ ІНША СТАТИСТИКА: масштаб приросту, не сила реверсії.")
        print("  Адитивна сезонність із beta скорочується за побудовою,")
        print("  тож сюди вона потрапляє, а у перевірку 1 — ні.")

    print()
    print("-" * 72)
    print("ПЕРЕВІРКА 4: КОНТРОЛЬ НА НОГАХ")
    print("-" * 72)
    lg = rep["legs"]
    for tag, key in (("BTC.atm30 лаг 1", "btc_lag1"),
                     ("ETH.atm30 лаг 1", "eth_lag1"),
                     ("BTC.atm30 лаг 6", "btc_lag6"),
                     ("ETH.atm30 лаг 6", "eth_lag6")):
        r = lg[key]
        print(f"  {tag}: beta {_fmt(r['beta'], 5)}  p_null "
              f"{_fmt(r['p_null'], 4)}  half-life "
              f"{_fmt(r['hl_days'], 2)} діб")
    print(f"  спред лаг 1 : beta {_fmt(rep['lag1']['beta'], 5)}")
    print(f"  [{lg['verdict']}] {lg['note']}")

    print()
    print("-" * 72)
    print("КОНТРОЛІ ПРИЛАДУ")
    print("-" * 72)
    c = rep["controls"]
    if c.get("skipped"):
        print("  [SKIP] контролі вимкнено — вердикт не видається")
    else:
        r = c["rates"]
        print(f"  реалізацій {c['reps']}, перестановок у контролі "
              f"{c['perm']}")
        print(f"  НЕГ блукання      : beta-тест "
              f"{_fmt(r['neg_walk']['slot_beta_fire'])}, |diff|-тест "
              f"{_fmt(r['neg_walk']['absdiff_fire'])}, лаг6 "
              f"{_fmt(r['neg_walk']['lag6_rev'])}  "
              f"(пороги <= {CTRL_FP_MAX})")
        print(f"  НЕГ однорідний OU : beta-тест "
              f"{_fmt(r['neg_ou']['slot_beta_fire'])} (не мусить "
              f"виглядати слотовою);  лаг6 бачить її у "
              f"{_fmt(r['neg_ou']['lag6_rev'])} (поріг >= "
              f"{LAG6_POWER_MIN}) — ПОТУЖНІСТЬ вирішальної перевірки")
        print(f"  ПОЗ синусоїда     : |diff|-тест "
              f"{_fmt(r['pos_sin']['absdiff_fire'])} (поріг >= "
              f"{CTRL_POWER_MIN});  підробка реверсії: лаг 1 "
              f"{_fmt(r['pos_sin']['lag1_rev'])} (поріг >= "
              f"{CTRL_ARTIFACT_MIN}), лаг 6 "
              f"{_fmt(r['pos_sin']['lag6_rev'])} (поріг <= "
              f"{CTRL_LAG6_MAX})")
        print(f"     ^ ЦЕ ГОЛОВНИЙ РЯДОК КРОКУ 3: лаг 6 імунний до "
              f"сезонності, лаг 1 — ні")
        print(f"  ПОЗ локальна beta : beta-тест "
              f"{_fmt(r['pos_slot']['slot_beta_fire'])}  (поріг >= "
              f"{CTRL_POWER_MIN}) — ПОТУЖНІСТЬ перевірки 1")
        print(f"  [{c['verdict']}] {c['note']}")

    print()
    print("=" * 72)
    print(f"  ВЕРДИКТ КРОКУ 3: [{rep['final_verdict']}] "
          f"{rep['final_note']}")
    print("=" * 72)
    fv = rep["final_verdict"]
    if fv == "PASS":
        print("  -> реверсія кроку 2 не є внутрішньодобовим артефактом:")
        print("     сила однакова в усіх слотах, переживає прорідження")
        print("     до одного знімка на добу і не пояснюється ногами.")
        print("     ДАЛІ: торгованість — спред стакана, гамма-хедж,")
        print("     виконання. Це інший клас питань.")
    elif fv == "SLOT-EFFECT":
        print("  -> сила реверсії залежить від часу доби. Гіпотеза A в")
        print("     теперішньому вигляді закрита: те, що крок 2 назвав")
        print("     реверсією, щонайменше частково є сезонністю сітки.")
        print("     Шукати джерело так само, як шукали пилку vrp30.")
    elif fv == "LAG6-NOT-CONFIRMED":
        print("  -> на добовому кроці реверсія НЕ ПІДТВЕРДЖЕНА. Це не")
        print("     те саме, що спростована: дивіться, чи узгоджені")
        print("     точкові оцінки half-life на двох лагах і чи всі")
        print("     слотові beta лага 6 одного знаку. Якщо так —")
        print("     ефект не зник, а не дотягнув до порога, і рядок")
        print("     потужності на ОЦІНЕНОМУ горизонті вище каже,")
        print("     скільки ще збирати. Артефакт поводиться інакше:")
        print("     у контролі з синусоїдою лаг 6 давав значущу")
        print("     реверсію в 0.000 випадків — ефект ЗНИКАВ.")
        print("     У будь-якому разі крок 3 НЕ пройдено: стратегію")
        print("     не будувати, пороги не рухати.")
    elif fv == "LEGS-SHARE":
        print("  -> ефект не належить спреду. Виграш у n_eff (крок 1)")
        print("     лишається чинним, але структура — не про кроси.")
    elif fv == "SLOT-INCONCLUSIVE":
        print("  -> вердикту немає. «Ефекту не видно» при недоведеній")
        print("     потужності — це НЕ «ефекту немає». Накопичувати")
        print("     дані і перезаміряти; порогів НЕ рухати.")
    elif fv == "VOID":
        print("  -> прилад несправний, число недійсне.")
    elif fv == "REFUSE":
        print("  -> тест не побудований. Це відмова, не результат.")


# ----------------------------------------------------------------------------
# Самотести
# ----------------------------------------------------------------------------

def run_self_tests():
    tc = TestCounters()
    base = datetime(2026, 8, 1, 0, 10, tzinfo=timezone.utc)

    # --- 1. Білий список сітки ----------------------------------------------
    tc.check("grid_accepts_canonical",
             on_canonical_grid(datetime(2026, 8, 1, 12, 10,
                                        tzinfo=timezone.utc)))
    tc.check("grid_rejects_hour_18",
             not on_canonical_grid(datetime(2026, 8, 1, 18, 35,
                                            tzinfo=timezone.utc)))
    # Дві ручні точки з КАНОНІЧНОЮ годиною — перевірка години сама їх
    # НЕ ловить. Це і є причина, чому фільтр по хвилині тут є.
    tc.check("grid_rejects_canonical_hour_bad_minute",
             not on_canonical_grid(datetime(2026, 8, 1, 16, 35,
                                            tzinfo=timezone.utc)))
    tc.check("grid_rejects_0855",
             not on_canonical_grid(datetime(2026, 8, 1, 8, 55,
                                            tzinfo=timezone.utc)))
    tc.check("grid_accepts_minute_bounds",
             on_canonical_grid(datetime(2026, 8, 1, 4, 5,
                                        tzinfo=timezone.utc))
             and on_canonical_grid(datetime(2026, 8, 1, 4, 15,
                                            tzinfo=timezone.utc)))

    # --- 2. Пари по слотах ---------------------------------------------------
    st_ok = synth_stamps(60, base)
    vals = [float(i) for i in range(60)]
    b1, c1 = slot_pairs(st_ok, vals, lag=1)
    tc.check("slot_pairs_lag1_kept", c1["kept"] == 59 and c1["bad_gap"] == 0,
             str(c1))
    tc.check("slot_pairs_six_buckets", len(b1) == 6, str(sorted(b1)))
    tc.check("slot_pairs_labelled_by_later",
             b1[4][0] == (0.0, 1.0), str(b1[4][0]))
    b6, c6 = slot_pairs(st_ok, vals, lag=LAG_DAY)
    tc.check("slot_pairs_lag6_window",
             abs(c6["gap_min_h"] - 23.5) < 1e-9
             and abs(c6["gap_max_h"] - 24.5) < 1e-9, str(c6))
    tc.check("slot_pairs_lag6_kept", c6["kept"] == 54, str(c6))
    tc.check("slot_pairs_lag6_same_slot",
             all(len(v) == 9 for v in b6.values()),
             str({h: len(v) for h, v in b6.items()}))
    # Дірка в сітці: пропущений знімок вибиває пари, а не мовчки
    # підмішує 8-годинний приріст.
    st_gap = list(st_ok)
    v_gap = list(vals)
    del st_gap[10]
    del v_gap[10]
    _bg, cg = slot_pairs(st_gap, v_gap, lag=1)
    tc.check("slot_pairs_drops_gap", cg["bad_gap"] == 1
             and cg["kept"] == 57, str(cg))
    tc.check("slot_pairs_counters_add_up",
             cg["kept"] + cg["bad_gap"] + cg["off_grid"]
             + cg["legacy_grid"] == cg["pairs_seen"], str(cg))
    # Ручний знімок 16:35 не має проходити НАВІТЬ маючи канонічну годину.
    st_man = list(st_ok)
    st_man[10] = st_man[10].replace(minute=35)
    _bm, cm = slot_pairs(st_man, vals, lag=1)
    tc.check("slot_pairs_drops_manual_minute", cm["off_grid"] == 2,
             str(cm))
    tc.check("slot_pairs_rejects_lag_zero",
             _raises(lambda: slot_pairs(st_ok, vals, lag=0), ValueError))

    # --- 3. Допоміжне --------------------------------------------------------
    tc.check("pool_preserves_count",
             len(pool_pairs(b1)) == c1["kept"])
    tc.check("usable_filters_small",
             len(usable_slots({0: [(1.0, 1.0)] * 5, 4: [(1.0, 1.0)] * 20},
                              12)) == 1)
    hl = hl_days_from_beta(-0.10511, STEP_DAYS_LAG1)
    tc.check("hl_matches_step2", abs(hl - 1.04) < 0.02, _fmt(hl, 3))
    tc.check("hl_lag6_uses_day_step",
             abs(hl_days_from_beta(-0.5, STEP_DAYS_LAG6) - 1.0) < 1e-9,
             _fmt(hl_days_from_beta(-0.5, STEP_DAYS_LAG6), 4))
    tc.check("hl_none_on_divergent",
             hl_days_from_beta(0.5, STEP_DAYS_LAG1) is None)
    tc.check("hl_none_on_none",
             hl_days_from_beta(None, STEP_DAYS_LAG1) is None)

    # --- 4. Перестановочний тест на beta: відома відповідь -------------------
    # Однорідний OU: beta однакова по слотах -> p має бути ВЕЛИКИМ.
    rng = random.Random(101)
    ou_v = gen_ou(rng, 600, CTRL_OU_HL_D, 1.0)
    st600 = synth_stamps(600, base)
    bou, _c = slot_pairs(st600, ou_v, lag=1)
    res_ou, _r = slot_beta_permutation(bou, 400, seed=7)
    tc.check("perm_homogeneous_not_significant",
             res_ou is not None and res_ou["p_value"] > P_SLOT_MAX,
             _fmt(res_ou["p_value"], 4) if res_ou else "None")
    tc.check("perm_reports_six_slots",
             res_ou is not None and len(res_ou["slots_used"]) == 6,
             str(res_ou["slots_used"]) if res_ou else "None")
    tc.check("perm_pooled_beta_negative",
             res_ou is not None and res_ou["beta_pooled"] < 0.0,
             _fmt(res_ou["beta_pooled"], 5) if res_ou else "None")
    # Локалізована реверсія: p має бути МАЛИМ. Без цього тесту «p > 0.05»
    # на живих даних не означало б нічого.
    loc = gen_slot_local_reversion(random.Random(102), 600,
                                   SLOT_LOCAL_HOUR // 4)
    bloc, _c = slot_pairs(st600, loc, lag=1)
    res_loc, _r = slot_beta_permutation(bloc, 400, seed=7)
    tc.check("perm_detects_local_reversion",
             res_loc is not None and res_loc["p_value"] <= P_SLOT_MAX,
             _fmt(res_loc["p_value"], 4) if res_loc else "None")
    tc.check("perm_finds_the_right_slot",
             res_loc is not None
             and res_loc["min_beta_slot"] == SLOT_LOCAL_HOUR,
             str(res_loc["min_beta_slot"]) if res_loc else "None")
    tc.check("perm_p_never_zero",
             res_loc is not None and res_loc["p_value"] > 0.0)
    tc.check("perm_refuses_one_slot",
             slot_beta_permutation({0: [(1.0, 0.1)] * 30}, 100)[0] is None)
    tc.check("perm_refuses_zero_perm",
             slot_beta_permutation(bou, 0)[0] is None)
    tc.check("perm_refuses_degenerate_slot",
             slot_beta_permutation({0: [(1.0, 0.1)] * 30,
                                    4: [(1.0, 0.2)] * 30}, 50)[0] is None,
             "константний рівень -> beta не існує")

    # --- 4b. АДИТИВНА СЕЗОННІСТЬ НЕ ВИДНА У BETA (причина двох тестів) ------
    # Це не дефект, а властивість статистики, і саме через неї
    # пре-реєстрований контроль із синусоїдою переїхав на |diff|-тест.
    rw_base = gen_random_walk(random.Random(103), 600, 1.0)
    sin_add = [v + SIN_AMP * math.sin(2.0 * math.pi * i / STEPS_PER_DAY)
               for i, v in enumerate(rw_base)]
    b_pure, _c = slot_pairs(st600, rw_base, lag=1)
    b_sin, _c = slot_pairs(st600, sin_add, lag=1)
    r_pure, _r = slot_beta_permutation(b_pure, 300, seed=9)
    r_sin, _r = slot_beta_permutation(b_sin, 300, seed=9)
    tc.check("beta_test_blind_to_additive_season",
             r_sin is not None and r_sin["p_value"] > P_SLOT_MAX,
             _fmt(r_sin["p_value"], 4) if r_sin else "None")
    # ...а |diff|-тест її бачить. Дві статистики, два різні призначення.
    ad_sin, _r = absdiff_slot_test(st600, sin_add, 1, 300, 11)
    tc.check("absdiff_test_sees_additive_season",
             ad_sin is not None and ad_sin["p_value"] <= P_SLOT_MAX,
             _fmt(ad_sin["p_value"], 4) if ad_sin else "None")
    ad_pure, _r = absdiff_slot_test(st600, rw_base, 1, 300, 11)
    tc.check("absdiff_test_quiet_on_walk",
             ad_pure is not None and ad_pure["p_value"] > P_SLOT_MAX,
             _fmt(ad_pure["p_value"], 4) if ad_pure else "None")
    tc.check("absdiff_reuses_calibrate_counters",
             ad_pure is not None and "counters" in ad_pure
             and ad_pure["counters"]["lag"] == 1)
    del r_pure

    # --- 5. Нульовий розподіл, узгоджений із лагом ---------------------------
    nb1 = null_beta_lag(200, 1, 300, 555)
    nb6 = null_beta_lag(200, LAG_DAY, 300, 555)
    tc.check("null_lag1_matches_step2_estimator",
             abs(_median(nb1)
                 - _median(null_beta_distribution(200, 300, 555))) < 0.01,
             f"{_fmt(_median(nb1), 5)} vs "
             f"{_fmt(_median(null_beta_distribution(200, 300, 555)), 5)}")
    tc.check("null_lag6_is_more_negative",
             _median(nb6) < _median(nb1),
             f"{_fmt(_median(nb6), 5)} vs {_fmt(_median(nb1), 5)}")
    tc.check("null_lag1_biased_negative", _median(nb1) < -0.003,
             _fmt(_median(nb1), 5))
    tc.check("null_reps_kept", len(nb1) >= 290 and len(nb6) >= 290)
    # Той самий урок, що на кроці 2: під нулем оцінювач НЕ дає нуля.
    # Якщо колись медіана стане ~0 — оцінювач змінили.
    tc.check("null_lag6_bias_is_material",
             _median(nb6) < -0.05, _fmt(_median(nb6), 5))
    # ЧОМУ ПРЕ-РЕЄСТРОВАНИЙ КРИТЕРІЙ ЗАМІНЕНО: сам знак beta6 під нулем
    # від'ємний майже завжди. Якщо ця частка колись впаде до ~0.5,
    # значить оцінювач або лаг змінили — і критерій треба переглядати.
    tc.check("sign_only_lag6_criterion_is_vacuous",
             sum(1 for b in nb6 if b < 0) / float(len(nb6)) > 0.80,
             _fmt(sum(1 for b in nb6 if b < 0) / float(len(nb6)), 3))
    tc.check("sign_only_lag1_criterion_is_weak_too",
             sum(1 for b in nb1 if b < 0) / float(len(nb1)) > 0.55,
             _fmt(sum(1 for b in nb1 if b < 0) / float(len(nb1)), 3))

    # --- 6. is_reverting: гілки за знаком ------------------------------------
    tc.check("reverting_needs_negative_beta",
             not is_reverting({"beta": 0.01, "p_null": 0.001}))
    tc.check("reverting_needs_significance",
             not is_reverting({"beta": -0.10, "p_null": 0.30}))
    tc.check("reverting_true_when_both",
             is_reverting({"beta": -0.10, "p_null": 0.01}))
    tc.check("reverting_false_on_none",
             not is_reverting({"beta": None, "p_null": None}))
    tc.check("reverting_boundary_included",
             is_reverting({"beta": -0.10, "p_null": P_NULL_MAX}))

    # --- 7. Ноги -------------------------------------------------------------
    rev = {"beta": -0.10, "p_null": 0.01}
    flat = {"beta": -0.001, "p_null": 0.60}
    weak = {"beta": -0.01, "p_null": 0.01}
    tc.check("legs_clean", legs_verdict(flat, flat, -0.10)[0]
             == "LEGS-CLEAN")
    tc.check("legs_share_when_both", legs_verdict(rev, rev, -0.10)[0]
             == "LEGS-SHARE")
    tc.check("legs_partial_when_one", legs_verdict(rev, flat, -0.10)[0]
             == "LEG-PARTIAL")
    # Значуща, але ВТРИЧІ слабша нога спредову beta не пояснює.
    tc.check("legs_ignore_too_weak",
             legs_verdict(weak, weak, -0.10)[0] == "LEGS-CLEAN",
             "|beta| 0.01 < 0.5 * 0.10")

    # --- 8. Контрольний вердикт: гілки за знаком -----------------------------
    good = {"neg_walk": {"slot_beta_fire": 0.04, "absdiff_fire": 0.05,
                         "lag1_rev": 0.05, "lag6_rev": 0.06},
            "neg_ou": {"slot_beta_fire": 0.05, "lag1_rev": 0.92,
                       "lag6_rev": 0.85},
            "pos_sin": {"absdiff_fire": 0.95, "lag1_rev": 0.90,
                        "lag6_rev": 0.03},
            "pos_slot": {"slot_beta_fire": 0.92}}
    ok, lab, _n = control_verdict(good)
    tc.check("control_ok", ok and lab == "CONTROL-OK", lab)
    bad_fp = json.loads(json.dumps(good))
    bad_fp["neg_walk"]["slot_beta_fire"] = 0.40
    tc.check("control_catches_false_positive",
             control_verdict(bad_fp)[1] == "CONTROL-FAIL")
    bad_ou = json.loads(json.dumps(good))
    bad_ou["neg_ou"]["slot_beta_fire"] = 0.35
    tc.check("homogeneous_ou_must_not_look_slotted",
             control_verdict(bad_ou)[1] == "CONTROL-FAIL")
    bad6 = json.loads(json.dumps(good))
    bad6["pos_sin"]["lag6_rev"] = 0.50
    tc.check("lag6_immunity_failure_is_fatal",
             control_verdict(bad6)[1] == "CONTROL-FAIL",
             "лаг 6 — головна перевірка; якщо він ловиться на "
             "сезонність, вердикт недійсний")
    bad_w6 = json.loads(json.dumps(good))
    bad_w6["neg_walk"]["lag6_rev"] = 0.35
    tc.check("walk_faking_lag6_is_fatal",
             control_verdict(bad_w6)[1] == "CONTROL-FAIL")
    blind6 = json.loads(json.dumps(good))
    blind6["neg_ou"]["lag6_rev"] = 0.40
    tc.check("blind_lag6_is_underpowered",
             control_verdict(blind6)[1] == "UNDERPOWERED-SLOT",
             "вирішальна перевірка сліпа -> PASS неможливий")
    low_pw = json.loads(json.dumps(good))
    low_pw["pos_slot"]["slot_beta_fire"] = 0.30
    tc.check("blindness_is_underpowered_not_fail",
             control_verdict(low_pw)[1] == "UNDERPOWERED-SLOT")
    weak_c = json.loads(json.dumps(good))
    weak_c["pos_sin"]["lag1_rev"] = 0.10
    tc.check("artifact_not_reproduced_is_weak",
             control_verdict(weak_c)[1] == "CONTROL-WEAK")
    # Пріоритет: хибний позитив старший за брак потужності.
    both = json.loads(json.dumps(low_pw))
    both["neg_walk"]["absdiff_fire"] = 0.50
    tc.check("false_positive_outranks_underpower",
             control_verdict(both)[1] == "CONTROL-FAIL")
    miss = json.loads(json.dumps(good))
    miss["neg_ou"]["slot_beta_fire"] = None
    tc.check("missing_control_is_fail",
             control_verdict(miss)[1] == "CONTROL-FAIL")

    # --- 9. step3_verdict: ЧИСТА функція, усі гілки ---------------------------
    L1 = {"n_pairs": 150, "beta": -0.105}
    L6 = {"n_pairs": 140, "beta": -0.30, "hl_days": 1.9, "reason": "OK",
          "p_null": 0.01, "null_median": -0.126}
    SB_OK = {"p_value": 0.40, "stat_spread": 0.10, "slots_used": [0, 4, 8,
                                                                  12, 16,
                                                                  20],
             "min_beta_slot": 8, "max_beta_slot": 20}
    SB_HIT = dict(SB_OK, p_value=0.01)
    tc.check("v_pass",
             step3_verdict(L1, L6, SB_OK, None, "LEGS-CLEAN", "CONTROL-OK",
                           "c", 159, 60)[0] == "PASS")
    tc.check("v_slot_effect",
             step3_verdict(L1, L6, SB_HIT, None, "LEGS-CLEAN",
                           "CONTROL-OK", "c", 159, 60)[0] == "SLOT-EFFECT")
    tc.check("v_p_boundary_is_effect",
             step3_verdict(L1, L6, dict(SB_OK, p_value=P_SLOT_MAX), None,
                           "LEGS-CLEAN", "CONTROL-OK", "c", 159, 60)[0]
             == "SLOT-EFFECT")
    tc.check("v_lag6_gone",
             step3_verdict(L1, dict(L6, beta=0.02), SB_OK, None,
                           "LEGS-CLEAN", "CONTROL-OK", "c", 159, 60)[0]
             == "LAG6-NOT-CONFIRMED")
    # ПОРОЖНІЙ КРИТЕРІЙ, ЗАФІКСОВАНИЙ ТЕСТОМ: від'ємна, але незначуща
    # beta лага 6 — це блукання, а не реверсія. Пре-реєстрований рядок
    # «має лишитись від'ємною» пропустив би це.
    tc.check("v_lag6_negative_but_insignificant_is_gone",
             step3_verdict(L1, dict(L6, beta=-0.12, p_null=0.60), SB_OK,
                           None, "LEGS-CLEAN", "CONTROL-OK", "c", 159,
                           60)[0] == "LAG6-NOT-CONFIRMED")
    tc.check("v_lag6_note_names_the_null_median",
             "медіана нуля" in step3_verdict(
                 L1, dict(L6, beta=-0.12, p_null=0.60), SB_OK, None,
                 "LEGS-CLEAN", "CONTROL-OK", "c", 159, 60)[1])
    tc.check("v_lag6_zero_is_gone",
             step3_verdict(L1, dict(L6, beta=0.0), SB_OK, None,
                           "LEGS-CLEAN", "CONTROL-OK", "c", 159, 60)[0]
             == "LAG6-NOT-CONFIRMED")
    tc.check("v_legs_share",
             step3_verdict(L1, L6, SB_OK, None, "LEGS-SHARE",
                           "CONTROL-OK", "c", 159, 60)[0] == "LEGS-SHARE")
    tc.check("v_leg_partial_still_pass",
             step3_verdict(L1, L6, SB_OK, None, "LEG-PARTIAL",
                           "CONTROL-OK", "c", 159, 60)[0] == "PASS")
    tc.check("v_leg_partial_flagged",
             "LEG-PARTIAL" in step3_verdict(L1, L6, SB_OK, None,
                                            "LEG-PARTIAL", "CONTROL-OK",
                                            "c", 159, 60)[1])
    # НАПРЯМОК, ПРОТИЛЕЖНИЙ ДО КРОКУ 2: тут брак потужності ГАСИТЬ PASS.
    tc.check("underpower_kills_pass_here",
             step3_verdict(L1, L6, SB_OK, None, "LEGS-CLEAN",
                           "UNDERPOWERED-SLOT", "c", 159, 60)[0]
             == "SLOT-INCONCLUSIVE")
    tc.check("underpower_note_explains_direction",
             "нульова гіпотеза" in step3_verdict(
                 L1, L6, SB_OK, None, "LEGS-CLEAN", "UNDERPOWERED-SLOT",
                 "c", 159, 60)[1])
    # ...але позитивний результат (ефект Є) від слабкості не залежить:
    # слабкий тест не вигадує ефектів, за це відповідає негативний
    # контроль. Тому SLOT-EFFECT переживає UNDERPOWERED.
    tc.check("underpower_does_not_erase_detected_effect",
             step3_verdict(L1, L6, SB_HIT, None, "LEGS-CLEAN",
                           "UNDERPOWERED-SLOT", "c", 159, 60)[0]
             == "SLOT-EFFECT")
    tc.check("weak_control_blocks_pass",
             step3_verdict(L1, L6, SB_OK, None, "LEGS-CLEAN",
                           "CONTROL-WEAK", "c", 159, 60)[0]
             == "SLOT-INCONCLUSIVE")
    tc.check("control_fail_voids",
             step3_verdict(L1, L6, SB_OK, None, "LEGS-CLEAN",
                           "CONTROL-FAIL", "c", 159, 60)[0] == "VOID")
    tc.check("lown_outranks_control_fail",
             step3_verdict(L1, L6, SB_OK, None, "LEGS-CLEAN",
                           "CONTROL-FAIL", "c", 40, 60)[0] == "LOW-N")
    tc.check("lown_by_pairs_lag1",
             step3_verdict({"n_pairs": 10, "beta": -0.1}, L6, SB_OK, None,
                           "LEGS-CLEAN", "CONTROL-OK", "c", 159, 60)[0]
             == "LOW-N")
    tc.check("lown_by_pairs_lag6",
             step3_verdict(L1, dict(L6, n_pairs=5), SB_OK, None,
                           "LEGS-CLEAN", "CONTROL-OK", "c", 159, 60)[0]
             == "LOW-N")
    tc.check("refuse_without_slot_test",
             step3_verdict(L1, L6, None, None, "LEGS-CLEAN", "CONTROL-OK",
                           "c", 159, 60)[0] == "REFUSE")
    tc.check("refuse_on_too_few_slots",
             step3_verdict(L1, L6, dict(SB_OK, slots_used=[0, 4]), None,
                           "LEGS-CLEAN", "CONTROL-OK", "c", 159, 60)[0]
             == "REFUSE")
    tc.check("refuse_without_beta6",
             step3_verdict(L1, dict(L6, beta=None), SB_OK, None,
                           "LEGS-CLEAN", "CONTROL-OK", "c", 159, 60)[0]
             == "REFUSE")
    tc.check("absdiff_flag_is_not_fatal",
             step3_verdict(L1, L6, SB_OK, {"p_value": 0.001,
                                           "peak_slot": 16},
                           "LEGS-CLEAN", "CONTROL-OK", "c", 159, 60)[0]
             == "PASS")
    tc.check("absdiff_flag_is_printed",
             "SEASONAL-ABSDIFF" in step3_verdict(
                 L1, L6, SB_OK, {"p_value": 0.001, "peak_slot": 16},
                 "LEGS-CLEAN", "CONTROL-OK", "c", 159, 60)[1])

    # --- 10. Наскрізь --------------------------------------------------------
    def _rows(values, start=base):
        return [(start + timedelta(hours=4 * i), 0.5, 0.5 + v, v)
                for i, v in enumerate(values)]

    rows_ou = _rows(gen_ou(random.Random(31), 400, CTRL_OU_HL_D, 0.012))
    rep_ou = run_measurement(rows_ou, 60, 300, 200, 0, 100, SEED_DEFAULT,
                             verbose=False, do_power=False)
    tc.check("e2e_ctrl_zero_is_explicit",
             rep_ou["final_verdict"] == "CONTROL-SKIPPED"
             and rep_ou["controls"]["skipped"] is True)
    tc.check("e2e_homogeneous_ou_no_slot_effect",
             rep_ou["slot_beta"]["p_value"] > P_SLOT_MAX,
             _fmt(rep_ou["slot_beta"]["p_value"], 4))
    tc.check("e2e_lag6_negative_on_real_reversion",
             rep_ou["lag6"]["beta"] < 0.0,
             _fmt(rep_ou["lag6"]["beta"], 5))
    tc.check("e2e_both_lags_agree_on_hl",
             rep_ou["lag1"]["hl_days"] is not None
             and rep_ou["lag6"]["hl_days"] is not None
             and 0.2 < (rep_ou["lag6"]["hl_days"]
                        / rep_ou["lag1"]["hl_days"]) < 5.0,
             f"{_fmt(rep_ou['lag1']['hl_days'], 2)} vs "
             f"{_fmt(rep_ou['lag6']['hl_days'], 2)}")
    tc.check("e2e_records_thresholds",
             rep_ou["thresholds"]["p_slot_max"] == P_SLOT_MAX
             and rep_ou["thresholds"]["ctrl_lag6_max"] == CTRL_LAG6_MAX)
    tc.check("e2e_reports_window",
             abs(rep_ou["window_days"] - 399 * 4 / 24.0) < 1e-6,
             _fmt(rep_ou["window_days"], 2))

    # ГОЛОВНИЙ НАСКРІЗНИЙ ТЕСТ: синусоїда без реверсії. Лаг 1 МУСИТЬ
    # підробити реверсію, лаг 6 — ні. Якщо це ламається, крок 3 не
    # відрізняє артефакт від структури, і вся його цінність нульова.
    rows_sin = _rows(gen_walk_with_daily_sin(random.Random(32), 400))
    rep_sin = run_measurement(rows_sin, 60, 300, 200, 0, 100,
                              SEED_DEFAULT, verbose=False, do_power=False)
    tc.check("e2e_sin_fakes_reversion_at_lag1",
             rep_sin["lag1"]["beta"] is not None
             and rep_sin["lag1"]["beta"] < 0.0
             and rep_sin["lag1"]["p_null"] <= P_NULL_MAX,
             f"beta {_fmt(rep_sin['lag1']['beta'], 5)} p "
             f"{_fmt(rep_sin['lag1']['p_null'], 4)}")
    tc.check("e2e_sin_does_not_survive_lag6",
             not is_reverting(rep_sin["lag6"]),
             f"beta {_fmt(rep_sin['lag6']['beta'], 5)} p "
             f"{_fmt(rep_sin['lag6']['p_null'], 4)}")
    tc.check("e2e_sin_caught_by_absdiff",
             rep_sin["absdiff"] is not None
             and rep_sin["absdiff"]["p_value"] <= P_SLOT_MAX,
             _fmt(rep_sin["absdiff"]["p_value"], 4)
             if rep_sin["absdiff"] else "None")

    # Локалізована реверсія проходить наскрізь до вердикту SLOT-EFFECT.
    rows_loc = _rows(gen_slot_local_reversion(random.Random(33), 400,
                                              SLOT_LOCAL_HOUR // 4))
    rep_loc = run_measurement(rows_loc, 60, 300, 200, 0, 100,
                              SEED_DEFAULT, verbose=False, do_power=False)
    tc.check("e2e_local_reversion_detected",
             rep_loc["slot_beta"]["p_value"] <= P_SLOT_MAX,
             _fmt(rep_loc["slot_beta"]["p_value"], 4))
    rep_low = run_measurement(rows_ou[:40], 60, 200, 100, 0, 100,
                              SEED_DEFAULT, verbose=False, do_power=False)
    tc.check("e2e_lown", rep_low["final_verdict"] == "CONTROL-SKIPPED",
             "контролі вимкнено — LOW-N перевіряється у step3_verdict")

    # --- 11. Міні-прогін контролів (конвеєр цілком) --------------------------
    nb1s = null_beta_lag(160, 1, 200, 777)
    nb6s = null_beta_lag(160, LAG_DAY, 200, 777)
    mini = run_controls(160, 25, 80, 4242, nb1s, nb6s, MIN_SLOT_PAIRS,
                        verbose=False)
    tc.check("mini_neg_walk_quiet",
             mini["neg_walk"]["slot_beta_fire"] <= 0.20,
             _fmt(mini["neg_walk"]["slot_beta_fire"]))
    tc.check("mini_pos_slot_louder_than_neg",
             mini["pos_slot"]["slot_beta_fire"]
             > mini["neg_walk"]["slot_beta_fire"],
             f"{_fmt(mini['pos_slot']['slot_beta_fire'])} vs "
             f"{_fmt(mini['neg_walk']['slot_beta_fire'])}")
    tc.check("mini_sin_lag1_beats_lag6",
             mini["pos_sin"]["lag1_rev"] > mini["pos_sin"]["lag6_rev"],
             f"{_fmt(mini['pos_sin']['lag1_rev'])} vs "
             f"{_fmt(mini['pos_sin']['lag6_rev'])}")
    tc.check("mini_absdiff_sees_sin",
             mini["pos_sin"]["absdiff_fire"]
             > mini["neg_walk"]["absdiff_fire"],
             f"{_fmt(mini['pos_sin']['absdiff_fire'])} vs "
             f"{_fmt(mini['neg_walk']['absdiff_fire'])}")

    # --- 12. Оточення --------------------------------------------------------
    tc.check("known_env_covers_reads",
             all(nm in KNOWN_ENV for nm in
                 ("VOLEDGE_SLOT3_PERM", "VOLEDGE_SLOT3_NULL",
                  "VOLEDGE_SLOT3_CTRL_REPS", "VOLEDGE_SLOT3_CTRL_PERM",
                  "VOLEDGE_SLOT3_MIN_N", "VOLEDGE_SLOT3_SEED",
                  "VOLEDGE_SLOT3_NO_WRITE", "VOLEDGE_SLOT3_NO_CACHE")))
    tc.check("known_env_has_universal", "VOLEDGE_OFFLINE" in KNOWN_ENV)
    tc.check("known_env_no_step2_names",
             "VOLEDGE_OU_BOOT" not in KNOWN_ENV,
             "крок 2 читає їх у СВОЄМУ main(), тут вони no-op")
    tc.check("env_warn_catches_typo",
             warn_unknown_env(KNOWN_ENV,
                              environ={"VOLEDGE_SLOT3_PERMS": "2000"},
                              quiet=True) == ["VOLEDGE_SLOT3_PERMS"])

    # --- 13. Пороги узгоджені ------------------------------------------------
    # --- 13b. Кеш нуля і потужність на горизонті -----------------------------
    _NULL_CACHE.clear()
    a1 = null_beta_lag(120, 1, 200, 4242)
    a2 = null_beta_lag(120, 1, 200, 4242)
    tc.check("null_cache_returns_identical", a1 is a2)
    tc.check("null_cache_keyed_by_lag",
             null_beta_lag(120, LAG_DAY, 200, 4242) is not a1)
    tc.check("null_cache_size", len(_NULL_CACHE) == 2, str(len(_NULL_CACHE)))
    pw_fast = power_lag6_at(400, 1.0, reps=40, nulls=200, seed=5)
    pw_slow = power_lag6_at(400, 8.0, reps=40, nulls=200, seed=5)
    tc.check("power_monotone_in_speed", pw_fast > pw_slow,
             f"{_fmt(pw_fast, 3)} vs {_fmt(pw_slow, 3)}")
    tc.check("power_grows_with_n",
             power_lag6_at(600, 2.0, reps=40, nulls=200, seed=5)
             >= power_lag6_at(120, 2.0, reps=40, nulls=200, seed=5))
    tc.check("power_none_without_hl", power_lag6_at(200, None) is None)
    tc.check("power_is_diagnostic_not_gate",
             "power_lag6" not in str(control_verdict(
                 {"neg_walk": {"slot_beta_fire": 0.04,
                               "absdiff_fire": 0.05, "lag6_rev": 0.05},
                  "neg_ou": {"slot_beta_fire": 0.05, "lag6_rev": 0.85},
                  "pos_sin": {"absdiff_fire": 0.95, "lag1_rev": 0.90,
                              "lag6_rev": 0.03},
                  "pos_slot": {"slot_beta_fire": 0.92}})),
             "гейт лишається на CTRL_OU_HL_D, як пре-реєстровано")
    tc.check("null_min_raised", NULL_MIN >= 1000 and NULL_DEFAULT >= 5000)

    tc.check("lag6_power_threshold_softer_and_declared",
             CTRL_FP_MAX < LAG6_POWER_MIN < CTRL_POWER_MIN,
             f"{CTRL_FP_MAX} < {LAG6_POWER_MIN} < {CTRL_POWER_MIN}")
    tc.check("thresholds_ordered", CTRL_FP_MAX < CTRL_POWER_MIN
             and CTRL_LAG6_MAX <= CTRL_FP_MAX
             and CTRL_ARTIFACT_MIN < CTRL_POWER_MIN)
    tc.check("min_slots_leaves_room", MIN_USABLE_SLOTS <= 6)
    tc.check("perm_min_below_default", PERM_MIN <= PERM_DEFAULT)

    print(f"[TEST] passed {tc.passed}/{tc.run}")
    for f_ in tc.failures:
        print(f"[TEST] FAILURE detail: {f_}")
    return tc


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


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
    print("Vol-Edge :: spread_slot — крок 3 гіпотези A (READ-ONLY)")
    print("=" * 72)
    warn_unknown_env(KNOWN_ENV, label="spread_slot")

    tc = run_self_tests()
    if tc.failures:
        print("[FAIL] самотести провалено — заміру НЕ буде.")
        return 1

    if _env_flag("VOLEDGE_OFFLINE"):
        print("[OFFLINE] лише самотести, замір пропущено.")
        return 0

    min_n = _env_int("VOLEDGE_SLOT3_MIN_N", MIN_N_DEFAULT)
    n_perm = _env_int("VOLEDGE_SLOT3_PERM", PERM_DEFAULT)
    nulls = _env_int("VOLEDGE_SLOT3_NULL", NULL_DEFAULT)
    ctrl_reps = _env_int("VOLEDGE_SLOT3_CTRL_REPS", CTRL_REPS_DEFAULT)
    ctrl_perm = _env_int("VOLEDGE_SLOT3_CTRL_PERM", CTRL_PERM_DEFAULT)
    seed = _env_int("VOLEDGE_SLOT3_SEED", SEED_DEFAULT)
    no_write = _env_flag("VOLEDGE_SLOT3_NO_WRITE")
    no_cache = _env_flag("VOLEDGE_SLOT3_NO_CACHE")

    if n_perm < PERM_MIN:
        print(f"[CTRL-WARN] перестановок {n_perm} < {PERM_MIN}: p грубий, "
              f"поріг {P_SLOT_MAX} на ньому ненадійний.")
    if nulls < NULL_MIN:
        print(f"[CTRL-WARN] нульових реалізацій {nulls} < {NULL_MIN}: "
              f"p_null грубий.")

    chain_pairs = list_artifacts(CHAIN_DIR, "chain")
    print(f"[INFO] chain-артефактів знайдено: {len(chain_pairs)} "
          f"у {CHAIN_DIR}")
    if not chain_pairs:
        raise RuntimeError(f"у {CHAIN_DIR} немає chain-артефактів")

    rows, counters = collect_triplets(chain_pairs, no_cache=no_cache)
    if len(rows) < 4:
        raise RuntimeError(f"якорів {len(rows)} < 4 — міряти нічого")

    print(f"[INFO] перестановок: {n_perm}; нульових: {nulls}; "
          f"контролів: {ctrl_reps} x {ctrl_perm} перестановок.")
    print("[INFO] контролі — найдовша частина прогону, кілька хвилин.")
    rep = run_measurement(rows, min_n, n_perm, nulls, ctrl_reps,
                          ctrl_perm, seed)
    rep["counters"] = counters
    print_report(rep, counters)

    if not no_write:
        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(OUT_DIR, f"spread_slot_{stamp}.json")
        with open(out_path, "w", encoding="utf-8") as _ofh:
            json.dump(rep, _ofh, ensure_ascii=False, indent=2, default=str)
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
