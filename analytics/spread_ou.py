#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[EN] Hypothesis A, step 2 (read-only): mean reversion of the ETH-BTC
ATM30 spread, dx[t] = alpha + beta*x[t-1], half-life from beta. The
p-value comes from a simulated null (OLS near a unit root is biased
towards fake reversion); bootstrap CI, controls, power table.

--- Ukrainian original below ---
Vol-Edge :: analytics/spread_ou.py — КРОК 2 гіпотези A. READ-ONLY.

ЩО МІРЯЄМО
----------
Крок 1 (spread_neff.py) показав: спред ethbtc_atm30 несе БІЛЬШЕ
незалежної інформації, ніж кожна нога (norm 5.18 при порозі 1.5,
n_eff = 8.6). Це твердження про ІНФОРМАЦІЮ, не про структуру.

Крок 2 питає, чи є в цій інформації mean reversion:
    dx[t] = alpha + beta * x[t-1] + eps
    half_life = -ln2 / ln|1 + beta|   (у кроках, далі в добах)

ПОРОГИ ЗАФІКСОВАНІ ДО ПРОГОНУ:
  FAIL          beta_hat >= 0 (реверсії немає навіть у точковій оцінці)
                або |1 + beta| >= 1 (немає збіжності)
  PASS          beta_hat < 0 І half_life <= 15 діб І p_null <= 0.05,
                де p_null — частка блукань із beta <= спостереженого
  INCONCLUSIVE  усе інше, зокрема 15 < half_life <= 20 і half_life > 20
  LOW-N         спільних якорів < 60 -> вердикту немає
  негативний контроль: частка PASS > 0.10 -> CONTROL-FAIL, VOID
  позитивний контроль: частка PASS < 0.80 -> UNDERPOWERED (окремий
                ярлик: прилад не зламаний, він СЛАБКИЙ для цього n)

ЧОМУ МЕЖА ПОВІЛЬНОСТІ 20 ДІБ, А НЕ 30 (правка внесена ДО коду)
---------------------------------------------------------------
Вікно даних — 26 діб. Half-life 25-30 діб ми в принципі не відрізнимо
від «реверсії немає»: за весь ряд процес не встиг би повернутись і
наполовину. Поріг 30 оголошував би FAIL там, де ми просто НЕ БАЧИЛИ.
Тому все повільніше за 20 діб — INCONCLUSIVE.

І ширше: FAIL тут вимагає ПОЗИТИВНОГО свідчення проти (beta_hat >= 0),
а не браку свідчення за. Повільність і широкий CI — це незнання, і
називається воно своїм ім'ям.

ЧОМУ ПРОТИ НУЛЬОВОГО РОЗПОДІЛУ, А НЕ ПРОТИ НУЛЯ (правка ДО прогону)
--------------------------------------------------------------------
Перша редакція вимагала «верхня межа CI95(beta) < 0». Негативний
контроль її СПРОСТУВАВ: чисте блукання давало PASS у 58% реалізацій.

МНК-оцінка beta на блуканні зміщена ВНИЗ приблизно на -(1+3*phi)/n,
тобто -0.025 при n=157 (спіймано: -0.0238). Це дає уявний half-life
~4.8 доби З НІЧОГО, а бутстреп-CI, центрований на зміщеній оцінці,
нуль не перетинає і зміщення не помічає. Класика Дікі-Фуллера.

Другий випадок того самого класу за два кроки: на кроці 1 зміщення
дав max() двох шумних n_eff, тут — сама МНК на майже одиничному корені.
УРОК ЗАГАЛЬНИЙ: під нулем оцінювач НЕ зобов'язаний давати нуль, і
припускати це не можна ЖОДНОГО разу.

Тому значущість береться з ЕМПІРИЧНОГО нульового розподілу: 500 блукань
тієї ж довжини, p_null = частка з beta <= спостереженого. Розподіл
залежить ЛИШЕ від n (beta = cov/var інваріантна до масштабу), що
перевірено самотестом.

CI НА n_eff — ТЕПЕР ЛИШЕ ДІАГНОСТИКА
-------------------------------------
Класична похибка beta припускає незалежні спостереження. У нас
r_lag1 = 0.896, n = 157, n_eff = 8.6 — звичайний CI був би вужчий
приблизно вчетверо і зробив би будь-який beta «значущим».

Блоковий бутстреп лишається, але вердикту вже НЕ дає — він показує
розкид half-life. Довжина блоку L = ceil(n / n_eff), тобто
довжина береться з НАШОГО Ж оцінювача n_eff, а не з правила великого
пальця. Блок мусить бути довший за пам'ять ряду, інакше бутстреп
відтворить незалежність, якої немає. L обмежується зверху так, щоб
блоків лишалось хоча б MIN_BLOCKS — інакше ресемплінг вироджується.

ПАСТКА КРОКУ 1, ЯКУ ТУТ ТРЕБА ПОВТОРИТИ (не забути)
----------------------------------------------------
n_eff і beta — величини «на КРОК». Крок мусить бути однаковий. Крок 1
уже спіймав шість ручних знімків поза сіткою; тут додається другий
рівень: навіть на канонічній сітці бувають ДІРКИ (пропущений прогін,
flock). Пара (x[t-1], dx[t]) береться ЛИШЕ якщо розрив між якорями
лежить у [3.5, 4.5] год. Решта рахується у pairs_gap_bad і НЕ бере
участі ні в регресії, ні в бутстрепі.

Модуль нічого не міряє наново з ланцюгів: збір якорів, білий список
сітки і статистика рядів імпортуються зі spread_neff. Двох копій
збирача бути не повинно.

Змінні оточення:
  VOLEDGE_OU_MIN_N      -> поріг LOW-N, default 60
  VOLEDGE_OU_BOOT       -> реалізацій бутстрепу (діагностика), 500
  VOLEDGE_OU_NULL       -> реалізацій нульового розподілу, default 500
  VOLEDGE_OU_CTRL_REPS  -> реалізацій контролю, default 200;
                           0 -> контролі пропускаються, вердикт
                           CONTROL-SKIPPED
  VOLEDGE_OU_SEED       -> зерно, default 20260826
  VOLEDGE_OU_NO_WRITE   -> 1: не писати звіт
  VOLEDGE_OU_NO_CACHE   -> 1: не читати/не писати кеш фіч

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
    from spread_neff import (collect_triplets, series_stats, gen_ar1,
                             _median, _quantile, _sd, _fmt, SPREAD,
                             LEG_BTC, LEG_ETH)
except ImportError:
    from analytics.spread_neff import (collect_triplets, series_stats,
                                       gen_ar1, _median, _quantile, _sd,
                                       _fmt, SPREAD, LEG_BTC, LEG_ETH)
try:
    from anomaly_detect import CHAIN_DIR, OUT_DIR, list_artifacts
except ImportError:
    from analytics.anomaly_detect import CHAIN_DIR, OUT_DIR, list_artifacts
try:
    from env_flags import merge_known, warn_unknown_env
except ImportError:
    from core.env_flags import merge_known, warn_unknown_env

KNOWN_ENV = merge_known((
    "VOLEDGE_OU_MIN_N",
    "VOLEDGE_OU_BOOT",
    "VOLEDGE_OU_NULL",
    "VOLEDGE_OU_CTRL_REPS",
    "VOLEDGE_OU_SEED",
    "VOLEDGE_OU_NO_WRITE",
    "VOLEDGE_OU_NO_CACHE",
))

# --- ПОРОГИ. ЗАФІКСОВАНІ ДО ЗАМІРУ. НЕ З ОТОЧЕННЯ НАВМИСНЕ -------------------
HL_PASS_D = 15.0      # швидше -> кандидат на PASS
HL_SLOW_D = 20.0      # повільніше -> INCONCLUSIVE (вікно 26 діб)
MIN_N_DEFAULT = 60
CI_LEVEL = 0.95
P_NULL_MAX = 0.05
NULL_DEFAULT = 500
NULL_MIN = 200
CTRL_NEG_MAX_PASS = 0.10
CTRL_POS_MIN_PASS = 0.80
CTRL_REPS_DEFAULT = 200
CTRL_REPS_MIN = 150
BOOT_DEFAULT = 500
BOOT_MIN = 200
SEED_DEFAULT = 20260826

STEPS_PER_DAY = 6.0            # сітка 00/04/08/12/16/20 UTC
GAP_MIN_H = 3.5
GAP_MAX_H = 4.5
MIN_BLOCKS = 4                 # менше -> ресемплінг вироджується
MIN_PAIRS = 30

POS_HL_D = 5.0                 # позитивний контроль: OU з half-life 5 діб

# Проєкція потужності: у скільки разів має вирости n. Заміряно 26 сер:
# при n=157 потужність на hl=5д = 0.10, тобто справжню реверсію тест
# побачив би раз із десяти. Проєкція перетворює це на ДАТУ, інакше
# вердикт UNDERPOWERED не підказує, що робити.
POWER_MULT = (1, 2, 4, 8)
POWER_REPS = 150
POWER_NULLS = 300
POWER_TARGET = 0.80


# ----------------------------------------------------------------------------
# Пари (рівень, приріст) на РІВНОМІРНОМУ кроці
# ----------------------------------------------------------------------------

def make_pairs(stamps, values):
    """
    ([(x_prev, dx), ...], counters).

    Пара береться ЛИШЕ з сусідніх якорів із розривом [3.5, 4.5] год.
    Дірка в сітці (пропущений cron, flock) дала б приріст за 8 годин,
    поставлений в один ряд із чотиригодинними — beta «на крок» після
    цього не означає нічого. Крок 1 уже показав ціну нерівного кроку.
    """
    counters = {"stamps": len(stamps), "pairs_ok": 0, "pairs_gap_bad": 0}
    out = []
    for i in range(1, len(stamps)):
        gap_h = (stamps[i] - stamps[i - 1]).total_seconds() / 3600.0
        if not (GAP_MIN_H <= gap_h <= GAP_MAX_H):
            counters["pairs_gap_bad"] += 1
            continue
        out.append((values[i - 1], values[i] - values[i - 1]))
        counters["pairs_ok"] += 1
    return out, counters


# ----------------------------------------------------------------------------
# OU-регресія
# ----------------------------------------------------------------------------

def ou_fit(pairs):
    """
    dict: alpha, beta, half_life_steps, half_life_days, n_pairs, reason.

    beta = cov(x_prev, dx) / var(x_prev). half_life рахується з |1+beta|:
    beta у (-2,-1) — коливна збіжність, вона теж реверсія, але
    позначається прапорцем oscillatory.
    """
    n = len(pairs)
    rep = {"n_pairs": n, "alpha": None, "beta": None,
           "half_life_steps": None, "half_life_days": None,
           "oscillatory": False, "reason": "OK"}
    if n < 3:
        rep["reason"] = f"пар {n} < 3"
        return rep
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0.0:
        rep["reason"] = "нульова дисперсія рівня"
        return rep
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    beta = sxy / sxx
    rep["beta"] = beta
    rep["alpha"] = my - beta * mx
    decay = abs(1.0 + beta)
    if decay <= 0.0:
        rep["half_life_steps"] = 0.0
        rep["half_life_days"] = 0.0
    elif decay >= 1.0:
        rep["reason"] = f"|1+beta| = {decay:.4f} >= 1 — збіжності немає"
        return rep
    else:
        hl = -math.log(2.0) / math.log(decay)
        rep["half_life_steps"] = hl
        rep["half_life_days"] = hl / STEPS_PER_DAY
    rep["oscillatory"] = beta < -1.0
    return rep


def block_len(n, n_eff):
    """L = ceil(n / n_eff), обрізаний так, щоб лишалось >= MIN_BLOCKS.

    Довжина блоку береться з НАШОГО оцінювача пам'яті ряду, а не з
    n^(1/3): правило великого пальця не знає нічого про r_lag1 = 0.896
    і дало б L ~ 5 при потрібних ~18.
    """
    if n <= 0:
        return 1
    if not n_eff or n_eff <= 0:
        return max(1, min(n, int(math.ceil(n ** (1.0 / 3.0)))))
    raw = int(math.ceil(n / float(n_eff)))
    cap = max(1, n // MIN_BLOCKS)
    return max(1, min(raw, cap))


def block_bootstrap_beta(pairs, blk, reps, rng):
    """[beta, ...] — рухомий блоковий бутстреп по парах.

    Ресемплимо ПАРИ блоками, не значення: пара (x_prev, dx) — атом
    регресії, розривати її не можна.
    """
    n = len(pairs)
    out = []
    if n < 2 or blk < 1:
        return out
    n_blocks = int(math.ceil(n / float(blk)))
    max_start = max(0, n - blk)
    for _ in range(reps):
        sample = []
        for _b in range(n_blocks):
            s = rng.randint(0, max_start)
            sample.extend(pairs[s:s + blk])
        fit = ou_fit(sample[:n])
        if fit["beta"] is not None:
            out.append(fit["beta"])
    return out


def null_beta_distribution(n, reps, seed):
    """[beta, ...] на блуканнях довжини n.

    Масштаб НЕ параметр: beta = cov/var інваріантна до множення ряду на
    константу, тож розподіл залежить лише від n. Самотест це фіксує —
    інакше хтось «підганяв» би sd і думав, що це має значення.
    """
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        vals = gen_random_walk(rng, n, 1.0)
        fit = ou_fit(_pairs_from_values(vals))
        if fit["beta"] is not None:
            out.append(fit["beta"])
    return out


def p_null(null_betas, beta):
    """Частка нульових beta <= спостереженої, з поправкою +1.

    Поправка не дає видати p = 0 на скінченній вибірці: «менше за 1/N»
    — це не «нуль».
    """
    if not null_betas or beta is None:
        return None
    k = sum(1 for b in null_betas if b <= beta)
    return (k + 1.0) / (len(null_betas) + 1.0)


def ci_bounds(sample, level=CI_LEVEL):
    """(lo, hi) перцентильний CI. (None, None) на порожньому."""
    if not sample:
        return None, None
    a = (1.0 - level) / 2.0
    return _quantile(sample, a), _quantile(sample, 1.0 - a)


def ou_verdict(fit, pval, n_common, min_n):
    """(label, note). Значущість — з нульового розподілу, НЕ з CI."""
    if n_common < min_n:
        return "LOW-N", (f"спільних якорів {n_common} < {min_n}; "
                         f"вердикту немає")
    if fit.get("n_pairs", 0) < MIN_PAIRS:
        return "LOW-N", (f"пар {fit.get('n_pairs')} < {MIN_PAIRS} після "
                         f"фільтра рівномірності кроку")
    beta = fit.get("beta")
    if beta is None:
        return "REFUSE", f"beta не оцінено: {fit.get('reason')}"
    if beta >= 0.0:
        return "FAIL", (f"beta {beta:+.5f} >= 0 — реверсії немає навіть "
                        f"у точковій оцінці")
    hl = fit.get("half_life_days")
    if hl is None:
        return "FAIL", f"збіжності немає: {fit.get('reason')}"
    if hl > HL_SLOW_D:
        return "INCONCLUSIVE", (f"half-life {hl:.1f} діб > {HL_SLOW_D} "
                                f"при вікні ~26 діб — НЕ БАЧИЛИ, а не "
                                f"спростували")
    if hl > HL_PASS_D:
        return "INCONCLUSIVE", (f"{HL_PASS_D} < half-life {hl:.1f} <= "
                                f"{HL_SLOW_D} діб — проміжна смуга")
    if pval is None:
        return "REFUSE", "нульовий розподіл не побудовано"
    if pval > P_NULL_MAX:
        return "INCONCLUSIVE", (f"half-life {hl:.1f} діб, але p_null = "
                                f"{pval:.4f} > {P_NULL_MAX}: блукання дає "
                                f"таке саме beta не рідше")
    return "PASS", (f"beta {beta:+.5f}, p_null {pval:.4f} <= "
                    f"{P_NULL_MAX}, half-life {hl:.1f} <= {HL_PASS_D} діб")


# ----------------------------------------------------------------------------
# Контролі
# ----------------------------------------------------------------------------

def gen_random_walk(rng, n, sd_step):
    """Блукання: beta = 0 за побудовою. Реверсії НЕМАЄ."""
    x = 0.0
    out = []
    for _ in range(n):
        x += rng.gauss(0.0, sd_step or 1.0)
        out.append(x)
    return out


def gen_ou(rng, n, hl_days, sd_level):
    """Дискретний OU із заданим half-life у ДОБАХ."""
    hl_steps = max(1e-6, hl_days * STEPS_PER_DAY)
    phi = 0.5 ** (1.0 / hl_steps)
    inn = math.sqrt(max(1e-12, 1.0 - phi * phi))
    x = rng.gauss(0.0, 1.0)
    out = []
    for _ in range(n):
        x = phi * x + inn * rng.gauss(0.0, 1.0)
        out.append(x * (sd_level or 1.0))
    return out


def _pairs_from_values(values):
    """Рівномірний крок за побудовою — синтетика дірок не має."""
    return [(values[i - 1], values[i] - values[i - 1])
            for i in range(1, len(values))]


def _verdict_of_series(values, null_betas, min_n):
    """Повний конвеєр вердикту на одному синтетичному ряді.

    Бутстрепу тут НЕМАЄ навмисне: вердикт його більше не використовує,
    а прогін контролю з ним був би в сотні разів довшим без жодного
    впливу на результат.
    """
    fit = ou_fit(_pairs_from_values(values))
    return ou_verdict(fit, p_null(null_betas, fit.get("beta")),
                      len(values), min_n)[0]


def control_negative(n, reps, seed, min_n, null_betas):
    """Блукання, ВАЛІДАЦІЙНЕ (зерно інше, ніж у нульового розподілу).

    Якби ми судили ті самі реалізації, з яких побудований нульовий
    розподіл, частка PASS дорівнювала б P_NULL_MAX тотожно і нічого не
    перевіряла — та сама пастка, що з нормувальником на кроці 1.
    """
    rng = random.Random(seed)
    labels = []
    for _ in range(reps):
        labels.append(_verdict_of_series(gen_random_walk(rng, n, 1.0),
                                         null_betas, min_n))
    return labels


def control_positive(n, reps, seed, min_n, null_betas):
    """OU з half-life POS_HL_D. Частка PASS = ПОТУЖНІСТЬ тесту."""
    rng = random.Random(seed)
    labels = []
    for _ in range(reps):
        labels.append(_verdict_of_series(gen_ou(rng, n, POS_HL_D, 1.0),
                                         null_betas, min_n))
    return labels


def power_at(n, hl_days, reps, nulls, seed, min_n):
    """Частка PASS на OU із заданим half-life при довжині n.

    Це та сама величина, що позитивний контроль, але для ІНШОГО n —
    відповідь на питання «скільки ще збирати».
    """
    nb = null_beta_distribution(n, nulls, seed)
    rng = random.Random(seed + 1)
    hits = 0
    for _ in range(reps):
        vals = gen_ou(rng, n, hl_days, 1.0)
        fit = ou_fit(_pairs_from_values(vals))
        if ou_verdict(fit, p_null(nb, fit.get("beta")),
                      n, min_n)[0] == "PASS":
            hits += 1
    return hits / float(reps)


def power_projection(n_now, hl_days, seed, min_n,
                     reps=POWER_REPS, nulls=POWER_NULLS):
    """[{n, power, extra_days, eta_utc}] для n_now * POWER_MULT."""
    out = []
    for m in POWER_MULT:
        n = int(n_now * m)
        pw = power_at(n, hl_days, reps, nulls, seed + 1000 * m, min_n)
        extra = max(0, n - n_now) / STEPS_PER_DAY
        eta = datetime.now(timezone.utc) + timedelta(days=extra)
        out.append({"n": n, "power": pw, "extra_days": extra,
                    "eta_utc": eta.strftime("%Y-%m-%d")})
    return out


def pass_rate(labels):
    if not labels:
        return None
    return sum(1 for x in labels if x == "PASS") / len(labels)


def control_verdict(neg_labels, pos_labels):
    """(ok, label, note). Провал АНУЛЮЄ основний вердикт."""
    r_neg = pass_rate(neg_labels)
    r_pos = pass_rate(pos_labels)
    if r_neg is None or r_pos is None:
        return False, "CONTROL-FAIL", "контроль не дав жодної реалізації"
    bad = []
    if r_neg > CTRL_NEG_MAX_PASS:
        bad.append(f"блукання дає PASS у {r_neg:.3f} > "
                   f"{CTRL_NEG_MAX_PASS} — тест бачить реверсію там, де "
                   f"її немає")
    if r_pos < CTRL_POS_MIN_PASS:
        bad.append(f"OU(hl={POS_HL_D:.0f}д) дає PASS лише у {r_pos:.3f} < "
                   f"{CTRL_POS_MIN_PASS} — тест сліпий до справжньої "
                   f"реверсії")
    # РІЗНІ ПРОВАЛИ — РІЗНІ ЯРЛИКИ. Хибний позитив означає зламаний
    # прилад (число недійсне). Брак потужності означає справний, але
    # слабкий прилад: PASS був недосяжний ЗА ПОБУДОВОЮ, і зливати це в
    # один ярлик — те саме, що безнапрямкова гілка у dvol_drift.
    if r_neg > CTRL_NEG_MAX_PASS:
        return False, "CONTROL-FAIL", "; ".join(bad)
    if bad:
        return False, "UNDERPOWERED", "; ".join(bad)
    return True, "CONTROL-OK", (f"блукання {r_neg:.3f}, "
                                f"OU(hl={POS_HL_D:.0f}д) {r_pos:.3f}")


# ----------------------------------------------------------------------------
# Замір
# ----------------------------------------------------------------------------

def final_verdict(label, note, clabel, cnote):
    """(final_label, final_note) — ЧИСТА функція, щоб гілки можна було
    перевірити самотестом, а не читанням очима.

    ПРІОРИТЕТ І НАПРЯМОК:
      1) LOW-N   — твердження про дані, істинне попри стан приладу.
      2) CONTROL-FAIL — хибний позитив роздутий, число недійсне.
      3) UNDERPOWERED + PASS -> PASS. Низька потужність множить хибні
         НЕГАТИВИ; за хибні позитиви відповідає негативний контроль.
      4) UNDERPOWERED без PASS -> UNDERPOWERED: «не побачили», не
         «немає».
    """
    if label == "LOW-N":
        return "LOW-N", note + ("" if clabel == "CONTROL-OK"
                                else f"; контроль: {cnote}")
    if clabel == "CONTROL-FAIL":
        return "VOID", f"вердикт анульовано контролем: {cnote}"
    if clabel == "UNDERPOWERED":
        if label == "PASS":
            return "PASS", (f"{note}. УВАГА: потужність низька "
                            f"({cnote}), але це стосується хибних "
                            f"НЕГАТИВІВ; хибний позитив у нормі.")
        return "UNDERPOWERED", f"{cnote}. Точкова оцінка: {note}"
    return label, note


def run_measurement(rows, min_n, boot, reps, seed, nulls=NULL_DEFAULT,
                    do_power=True):
    stamps = [r[0] for r in rows]
    spread = [r[3] for r in rows]
    n = len(rows)

    pairs, pc = make_pairs(stamps, spread)
    fit = ou_fit(pairs)
    st_s = series_stats(spread) if n >= 4 else {"n": n, "r1": None,
                                                "n_eff": None, "sd": None}
    blk = block_len(len(pairs), st_s.get("n_eff"))
    rng = random.Random(seed)
    betas = block_bootstrap_beta(pairs, blk, boot, rng)
    ci_lo, ci_hi = ci_bounds(betas)
    null_betas = null_beta_distribution(len(pairs) + 1, nulls, seed + 31)
    pv = p_null(null_betas, fit.get("beta"))

    hl_boot = []
    for b in betas:
        d = abs(1.0 + b)
        if 0.0 < d < 1.0:
            hl_boot.append((-math.log(2.0) / math.log(d)) / STEPS_PER_DAY)

    rep = {"n_common": n,
           "first_utc": stamps[0].isoformat() if stamps else None,
           "last_utc": stamps[-1].isoformat() if stamps else None,
           "window_days": ((stamps[-1] - stamps[0]).total_seconds()
                           / 86400.0) if n >= 2 else None,
           "pair_counters": pc,
           "spread_stats": st_s,
           "fit": fit,
           "block_len": blk,
           "n_blocks": (int(math.ceil(len(pairs) / float(blk)))
                        if blk else None),
           "boot_reps": len(betas),
           "beta_ci_lo": ci_lo, "beta_ci_hi": ci_hi,
           "null_reps": len(null_betas),
           "null_beta_median": _median(null_betas),
           "null_beta_q05": _quantile(null_betas, 0.05)
           if null_betas else None,
           "p_null": pv,
           "hl_days_ci_lo": _quantile(hl_boot, 0.025) if hl_boot else None,
           "hl_days_ci_hi": _quantile(hl_boot, 0.975) if hl_boot else None,
           "thresholds": {"hl_pass_d": HL_PASS_D, "hl_slow_d": HL_SLOW_D,
                          "min_n": min_n, "ci_level": CI_LEVEL,
                          "neg_max_pass": CTRL_NEG_MAX_PASS,
                          "pos_min_pass": CTRL_POS_MIN_PASS}}

    label, note = ou_verdict(fit, pv, n, min_n)
    rep["ou_verdict"] = label
    rep["ou_note"] = note

    if reps <= 0:
        rep["controls"] = {"skipped": True}
        rep["final_verdict"] = "CONTROL-SKIPPED"
        rep["final_note"] = ("VOLEDGE_OU_CTRL_REPS=0 — без контролів "
                             "вердикт НЕ видається")
        return rep

    if reps < CTRL_REPS_MIN:
        print(f"[CTRL-WARN] реалізацій {reps} < {CTRL_REPS_MIN}: частка "
              f"PASS оцінена грубо.")
    neg = control_negative(n, reps, seed + 7, min_n, null_betas)
    pos = control_positive(n, reps, seed + 13, min_n, null_betas)
    ok, clabel, cnote = control_verdict(neg, pos)
    rep["controls"] = {"skipped": False, "reps": reps, "boot": boot,
                       "nulls": len(null_betas), "seed": seed,
                       "neg_pass_rate": pass_rate(neg),
                       "pos_pass_rate": pass_rate(pos),
                       "neg_labels": _tally(neg), "pos_labels": _tally(pos),
                       "verdict": clabel, "note": cnote}

    if do_power:
        rep["power_projection"] = power_projection(n, POS_HL_D, seed + 61,
                                                   min_n)
        # Потужність, ЗАФІКСОВАНА наперед на hl=5д, міряє сліпоту до
        # ефекту, якого ми не бачили. Якщо оцінка дала інший horizon,
        # потужність треба знати САМЕ для нього — інакше метрика
        # прибита до очікуваної відповіді.
        hl_est = fit.get("half_life_days")
        if hl_est is not None and 0.05 <= hl_est <= HL_SLOW_D:
            rep["power_at_estimated_hl"] = {
                "hl_days": hl_est,
                "power": power_at(len(pairs) + 1, hl_est, POWER_REPS,
                                  POWER_NULLS, seed + 71, min_n)}
        else:
            rep["power_at_estimated_hl"] = None
    else:
        rep["power_projection"] = None
        rep["power_at_estimated_hl"] = None

    fl, fn = final_verdict(label, note, clabel, cnote)
    rep["final_verdict"] = fl
    rep["final_note"] = fn
    return rep


def _tally(labels):
    out = {}
    for x in labels:
        out[x] = out.get(x, 0) + 1
    return out


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
    pc = rep["pair_counters"]
    print(f"  пар з рівним кроком    : {pc['pairs_ok']}")
    print(f"  пар відкинуто (дірка)  : {pc['pairs_gap_bad']}")
    print(f"  вікно UTC              : {rep['first_utc']} .. "
          f"{rep['last_utc']}")
    print(f"  довжина вікна, діб     : {_fmt(rep['window_days'], 1)}")

    st = rep["spread_stats"]
    print()
    print("-" * 72)
    print("РЯД СПРЕДА")
    print("-" * 72)
    print(f"  r_lag1 {_fmt(st.get('r1'), 4)}   n_eff "
          f"{_fmt(st.get('n_eff'), 2)}   sd {_fmt(st.get('sd'), 4)}")
    print(f"  довжина блоку L = ceil(n/n_eff) = {rep['block_len']} "
          f"({rep['n_blocks']} блоків, мінімум {MIN_BLOCKS})")

    f = rep["fit"]
    print()
    print("-" * 72)
    print("OU-РЕГРЕСІЯ  dx[t] = alpha + beta * x[t-1]")
    print("-" * 72)
    print(f"  beta       : {_fmt(f.get('beta'), 5)}   "
          f"({f.get('reason')})")
    print(f"  CI95(beta) : [{_fmt(rep['beta_ci_lo'], 5)}, "
          f"{_fmt(rep['beta_ci_hi'], 5)}]  "
          f"(бутстреп, {rep['boot_reps']} реалізацій — ДІАГНОСТИКА)")
    print(f"  нульовий розподіл beta ({rep['null_reps']} блукань): "
          f"медіана {_fmt(rep['null_beta_median'], 5)}, "
          f"q05 {_fmt(rep['null_beta_q05'], 5)}")
    print(f"  ЗМІЩЕННЯ МНК: медіана нуля НЕ нуль — саме через це "
          f"критерій «CI < 0» був відкинутий")
    print(f"  p_null     : {_fmt(rep['p_null'], 4)} "
          f"(поріг <= {P_NULL_MAX})")
    print(f"  half-life  : {_fmt(f.get('half_life_days'), 2)} діб   "
          f"CI95 [{_fmt(rep['hl_days_ci_lo'], 2)}, "
          f"{_fmt(rep['hl_days_ci_hi'], 2)}]")
    if f.get("oscillatory"):
        print("  [FLAG] beta < -1: збіжність коливна, не монотонна")
    th = rep["thresholds"]
    print(f"  пороги (до заміру): PASS при hl <= {th['hl_pass_d']} діб і "
          f"p_null <= {P_NULL_MAX}; INCONCLUSIVE при hl > "
          f"{th['hl_slow_d']}")
    print(f"  [{rep['ou_verdict']}] {rep['ou_note']}")

    print()
    print("-" * 72)
    print("КОНТРОЛІ")
    print("-" * 72)
    c = rep["controls"]
    if c.get("skipped"):
        print("  [SKIP] контролі вимкнено — вердикт не видається")
    else:
        print(f"  реалізацій: {c['reps']}, нульових: {c['nulls']}")
        print(f"  негативний (блукання)    : PASS у "
              f"{_fmt(c['neg_pass_rate'])} (поріг <= "
              f"{CTRL_NEG_MAX_PASS})  {c['neg_labels']}")
        print(f"  позитивний (OU hl={POS_HL_D:.0f}д): PASS у "
              f"{_fmt(c['pos_pass_rate'])} (поріг >= "
              f"{CTRL_POS_MIN_PASS})  {c['pos_labels']}")
        print(f"     ^ це ПОТУЖНІСТЬ тесту: частка випадків, коли він "
              f"побачив би реверсію, яка ТОЧНО є")
        print(f"  [{c['verdict']}] {c['note']}")

    pe = rep.get("power_at_estimated_hl")
    if pe:
        print()
        print("-" * 72)
        print("ПОТУЖНІСТЬ НА ОЦІНЕНОМУ ГОРИЗОНТІ")
        print("-" * 72)
        print(f"  hl_оцінене = {pe['hl_days']:.2f} діб -> потужність "
              f"{pe['power']:.3f} при n = {rep['n_common']}")
        print(f"  (контроль вище рахує сліпоту до hl={POS_HL_D:.0f}д — "
              f"іншого ефекту, ніж спостережений)")

    pp = rep.get("power_projection")
    if pp:
        print()
        print("-" * 72)
        print(f"ПОТУЖНІСТЬ ПРОТИ ДОВЖИНИ РЯДУ (OU hl={POS_HL_D:.0f}д)")
        print("-" * 72)
        print(f"{'n':>7}{'потужність':>13}{'ще діб':>10}   орієнтовно")
        for row in pp:
            mark = " <-- зараз" if row["extra_days"] < 1e-9 else ""
            print(f"{row['n']:>7}{row['power']:>13.3f}"
                  f"{row['extra_days']:>10.0f}   {row['eta_utc']}{mark}")
        reach = [r for r in pp if r["power"] >= POWER_TARGET]
        if reach:
            r0 = reach[0]
            print(f"  Потужність {POWER_TARGET} досяжна при n = {r0['n']} "
                  f"(~{r0['extra_days']:.0f} діб, {r0['eta_utc']})")
        else:
            print(f"  Потужності {POWER_TARGET} не досягнуто навіть при "
                  f"n = {pp[-1]['n']} — потрібен інший підхід, не час")

    print()
    print("=" * 72)
    print(f"  ВЕРДИКТ КРОКУ 2: [{rep['final_verdict']}] "
          f"{rep['final_note']}")
    print("=" * 72)
    fv = rep["final_verdict"]
    if fv == "PASS":
        print("  -> у спреді є mean reversion на вимірюваному горизонті.")
        print("     PASS чинний і при низькій потужності: слабкий тест")
        print("     пропускає ефекти, але не вигадує їх — за це")
        print("     відповідає негативний контроль, і він чистий.")
        print("     ДАЛІ: це ще НЕ стратегія — торгованість, спред")
        print("     стакана і гамма-хедж не міряні.")
    elif fv == "FAIL":
        print("  -> реверсії немає. Гіпотеза A закрита на кроці 2;")
        print("     виграш у n_eff реальний, але структури в ньому нема.")
    elif fv == "INCONCLUSIVE":
        print("  -> вердикту немає. Перезамір при довшому вікні;")
        print("     на проміжній смузі стратегію НЕ будувати.")
    elif fv == "UNDERPOWERED":
        print("  -> прилад СПРАВНИЙ, але слабкий: навіть справжня")
        print("     реверсія з half-life 5 діб виявлялась би рідше")
        print("     за поріг. PASS був недосяжний за побудовою, тож")
        print("     відсутність PASS нічого не спростовує.")
        print("     Це та сама стіна n_eff, що в трьох попередніх")
        print("     питаннях — не дефект коду. Дивись таблицю")
        print("     потужності вище: там ДАТА перезаміру.")
    elif fv == "VOID":
        print("  -> прилад несправний, число недійсне.")


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


def run_self_tests():
    tc = TestCounters()

    # --- 1. Пари і рівність кроку -------------------------------------------
    base = datetime(2026, 8, 1, 0, 10, tzinfo=timezone.utc)
    st_ok = [base + timedelta(hours=4 * i) for i in range(10)]
    vals = [float(i) for i in range(10)]
    prs, pc = make_pairs(st_ok, vals)
    tc.check("pairs_all_kept_on_even_grid",
             pc["pairs_ok"] == 9 and pc["pairs_gap_bad"] == 0, str(pc))
    tc.check("pairs_are_level_and_diff",
             prs[0] == (0.0, 1.0), str(prs[0]))
    # Дірка = пропущений знімок. 8-годинний приріст поруч із
    # 4-годинними зробив би beta безглуздою.
    st_gap = list(st_ok)
    del st_gap[5]
    v_gap = list(vals)
    del v_gap[5]
    _p2, pc2 = make_pairs(st_gap, v_gap)
    tc.check("pairs_drop_gap", pc2["pairs_gap_bad"] == 1
             and pc2["pairs_ok"] == 7, str(pc2))
    tc.check("pairs_counters_add_up",
             pc2["pairs_ok"] + pc2["pairs_gap_bad"] == len(st_gap) - 1)

    # --- 2. ou_fit на відомій відповіді -------------------------------------
    # Детермінований OU: x[t] = phi*x[t-1] -> beta = phi-1 точно.
    phi = 0.9
    det = [phi ** i for i in range(40)]
    fit_det = ou_fit(_pairs_from_values(det))
    tc.check("ou_recovers_known_beta",
             abs(fit_det["beta"] - (phi - 1.0)) < 1e-9,
             _fmt(fit_det["beta"], 6))
    hl_true = (-math.log(2.0) / math.log(phi)) / STEPS_PER_DAY
    tc.check("ou_recovers_known_halflife",
             abs(fit_det["half_life_days"] - hl_true) < 1e-6,
             f"{_fmt(fit_det['half_life_days'])} vs {_fmt(hl_true)}")
    tc.check("ou_refuses_constant",
             ou_fit(_pairs_from_values([2.0] * 30))["beta"] is None)
    tc.check("ou_refuses_short", ou_fit([(1.0, 0.1)])["beta"] is None)
    # Розбіжний ряд: |1+beta| >= 1 -> half_life відсутній, ЯВНО.
    div = [1.05 ** i for i in range(40)]
    fd = ou_fit(_pairs_from_values(div))
    tc.check("ou_refuses_divergent",
             fd["half_life_days"] is None and "збіжн" in fd["reason"],
             fd["reason"])
    osc = [(-0.5) ** i for i in range(40)]
    fo = ou_fit(_pairs_from_values(osc))
    tc.check("ou_flags_oscillatory",
             fo["oscillatory"] is True and fo["half_life_days"] is not None,
             _fmt(fo.get("beta"), 4))

    # --- 3. Довжина блоку ----------------------------------------------------
    tc.check("block_len_from_neff", block_len(157, 8.6) == 19,
             str(block_len(157, 8.6)))
    tc.check("block_len_respects_min_blocks",
             block_len(100, 1.0) == 25, str(block_len(100, 1.0)))
    tc.check("block_len_fallback_without_neff",
             block_len(157, None) >= 1)
    tc.check("block_len_never_zero", block_len(5, 100.0) >= 1)
    # Ключове: L з n_eff МУСИТЬ бути більшим за наївне n^(1/3).
    tc.check("block_len_beats_rule_of_thumb",
             block_len(157, 8.6) > int(math.ceil(157 ** (1 / 3.0))),
             f"{block_len(157, 8.6)} vs "
             f"{int(math.ceil(157 ** (1 / 3.0)))}")

    # --- 4. Бутстреп ---------------------------------------------------------
    rng = random.Random(1)
    ou_v = gen_ou(random.Random(2), 160, 5.0, 0.012)
    prs_ou = _pairs_from_values(ou_v)
    bs = block_bootstrap_beta(prs_ou, 19, 200, rng)
    tc.check("boot_returns_reps", len(bs) >= 190, str(len(bs)))
    lo, hi = ci_bounds(bs)
    tc.check("boot_ci_ordered", lo < hi, f"{_fmt(lo, 5)} {_fmt(hi, 5)}")
    tc.check("boot_ci_brackets_point",
             lo <= ou_fit(prs_ou)["beta"] <= hi)
    tc.check("ci_empty_is_none", ci_bounds([]) == (None, None))
    # Довший блок -> ШИРШИЙ CI. Якщо це не так, блокування не працює
    # і бутстреп відтворює незалежність, якої немає.
    bs_short = block_bootstrap_beta(prs_ou, 2, 300, random.Random(3))
    bs_long = block_bootstrap_beta(prs_ou, 30, 300, random.Random(3))
    w_s = ci_bounds(bs_short)[1] - ci_bounds(bs_short)[0]
    w_l = ci_bounds(bs_long)[1] - ci_bounds(bs_long)[0]
    tc.check("longer_block_widens_ci", w_l > w_s,
             f"{_fmt(w_l, 5)} vs {_fmt(w_s, 5)}")

    # --- 5. Вердикт: смуги і напрямки ---------------------------------------
    def _f(beta, hl):
        return {"beta": beta, "half_life_days": hl, "n_pairs": 100,
                "reason": "OK"}
    tc.check("verdict_pass",
             ou_verdict(_f(-0.05, 10.0), 0.01, 157, 60)[0] == "PASS")
    tc.check("verdict_fail_on_positive_beta",
             ou_verdict(_f(0.01, None), 0.001, 157, 60)[0] == "FAIL")
    tc.check("verdict_fail_on_zero_beta",
             ou_verdict(_f(0.0, None), 0.001, 157, 60)[0] == "FAIL")
    # Повільна реверсія — НЕ спростування. Це і є правка 30 -> 20.
    tc.check("slow_is_inconclusive_not_fail",
             ou_verdict(_f(-0.005, 25.0), 0.001, 157, 60)[0]
             == "INCONCLUSIVE")
    tc.check("middle_band_inconclusive",
             ou_verdict(_f(-0.01, 17.0), 0.001, 157, 60)[0]
             == "INCONCLUSIVE")
    # Незначуще проти блукання -> PASS знімається навіть при швидкому hl.
    tc.check("insignificant_vs_null_blocks_pass",
             ou_verdict(_f(-0.05, 10.0), 0.30, 157, 60)[0]
             == "INCONCLUSIVE")
    tc.check("p_boundary_included",
             ou_verdict(_f(-0.05, 10.0), P_NULL_MAX, 157, 60)[0] == "PASS")
    tc.check("verdict_lown_by_anchors",
             ou_verdict(_f(-0.05, 5.0), 0.01, 59, 60)[0] == "LOW-N")
    tc.check("verdict_lown_by_pairs",
             ou_verdict({"beta": -0.05, "half_life_days": 5.0,
                         "n_pairs": 10, "reason": "OK"},
                        0.01, 157, 60)[0] == "LOW-N")
    tc.check("verdict_refuse_without_null",
             ou_verdict(_f(-0.05, 5.0), None, 157, 60)[0] == "REFUSE")
    tc.check("verdict_refuse_without_beta",
             ou_verdict({"beta": None, "n_pairs": 100,
                         "reason": "нульова дисперсія"},
                        0.01, 157, 60)[0] == "REFUSE")
    tc.check("hl_thresholds_ordered", HL_PASS_D < HL_SLOW_D)

    # --- 5b. НУЛЬОВИЙ РОЗПОДІЛ І ЗМІЩЕННЯ МНК -------------------------------
    # ЦЕЙ БЛОК ФІКСУЄ ПРИЧИНУ ДРУГОЇ ПРАВКИ. Медіана beta на блуканні
    # НЕ нуль, а близько -(1+3phi)/n. Якщо колись стане ~0, значить
    # оцінювач змінили — і критерій треба переглядати.
    nb = null_beta_distribution(157, 400, 31337)
    med_nb = _median(nb)
    tc.check("null_beta_is_biased_negative",
             med_nb is not None and med_nb < -0.005, _fmt(med_nb, 5))
    tc.check("null_beta_bias_matches_theory",
             abs(med_nb - (-(1.0 + 3.0) / 157.0)) < 0.02, _fmt(med_nb, 5))
    tc.check("null_reps_kept", len(nb) >= 390, str(len(nb)))
    # Інваріантність до масштабу: розподіл залежить ЛИШЕ від n.
    rw_a = gen_random_walk(random.Random(77), 200, 1.0)
    rw_b = [v * 1000.0 for v in rw_a]
    tc.check("beta_scale_invariant",
             abs(ou_fit(_pairs_from_values(rw_a))["beta"]
                 - ou_fit(_pairs_from_values(rw_b))["beta"]) < 1e-9)
    # p_null: монотонність і відсутність нуля.
    tc.check("p_null_monotone",
             p_null(nb, -0.10) < p_null(nb, -0.01),
             f"{_fmt(p_null(nb, -0.10), 4)} vs "
             f"{_fmt(p_null(nb, -0.01), 4)}")
    tc.check("p_null_never_zero", p_null(nb, -99.0) > 0.0,
             _fmt(p_null(nb, -99.0), 5))
    tc.check("p_null_none_without_dist", p_null([], -0.05) is None)
    tc.check("p_null_none_without_beta", p_null(nb, None) is None)
    # СТАРИЙ КРИТЕРІЙ ПРОВАЛИВСЯ Б: типове блукання має hl у смузі PASS
    # і CI, що не перетинає нуль. Тест фіксує, що новий його ловить.
    rw_typ = gen_random_walk(random.Random(4242), 157, 1.0)
    fit_typ = ou_fit(_pairs_from_values(rw_typ))
    tc.check("random_walk_fakes_a_halflife",
             fit_typ["half_life_days"] is not None
             and fit_typ["half_life_days"] < HL_SLOW_D,
             _fmt(fit_typ["half_life_days"], 2))
    tc.check("null_criterion_rejects_that_walk",
             ou_verdict(fit_typ, p_null(nb, fit_typ["beta"]),
                        157, 60)[0] != "PASS",
             _fmt(p_null(nb, fit_typ["beta"]), 4))

    # --- 6. Генератори контролю ---------------------------------------------
    rw = gen_random_walk(random.Random(4), 4000, 1.0)
    tc.check("rw_beta_near_zero",
             abs(ou_fit(_pairs_from_values(rw))["beta"]) < 0.02,
             _fmt(ou_fit(_pairs_from_values(rw))["beta"], 5))
    ou_long = gen_ou(random.Random(5), 6000, POS_HL_D, 1.0)
    hl_est = ou_fit(_pairs_from_values(ou_long))["half_life_days"]
    tc.check("ou_generator_hits_target_hl",
             abs(hl_est - POS_HL_D) < 1.0, _fmt(hl_est, 2))
    tc.check("gen_ou_length", len(gen_ou(rng, 50, 5.0, 1.0)) == 50)
    tc.check("gen_rw_length", len(gen_random_walk(rng, 50, 1.0)) == 50)

    # --- 7. Контрольні вердикти ---------------------------------------------
    ok1, l1, _ = control_verdict(["FAIL"] * 95 + ["PASS"] * 5,
                                 ["PASS"] * 90 + ["FAIL"] * 10)
    tc.check("control_ok", ok1 and l1 == "CONTROL-OK")
    ok2, _, n2 = control_verdict(["PASS"] * 50 + ["FAIL"] * 50,
                                 ["PASS"] * 90 + ["FAIL"] * 10)
    tc.check("control_catches_false_positive",
             (not ok2) and "блукання" in n2, n2)
    ok3, l3, n3 = control_verdict(["FAIL"] * 100,
                                  ["INCONCLUSIVE"] * 100)
    tc.check("control_catches_blindness",
             (not ok3) and "сліпий" in n3, n3)
    # Брак потужності і хибний позитив — РІЗНІ ярлики.
    tc.check("blindness_is_underpowered_not_fail",
             l3 == "UNDERPOWERED", l3)
    _ok5, l5, _ = control_verdict(["PASS"] * 50 + ["FAIL"] * 50,
                                  ["INCONCLUSIVE"] * 100)
    tc.check("false_positive_outranks_underpower",
             l5 == "CONTROL-FAIL", l5)
    ok4, _, _ = control_verdict([], ["PASS"] * 10)
    tc.check("control_empty_is_fail", not ok4)
    tc.check("pass_rate_none_on_empty", pass_rate([]) is None)
    tc.check("pass_rate_counts_only_pass",
             abs(pass_rate(["PASS", "FAIL", "INCONCLUSIVE", "PASS"])
                 - 0.5) < 1e-12)

    # --- 8. Наскрізь ---------------------------------------------------------
    def _rows_from(values, start=base):
        return [(start + timedelta(hours=4 * i), 0.5, 0.5 + v, v)
                for i, v in enumerate(values)]

    # ВАЖЛИВО: при n=157 справжній OU НЕ детектується — потужність 0.10
    # (заміряно 26 сер). Тому тест «конвеєр бачить реверсію» ставиться
    # на ДОВГОМУ ряді, де потужність ~1.0. Ставити його на 157 означало
    # б вимагати від приладу того, чого він фізично не може, і спокушало
    # б «полагодити» це пониженням порогів.
    rows_long = _rows_from(gen_ou(random.Random(11), 1200, 4.0, 0.012))
    rep_long = run_measurement(rows_long, 60, 200, 0, SEED_DEFAULT, 300,
                               do_power=False)
    tc.check("e2e_ou_detected_when_powered",
             rep_long["ou_verdict"] == "PASS",
             f"{rep_long['ou_verdict']} hl="
             f"{_fmt(rep_long['fit'].get('half_life_days'), 2)} p="
             f"{_fmt(rep_long['p_null'], 4)}")
    rows_ou = _rows_from(gen_ou(random.Random(11), 157, 4.0, 0.012))
    rep_ou = run_measurement(rows_ou, 60, 200, 0, SEED_DEFAULT, 300,
                             do_power=False)
    rows_rw = _rows_from(gen_random_walk(random.Random(12), 157, 0.002))
    rep_rw = run_measurement(rows_rw, 60, 200, 0, SEED_DEFAULT, 300,
                             do_power=False)
    tc.check("e2e_rw_not_pass", rep_rw["ou_verdict"] != "PASS",
             f"{rep_rw['ou_verdict']} beta="
             f"{_fmt(rep_rw['fit'].get('beta'), 5)} p="
             f"{_fmt(rep_rw['p_null'], 4)}")
    tc.check("e2e_reports_p_null",
             rep_rw["p_null"] is not None
             and rep_rw["null_reps"] >= 290, str(rep_rw["null_reps"]))
    tc.check("e2e_reports_window",
             abs(rep_ou["window_days"] - 156 * 4 / 24.0) < 1e-6,
             _fmt(rep_ou["window_days"], 2))
    tc.check("e2e_ctrl_zero_is_explicit",
             rep_ou["final_verdict"] == "CONTROL-SKIPPED"
             and rep_ou["controls"]["skipped"] is True)
    rep_low = run_measurement(rows_ou[:40], 60, 200, 0, SEED_DEFAULT,
                              100, do_power=False)
    tc.check("e2e_lown", rep_low["ou_verdict"] == "LOW-N",
             rep_low["ou_verdict"])
    tc.check("e2e_thresholds_recorded",
             rep_ou["thresholds"]["hl_slow_d"] == HL_SLOW_D
             and rep_ou["thresholds"]["hl_pass_d"] == HL_PASS_D)
    tc.check("e2e_block_len_recorded",
             rep_ou["block_len"] >= 1
             and rep_ou["n_blocks"] >= MIN_BLOCKS,
             f"L={rep_ou['block_len']} blocks={rep_ou['n_blocks']}")

    # Малий, але справжній контрольний прогін: конвеєр цілком.
    nb120 = null_beta_distribution(120, 400, 555)
    neg_s = control_negative(120, 120, 99, 60, nb120)
    pos_s = control_positive(120, 120, 101, 60, nb120)
    # Головний тест усього модуля: калібрування нульового розподілу
    # тримає хибний позитив біля номінальних 5%.
    tc.check("mini_control_neg_near_nominal",
             pass_rate(neg_s) <= CTRL_NEG_MAX_PASS,
             _fmt(pass_rate(neg_s)))
    tc.check("mini_control_pos_above_neg",
             pass_rate(pos_s) > pass_rate(neg_s),
             f"{_fmt(pass_rate(pos_s))} vs {_fmt(pass_rate(neg_s))}")

    # --- 8b. ПОТУЖНІСТЬ: стіна n_eff, зафіксована числом ---------------------
    pw_now = power_at(157, POS_HL_D, 100, 300, 4321, 60)
    tc.check("power_at_current_n_is_low",
             pw_now < 0.30, _fmt(pw_now, 3))
    pw_big = power_at(1200, POS_HL_D, 100, 300, 4321, 60)
    tc.check("power_grows_with_n", pw_big > pw_now + 0.40,
             f"{_fmt(pw_big, 3)} vs {_fmt(pw_now, 3)}")
    tc.check("power_reaches_target_eventually",
             pw_big >= POWER_TARGET, _fmt(pw_big, 3))
    # Швидша реверсія має детектуватись легше — інакше тест не міряє
    # те, що думає, що міряє.
    tc.check("power_monotone_in_speed",
             power_at(157, 2.0, 100, 300, 4321, 60)
             > power_at(157, 8.0, 100, 300, 4321, 60))
    pj = power_projection(157, POS_HL_D, 5, 60, reps=60, nulls=200)
    tc.check("projection_covers_grid", len(pj) == len(POWER_MULT))
    tc.check("projection_now_has_zero_wait",
             abs(pj[0]["extra_days"]) < 1e-9 and pj[0]["n"] == 157)
    tc.check("projection_days_from_grid_rate",
             abs(pj[1]["extra_days"] - 157 / STEPS_PER_DAY) < 1e-6,
             _fmt(pj[1]["extra_days"], 2))
    tc.check("projection_power_increasing",
             pj[-1]["power"] > pj[0]["power"],
             f"{_fmt(pj[-1]['power'], 3)} vs {_fmt(pj[0]['power'], 3)}")

    # --- 8c. НАПРЯМОК ПРАВИЛА ПРО ПОТУЖНІСТЬ --------------------------------
    # Живий прогін 26 сер дав PASS (p_null 0.024) при потужності 0.17.
    # Перша редакція гасила такий PASS — безнапрямкове правило. Тести
    # фіксують ОБИДВІ гілки окремо.
    rows_p = _rows_from(gen_ou(random.Random(21), 1200, 4.0, 0.012))
    rep_p = run_measurement(rows_p, 60, 100, 0, SEED_DEFAULT, 300,
                            do_power=False)
    tc.check("underpower_keeps_pass",
             final_verdict("PASS", "n", "UNDERPOWERED", "c")[0] == "PASS")
    tc.check("underpower_note_explains_direction",
             "НЕГАТИВ" in final_verdict("PASS", "n", "UNDERPOWERED",
                                        "c")[1])
    tc.check("underpower_without_pass_is_underpowered",
             final_verdict("INCONCLUSIVE", "n", "UNDERPOWERED", "c")[0]
             == "UNDERPOWERED")
    tc.check("underpower_does_not_flip_fail_to_pass",
             final_verdict("FAIL", "n", "UNDERPOWERED", "c")[0]
             == "UNDERPOWERED")
    tc.check("control_fail_voids_even_pass",
             final_verdict("PASS", "n", "CONTROL-FAIL", "c")[0] == "VOID")
    tc.check("lown_outranks_control_fail",
             final_verdict("LOW-N", "n", "CONTROL-FAIL", "c")[0]
             == "LOW-N")
    tc.check("clean_control_passes_through",
             final_verdict("INCONCLUSIVE", "n", "CONTROL-OK", "c")
             == ("INCONCLUSIVE", "n"))
    tc.check("e2e_pass_pipeline_intact", rep_p["ou_verdict"] == "PASS",
             rep_p["ou_verdict"])

    # --- 9. Оточення ---------------------------------------------------------
    tc.check("known_env_covers_reads",
             all(nm in KNOWN_ENV for nm in
                 ("VOLEDGE_OU_MIN_N", "VOLEDGE_OU_BOOT",
                  "VOLEDGE_OU_CTRL_REPS", "VOLEDGE_OU_SEED",
                  "VOLEDGE_OU_NO_WRITE", "VOLEDGE_OU_NO_CACHE",
                  "VOLEDGE_OU_NULL")))
    tc.check("known_env_has_universal", "VOLEDGE_OFFLINE" in KNOWN_ENV)
    tc.check("known_env_no_step1_names",
             "VOLEDGE_SPREAD_MIN_N" not in KNOWN_ENV,
             "крок 1 читає їх у СВОЄМУ main(), тут вони no-op")
    tc.check("env_warn_catches_typo",
             warn_unknown_env(KNOWN_ENV,
                              environ={"VOLEDGE_OU_BOOTS": "500"},
                              quiet=True) == ["VOLEDGE_OU_BOOTS"])

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
    print("Vol-Edge :: spread_ou — крок 2 гіпотези A (READ-ONLY)")
    print("=" * 72)
    warn_unknown_env(KNOWN_ENV, label="spread_ou")

    tc = run_self_tests()
    if tc.failures:
        print("[FAIL] самотести провалено — заміру НЕ буде.")
        return 1

    if _env_flag("VOLEDGE_OFFLINE"):
        print("[OFFLINE] лише самотести, замір пропущено.")
        return 0

    min_n = _env_int("VOLEDGE_OU_MIN_N", MIN_N_DEFAULT)
    boot = _env_int("VOLEDGE_OU_BOOT", BOOT_DEFAULT)
    nulls = _env_int("VOLEDGE_OU_NULL", NULL_DEFAULT)
    reps = _env_int("VOLEDGE_OU_CTRL_REPS", CTRL_REPS_DEFAULT)
    seed = _env_int("VOLEDGE_OU_SEED", SEED_DEFAULT)
    no_write = _env_flag("VOLEDGE_OU_NO_WRITE")
    no_cache = _env_flag("VOLEDGE_OU_NO_CACHE")

    if 0 < boot < BOOT_MIN:
        print(f"[CTRL-WARN] бутстреп {boot} < {BOOT_MIN}: межі CI грубі.")
    if nulls < NULL_MIN:
        print(f"[CTRL-WARN] нульових реалізацій {nulls} < {NULL_MIN}: "
              f"p_null грубий, поріг {P_NULL_MAX} на ньому ненадійний.")

    chain_pairs = list_artifacts(CHAIN_DIR, "chain")
    print(f"[INFO] chain-артефактів знайдено: {len(chain_pairs)} "
          f"у {CHAIN_DIR}")
    if not chain_pairs:
        raise RuntimeError(f"у {CHAIN_DIR} немає chain-артефактів")

    rows, counters = collect_triplets(chain_pairs, no_cache=no_cache)
    if len(rows) < 4:
        raise RuntimeError(f"якорів {len(rows)} < 4 — міряти нічого")

    print(f"[INFO] нульовий розподіл: {nulls}; контролі: {reps}.")
    rep = run_measurement(rows, min_n, boot, reps, seed, nulls)
    rep["counters"] = counters
    print_report(rep, counters)

    if not no_write:
        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(OUT_DIR, f"spread_ou_{stamp}.json")
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
