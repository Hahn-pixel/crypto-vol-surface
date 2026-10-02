# -*- coding: utf-8 -*-
"""
[EN] Read-only calibration of the anomaly detector: compares detection
specifications, alert rates, time-of-day and day-of-week effects and
debounce redundancy against permutation nulls. Changes nothing in the
detector or its artifacts.

--- Ukrainian original below ---
Vol-Edge :: analytics/anomaly_calibrate.py

READ-ONLY калібрувальний аналіз детектора аномалій (Модуль 5).
НІЧОГО не змінює в anomaly_detect.py і не пише в artifacts/anomaly/
робочі артефакти — лише окремий калібрувальний звіт.

Мета: відповісти цифрами на питання «чому при n=167 частка алертів
65%, а не <1%?». Гіпотеза: ряди рівнів сильно автокорельовані, тож
номінальне n не є числом незалежних спостережень, а MAD міряє
внутрішньорежимне тремтіння, а не справжню мінливість фічі.

Що рахує по кожній фічі:
  1. Розподіл значень: n, median, MAD, min/max.
  2. Автокореляція рівнів на лагах 1, 3, 6 (6 знімків = 1 доба).
     n_eff = n * (1-r1)/(1+r1) — ефективний розмір вибірки для AR(1).
  3. Walk-forward |z| для трьох специфікацій:
       levels_expanding — як зараз (уся історія);
       levels_window    — ковзне вікно VOLEDGE_CALIB_WINDOW (default 60);
       diffs_expanding  — z по приростах між сусідніми знімками.
     Для кожної: медіана |z|, q90, q99, max, частка |z| >= порогу.
     Специфікація з часткою, близькою до теоретичної, — кандидат.
  4. Розбивка алертів по годині UTC (слот 00/04/08/12/16/20) і по
     дню тижня — перевірка на вікенд-ефект і слот-ефект.
  5. СЛОТОВИЙ ТЕСТ для *.vrp30 (додано 22 сер): чи диктує добовий
     знаменник (RV оновлюється раз/добу о 00:40 UTC) форму приростів.
     H0: розподіл |приросту| однаковий по UTC-слотах. Статистика —
     розкид медіан |diff| між слотами; p-value перестановочний,
     distribution-free. Якщо ефект є, слот 04:xx (перший після
     оновлення rv) має більший |приріст| за решту.
     Точки СТАРОЇ сітки (21/01/05/09/13/17 UTC, до фіксу CRON_TZ
     31 лип) відкидаються явно, з лічильником legacy_grid — інакше
     слот 04 змішується зі слотом 05.

Читає: artifacts/chain/chain_*.json(.gz) — ВСІ;
       artifacts/rv/rv_*.json(.gz) — для vrp30.
Пише:  artifacts/anomaly/calib_YYYYMMDD_HHMMSS.json (звіт)
       artifacts/anomaly/_calib_cache.json (кеш фіч по знімках)

Кеш: перерахунок фіч по 168 знімках включає SVI->JW конвертацію на
кожен зріз. Кеш ключований іменем файлу артефакту; лічильники
[CACHE] hit/miss друкуються явно. Видалити кеш = повний перерахунок.

Env-флаги:
  VOLEDGE_OFFLINE=1          -> лише самотести.
  VOLEDGE_Z_THRESH           -> поріг |z|, default 3.0.
  VOLEDGE_MIN_HISTORY        -> мінімум точок історії, default 12.
  VOLEDGE_VRP_MAX_AGE_H      -> макс. вік rv-артефакту, default 5
                                (ковзний rvroll оновлюється кожні 4 год).
  VOLEDGE_VRP_SOURCE         -> префікс артефактів знаменника vrp30:
                                "rvroll" (ковзне вікно, default) або
                                "rv" (добовий, legacy). Має збігатися
                                з тим, що читає anomaly_detect, інакше
                                калібратор міряє не ту схему.
  VOLEDGE_CALIB_WINDOW       -> ковзне вікно, default 60.
  VOLEDGE_CALIB_NO_CACHE=1   -> ігнорувати кеш, перерахувати все.
  VOLEDGE_CALIB_WD_PERM      -> перестановок у тесті на день тижня,
                                default 2000; 0 -> тест пропускається явно.
  VOLEDGE_CALIB_SLOT_PERM    -> перестановок у слотовому тесті, default
                                2000; 0 -> тест пропускається явно.
  VOLEDGE_CALIB_REDUND_LAGS  -> лаги для заміру надмірності дебаунсу,
                                default "1,6".
  VOLEDGE_CALIB_REDUND_SUFFIXES -> які фічі йдуть у замір надмірності,
                                default ".vrp30,.atm30" (друге — фіча
                                без відомої патології, негативний
                                контроль).

Чистий stdlib; мережі немає. Подвійний клік + input() завжди.
"""

import json
import math
import os
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
    from anomaly_detect import (CHAIN_DIR, RV_DIR, OUT_DIR, _load_json,
                                list_artifacts, features_of_snapshot,
                                vrp_from_rv, robust_z)
except ImportError:
    from analytics.anomaly_detect import (CHAIN_DIR, RV_DIR, OUT_DIR,
                                          _load_json, list_artifacts,
                                          features_of_snapshot,
                                          vrp_from_rv, robust_z)
try:
    from env_flags import merge_known, warn_unknown_env
except ImportError:
    from core.env_flags import merge_known, warn_unknown_env

# Змінні, які читає САМЕ ЦЯ точка входу. Список НЕ успадковує
# anomaly_detect.KNOWN_ENV, і це перевірено, а не припущено: усі сім
# os.environ.get детектора сидять у його main(), а звідси імпортуються
# лише чисті функції — main() детектора не виконується. Тому
# VOLEDGE_RANK_K під час калібрувального прогону справді no-op
# (працює VOLEDGE_CALIB_RANK_K), і попередження на неї ПРАВИЛЬНЕ:
# сплутати RANK_K з CALIB_RANK_K легко рівно так само, як 22 сер
# сплутали RVROLL_MODE з ROLL_MODE.
KNOWN_ENV = merge_known((
    "VOLEDGE_Z_THRESH",
    "VOLEDGE_MIN_HISTORY",
    "VOLEDGE_VRP_MAX_AGE_H",
    "VOLEDGE_VRP_SOURCE",
    "VOLEDGE_CALIB_WINDOW",
    "VOLEDGE_CALIB_NO_CACHE",
    "VOLEDGE_CALIB_RANK_K",
    "VOLEDGE_CALIB_COOLDOWN",
    "VOLEDGE_CALIB_SLOT_PERM",
    "VOLEDGE_CALIB_SLOT_SUFFIXES",
    "VOLEDGE_CALIB_SLOT_LAGS",
    "VOLEDGE_CALIB_REDUND_LAGS",
    "VOLEDGE_CALIB_REDUND_SUFFIXES",
    "VOLEDGE_CALIB_WD_PERM",
))

CACHE_PATH = os.path.join(OUT_DIR, "_calib_cache.json")
CACHE_VERSION = 1
LAGS = (1, 3, 6)

# --- Слотовий тест (добова ступінчастість знаменника vrp30) ------------------
# Канонічна UTC-сітка знімків після фіксу таймзон (utc_gate.sh, 31 лип).
CANONICAL_SLOT_HOURS = (0, 4, 8, 12, 16, 20)
# Стара сітка до фіксу (CRON_TZ no-op, зсув -3 год). ~35 точок 25-31 лип.
# Їх ОБОВ'ЯЗКОВО відкидати явно: інакше слот 04 змішається зі слотом 05.
LEGACY_SLOT_HOURS = (21, 1, 5, 9, 13, 17)
# rv_snapshot.sh о 00:40 UTC -> перший знімок ПІСЛЯ оновлення знаменника.
SLOT_AFTER_RV = 4
# Пара знімків вважається сусідньою, якщо розрив у цих межах (год).
SLOT_GAP_MIN_H = 3.5
SLOT_GAP_MAX_H = 4.5
SLOT_MIN_PER_BUCKET = 5
SLOT_PERM_DEFAULT = 2000

# --- Тест на день тижня (додано 23 сер, «ефект п'ятниці/суботи») -----------
# Наявна розбивка «Пн 20 / Чт 61 / Пт 91 / Сб 81» береться з alerts_levels,
# тобто зі СПЕЦИФІКАЦІЇ levels_expanding, яку калібрування відкинуло
# (надмірність 4.0x), і ПРОСУМОВАНА по всіх 17 фічах. З неї не видно ані
# того, чи кластер виживає в робочій схемі, ані в яких саме фічах він є.
# Ця секція міряє день тижня на РОБОЧІЙ схемі rank_diffs, per-feature.
#
# ЧОМУ ТЕСТ НА СИРИХ АЛЕРТАХ, А НЕ НА ДЕБАУНСНУТИХ: дебаунс переносить
# придушення на СУСІДНІЙ день (алерт у Пт глушить Сб), тобто сам створює
# залежність між буднями. Перестановка міток на дебаунснутих алертах цю
# структуру зруйнує і дасть занижене p. Сирі алерти — коректна одиниця;
# дебаунснуті друкуються поруч довідково.
WD_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")
WD_PERM_DEFAULT = 2000
WD_SEED = 20260823
# Пороги ЗАФІКСОВАНІ ДО ЗАМІРУ (та сама дисципліна, що в замірі
# надмірності 23 сер, де саме LOW-N і врятував від хибного висновку).
WD_MIN_RAW_ALERTS = 10      # менше -> LOW-N, вердикту немає
WD_MIN_SCORED_PER_DAY = 5   # день тижня з меншою експозицією не бере участі
WD_P_STRONG = 0.01
WD_EFFECT_RATE = 0.05       # розкид часток алертів між днями


# ----------------------------------------------------------------------------
# Статистика
# ----------------------------------------------------------------------------

def _median(xs):
    n = len(xs)
    if n == 0:
        return None
    s = sorted(xs)
    return s[n // 2] if n % 2 == 1 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _mad(xs):
    med = _median(xs)
    if med is None:
        return None
    return _median([abs(v - med) for v in xs])


def _quantile(xs, q):
    """Лінійна інтерполяція між порядковими статистиками. q у [0,1]."""
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


def autocorr(values, lag):
    """(r|None, reason). Пірсон між x[t] і x[t-lag]."""
    n = len(values)
    if lag <= 0:
        return None, f"лаг {lag} <= 0"
    if n - lag < 3:
        return None, f"пар {max(0, n - lag)} < 3"
    a = values[lag:]
    b = values[:-lag]
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da <= 0.0 or db <= 0.0:
        return None, "нульова дисперсія на одному з плечей"
    return num / (da * db), "OK"


def effective_n(n, r1):
    """n_eff для AR(1). r1 >= 1 або <= -1 -> None (виродженість)."""
    if r1 is None:
        return None
    if r1 >= 0.999999:
        return None
    if r1 <= -0.999999:
        return None
    val = n * (1.0 - r1) / (1.0 + r1)
    return max(1.0, val)


def walkforward_z(values, min_history, window=None):
    """
    [(idx, z), ...] — z кожної точки проти історії ДО неї.
    window=None -> розширювана історія; інакше останні `window` точок.
    Точки з відмовою robust_z (MAD=0) пропускаються з лічильником.
    Повертає (list_of_pairs, n_refused).
    """
    out = []
    refused = 0
    for i in range(len(values)):
        lo = 0 if window is None else max(0, i - window)
        hist = values[lo:i]
        if len(hist) < min_history:
            continue
        z, _reason = robust_z(hist, values[i])
        if z is None:
            refused += 1
            continue
        out.append((i, z))
    return out, refused


def walkforward_rank(values, min_history, k, window=None):
    """
    Рангова (distribution-free) детекція.
    Для кожної точки: скільки значень історії строго менші / більші.
    Алерт, якщо точка входить у K найменших АБО K найбільших історії.
    Очікувана частка для i.i.d. ряду = 2K/(len(hist)+1) ТОЧНО,
    незалежно від форми розподілу — калібрування не потрібне.
    Повертає ([(idx, tail_rank, side)], n_scored).
    tail_rank = 0 -> новий екстремум; side у {'low','high'}.
    """
    if k < 1:
        raise ValueError(f"k={k} має бути >= 1")
    out = []
    n_scored = 0
    for i in range(len(values)):
        lo = 0 if window is None else max(0, i - window)
        hist = values[lo:i]
        if len(hist) < min_history:
            continue
        n_scored += 1
        x = values[i]
        n_below = sum(1 for v in hist if v < x)
        n_above = sum(1 for v in hist if v > x)
        if n_below < k:
            out.append((i, n_below, "low"))
        elif n_above < k:
            out.append((i, n_above, "high"))
    return out, n_scored


def summarize_rank(hits, n_scored, k, hist_len_hint):
    if n_scored == 0:
        return {"status": "SKIP", "reason": "нуль оцінених точок"}
    n_low = sum(1 for _, _, s in hits if s == "low")
    n_high = sum(1 for _, _, s in hits if s == "high")
    return {"status": "OK", "n_scored": n_scored, "k": k,
            "n_alert": len(hits), "n_low": n_low, "n_high": n_high,
            "alert_rate": len(hits) / n_scored,
            "expected_rate_iid": 2.0 * k / (hist_len_hint + 1.0)}


def _rank_rel_by(groups):
    """{ключ: середній нормований ранг} значень серед УСІХ значень разом.

    Ранг нормується на (N-1), тож 0.5 = група поводиться як решта.
    Збіги отримують середній ранг — інакше замерзлі ділянки ряду
    систематично зсували б групу вниз.
    """
    pool = []
    for k, vals in groups.items():
        for v in vals:
            pool.append((v, k))
    n = len(pool)
    if n < 2:
        return {}
    pool.sort(key=lambda p: p[0])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pool[j + 1][0] == pool[i][0]:
            j += 1
        avg = 0.5 * (i + j)
        for t in range(i, j + 1):
            ranks[t] = avg / (n - 1.0)
        i = j + 1
    acc = {}
    for (_v, k), r in zip(pool, ranks):
        a = acc.setdefault(k, [0.0, 0])
        a[0] += r
        a[1] += 1
    return {k: a[0] / a[1] for k, a in acc.items()}


def _top_share_by(groups, frac):
    """{ключ: частка точок групи, що входять у верхні frac усіх значень}."""
    pool = sorted(v for vals in groups.values() for v in vals)
    n = len(pool)
    if n < 2 or not (0.0 < frac < 1.0):
        return {}
    cut = pool[max(0, int(math.ceil((1.0 - frac) * n)) - 1)]
    out = {}
    for k, vals in groups.items():
        if not vals:
            continue
        out[k] = sum(1 for v in vals if v >= cut) / len(vals)
    return out


def scored_indices(n, min_history, window=None):
    """Індекси точок, які walkforward_rank реально оцінює.

    Правило дублює умову `len(hist) >= min_history` з walkforward_rank.
    Дублювання правила — ризик тихого розходження, тому самотест
    scored_indices_matches_walkforward прибиває їх одне до одного.
    Потрібне для ЕКСПОЗИЦІЇ: без знаменника «скільки точок цього дня
    взагалі оцінювалось» сирі суми алертів по днях тижня міряють
    кількість знімків, а не схильність до алертів.
    """
    out = []
    for i in range(n):
        lo = 0 if window is None else max(0, i - window)
        if (i - lo) >= min_history:
            out.append(i)
    return out


def weekday_exposure(idxs, stamp_list):
    """{weekday: n} по списку індексів. Індекси поза межами — ігноруються."""
    out = {}
    for i in idxs:
        if 0 <= i < len(stamp_list):
            wd = stamp_list[i].weekday()
            out[wd] = out.get(wd, 0) + 1
    return out


def apply_cooldown(idxs, cooldown):
    """
    Дебаунс: після алерту фіча мовчить `cooldown` кроків.
    Жадібно зліва направо. cooldown<=0 -> без змін (явно).
    Повертає список залишених індексів.
    """
    if cooldown <= 0:
        return list(idxs)
    kept = []
    last = None
    for i in sorted(idxs):
        if last is None or (i - last) > cooldown:
            kept.append(i)
            last = i
    return kept


def summarize_z(zpairs, z_thresh):
    """Зведення по списку [(idx, z)]."""
    if not zpairs:
        return {"status": "SKIP", "reason": "нуль оцінених точок"}
    az = [abs(z) for _, z in zpairs]
    n_alert = sum(1 for v in az if v >= z_thresh)
    return {
        "status": "OK",
        "n_scored": len(az),
        "abs_z_median": _median(az),
        "abs_z_q90": _quantile(az, 0.90),
        "abs_z_q99": _quantile(az, 0.99),
        "abs_z_max": max(az),
        "n_alert": n_alert,
        "alert_rate": n_alert / len(az),
    }


# ----------------------------------------------------------------------------
# Побудова рядів (з кешем)
# ----------------------------------------------------------------------------

def _load_cache(no_cache):
    if no_cache:
        print("[ENV] VOLEDGE_CALIB_NO_CACHE=1 -> кеш ігноровано явно.")
        return {}
    if not os.path.isfile(CACHE_PATH):
        print(f"[CACHE] файлу немає ({CACHE_PATH}) — повний перерахунок.")
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            blob = json.load(f)
    except Exception as e:
        print(f"[CACHE] не прочитано ({e}) — повний перерахунок.")
        return {}
    if blob.get("version") != CACHE_VERSION:
        print(f"[CACHE] версія {blob.get('version')} != {CACHE_VERSION} "
              f"— повний перерахунок.")
        return {}
    entries = blob.get("entries", {})
    print(f"[CACHE] завантажено записів: {len(entries)}")
    return entries


def _save_cache(entries):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"version": CACHE_VERSION, "entries": entries},
                      f, ensure_ascii=False)
        print(f"[CACHE] збережено записів: {len(entries)} -> {CACHE_PATH}")
    except Exception as e:
        print(f"[CACHE] ЗБЕРЕЖЕННЯ ПРОВАЛЕНО (не критично): {e}")


def build_series(chain_pairs, rv_pairs, vrp_age_h, no_cache):
    """
    ({feature: [(stamp_dt, value), ...]}, counters).
    Ряди хронологічні. Пропуск фічі на знімку — це дірка в ряді,
    вона рахується у counters['feature_gaps'].
    """
    cache = _load_cache(no_cache)
    counters = {"snapshots": 0, "cache_hit": 0, "cache_miss": 0,
                "read_fail": 0, "feature_gaps": 0}
    series = {}
    stamps = []
    all_names = set()

    for st, path in chain_pairs:
        counters["snapshots"] += 1
        key = os.path.basename(path)
        feats = None
        if key in cache:
            feats = cache[key]
            counters["cache_hit"] += 1
        else:
            try:
                data = _load_json(path)
                f_, _skips, _q = features_of_snapshot(data)
                feats = f_
                cache[key] = f_
                counters["cache_miss"] += 1
            except Exception as e:
                counters["read_fail"] += 1
                print(f"[SKIP] знімок {key}: читання/фічі провалено: {e}")
                continue
        # vrp не кешуємо: залежить від пари (знімок, набір rv-артефактів).
        # Чисельник — atm30 ЦЬОГО знімка (контракт vrp_from_rv від 22 сер).
        atm30_by_cur = {}
        for _cur in ("BTC", "ETH"):
            _v = feats.get(f"{_cur}.atm30")
            if _v is not None:
                atm30_by_cur[_cur] = _v
        try:
            vf, _vs = vrp_from_rv(rv_pairs, st, vrp_age_h, atm30_by_cur)
        except TypeError as e:
            raise RuntimeError(
                f"[FAIL] контракт vrp_from_rv змінився: {e}. "
                f"Калібратор не має тихо втрачати vrp-фічі.") from e
        except Exception as e:
            vf = {}
            print(f"[SKIP] vrp для {key}: {e}")
        merged = dict(feats)
        merged.update(vf)
        stamps.append(st)
        all_names.update(merged)
        for k, v in merged.items():
            series.setdefault(k, []).append((st, v))

    for name in sorted(all_names):
        gaps = len(stamps) - len(series.get(name, []))
        if gaps > 0:
            counters["feature_gaps"] += gaps
            print(f"[SKIP] фіча {name}: дірок у ряді {gaps} "
                  f"з {len(stamps)} знімків")

    if not no_cache:
        _save_cache(cache)
    return series, counters


# ----------------------------------------------------------------------------
# Аналіз однієї фічі
# ----------------------------------------------------------------------------

def analyze_feature(name, pts, min_history, z_thresh, window,
                    rank_k=1, cooldown=6):
    """pts: [(stamp_dt, value)] хронологічно. -> dict звіту."""
    values = [v for _, v in pts]
    stamps = [s for s, _ in pts]
    n = len(values)
    rep = {"n": n,
           "first_utc": stamps[0].isoformat() if stamps else None,
           "last_utc": stamps[-1].isoformat() if stamps else None,
           "median": _median(values),
           "mad": _mad(values),
           "min": min(values) if values else None,
           "max": max(values) if values else None}

    ac = {}
    for lag in LAGS:
        r, reason = autocorr(values, lag)
        ac[f"lag{lag}"] = r if r is not None else None
        if r is None:
            ac[f"lag{lag}_reason"] = reason
    rep["autocorr"] = ac
    rep["n_eff_ar1"] = effective_n(n, ac.get("lag1"))

    diffs = [values[i] - values[i - 1] for i in range(1, n)]
    diff_stamps = stamps[1:]

    specs = {}

    zp, refused = walkforward_z(values, min_history, None)
    s = summarize_z(zp, z_thresh)
    s["n_refused_mad0"] = refused
    specs["levels_expanding"] = s
    alert_idx_levels = [i for i, z in zp if abs(z) >= z_thresh]

    zp_w, refused_w = walkforward_z(values, min_history, window)
    s = summarize_z(zp_w, z_thresh)
    s["n_refused_mad0"] = refused_w
    s["window"] = window
    specs["levels_window"] = s

    zp_d, refused_d = walkforward_z(diffs, min_history, None)
    s = summarize_z(zp_d, z_thresh)
    s["n_refused_mad0"] = refused_d
    specs["diffs_expanding"] = s
    alert_idx_diffs = [i for i, z in zp_d if abs(z) >= z_thresh]

    # Прирости, нормовані ЛОКАЛЬНОЮ шкалою: знімає і автокореляцію
    # (через різницю), і кластеризацію волатильності (через ковзний MAD
    # саме по приростах). Кандидат на робочу специфікацію.
    zp_dl, refused_dl = walkforward_z(diffs, min_history, window)
    s = summarize_z(zp_dl, z_thresh)
    s["n_refused_mad0"] = refused_dl
    s["window"] = window
    specs["diffs_local_scale"] = s

    # Емпіричні пороги: |z|, який історично дав би цільову частку.
    for spec_name, zpairs in (("levels_expanding", zp),
                              ("levels_window", zp_w),
                              ("diffs_expanding", zp_d),
                              ("diffs_local_scale", zp_dl)):
        az = sorted(abs(z) for _, z in zpairs)
        tgt = {}
        if az:
            for rate, label in ((0.01, "thresh_for_1pct"),
                                (0.005, "thresh_for_0p5pct"),
                                (0.02, "thresh_for_2pct")):
                tgt[label] = _quantile(az, 1.0 - rate)
        else:
            tgt["reason"] = "нуль оцінених точок"
        specs[spec_name]["empirical_thresholds"] = tgt

    # Рангові специфікації: частка задається K, а не порогом.
    hits_lv, ns_lv = walkforward_rank(values, min_history, rank_k, None)
    specs["rank_levels"] = summarize_rank(hits_lv, ns_lv, rank_k, n)
    hits_df, ns_df = walkforward_rank(diffs, min_history, rank_k, None)
    specs["rank_diffs"] = summarize_rank(hits_df, ns_df, rank_k, len(diffs))

    # Дебаунс поверх КОЖНОЇ специфікації: скільки алертів лишиться,
    # якщо після спрацювання фіча мовчить `cooldown` кроків.
    alert_sets = {
        "levels_expanding": [i for i, z in zp if abs(z) >= z_thresh],
        "levels_window": [i for i, z in zp_w if abs(z) >= z_thresh],
        "diffs_expanding": alert_idx_diffs,
        "diffs_local_scale": [i for i, z in zp_dl if abs(z) >= z_thresh],
        "rank_levels": [i for i, _, _ in hits_lv],
        "rank_diffs": [i for i, _, _ in hits_df],
    }
    for spec_name, idxs in alert_sets.items():
        s = specs[spec_name]
        if s.get("status") != "OK":
            continue
        kept = apply_cooldown(idxs, cooldown)
        s["n_alert_debounced"] = len(kept)
        s["cooldown"] = cooldown
        s["alert_rate_debounced"] = (len(kept) / s["n_scored"]
                                     if s["n_scored"] else None)

    rep["specs"] = specs
    rep["rank_hits_levels"] = [{"idx": i, "tail": t, "side": sd}
                               for i, t, sd in hits_lv]

    # Розбивка алертів по годині UTC і дню тижня (для рівнів і приростів)
    def _breakdown(idxs, stamp_list):
        by_hour, by_dow = {}, {}
        for i in idxs:
            if i >= len(stamp_list):
                continue
            st = stamp_list[i]
            by_hour[f"{st.hour:02d}"] = by_hour.get(f"{st.hour:02d}", 0) + 1
            by_dow[str(st.weekday())] = by_dow.get(str(st.weekday()), 0) + 1
        return {"by_hour_utc": by_hour, "by_weekday": by_dow}

    rep["alerts_levels"] = _breakdown(alert_idx_levels, stamps)
    rep["alerts_diffs"] = _breakdown(alert_idx_diffs, diff_stamps)

    # --- РОБОЧА СХЕМА: rank_diffs по днях тижня, з експозицією ---
    # Одиниця — ПРИРІСТ, тож мітки беруться з diff_stamps (пізніший знімок
    # пари), а не зі stamps. Плутанина тут зсунула б усі дні на один крок.
    sc_df = scored_indices(len(diffs), min_history, None)
    raw_df = [i for i, _, _ in hits_df]
    deb_df = apply_cooldown(raw_df, cooldown)
    exp_wd = weekday_exposure(sc_df, diff_stamps)
    raw_wd = weekday_exposure(raw_df, diff_stamps)
    deb_wd = weekday_exposure(deb_df, diff_stamps)
    # МАСШТАБ ПРИРОСТІВ ПО ДНЯХ. Рангова схема порівнює приріст із ЄДИНИМ
    # пулом історії, у якому змішані дні з різною мінливістю. Якщо в
    # якісь дні |приріст| систематично менший, ці дні майже не можуть
    # поставити рекорд, а решта конкурує з пулом, розбавленим малими
    # значеннями. Тоді різниця часток алертів по днях — НЕ подія, а
    # гетероскедастичність, і рангова схема на неї не розрахована
    # (той самий клас, що слотовий ефект vrp30).
    absd_wd = {}
    for i in sc_df:
        if 0 <= i < len(diff_stamps):
            absd_wd.setdefault(diff_stamps[i].weekday(),
                               []).append(abs(diffs[i]))
    rep["weekday_rank_diffs"] = {
        "n_scored": len(sc_df),
        "n_raw": len(raw_df),
        "n_debounced": len(deb_df),
        "cooldown": cooldown,
        "scored_by_weekday": {str(k): v for k, v in sorted(exp_wd.items())},
        "raw_by_weekday": {str(k): v for k, v in sorted(raw_wd.items())},
        "debounced_by_weekday": {str(k): v for k, v in sorted(deb_wd.items())},
        "rate_by_weekday": {
            str(k): (raw_wd.get(k, 0) / v if v else None)
            for k, v in sorted(exp_wd.items())},
        "med_abs_diff_by_weekday": {
            str(k): _median(v) for k, v in sorted(absd_wd.items())},
        # ХВІСТ, А НЕ ЦЕНТР. Алерт при K=1 — це РЕКОРД, тобто подія
        # хвоста розподілу. Медіана |diff| про хвіст не каже нічого:
        # день може мати нормальну медіану і при цьому втричі тоншу
        # праву хвостову частину, і тоді рекорди в ньому не ставляться
        # ніколи. Замір 23 сер на медіані саме через це нічого не
        # пояснив. q90/max рахуються на тих самих оцінених точках.
        "q90_abs_diff_by_weekday": {
            str(k): _quantile(sorted(v), 0.90)
            for k, v in sorted(absd_wd.items()) if v},
        "max_abs_diff_by_weekday": {
            str(k): max(v) for k, v in sorted(absd_wd.items()) if v},
        # q90/max на ~24 точках НА ДЕНЬ самі надто шумні: q90 — це
        # друге-третє значення, max — одне. Стабільні альтернативи на
        # тій самій вибірці:
        #   rank_rel  — середній нормований ранг |diff| дня серед УСІХ
        #               оцінених точок фічі (0.5 = день як усі);
        #   top10     — частка точок дня, що потрапили у верхні 10%
        #               |diff| фічі (0.1 = день як усі).
        # Обидві — середні по всіх точках дня, а не по одній-двох.
        # МОМЕНТИ у ISO — для згортки корельованих фіч в епізоди.
        "stamp_scored": [diff_stamps[i].isoformat() for i in sc_df
                         if 0 <= i < len(diff_stamps)],
        "stamp_raw": [diff_stamps[i].isoformat() for i in raw_df
                      if 0 <= i < len(diff_stamps)],
        "rank_rel_by_weekday": {
            str(k): v for k, v in sorted(_rank_rel_by(absd_wd).items())},
        "top10_by_weekday": {
            str(k): v for k, v in sorted(_top_share_by(absd_wd, 0.10).items())},
    }
    return rep


# ----------------------------------------------------------------------------
# Слотовий тест: чи диктує добовий знаменник форму приростів
# ----------------------------------------------------------------------------

def _lcg_stream(seed):
    """Детермінований потік u ~ U[0,1) без залежностей."""
    x = seed & 0x7FFFFFFF
    while True:
        x = (1103515245 * x + 12345) % 2147483648
        yield x / 2147483648.0


def _shuffled(items, rng):
    """Фішер-Йейтс на детермінованому потоці. Не мутує вхід."""
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = int(next(rng) * (i + 1))
        if j > i:
            j = i
        out[i], out[j] = out[j], out[i]
    return out


def slot_diffs(pts, lag=1):
    """
    (buckets, counters) для одного ряду.

    buckets: {hour_int: [diff, ...]} — приріст x[i] - x[i-lag], віднесений
    до UTC-години ПІЗНІШОГО знімка пари.

    lag=1 — сусідні знімки (крок 4 год).
    lag=6 — той самий слот попередньої доби (крок 24 год). Додано 22 сер:
      на лазі 1 ковзне вікно RV на кроці 12:10->16:10 викидає й додає
      ретерни НАЙВОЛАТИЛЬНІШОГО слота доби (12-16 UTC, відкриття США),
      тож при спадному режимі викинуте систематично більше за додане і
      RV осідає саме там. На лазі 6 обидва краї вікна припадають на
      однаковий час доби, тож ефект зникає ЗА ПОБУДОВОЮ, а не наближено.

    Відкидається явно, з лічильниками:
      legacy_grid  — хоч один кінець пари у старій сітці (до фіксу TZ);
      off_grid     — година не належить ані канонічній, ані старій сітці;
      bad_gap      — розрив пари поза очікуваним для цього лагу вікном.
    """
    if lag < 1:
        raise ValueError(f"lag={lag} має бути >= 1")
    buckets = {}
    c = {"pairs_seen": 0, "kept": 0, "legacy_grid": 0, "off_grid": 0,
         "bad_gap": 0, "lag": lag}
    gap_min = SLOT_GAP_MIN_H + 4.0 * (lag - 1)
    gap_max = SLOT_GAP_MAX_H + 4.0 * (lag - 1)
    c["gap_min_h"], c["gap_max_h"] = gap_min, gap_max
    for i in range(lag, len(pts)):
        st_prev, v_prev = pts[i - lag]
        st_cur, v_cur = pts[i]
        c["pairs_seen"] += 1
        hp, hc = st_prev.hour, st_cur.hour
        if hp in LEGACY_SLOT_HOURS or hc in LEGACY_SLOT_HOURS:
            c["legacy_grid"] += 1
            continue
        if hp not in CANONICAL_SLOT_HOURS or hc not in CANONICAL_SLOT_HOURS:
            c["off_grid"] += 1
            continue
        gap_h = (st_cur - st_prev).total_seconds() / 3600.0
        if not (gap_min <= gap_h <= gap_max):
            c["bad_gap"] += 1
            continue
        buckets.setdefault(hc, []).append(v_cur - v_prev)
        c["kept"] += 1
    return buckets, c


def slot_permutation_test(buckets, n_perm, seed=20260822):
    """
    ({...}|None, reason). H0: розподіл |приросту| однаковий по слотах.

    Статистика — розкид медіан |diff| між слотами (max - min). p-value:
    частка перестановок міток слотів зі статистикою >= спостереженої.
    Distribution-free; форма розподілу приростів не припускається.
    """
    usable = {h: v for h, v in buckets.items()
              if len(v) >= SLOT_MIN_PER_BUCKET}
    if len(usable) < 2:
        return None, (f"придатних слотів {len(usable)} < 2 "
                      f"(поріг {SLOT_MIN_PER_BUCKET} точок на слот)")
    if n_perm < 1:
        return None, f"n_perm={n_perm} < 1"
    sizes = [(h, len(usable[h])) for h in sorted(usable)]
    pool = []
    for h in sorted(usable):
        pool.extend(abs(d) for d in usable[h])

    def _stat(labels_pool):
        meds = []
        pos = 0
        for _h, n_ in sizes:
            meds.append(_median(labels_pool[pos:pos + n_]))
            pos += n_
        return max(meds) - min(meds), meds

    obs_stat, obs_meds = _stat(pool)
    rng = _lcg_stream(seed)
    n_ge = 0
    for _ in range(n_perm):
        s, _m = _stat(_shuffled(pool, rng))
        if s >= obs_stat:
            n_ge += 1
    p_val = (n_ge + 1) / (n_perm + 1)

    per_slot = {}
    for (h, n_), med in zip(sizes, obs_meds):
        signed = [d for d in usable[h]]
        per_slot[str(h)] = {"n": n_, "median_abs_diff": med,
                            "median_signed_diff": _median(signed)}

    # Цільова гіпотеза: слот одразу після оновлення rv (04:10) має
    # систематично більший |приріст|, бо знаменник стрибає саме там.
    ratio = None
    others = [m for (h, _n), m in zip(sizes, obs_meds)
              if h != SLOT_AFTER_RV]
    after = [m for (h, _n), m in zip(sizes, obs_meds)
             if h == SLOT_AFTER_RV]
    if after and others:
        base = _median(others)
        if base > 0:
            ratio = after[0] / base

    # ratio_after_rv_vs_rest прибитий до слота 04 і після переходу на
    # ковзне вікно втратив сенс як головна метрика. Додаємо ПІКОВИЙ слот
    # — той, що має максимальну медіану |приросту|, яким би він не був.
    peak_h, peak_med = None, None
    for (h, _n), m in zip(sizes, obs_meds):
        if peak_med is None or m > peak_med:
            peak_h, peak_med = h, m
    peak_ratio = None
    rest = [m for (h, _n), m in zip(sizes, obs_meds) if h != peak_h]
    if rest:
        base_p = _median(rest)
        if base_p > 0:
            peak_ratio = peak_med / base_p

    return {"n_perm": n_perm, "seed": seed, "stat_spread": obs_stat,
            "p_value": p_val, "per_slot": per_slot,
            "slot_after_rv": SLOT_AFTER_RV,
            "ratio_after_rv_vs_rest": ratio,
            "peak_slot": peak_h, "peak_median_abs_diff": peak_med,
            "ratio_peak_vs_rest": peak_ratio}, "OK"


def slot_analysis(series, n_perm, name_filter=None, lags=None):
    """
    {feature: {...}} для фіч, чия назва проходить name_filter.

    За замовчуванням — *.vrp30 І *.atm30. Друге додано 22 сер: після
    переходу на ковзне вікно пік у слоті 04:xx зник (ratio 3.125 -> 1.069
    BTC, 4.491 -> 0.942 ETH), але з'явився НОВИЙ пік о 16:xx на ОБОХ
    валютах. Знаменник тепер спільний і гладкий, тож підозра переходить
    на чисельник. atm30 — це і є чисельник vrp30, тому прогін по ньому
    окремо розділяє дві причини: якщо пік о 16:xx є в чистому atm30,
    ковзне вікно ні до чого.
    """
    if name_filter is None:
        _suf = os.environ.get("VOLEDGE_CALIB_SLOT_SUFFIXES",
                              ".vrp30,.atm30")
        _sufs = tuple(x.strip() for x in _suf.split(",") if x.strip())
        if not _sufs:
            raise RuntimeError(
                f"VOLEDGE_CALIB_SLOT_SUFFIXES={_suf!r} — порожній список")
        print(f"[ENV] VOLEDGE_CALIB_SLOT_SUFFIXES={_suf!r} -> {_sufs}")

        def name_filter(nm):
            return nm.endswith(_sufs)
    if lags is None:
        _lg = os.environ.get("VOLEDGE_CALIB_SLOT_LAGS", "1,6")
        try:
            lags = tuple(int(x.strip()) for x in _lg.split(",")
                         if x.strip())
        except ValueError:
            raise RuntimeError(
                f"VOLEDGE_CALIB_SLOT_LAGS={_lg!r} — очікується список "
                f"цілих через кому, напр. '1,6'")
        if not lags or any(l < 1 for l in lags):
            raise RuntimeError(
                f"VOLEDGE_CALIB_SLOT_LAGS={_lg!r} — лаги мають бути >= 1")
        print(f"[ENV] VOLEDGE_CALIB_SLOT_LAGS={_lg!r} -> {lags}")
    out = {}
    for name in sorted(series):
        if not name_filter(name):
            continue
        for lag in lags:
            buckets, counters = slot_diffs(series[name], lag=lag)
            res, reason = slot_permutation_test(buckets, n_perm)
            rec = {"counters": counters, "lag": lag,
                   "bucket_sizes": {str(h): len(v)
                                    for h, v in sorted(buckets.items())}}
            if res is None:
                rec["status"] = "SKIP"
                rec["reason"] = reason
            else:
                rec["status"] = "OK"
                rec.update(res)
            out[f"{name}@lag{lag}" if len(lags) > 1 else name] = rec
    return out


# ----------------------------------------------------------------------------
# Надмірність дебаунсу: перекривний лаг проти неперекривних добових точок
# ----------------------------------------------------------------------------
#
# ПИТАННЯ (пункт 1 «Наступних кроків», 23 сер): слотовий пік о 16:xx у
# vrp30 знімається приростом на ЛАЗІ 6 (той самий слот попередньої доби).
# Але прирости на лазі 6 ПЕРЕКРИВАЮТЬСЯ: x[t]-x[t-6] і x[t+1]-x[t-5]
# ділять 5 із 6 кроків. Рангова схема цього не любить — потрапивши в
# екстремум, ряд потрапить туди й наступні кілька разів, і дебаунс
# змушений глушити власний вихід.
#
# МЕТРИКА — та сама, що вирішила вибір rank_diffs проти levels 22 сер:
#   надмірність = алерти ДО дебаунсу / алерти ПІСЛЯ дебаунсу.
# Робоча схема (lag 1, cd=6) дає 1.7x. Поріг відмови 4.0x зафіксований
# ДО заміру (REDUND_REJECT_RATIO).
#
# ПАСТКА, ЯКУ ТУТ ЯВНО ЗАКРИТО: при cooldown=0 надмірність тотожно
# дорівнює 1.0 — не тому, що схема хороша, а тому, що глушити нема чим.
# Добовий варіант при cd=0 виглядав би переможцем за побудовою. Тому
# самотест redundancy_cd0_is_unity фіксує це як АРТЕФАКТ, а друк
# позначає такі рядки як [NOT-A-WIN].
#
# СПРАВЕДЛИВІСТЬ ПОРІВНЯННЯ: lag 1 має на 5 оцінених точок більше за
# lag 6 (менший стартовий розгін). Різниця в n сама зсуває частку, тож
# поруч із «сирими» числами рахується СПІЛЬНА ПІДМНОЖИНА: обидва
# варіанти оцінюються лише з того моменту, з якого визначені обидва.
# Історія для рангів при цьому НЕ обрізається — точки до старту
# скорингу лишаються в історії, інакше ми б порівнювали різні глибини.

REDUND_STEP_H = 4.0          # крок канонічної сітки знімків
REDUND_DAY_H = 24.0          # крок неперекривного добового варіанта
REDUND_GAP_TOL_H = 0.5       # допуск на розрив пари
REDUND_REJECT_RATIO = 4.0    # надмірність >= цього -> варіант відпадає
REDUND_WORKING_RATIO = 1.7   # робоча схема (lag 1, cd=6), для довідки
# Відношення n_raw/n_kept при 3 сирих алертах — шум, а не вимір.
# Нижче цього порогу число друкується, але позначається [LOW-N] і НЕ
# може дати вердикт: інакше добовий варіант «виграє» просто тому, що
# в нього замало подій, щоб було чому повторюватись.
REDUND_MIN_RAW_FOR_RATIO = 10


def _grid_idx(st, t0, step_h):
    """Ціла позиція знімка на сітці кроку step_h від t0."""
    return int(round((st - t0).total_seconds() / 3600.0 / step_h))


def lag_diff_points(pts, lag, t0=None):
    """
    ([(grid_idx, diff, stamp)], counters) — прирости x[i]-x[i-lag].

    grid_idx — позиція ПІЗНІШОГО знімка на 4-годинній сітці від t0.
    Він, а не порядковий номер у списку, подається в дебаунс: при дірці
    в даних порядковий номер збрехав би про фактичний час мовчання.

    Відкидається явно, з лічильниками (ті самі правила, що в slot_diffs):
      legacy_grid — хоч один кінець пари у старій сітці (до фіксу TZ);
      off_grid    — година поза канонічною і старою сітками (напр.
                    ручний прогін 18:05);
      bad_gap     — розрив пари поза 4*lag +- REDUND_GAP_TOL_H.
    """
    if lag < 1:
        raise ValueError(f"lag={lag} має бути >= 1")
    if not pts:
        return [], {"pairs_seen": 0, "kept": 0, "legacy_grid": 0,
                    "off_grid": 0, "bad_gap": 0, "lag": lag}
    if t0 is None:
        t0 = pts[0][0]
    out = []
    c = {"pairs_seen": 0, "kept": 0, "legacy_grid": 0, "off_grid": 0,
         "bad_gap": 0, "lag": lag}
    want = REDUND_STEP_H * lag
    c["gap_min_h"] = want - REDUND_GAP_TOL_H
    c["gap_max_h"] = want + REDUND_GAP_TOL_H
    for i in range(lag, len(pts)):
        st_prev, v_prev = pts[i - lag]
        st_cur, v_cur = pts[i]
        c["pairs_seen"] += 1
        hp, hc = st_prev.hour, st_cur.hour
        if hp in LEGACY_SLOT_HOURS or hc in LEGACY_SLOT_HOURS:
            c["legacy_grid"] += 1
            continue
        if hp not in CANONICAL_SLOT_HOURS or hc not in CANONICAL_SLOT_HOURS:
            c["off_grid"] += 1
            continue
        gap_h = (st_cur - st_prev).total_seconds() / 3600.0
        if not (c["gap_min_h"] <= gap_h <= c["gap_max_h"]):
            c["bad_gap"] += 1
            continue
        out.append((_grid_idx(st_cur, t0, REDUND_STEP_H),
                    v_cur - v_prev, st_cur))
        c["kept"] += 1
    return out, c


def daily_diff_points(pts, anchor_hour, t0=None):
    """
    ([(day_idx, diff, stamp)], counters) — НЕПЕРЕКРИВНІ добові прирости.

    Ряд проріджується до одного знімка на добу (година == anchor_hour),
    далі беруться сусідні пари з розривом 24 +- REDUND_GAP_TOL_H год.
    Сусідні прирости не мають спільних кроків ЗА ПОБУДОВОЮ — саме цим
    варіант відрізняється від перекривного лага 6.

    Ціна: точок у 6 разів менше. Це не недолік реалізації, а суть
    компромісу, і друк зобов'язаний показувати n_scored поруч із
    надмірністю.
    """
    if anchor_hour not in CANONICAL_SLOT_HOURS:
        raise ValueError(f"anchor_hour={anchor_hour} не з канонічної "
                         f"сітки {CANONICAL_SLOT_HOURS}")
    if not pts:
        return [], {"points_seen": 0, "anchor_kept": 0, "legacy_grid": 0,
                    "off_anchor": 0, "pairs_seen": 0, "kept": 0,
                    "bad_gap": 0, "anchor_hour": anchor_hour}
    if t0 is None:
        t0 = pts[0][0]
    c = {"points_seen": 0, "anchor_kept": 0, "legacy_grid": 0,
         "off_anchor": 0, "pairs_seen": 0, "kept": 0, "bad_gap": 0,
         "anchor_hour": anchor_hour}
    picked = []
    for st, v in pts:
        c["points_seen"] += 1
        if st.hour in LEGACY_SLOT_HOURS:
            c["legacy_grid"] += 1
            continue
        if st.hour != anchor_hour:
            c["off_anchor"] += 1
            continue
        picked.append((st, v))
        c["anchor_kept"] += 1
    out = []
    for i in range(1, len(picked)):
        st_prev, v_prev = picked[i - 1]
        st_cur, v_cur = picked[i]
        c["pairs_seen"] += 1
        gap_h = (st_cur - st_prev).total_seconds() / 3600.0
        if not (REDUND_DAY_H - REDUND_GAP_TOL_H <= gap_h
                <= REDUND_DAY_H + REDUND_GAP_TOL_H):
            c["bad_gap"] += 1
            continue
        out.append((_grid_idx(st_cur, t0, REDUND_DAY_H),
                    v_cur - v_prev, st_cur))
        c["kept"] += 1
    return out, c


def rank_redundancy(dpoints, min_history, k, cooldown, min_gi=None):
    """
    Надмірність дебаунсу для одного варіанта приросту.

    dpoints: [(grid_idx, diff, stamp)] хронологічно.
    min_gi:  скоринг починається з цього grid_idx (точки до нього
             лишаються в ІСТОРІЇ, але не оцінюються) — механізм
             справедливого порівняння варіантів на спільній підмножині.

    Дебаунс застосовується до grid_idx, тобто до фактичного часу, а не
    до порядкового номера: cooldown=6 при кроці 4 год = мовчання доби
    незалежно від дірок у зборі.

    -> {status, n_scored, n_raw, n_kept, redundancy, rate_raw,
        rate_kept, cooldown, cd_is_zero}
    """
    if k < 1:
        raise ValueError(f"k={k} має бути >= 1")
    if cooldown < 0:
        raise ValueError(f"cooldown={cooldown} має бути >= 0")
    vals = [d for _gi, d, _st in dpoints]
    n_scored = 0
    hit_gis = []
    for i in range(len(dpoints)):
        gi = dpoints[i][0]
        hist = vals[:i]
        if len(hist) < min_history:
            continue
        if min_gi is not None and gi < min_gi:
            continue
        n_scored += 1
        x = vals[i]
        n_below = sum(1 for v in hist if v < x)
        n_above = sum(1 for v in hist if v > x)
        if n_below < k or n_above < k:
            hit_gis.append(gi)
    if n_scored == 0:
        return {"status": "SKIP", "reason": "нуль оцінених точок",
                "n_scored": 0, "cooldown": cooldown}
    kept = apply_cooldown(hit_gis, cooldown)
    n_raw, n_kept = len(hit_gis), len(kept)
    return {"status": "OK", "n_scored": n_scored, "n_raw": n_raw,
            "n_kept": n_kept,
            "redundancy": (n_raw / n_kept) if n_kept > 0 else None,
            "rate_raw": n_raw / n_scored,
            "rate_kept": n_kept / n_scored,
            "cooldown": cooldown, "cd_is_zero": cooldown == 0,
            "low_n": n_raw < REDUND_MIN_RAW_FOR_RATIO,
            "min_raw_for_ratio": REDUND_MIN_RAW_FOR_RATIO,
            "k": k}


def redundancy_analysis(series, min_history, rank_k, cooldown,
                        lags=None, name_filter=None,
                        daily_cooldowns=(0, 1)):
    """
    {feature: {variant: {...}}} — замір надмірності по варіантах приросту.

    Варіанти на фічу:
      lag{L}          — перекривний приріст на лазі L, дебаунс cooldown;
      lag{L}_common   — те саме, але скоринг лише зі спільного старту
                        (порівняння варіантів на однаковій підмножині);
      daily@HH_cd{C}  — неперекривні добові точки з якорем HH, дебаунс C
                        (у ДОБАХ, бо крок варіанта — доба).

    Добовий варіант міряється на ВСІХ шести якорях, а не на одному:
    якщо надмірність стрибає між якорями, ми міряємо конкретну добову
    фазу, а не властивість схеми. Розкид по якорях друкується явно.
    """
    if lags is None:
        _lg = os.environ.get("VOLEDGE_CALIB_REDUND_LAGS", "1,6")
        try:
            lags = tuple(int(x.strip()) for x in _lg.split(",") if x.strip())
        except ValueError:
            raise RuntimeError(
                f"VOLEDGE_CALIB_REDUND_LAGS={_lg!r} — очікується список "
                f"цілих через кому, напр. '1,6'")
        if not lags or any(l < 1 for l in lags):
            raise RuntimeError(
                f"VOLEDGE_CALIB_REDUND_LAGS={_lg!r} — лаги мають бути >= 1")
        print(f"[ENV] VOLEDGE_CALIB_REDUND_LAGS={_lg!r} -> {lags}")
    if name_filter is None:
        _suf = os.environ.get("VOLEDGE_CALIB_REDUND_SUFFIXES",
                              ".vrp30,.atm30")
        _sufs = tuple(x.strip() for x in _suf.split(",") if x.strip())
        if not _sufs:
            raise RuntimeError(
                f"VOLEDGE_CALIB_REDUND_SUFFIXES={_suf!r} — порожній список")
        print(f"[ENV] VOLEDGE_CALIB_REDUND_SUFFIXES={_suf!r} -> {_sufs}")

        def name_filter(nm):
            return nm.endswith(_sufs)

    out = {}
    for name in sorted(series):
        if not name_filter(name):
            continue
        pts = series[name]
        if not pts:
            out[name] = {"_status": "SKIP", "_reason": "нуль точок"}
            continue
        t0 = pts[0][0]
        variants = {}

        # --- перекривні лаги -------------------------------------------
        by_lag = {}
        for lag in lags:
            dp, c = lag_diff_points(pts, lag, t0)
            by_lag[lag] = (dp, c)
            r = rank_redundancy(dp, min_history, rank_k, cooldown)
            r["counters"] = c
            r["overlap_steps"] = lag - 1
            r["step_h"] = REDUND_STEP_H
            variants[f"lag{lag}"] = r

        # --- спільна підмножина для чесного порівняння лагів ------------
        starts = []
        for lag in lags:
            dp, _c = by_lag[lag]
            if len(dp) > min_history:
                starts.append(dp[min_history][0])
        if len(starts) >= 2:
            common_gi = max(starts)
            for lag in lags:
                dp, c = by_lag[lag]
                r = rank_redundancy(dp, min_history, rank_k, cooldown,
                                    min_gi=common_gi)
                r["counters"] = c
                r["common_start_grid_idx"] = common_gi
                r["overlap_steps"] = lag - 1
                variants[f"lag{lag}_common"] = r

        # --- неперекривні добові точки по всіх якорях -------------------
        for cd_d in daily_cooldowns:
            ratios = []
            for anchor in CANONICAL_SLOT_HOURS:
                dp, c = daily_diff_points(pts, anchor, t0)
                r = rank_redundancy(dp, min_history, rank_k, cd_d)
                r["counters"] = c
                r["overlap_steps"] = 0
                r["step_h"] = REDUND_DAY_H
                r["anchor_hour"] = anchor
                variants[f"daily@{anchor:02d}_cd{cd_d}"] = r
                if r.get("status") == "OK" and r.get("redundancy")                         is not None:
                    ratios.append(r["redundancy"])
            if ratios:
                variants[f"daily_spread_cd{cd_d}"] = {
                    "status": "OK", "n_anchors": len(ratios),
                    "min_redundancy": min(ratios),
                    "max_redundancy": max(ratios),
                    "median_redundancy": _median(ratios),
                    "cooldown": cd_d,
                    "cd_is_zero": cd_d == 0}

        out[name] = variants
    return out


def redundancy_verdict(variants, lag_ref=1, lag_test=6):
    """
    ({...}, текст) — механічний вердикт за зафіксованим ДО заміру порогом.

    Не «вирішує», а лише прикладає REDUND_REJECT_RATIO до виміряного.
    Сіра зона (між робочою 1.7x і порогом 4.0x) називається сірою зоною
    явно, а не округлюється до бажаної відповіді.
    """
    ref = variants.get(f"lag{lag_ref}_common")         or variants.get(f"lag{lag_ref}") or {}
    tst = variants.get(f"lag{lag_test}_common")         or variants.get(f"lag{lag_test}") or {}
    if ref.get("status") != "OK" or tst.get("status") != "OK":
        return {"verdict": "SKIP"}, "недостатньо оцінених точок"
    r_ref = ref.get("redundancy")
    r_tst = tst.get("redundancy")
    if r_ref is None or r_tst is None:
        return {"verdict": "SKIP"}, "нуль алертів після дебаунсу"
    if ref.get("low_n") or tst.get("low_n"):
        return ({"verdict": "LOW-N", "redundancy_ref": r_ref,
                 "redundancy_test": r_tst,
                 "n_raw_ref": ref.get("n_raw"),
                 "n_raw_test": tst.get("n_raw"),
                 "min_raw_for_ratio": REDUND_MIN_RAW_FOR_RATIO},
                f"сирих алертів {ref.get('n_raw')}/{tst.get('n_raw')} < "
                f"{REDUND_MIN_RAW_FOR_RATIO} — відношення не вимір, "
                f"вердикту немає")
    if r_tst >= REDUND_REJECT_RATIO:
        v = "REJECT"
        txt = (f"надмірність {r_tst:.2f}x >= порога "
               f"{REDUND_REJECT_RATIO:.1f}x — перекривний лаг "
               f"{lag_test} відпадає")
    elif r_tst <= r_ref * 1.15:
        v = "ACCEPT"
        txt = (f"надмірність {r_tst:.2f}x не гірша за робочу "
               f"{r_ref:.2f}x — перекриття не роздуло вихід")
    else:
        v = "GREY"
        txt = (f"надмірність {r_tst:.2f}x між робочою {r_ref:.2f}x і "
               f"порогом {REDUND_REJECT_RATIO:.1f}x — сіра зона, "
               f"вирішує добовий варіант")
    return {"verdict": v, "redundancy_ref": r_ref,
            "redundancy_test": r_tst,
            "reject_ratio": REDUND_REJECT_RATIO,
            "lag_ref": lag_ref, "lag_test": lag_test}, txt


def print_redundancy(redund):
    """Друк секції надмірності."""
    if not redund:
        return
    print("\n" + "=" * 78)
    print("НАДМІРНІСТЬ ДЕБАУНСУ: перекривний лаг проти добових точок")
    print("=" * 78)
    print(f"  Метрика: алерти ДО дебаунсу / алерти ПІСЛЯ. Робоча схема "
          f"(lag 1, cd=6) = {REDUND_WORKING_RATIO}x.")
    print(f"  Поріг відмови зафіксований ДО заміру: "
          f"{REDUND_REJECT_RATIO:.1f}x.")
    print("  [NOT-A-WIN] = cooldown 0: надмірність тотожно 1.0 за "
          "побудовою, не результат.")
    for name in sorted(redund):
        variants = redund[name]
        print(f"\n  {name}")
        if variants.get("_status") == "SKIP":
            print(f"    [SKIP] {variants.get('_reason')}")
            continue
        print(f"    {'варіант':<18}{'n':>6}{'сирих':>7}{'після':>7}"
              f"{'надмір':>9}{'частка':>9}")
        for key in sorted(variants):
            r = variants[key]
            if key.startswith("daily_spread"):
                continue
            if r.get("status") != "OK":
                print(f"    {key:<18}[SKIP] {r.get('reason', '')}")
                continue
            mark = "  [NOT-A-WIN]" if r.get("cd_is_zero") else ""
            if r.get("low_n"):
                mark += "  [LOW-N]"
            print(f"    {key:<18}{r['n_scored']:>6}{r['n_raw']:>7}"
                  f"{r['n_kept']:>7}{_f(r.get('redundancy'), 2):>9}"
                  f"{_f(r.get('rate_kept'), 4):>9}{mark}")
        for cd_d in (0, 1):
            sp = variants.get(f"daily_spread_cd{cd_d}")
            if not sp:
                continue
            mark = "  [NOT-A-WIN]" if sp.get("cd_is_zero") else ""
            print(f"    розкид по {sp['n_anchors']} якорях (cd={cd_d}): "
                  f"{_f(sp['min_redundancy'], 2)}.."
                  f"{_f(sp['max_redundancy'], 2)}, "
                  f"медіана {_f(sp['median_redundancy'], 2)}{mark}")
        vd, txt = redundancy_verdict(variants)
        print(f"    [ВЕРДИКТ {vd['verdict']}] {txt}")


# ----------------------------------------------------------------------------
# Друк
# ----------------------------------------------------------------------------

def _f(x, prec=3):
    return "—" if x is None else f"{x:.{prec}f}"


# ----------------------------------------------------------------------------
# Тест на день тижня (робоча схема rank_diffs)
# ----------------------------------------------------------------------------

def weekday_permutation_test(exposure, alerts, n_perm,
                             min_scored=WD_MIN_SCORED_PER_DAY,
                             seed=WD_SEED):
    """({...}|None, reason). H0: ймовірність алерту не залежить від дня.

    exposure: {weekday: n_scored}; alerts: {weekday: n_alert}.
    Пул — по одному індикатору 0/1 на КОЖНУ оцінену точку; перестановка
    йде по мітках днів, тож нерівна експозиція врахована за побудовою.
    Статистика — розкид часток (max - min) між днями.
    """
    usable = {wd: n for wd, n in exposure.items() if n >= min_scored}
    if len(usable) < 2:
        return None, (f"придатних днів {len(usable)} < 2 "
                      f"(поріг {min_scored} оцінених точок на день)")
    if n_perm < 1:
        return None, f"n_perm={n_perm} < 1"
    keys = sorted(usable)
    sizes = [(wd, usable[wd]) for wd in keys]
    pool = []
    for wd in keys:
        n_a = min(alerts.get(wd, 0), usable[wd])
        pool.extend([1] * n_a + [0] * (usable[wd] - n_a))
    n_total_alerts = sum(pool)
    if n_total_alerts == 0:
        return None, "нуль алертів на придатних днях"

    def _stat(vals):
        rates, pos = [], 0
        for _wd, n_ in sizes:
            rates.append(sum(vals[pos:pos + n_]) / n_)
            pos += n_
        return max(rates) - min(rates), rates

    obs_stat, obs_rates = _stat(pool)
    rng = _lcg_stream(seed)
    ge = 0
    for _ in range(n_perm):
        st_, _r = _stat(_shuffled(pool, rng))
        if st_ >= obs_stat:
            ge += 1
    p = (ge + 1.0) / (n_perm + 1.0)
    peak_i = max(range(len(keys)), key=lambda i: obs_rates[i])
    return ({
        "days": keys,
        "scored": [n_ for _wd, n_ in sizes],
        "alerts": [alerts.get(wd, 0) for wd in keys],
        "rates": obs_rates,
        "n_raw_alerts": n_total_alerts,
        "effect_rate": obs_stat,
        "p": p,
        "n_perm": n_perm,
        "seed": seed,
        "peak_day": keys[peak_i],
        "peak_rate": obs_rates[peak_i],
    }, None)


def weekday_verdict(test):
    """Вердикт за порогами, зафіксованими ДО заміру."""
    if test is None:
        return "NO-TEST"
    if test["n_raw_alerts"] < WD_MIN_RAW_ALERTS:
        return "LOW-N"
    if test["p"] < WD_P_STRONG and test["effect_rate"] >= WD_EFFECT_RATE:
        return "WEEKDAY-EFFECT"
    if test["p"] >= 0.05:
        return "NO-EFFECT"
    return "INCONCLUSIVE"


def weekday_analysis(feats, n_perm):
    """Per-feature і зведений тест на день тижня для rank_diffs."""
    out = {"per_feature": {}, "n_perm": n_perm,
           "thresholds": {"min_raw_alerts": WD_MIN_RAW_ALERTS,
                          "min_scored_per_day": WD_MIN_SCORED_PER_DAY,
                          "p_strong": WD_P_STRONG,
                          "effect_rate": WD_EFFECT_RATE,
                          "fixed_before_measurement": True}}
    pool_exp, pool_raw, pool_deb = {}, {}, {}
    for name in sorted(feats):
        wd = feats[name].get("weekday_rank_diffs")
        if not wd:
            continue
        exp = {int(k): v for k, v in wd["scored_by_weekday"].items()}
        raw = {int(k): v for k, v in wd["raw_by_weekday"].items()}
        deb = {int(k): v for k, v in wd["debounced_by_weekday"].items()}
        for d, v in exp.items():
            pool_exp[d] = pool_exp.get(d, 0) + v
        for d, v in raw.items():
            pool_raw[d] = pool_raw.get(d, 0) + v
        for d, v in deb.items():
            pool_deb[d] = pool_deb.get(d, 0) + v
        # НОРМОВАНИЙ МАСШТАБ: абсолютні величини по фічах непорівнювані
        # (atm у в.п., vrp30 — відношення), тож кожен профіль ділиться на
        # медіану по днях ЦІЄЇ Ж фічі. Три моменти: центр (med), хвіст
        # (q90) і екстремум (max). Рекорд — подія хвоста, тож вирішує
        # q90/max, а не med.
        def _rel(key):
            raw_ = {int(k): v for k, v in (wd.get(key) or {}).items()
                    if v is not None}
            b = _median(list(raw_.values())) if raw_ else None
            if not b or abs(b) < 1e-15:
                return {}
            return {d: v / b for d, v in raw_.items()}

        scale_rel = _rel("med_abs_diff_by_weekday")
        tail_rel = _rel("q90_abs_diff_by_weekday")
        max_rel = _rel("max_abs_diff_by_weekday")
        # rank_rel і top10 НЕ нормуються на медіану: вони вже в
        # безрозмірній шкалі з відомим нейтральним рівнем (0.5 і 0.10).
        rank_abs = {int(k): v
                    for k, v in (wd.get("rank_rel_by_weekday") or {}).items()}
        top10_abs = {int(k): v
                     for k, v in (wd.get("top10_by_weekday") or {}).items()}
        t, reason = weekday_permutation_test(exp, raw, n_perm)
        out["per_feature"][name] = {
            "test": t, "reason": reason, "verdict": weekday_verdict(t),
            "n_raw": wd["n_raw"], "n_debounced": wd["n_debounced"],
            "scored": exp, "raw": raw, "debounced": deb,
            "scale_rel": scale_rel,
            "tail_rel": tail_rel,
            "max_rel": max_rel,
            "rank_abs": rank_abs,
            "top10_abs": top10_abs,
            "stamps": {"scored": wd.get("stamp_scored", []),
                       "raw": wd.get("stamp_raw", [])},
        }
    t_all, reason_all = weekday_permutation_test(pool_exp, pool_raw, n_perm)
    # ЗАСТЕРЕЖЕННЯ ПРО ПУЛ: фічі не незалежні (спільні знімки, спільні
    # зрізи), тож зведене p занижене. Воно для ОРІЄНТИРУ; вердикт
    # ухвалюється по per-feature рядках.
    out["pooled"] = {"test": t_all, "reason": reason_all,
                     "verdict": weekday_verdict(t_all),
                     "caveat": "фічі корельовані — зведене p занижене",
                     "scored": pool_exp, "raw": pool_raw,
                     "debounced": pool_deb}

    # --- ГРУПОВІ ПУЛИ: канал atm7 проти решти ------------------------------
    # Крок A показав, що структурний канал (плече інтерполяції їздить по
    # днях тижня) існує ТІЛЬКИ в atm7: у atm30/atm90 вузли стоять.
    # Per-feature при ~4 сирих алертах на фічу дасть LOW-N за побудовою,
    # тож контраст «atm7 проти решти» — єдиний варіант з експозицією.
    # Це НЕ обхід порогу LOW-N: поріг застосовується і тут, просто
    # чисельник накопичується по фічах одного каналу.
    groups = {}
    for name in sorted(out["per_feature"]):
        g = "atm7" if name.endswith(".atm7") else "other"
        gd = groups.setdefault(g, {"exp": {}, "raw": {}, "deb": {},
                                   "features": []})
        gd["features"].append(name)
        r = out["per_feature"][name]
        for _key, _dst in (("scale_rel", "scale"), ("tail_rel", "tail"),
                           ("max_rel", "maxr"), ("rank_abs", "rankr"),
                           ("top10_abs", "top10")):
            for d, v in r[_key].items():
                gd.setdefault(_dst, {}).setdefault(d, []).append(v)
        for d, v in r["scored"].items():
            gd["exp"][d] = gd["exp"].get(d, 0) + v
        for d, v in r["raw"].items():
            gd["raw"][d] = gd["raw"].get(d, 0) + v
        for d, v in r["debounced"].items():
            gd["deb"][d] = gd["deb"].get(d, 0) + v
    out["groups"] = {}
    for g, gd in sorted(groups.items()):
        t_g, reason_g = weekday_permutation_test(gd["exp"], gd["raw"], n_perm)
        out["groups"][g] = {
            "test": t_g, "reason": reason_g,
            "verdict": weekday_verdict(t_g),
            "features": gd["features"],
            "scored": gd["exp"], "raw": gd["raw"], "debounced": gd["deb"],
            "scale_rel": {d: _median(v)
                          for d, v in sorted(gd.get("scale", {}).items())},
            "tail_rel": {d: _median(v)
                         for d, v in sorted(gd.get("tail", {}).items())},
            "max_rel": {d: _median(v)
                        for d, v in sorted(gd.get("maxr", {}).items())},
            "rank_abs": {d: _median(v)
                         for d, v in sorted(gd.get("rankr", {}).items())},
            "top10_abs": {d: _median(v)
                          for d, v in sorted(gd.get("top10", {}).items())},
        }

    # --- ЕПІЗОДИ: унікальні МОМЕНТИ, а не алерти по фічах ------------------
    # 106 сирих алертів у пулі other — це 15 фіч, побудованих з ОДНИХ І ТИХ
    # САМИХ зрізів одного знімка. Один рух ринку піднімає пів-десятка фіч
    # одночасно, і пул рахує його як пів-десятка подій. Незалежна одиниця —
    # МОМЕНТ, у який спрацювала хоча б одна фіча групи.
    # Це не косметика: якщо 106 алертів сидять на ~20 моментах, фактичне n
    # у п'ять разів менше, і поріг LOW-N стає визначальним.
    out["episodes"] = {}
    for g, gd in sorted(groups.items()):
        a_st, s_st = set(), set()
        n_fa = 0
        for name in gd["features"]:
            st = out["per_feature"][name].get("stamps") or {}
            a_st |= set(st.get("raw", ()))
            s_st |= set(st.get("scored", ()))
            n_fa += out["per_feature"][name]["n_raw"]
        exp_e, raw_e = {}, {}
        for iso in s_st:
            wd = datetime.fromisoformat(iso).weekday()
            exp_e[wd] = exp_e.get(wd, 0) + 1
        for iso in a_st:
            wd = datetime.fromisoformat(iso).weekday()
            raw_e[wd] = raw_e.get(wd, 0) + 1
        t_e, reason_e = weekday_permutation_test(exp_e, raw_e, n_perm)
        out["episodes"][g] = {
            "n_feature_alerts": n_fa,
            "n_episodes": len(a_st),
            "n_moments": len(s_st),
            "collapse": (len(a_st) / n_fa) if n_fa else None,
            "scored": exp_e, "raw": raw_e,
            "test": t_e, "reason": reason_e,
            "verdict": weekday_verdict(t_e),
        }
    return out


def print_weekday(wa):
    print("\n" + "=" * 78)
    print("ДЕНЬ ТИЖНЯ НА РОБОЧІЙ СХЕМІ (rank_diffs, сирі алерти)")
    print("=" * 78)
    th = wa["thresholds"]
    print(f"  Пороги ДО заміру: p<{th['p_strong']} і розкид часток "
          f">={th['effect_rate']} -> WEEKDAY-EFFECT; "
          f"<{th['min_raw_alerts']} сирих алертів -> LOW-N.")
    print("  Тест на СИРИХ алертах: дебаунс переносить придушення на")
    print("  сусідній день і сам створює залежність між буднями.")
    hdr = "  " + f"{'фіча':<20}" + "".join(f"{d:>7}" for d in WD_NAMES) \
          + f"{'сирих':>8}{'деб.':>7}{'p':>8}{'ефект':>8}  вердикт"
    print("\n" + hdr)
    print("  " + "-" * (len(hdr) - 2))
    for name in sorted(wa["per_feature"]):
        r = wa["per_feature"][name]
        cells = ""
        for d in range(7):
            sc = r["scored"].get(d, 0)
            al = r["raw"].get(d, 0)
            cells += f"{(al / sc):>7.2f}" if sc else f"{'--':>7}"
        t = r["test"]
        p_s = f"{t['p']:>8.4f}" if t else f"{'--':>8}"
        e_s = f"{t['effect_rate']:>8.3f}" if t else f"{'--':>8}"
        print(f"  {name:<20}{cells}{r['n_raw']:>8}{r['n_debounced']:>7}"
              f"{p_s}{e_s}  {r['verdict']}")
    print("\n  (числа в клітинках — ЧАСТКА сирих алертів на оцінену точку "
          "цього дня)")
    print("\n  ГРУПОВІ ПУЛИ (крок A: структурний канал лише в atm7)")
    for g in sorted(wa.get("groups", {})):
        gr = wa["groups"][g]
        t = gr["test"]
        cells = ""
        for d in range(7):
            sc = gr["scored"].get(d, 0)
            al = gr["raw"].get(d, 0)
            cells += f"{(al / sc):>7.3f}" if sc else f"{'--':>7}"
        n_raw = sum(gr["raw"].values())
        if t is None:
            print(f"  {g:<8} ({len(gr['features'])} фіч){cells}  "
                  f"сирих {n_raw}  тесту немає: {gr['reason']}")
            continue
        print(f"  {g:<8} ({len(gr['features'])} фіч){cells}  "
              f"сирих {n_raw}  p={t['p']:.4f}  "
              f"ефект {t['effect_rate']:.3f}  пік {WD_NAMES[t['peak_day']]}"
              f"  -> {gr['verdict']}")
    print("\n  ПРОФІЛЬ |diff| ПО ДНЯХ (нормовано на медіану по днях)")
    print("  РЕКОРД — ПОДІЯ ХВОСТА: вирішує q90/max, а не med.")
    for g in sorted(wa.get("groups", {})):
        gr = wa["groups"][g]
        for lbl, key in (("med", "scale_rel"), ("q90", "tail_rel"),
                         ("max", "max_rel"), ("rank", "rank_abs"),
                         ("top10", "top10_abs")):
            sr = gr.get(key) or {}
            cells = "".join(f"{sr[d]:>7.2f}" if d in sr else f"{'--':>7}"
                            for d in range(7))
            print(f"  {g:<8} {lbl:<4}    {cells}")
    print("  med/q90/max — нормовані на медіану по днях (нейтраль 1.00).")
    print("  rank — середній ранг |diff| дня (нейтраль 0.50); top10 —")
    print("  частка точок дня у верхніх 10% |diff| (нейтраль 0.10).")
    print("  q90/max на ~24 точках шумні; СУДИТИ ТРЕБА ПО rank і top10.")
    print("  Якщо rank/top10 рівні, а частка алертів — ні, справа НЕ в")
    print("  величині приростів, і пояснення треба шукати деінде.")

    # ІНТЕРПРЕТАЦІЯ ПЕЧАТАЄТЬСЯ ПО ФАКТУ, А НЕ ШАБЛОНОМ. Перша версія
    # друкувала обидві гілки завжди, і на прогоні 23 сер це прочиталось
    # як підтвердження гіпотези, якої дані НЕ дали.
    _v7 = (wa.get("groups", {}).get("atm7") or {}).get("verdict")
    _vo = (wa.get("groups", {}).get("other") or {}).get("verdict")
    if _v7 == "WEEKDAY-EFFECT" and _vo != "WEEKDAY-EFFECT":
        print("  [ВИСНОВОК] ефект лише в atm7 -> кластер СТРУКТУРНИЙ "
              "(плече інтерполяції).")
    elif _vo == "WEEKDAY-EFFECT" and _v7 != "WEEKDAY-EFFECT":
        print("  [ВИСНОВОК] ефект у other, але НЕ в atm7 -> причина НЕ в "
              "інтерполяції: у other цього каналу немає.")
    elif _v7 == "WEEKDAY-EFFECT" and _vo == "WEEKDAY-EFFECT":
        print("  [ВИСНОВОК] ефект в обох групах -> спільна причина, "
              "не специфічна для atm7.")
    else:
        print("  [ВИСНОВОК] ефекту в жодній групі -> денний кластер на "
              "робочій схемі не підтверджено.")

    print("\n  ЕПІЗОДИ (унікальні МОМЕНТИ; фічі одного знімка — одна подія)")
    for g in sorted(wa.get("episodes", {})):
        e = wa["episodes"][g]
        cells = ""
        for d in range(7):
            sc = e["scored"].get(d, 0)
            al = e["raw"].get(d, 0)
            cells += f"{(al / sc):>7.2f}" if sc else f"{'--':>7}"
        col = (f"{e['collapse']:.2f}" if e["collapse"] is not None else "--")
        t = e["test"]
        tail = (f"p={t['p']:.4f}  ефект {t['effect_rate']:.3f}  "
                f"пік {WD_NAMES[t['peak_day']]}" if t
                else f"тесту немає: {e['reason']}")
        print(f"  {g:<8}        {cells}  алертів {e['n_feature_alerts']}"
              f" -> епізодів {e['n_episodes']} (x{col})  {tail}"
              f"  -> {e['verdict']}")
    print("  ЕПІЗОД — незалежна одиниця. Якщо тут LOW-N, то WEEKDAY-EFFECT")
    print("  вище був порахований на кратно продубльованих подіях.")

    pl = wa["pooled"]
    t = pl["test"]
    if t is None:
        print(f"\n  ЗВЕДЕНО: тесту немає ({pl['reason']})")
    else:
        print(f"\n  ЗВЕДЕНО ПО ВСІХ ФІЧАХ: p={t['p']:.4f}, "
              f"розкид часток {t['effect_rate']:.3f}, "
              f"пік {WD_NAMES[t['peak_day']]}, сирих {t['n_raw_alerts']} "
              f"-> {pl['verdict']}")
        print(f"  [CAVEAT] {pl['caveat']}")
    verds = [r["verdict"] for r in wa["per_feature"].values()]
    n_eff_ = sum(1 for v in verds if v == "WEEKDAY-EFFECT")
    n_low = sum(1 for v in verds if v == "LOW-N")
    print(f"  Фіч із WEEKDAY-EFFECT: {n_eff_}; LOW-N: {n_low}; "
          f"усього: {len(verds)}")


def print_report(report, z_thresh):
    feats = report["features"]
    print("\n" + "=" * 78)
    print("АВТОКОРЕЛЯЦІЯ РІВНІВ (r1 близько 1 => номінальне n оманливе)")
    print("=" * 78)
    print(f"{'фіча':<20}{'n':>5}{'r_lag1':>9}{'r_lag3':>9}"
          f"{'r_lag6':>9}{'n_eff':>9}")
    for name in sorted(feats):
        r = feats[name]
        ac = r["autocorr"]
        print(f"{name:<20}{r['n']:>5}{_f(ac.get('lag1')):>9}"
              f"{_f(ac.get('lag3')):>9}{_f(ac.get('lag6')):>9}"
              f"{_f(r['n_eff_ar1'], 1):>9}")

    for spec in ("levels_expanding", "levels_window", "diffs_expanding",
                 "diffs_local_scale"):
        print("\n" + "=" * 78)
        print(f"СПЕЦИФІКАЦІЯ: {spec}   (поріг |z| = {z_thresh})")
        print("=" * 78)
        print(f"{'фіча':<20}{'scored':>8}{'med|z|':>9}{'q90':>9}"
              f"{'q99':>9}{'max':>9}{'alert%':>9}")
        rates = []
        for name in sorted(feats):
            s = feats[name]["specs"][spec]
            if s["status"] != "OK":
                print(f"{name:<20}{'SKIP':>8}  {s['reason']}")
                continue
            rates.append(s["alert_rate"])
            print(f"{name:<20}{s['n_scored']:>8}"
                  f"{_f(s['abs_z_median'], 2):>9}{_f(s['abs_z_q90'], 2):>9}"
                  f"{_f(s['abs_z_q99'], 2):>9}{_f(s['abs_z_max'], 2):>9}"
                  f"{100.0 * s['alert_rate']:>8.1f}%")
        if rates:
            print(f"{'СЕРЕДНЯ ЧАСТКА':<20}{'':>8}{'':>9}{'':>9}{'':>9}"
                  f"{'':>9}{100.0 * sum(rates) / len(rates):>8.1f}%")

    print("\n" + "=" * 78)
    print("ЕМПІРИЧНІ ПОРОГИ |z| для цільової частки алертів")
    print("(поріг, який на НАЯВНІЙ історії дав би саме таку частку)")
    print("=" * 78)
    for spec in ("levels_expanding", "diffs_expanding", "diffs_local_scale"):
        print(f"\n  -- {spec} --")
        print(f"  {'фіча':<20}{'2%':>9}{'1%':>9}{'0.5%':>9}")
        for name in sorted(feats):
            t = feats[name]["specs"][spec].get("empirical_thresholds", {})
            if "reason" in t:
                print(f"  {name:<20}{'SKIP':>9}  {t['reason']}")
                continue
            print(f"  {name:<20}{_f(t.get('thresh_for_2pct'), 2):>9}"
                  f"{_f(t.get('thresh_for_1pct'), 2):>9}"
                  f"{_f(t.get('thresh_for_0p5pct'), 2):>9}")

    print("\n" + "=" * 78)
    print("РАНГОВІ СПЕЦИФІКАЦІЇ (distribution-free: частка задана K)")
    print("=" * 78)
    for spec in ("rank_levels", "rank_diffs"):
        print(f"\n  -- {spec} --")
        print(f"  {'фіча':<20}{'scored':>8}{'алертів':>9}{'низ':>6}"
              f"{'верх':>6}{'факт%':>8}{'теор%':>8}{'деб.%':>8}")
        for name in sorted(feats):
            s = feats[name]["specs"][spec]
            if s["status"] != "OK":
                print(f"  {name:<20}{'SKIP':>8}  {s['reason']}")
                continue
            deb = s.get("alert_rate_debounced")
            print(f"  {name:<20}{s['n_scored']:>8}{s['n_alert']:>9}"
                  f"{s['n_low']:>6}{s['n_high']:>6}"
                  f"{100.0 * s['alert_rate']:>7.1f}%"
                  f"{100.0 * s['expected_rate_iid']:>7.1f}%"
                  f"{(100.0 * deb if deb is not None else 0.0):>7.1f}%")

    print("\n" + "=" * 78)
    print(f"ЕФЕКТ ДЕБАУНСУ (мовчання після алерту)")
    print("=" * 78)
    print(f"  {'специфікація':<22}{'алертів':>10}{'після деб.':>12}"
          f"{'частка до':>12}{'частка після':>14}")
    for spec in ("levels_expanding", "levels_window", "diffs_expanding",
                 "diffs_local_scale", "rank_levels", "rank_diffs"):
        tot_a = tot_d = tot_s = 0
        for name in sorted(feats):
            s = feats[name]["specs"][spec]
            if s.get("status") != "OK":
                continue
            tot_a += s["n_alert"]
            tot_d += s.get("n_alert_debounced", 0)
            tot_s += s["n_scored"]
        if tot_s == 0:
            print(f"  {spec:<22}{'SKIP':>10}")
            continue
        print(f"  {spec:<22}{tot_a:>10}{tot_d:>12}"
              f"{100.0 * tot_a / tot_s:>11.1f}%"
              f"{100.0 * tot_d / tot_s:>13.1f}%")

    print("\n" + "=" * 78)
    print("АЛЕРТИ ПО ГОДИНІ UTC (специфікація levels_expanding)")
    print("=" * 78)
    tot_hour, tot_dow = {}, {}
    for name in sorted(feats):
        for h, c in feats[name]["alerts_levels"]["by_hour_utc"].items():
            tot_hour[h] = tot_hour.get(h, 0) + c
        for d, c in feats[name]["alerts_levels"]["by_weekday"].items():
            tot_dow[d] = tot_dow.get(d, 0) + c
    if tot_hour:
        for h in sorted(tot_hour):
            print(f"  {h}:xx UTC -> {tot_hour[h]} алертів")
    else:
        print("  (алертів немає)")
    dow_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
    print("\nАЛЕРТИ ПО ДНЮ ТИЖНЯ")
    if tot_dow:
        for d in sorted(tot_dow):
            print(f"  {dow_names[int(d)]} -> {tot_dow[d]} алертів")
    else:
        print("  (алертів немає)")

    slots = report.get("slot_test") or {}
    if slots:
        print("\n" + "=" * 78)
        print("СЛОТОВИЙ ТЕСТ: чи диктує добовий знаменник форму приростів")
        print("=" * 78)
        print("  H0: розподіл |приросту| однаковий по UTC-слотах.")
        print(f"  Слот одразу після оновлення rv (00:40 UTC) -> "
              f"{SLOT_AFTER_RV:02d}:xx.")
        for name in sorted(slots):
            r = slots[name]
            c = r["counters"]
            _lag = r.get("lag", 1)
            _lab = ("крок 4 год" if _lag == 1
                    else f"крок {_lag * 4} год — той самий слот "
                         f"{_lag // 6} доби тому" if _lag % 6 == 0
                    else f"крок {_lag * 4} год")
            print(f"\n  {name}   [lag={_lag}, {_lab}]")
            print(f"    пар: {c['pairs_seen']}, узято: {c['kept']}, "
                  f"стара сітка: {c['legacy_grid']}, "
                  f"поза сіткою: {c['off_grid']}, "
                  f"розрив: {c['bad_gap']}")
            if r["status"] != "OK":
                print(f"    [SKIP] {r['reason']}")
                continue
            print(f"    {'слот':<8}{'n':>5}{'med|d|':>11}{'med d':>11}")
            for h in sorted(r["per_slot"], key=int):
                s = r["per_slot"][h]
                mark = "  <- після rv" if int(h) == SLOT_AFTER_RV else ""
                if r.get("peak_slot") is not None \
                        and int(h) == r["peak_slot"]:
                    mark += "  <== ПІК"
                print(f"    {int(h):02d}:xx  {s['n']:>5}"
                      f"{_f(s['median_abs_diff'], 5):>11}"
                      f"{_f(s['median_signed_diff'], 5):>11}{mark}")
            rt = r["ratio_after_rv_vs_rest"]
            print(f"    розкид медіан: {_f(r['stat_spread'], 5)}, "
                  f"p = {r['p_value']:.4f} "
                  f"({r['n_perm']} перестановок, seed={r['seed']})")
            print(f"    med|d|(після rv 04:xx) / med|d|(решта) = "
                  f"{_f(rt, 3)}")
            if r.get("peak_slot") is not None:
                print(f"    ПІК у слоті {r['peak_slot']:02d}:xx, "
                      f"med|d|(пік) / med|d|(решта) = "
                      f"{_f(r.get('ratio_peak_vs_rest'), 3)}")
            if r["p_value"] < 0.05:
                if r.get("peak_slot") == SLOT_AFTER_RV:
                    print("    [FLAG] H0 відхилено, пік у слоті після "
                          "оновлення rv — знаменник диктує форму "
                          "приростів.")
                else:
                    print(f"    [FLAG] H0 відхилено, але пік НЕ у слоті "
                          f"після rv, а о {r['peak_slot']:02d}:xx — "
                          f"причина не в оновленні знаменника.")
            else:
                print("    [OK] H0 не відхилено: слотового ефекту не "
                      "виявлено на наявних даних.")


# ----------------------------------------------------------------------------
# Самотести
# ----------------------------------------------------------------------------

def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


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

    # _median / _mad / _quantile
    tc.check("median_odd", _median([3.0, 1.0, 2.0]) == 2.0, "")
    tc.check("median_even", _median([1.0, 2.0, 3.0, 4.0]) == 2.5, "")
    tc.check("mad_basic", abs(_mad([1.0, 2.0, 3.0, 4.0]) - 1.0) < 1e-12,
             str(_mad([1.0, 2.0, 3.0, 4.0])))
    tc.check("quantile_ends", _quantile([1.0, 2.0, 3.0], 0.0) == 1.0
             and _quantile([1.0, 2.0, 3.0], 1.0) == 3.0, "")
    tc.check("quantile_mid", abs(_quantile([1.0, 2.0, 3.0], 0.5) - 2.0)
             < 1e-12, "")

    # autocorr: білий шум vs жорсткий тренд
    noise = [math.sin(i * 12.9898) * 1000.0 % 1.0 for i in range(200)]
    r_noise, _ = autocorr(noise, 1)
    tc.check("autocorr_noise_small", r_noise is not None
             and abs(r_noise) < 0.3, f"r={r_noise}")
    trend = [float(i) for i in range(100)]
    r_trend, _ = autocorr(trend, 1)
    tc.check("autocorr_trend_high", r_trend is not None and r_trend > 0.95,
             f"r={r_trend}")
    r_none, reason = autocorr([1.0, 2.0], 1)
    tc.check("autocorr_short_refuses", r_none is None and "пар" in reason,
             reason)
    r_flat, reason2 = autocorr([5.0] * 20, 1)
    tc.check("autocorr_flat_refuses", r_flat is None
             and "дисперсія" in reason2, reason2)

    # effective_n
    tc.check("neff_iid", abs(effective_n(100, 0.0) - 100.0) < 1e-9, "")
    tc.check("neff_persistent", effective_n(100, 0.9) < 10.0,
             str(effective_n(100, 0.9)))
    tc.check("neff_degenerate_none", effective_n(100, 1.0) is None, "")

    # walkforward_z: min_history поважається
    vals = [0.30 + 0.001 * math.sin(i) for i in range(30)]
    zp, refused = walkforward_z(vals, min_history=12, window=None)
    tc.check("wf_respects_min_history", len(zp) == 30 - 12 and refused == 0,
             f"len={len(zp)} refused={refused}")
    zp_w, _ = walkforward_z(vals, min_history=12, window=15)
    tc.check("wf_window_same_count", len(zp_w) == 30 - 12, str(len(zp_w)))

    # walkforward_z: константний ряд -> MAD=0 -> явна відмова, не тиша
    zp0, refused0 = walkforward_z([1.0] * 30, min_history=12, window=None)
    tc.check("wf_mad0_counted", len(zp0) == 0 and refused0 == 30 - 12,
             f"refused={refused0}")

    # walkforward_z: зсув режиму дає багато алертів на рівнях,
    # але мало на приростах — це і є діагностичне ядро модуля
    shifted = [0.30] * 60 + [0.40] * 60
    shifted = [v + 0.0005 * math.sin(i * 2.3)
               for i, v in enumerate(shifted)]
    zl, _ = walkforward_z(shifted, 12, None)
    dl = [shifted[i] - shifted[i - 1] for i in range(1, len(shifted))]
    zd, _ = walkforward_z(dl, 12, None)
    sl = summarize_z(zl, 3.0)
    sd = summarize_z(zd, 3.0)
    tc.check("regime_shift_levels_alert_heavy", sl["alert_rate"] > 0.3,
             f"rate={sl['alert_rate']:.3f}")
    tc.check("regime_shift_diffs_alert_light",
             sd["alert_rate"] < sl["alert_rate"],
             f"diffs={sd['alert_rate']:.3f} levels={sl['alert_rate']:.3f}")

    # summarize_z порожній -> SKIP, не нуль
    tc.check("summarize_empty_skip",
             summarize_z([], 3.0)["status"] == "SKIP", "")

    # analyze_feature end-to-end на синтетиці
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    from datetime import timedelta
    pts = [(base + timedelta(hours=4 * i), 0.30 + 0.001 * math.sin(i))
           for i in range(40)]
    rep = analyze_feature("syn", pts, 12, 3.0, 20)
    # Рангова детекція: частка задана K і НЕ залежить від розподілу.
    # Ключовий тест — важкі хвости не ламають частку.
    def _lcg(n_, seed=12345):
        x = seed
        out = []
        for _ in range(n_):
            x = (1103515245 * x + 12345) % 2147483648
            out.append(x / 2147483648.0)
        return out

    u = _lcg(400)
    gauss_like = [sum(u[i:i + 4]) - 2.0 for i in range(0, 400, 4)]
    # важкі хвости: кубічне перетворення того самого шуму
    heavy = [v ** 3 * 50.0 for v in gauss_like]
    h_g, ns_g = walkforward_rank(gauss_like, 12, 1, None)
    h_h, ns_h = walkforward_rank(heavy, 12, 1, None)
    r_g = len(h_g) / ns_g
    r_h = len(h_h) / ns_h
    tc.check("rank_rate_invariant_to_tails", abs(r_g - r_h) < 1e-9,
             f"gauss={r_g:.4f} heavy={r_h:.4f} (мають збігатися точно)")
    tc.check("rank_rate_is_low", r_g < 0.15, f"rate={r_g:.4f}")
    # монотонний ряд — кожна точка новий максимум, частка 100%: рангова
    # схема НЕ рятує від тренду, і тест це фіксує явно
    h_m, ns_m = walkforward_rank([float(i) for i in range(60)], 12, 1, None)
    tc.check("rank_trend_alerts_all", len(h_m) == ns_m,
             f"{len(h_m)}/{ns_m}")
    tc.check("rank_k_zero_refuses",
             _raises(lambda: walkforward_rank([1.0] * 20, 12, 0, None),
                     ValueError), "")

    # cooldown
    tc.check("cooldown_collapses_run",
             apply_cooldown([10, 11, 12, 13, 30], 6) == [10, 30],
             str(apply_cooldown([10, 11, 12, 13, 30], 6)))
    tc.check("cooldown_zero_noop",
             apply_cooldown([1, 2, 3], 0) == [1, 2, 3], "")
    tc.check("cooldown_boundary_exclusive",
             apply_cooldown([0, 6, 7], 6) == [0, 7],
             str(apply_cooldown([0, 6, 7], 6)))

    tc.check("analyze_has_specs",
             set(rep["specs"]) == {"levels_expanding", "levels_window",
                                   "diffs_expanding", "diffs_local_scale",
                                   "rank_levels", "rank_diffs"},
             str(list(rep["specs"])))
    tc.check("analyze_has_thresholds",
             "thresh_for_1pct" in
             rep["specs"]["diffs_local_scale"]["empirical_thresholds"],
             str(rep["specs"]["diffs_local_scale"]["empirical_thresholds"]))

    # Кластеризація волатильності: локальна шкала має бити глобальну.
    # Спокійний період, потім бурхливий; жодних справжніх аномалій немає.
    calm = [0.0008 * math.sin(i * 1.13) for i in range(120)]
    wild = [0.0080 * math.sin(i * 1.13) for i in range(120)]
    clustered = []
    acc = 0.30
    for d in calm + wild:
        acc += d
        clustered.append(acc)
    cl_diffs = [clustered[i] - clustered[i - 1]
                for i in range(1, len(clustered))]
    zg, _ = walkforward_z(cl_diffs, 12, None)
    zl2, _ = walkforward_z(cl_diffs, 12, 40)
    sg = summarize_z(zg, 3.0)
    sl2 = summarize_z(zl2, 3.0)
    tc.check("local_scale_beats_global_under_clustering",
             sl2["alert_rate"] < sg["alert_rate"],
             f"local={sl2['alert_rate']:.3f} global={sg['alert_rate']:.3f}")
    tc.check("local_scale_tails_tamer",
             sl2["abs_z_q99"] < sg["abs_z_q99"],
             f"local_q99={sl2['abs_z_q99']:.2f} "
             f"global_q99={sg['abs_z_q99']:.2f}")
    tc.check("analyze_n", rep["n"] == 40, str(rep["n"]))
    tc.check("analyze_breakdown_hours",
             isinstance(rep["alerts_levels"]["by_hour_utc"], dict), "")

    # --- слотовий тест ---
    from datetime import timedelta as _td

    def _mk_series(n_snap, step_fn, start_h=0, legacy=0):
        """Ряд знімків на канонічній сітці 0/4/8/12/16/20 UTC.
        legacy>0 -> перші legacy точок посунуті у стару сітку (-3 год)."""
        base = datetime(2026, 7, 25, start_h, 10, tzinfo=timezone.utc)
        pts = []
        for i in range(n_snap):
            st = base + _td(hours=4 * i)
            if i < legacy:
                st = st - _td(hours=3)
            pts.append((st, step_fn(i, st)))
        return pts

    # (а) ГЛАДКИЙ ряд: слотового ефекту немає -> H0 не відхиляється
    smooth = _mk_series(120, lambda i, st: 1.0 + 0.001 * ((i * 37) % 11))
    b_s, c_s = slot_diffs(smooth)
    tc.check("slot_smooth_all_kept", c_s["kept"] == c_s["pairs_seen"]
             and c_s["legacy_grid"] == 0,
             f"kept={c_s['kept']}/{c_s['pairs_seen']}")
    r_s, why_s = slot_permutation_test(b_s, 400)
    tc.check("slot_smooth_no_effect",
             r_s is not None and r_s["p_value"] > 0.05,
             f"p={None if r_s is None else r_s['p_value']} ({why_s})")

    # (б) СТУПІНЧАСТИЙ знаменник: стрибок рівня рівно на межі оновлення
    # rv (00:40 UTC), тобто у слоті 04:10 -> H0 падає, ratio > 1.
    # Це сигнатура, заради якої тест і написаний.
    _rv_epoch = datetime(2026, 7, 25, 0, 40, tzinfo=timezone.utc)

    def _stepped(i, st):
        n_upd = int((st - _rv_epoch).total_seconds() // 86400)
        return 1.0 + 0.05 * n_upd + 0.0002 * ((i * 29) % 7)
    step = _mk_series(120, _stepped)
    b_p, _c_p = slot_diffs(step)
    r_p, why_p = slot_permutation_test(b_p, 400)
    tc.check("slot_stepped_detected",
             r_p is not None and r_p["p_value"] < 0.05,
             f"p={None if r_p is None else r_p['p_value']} ({why_p})")
    tc.check("slot_stepped_ratio_gt_1",
             r_p is not None and r_p["ratio_after_rv_vs_rest"] is not None
             and r_p["ratio_after_rv_vs_rest"] > 1.0,
             str(None if r_p is None else r_p["ratio_after_rv_vs_rest"]))

    # (в) стара сітка відкидається явно, з лічильником
    mixed = _mk_series(60, lambda i, st: 1.0 + 0.001 * i, legacy=10)
    _b_m, c_m = slot_diffs(mixed)
    tc.check("slot_legacy_grid_dropped",
             c_m["legacy_grid"] >= 10 and c_m["kept"] < c_m["pairs_seen"],
             f"legacy={c_m['legacy_grid']} kept={c_m['kept']}"
             f"/{c_m['pairs_seen']}")

    # (г) розрив у сітці (пропущений знімок) відкидається
    gapped = [p for i, p in enumerate(_mk_series(40, lambda i, st: float(i)))
              if i not in (10, 11, 12)]
    _b_g, c_g = slot_diffs(gapped)
    tc.check("slot_bad_gap_dropped", c_g["bad_gap"] >= 1,
             str(c_g["bad_gap"]))

    # (д) замало точок -> явний SKIP, не мовчазний None
    r_few, why_few = slot_permutation_test({0: [0.1, 0.2]}, 100)
    tc.check("slot_thin_refuses",
             r_few is None and "придатних слотів" in why_few, why_few)

    # (е) n_perm=0 -> явна відмова
    r_zero, why_zero = slot_permutation_test(b_s, 0)
    tc.check("slot_perm_zero_refuses",
             r_zero is None and "n_perm" in why_zero, why_zero)

    # (ж) детермінованість: два прогони з тим самим seed збігаються
    r_a, _ = slot_permutation_test(b_s, 200)
    r_b, _ = slot_permutation_test(b_s, 200)
    tc.check("slot_deterministic",
             r_a["p_value"] == r_b["p_value"], f"{r_a['p_value']}")

    # (з) slot_analysis бере лише *.vrp30
    sa = slot_analysis({"BTC.vrp30": smooth, "BTC.atm30": smooth}, 100,
                       name_filter=lambda nm: nm.endswith(".vrp30"),
                       lags=(1,))
    tc.check("slot_analysis_filters_vrp",
             sorted(sa) == ["BTC.vrp30"], str(sorted(sa)))

    # --- контракт vrp_from_rv: арність перевіряється, а не ловиться ---
    # 22 сер чисельник переїхав на atm30 поточного знімка. Якщо
    # anomaly_detect змінить контракт знову, build_series мовчки
    # втратить vrp-фічі — тест має впасти РАНІШЕ за живий прогін.
    import inspect as _inspect
    _sig = _inspect.signature(vrp_from_rv)
    tc.check("vrp_from_rv_arity_is_4",
             len(_sig.parameters) == 4
             and "atm30_by_cur" in _sig.parameters,
             str(_sig))

    # run_live використовує vrp_source; якщо параметра немає в
    # сигнатурі — це NameError на живому прогоні, після хвилин роботи.
    # Ловимо офлайн.
    _sig_rl = _inspect.signature(run_live)
    tc.check("run_live_takes_vrp_source",
             "vrp_source" in _sig_rl.parameters, str(_sig_rl))

    # піковий слот має знаходитись там, де він справді є
    tc.check("slot_peak_is_after_rv_when_stepped",
             r_p is not None and r_p.get("peak_slot") == SLOT_AFTER_RV
             and r_p.get("ratio_peak_vs_rest") > 1.0,
             f"peak={None if r_p is None else r_p.get('peak_slot')} "
             f"ratio={None if r_p is None else r_p.get('ratio_peak_vs_rest')}")

    # фільтр суфіксів: за замовчуванням беруться і vrp30, і atm30
    _sa2 = slot_analysis({"BTC.vrp30": smooth, "BTC.atm30": smooth,
                          "BTC.psi30": smooth}, 100, lags=(1,))
    tc.check("slot_analysis_default_suffixes",
             sorted(_sa2) == ["BTC.atm30", "BTC.vrp30"], str(sorted(_sa2)))

    # --- лаг 6: той самий слот доби на обох кінцях пари ---
    # Ряд із внутрішньодобовою сезонністю: слот 16 систематично
    # зсунутий. На лазі 1 це дає слотовий ефект; на лазі 6 обидва кінці
    # пари в тому самому слоті, тож ефект зникає за побудовою.
    seas = []
    _b = datetime(2026, 7, 25, 0, 10, tzinfo=timezone.utc)
    for i in range(150):
        st = _b + _td(hours=4 * i)
        lvl = 1.0 + 0.0003 * i + (0.02 if st.hour == 16 else 0.0)
        seas.append((st, lvl))
    b_l1, _c1 = slot_diffs(seas, lag=1)
    b_l6, c6 = slot_diffs(seas, lag=6)
    p_l1, _ = slot_permutation_test(b_l1, 400)
    p_l6, why6 = slot_permutation_test(b_l6, 400)
    tc.check("lag1_sees_intraday_seasonality",
             p_l1 is not None and p_l1["p_value"] < 0.05,
             f"p={None if p_l1 is None else p_l1['p_value']:.4f}")
    tc.check("lag6_removes_intraday_seasonality",
             p_l6 is not None and p_l6["p_value"] > 0.05,
             f"p={None if p_l6 is None else p_l6['p_value']:.4f} ({why6})")
    tc.check("lag6_gap_window_shifted",
             c6["gap_min_h"] == 23.5 and c6["gap_max_h"] == 24.5,
             f"[{c6['gap_min_h']}, {c6['gap_max_h']}]")
    tc.check("lag_zero_refuses",
             _raises(lambda: slot_diffs(seas, lag=0), ValueError), "")
    _multi = slot_analysis({"BTC.vrp30": seas}, 100, lags=(1, 6))
    tc.check("slot_analysis_multi_lag_keys",
             sorted(_multi) == ["BTC.vrp30@lag1", "BTC.vrp30@lag6"],
             str(sorted(_multi)))

    # --- Надмірність дебаунсу -------------------------------------------
    from datetime import timedelta as _td
    _t0 = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

    def _mk(vals, start=_t0, step_h=4):
        return [(start + _td(hours=step_h * i), v)
                for i, v in enumerate(vals)]

    # Сітка та лічильники відкидання
    _pts_leg = ([(datetime(2026, 7, 30, 21, 0, tzinfo=timezone.utc), 1.0),
                 (datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc), 2.0)]
                + _mk([1.0, 2.0, 3.0]))
    _dp, _c = lag_diff_points(_pts_leg, 1)
    tc.check("lag_diff_drops_legacy_grid", _c["legacy_grid"] >= 2,
             str(_c))
    _pts_off = [(datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc), 1.0),
                (datetime(2026, 8, 1, 18, 5, tzinfo=timezone.utc), 2.0),
                (datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc), 3.0)]
    _dp, _c = lag_diff_points(_pts_off, 1)
    tc.check("lag_diff_drops_off_grid_1805", _c["off_grid"] == 2,
             str(_c))
    tc.check("lag_diff_zero_refuses",
             _raises(lambda: lag_diff_points(_mk([1.0, 2.0]), 0),
                     ValueError), "")
    _dp, _c = lag_diff_points(_mk([float(i) for i in range(13)]), 1)
    tc.check("lag_diff_grid_idx_is_time_based",
             [g for g, _d, _s in _dp] == list(range(1, 13)),
             str([g for g, _d, _s in _dp][:4]))

    # Дірка в зборі: індекс дебаунсу має відповідати часу, не позиції
    _hole = ([(_t0 + _td(hours=4 * i), float(i)) for i in range(3)]
             + [(_t0 + _td(hours=4 * i), float(i)) for i in range(5, 9)])
    _dp, _c = lag_diff_points(_hole, 1)
    tc.check("lag_diff_hole_counted_as_bad_gap", _c["bad_gap"] == 1,
             str(_c))
    tc.check("lag_diff_grid_idx_survives_hole",
             [g for g, _d, _s in _dp] == [1, 2, 6, 7, 8],
             str([g for g, _d, _s in _dp]))

    # Добові точки: якір, off-anchor, розрив
    _day = _mk([float(i) for i in range(30)])
    _dpd, _cd = daily_diff_points(_day, 4)
    tc.check("daily_points_respect_anchor",
             _cd["anchor_kept"] == 5 and _cd["off_anchor"] == 25,
             str(_cd))
    tc.check("daily_points_are_nonoverlapping",
             _cd["kept"] == 4 and all(
                 _dpd[i + 1][0] - _dpd[i][0] == 1
                 for i in range(len(_dpd) - 1)), str(_cd))
    _day_hole = [p for p in _day if p[0].day != 3]
    _dpd2, _cd2 = daily_diff_points(_day_hole, 4)
    tc.check("daily_gap_dropped_explicitly",
             _cd2["bad_gap"] == 1 and _cd2["anchor_kept"] == 4,
             str(_cd2))
    tc.check("daily_anchor_off_grid_refuses",
             _raises(lambda: daily_diff_points(_day, 18), ValueError), "")

    # Ручний підрахунок надмірності
    _man = [(i, 0.0, None) for i in range(6)]
    _man.append((6, 100.0, None))
    _man.append((7, 101.0, None))
    _man.append((8, 102.0, None))
    _r = rank_redundancy(_man, min_history=5, k=1, cooldown=6)
    tc.check("redundancy_metric_matches_manual_count",
             _r["n_scored"] == 4 and _r["n_raw"] == 4
             and _r["n_kept"] == 1 and abs(_r["redundancy"] - 4.0) < 1e-12,
             str(_r))
    _r0 = rank_redundancy(_man, min_history=5, k=1, cooldown=0)
    tc.check("redundancy_cd0_is_unity",
             abs(_r0["redundancy"] - 1.0) < 1e-12 and _r0["cd_is_zero"],
             str(_r0))
    tc.check("redundancy_negative_cooldown_refuses",
             _raises(lambda: rank_redundancy(_man, 5, 1, -1), ValueError),
             "")
    tc.check("redundancy_k_zero_refuses",
             _raises(lambda: rank_redundancy(_man, 5, 0, 6), ValueError),
             "")

    # ВИРОДЖЕНІСТЬ TIES (фіксуємо явно, щоб не сплутати з результатом):
    # на КОНСТАНТНОМУ ряді всі прирости — точні нулі, і рангова умова
    # n_below < K виконується на КОЖНІЙ точці. Частка сирих алертів 1.0.
    # Це не хиба вимірювача, а та сама патологія, що ламала стару vrp30
    # (zeros_frac ~0.83). Синтетика для контролю перекриття мусить бути
    # НЕвиродженою — інакше тест «проходить» на артефакті.
    _const = _mk([1.0] * 60)
    _dpc, _ = lag_diff_points(_const, 1)
    _rc = rank_redundancy(_dpc, min_history=12, k=1, cooldown=0)
    tc.check("rank_on_constant_series_alerts_everything",
             abs(_rc["rate_raw"] - 1.0) < 1e-12, str(_rc["rate_raw"]))

    # НЕГАТИВНИЙ КОНТРОЛЬ: перекриття має роздувати надмірність.
    # Шум + одна велика сходинка. На лазі 1 сходинка дає ОДИН
    # екстремальний приріст; на лазі 6 вікно проїжджає по ній і та сама
    # подія потрапляє у ШІСТЬ послідовних приростів.
    _rng_t = _lcg_stream(4242)
    _noise = [next(_rng_t) for _ in range(120)]
    _ovl = _mk([_noise[i] + (50.0 if i >= 60 else 0.0)
                for i in range(120)])
    _dp1, _ = lag_diff_points(_ovl, 1)
    _dp6, _ = lag_diff_points(_ovl, 6)
    #
    # ВИМІРЯНО НА ЦІЙ САМІЙ СИНТЕТИЦІ (не припущення): інфляція від
    # перекриття МОНОТОННА по K і при K=1 СЛАБКА.
    #   K=1: lag1 1.00x -> lag6 1.33x
    #   K=2: lag1 1.83x -> lag6 2.50x
    #   K=3: lag1 2.17x -> lag6 3.00x
    # Причина: при K=1 алерт вимагає РЕКОРДУ. Одна подія, розмазана по
    # шести перекривних приростах, ставить рекорд лише ПЕРШИМ із них —
    # решта п'ять падають усередину історії. Тобто рангова схема на
    # екстремумах сама частково гасить перекриття, і апріорний страх
    # «потрапивши раз, потрапить і наступні п'ять» справджується тим
    # більше, чим більше K.
    _r1 = rank_redundancy(_dp1, min_history=12, k=3, cooldown=6)
    _r6 = rank_redundancy(_dp6, min_history=12, k=3, cooldown=6)
    tc.check("redundancy_detects_overlap",
             _r6["redundancy"] is not None and _r1["redundancy"] is not None
             and _r6["redundancy"] > _r1["redundancy"] * 1.2,
             f"K=3 lag1={_r1['n_raw']}/{_r1['n_kept']}="
             f"{_f(_r1['redundancy'], 2)} "
             f"lag6={_r6['n_raw']}/{_r6['n_kept']}="
             f"{_f(_r6['redundancy'], 2)}")
    _k1a = rank_redundancy(_dp1, 12, 1, 6)
    _k1b = rank_redundancy(_dp6, 12, 1, 6)
    tc.check("overlap_inflation_monotone_in_k",
             (_r6["redundancy"] - _r1["redundancy"])
             > (_k1b["redundancy"] - _k1a["redundancy"]),
             f"dK1={_f(_k1b['redundancy'] - _k1a['redundancy'], 2)} "
             f"dK3={_f(_r6['redundancy'] - _r1['redundancy'], 2)}")
    tc.check("overlap_control_has_power_at_k3",
             _r6["n_kept"] < _r1["n_kept"] and _r6["n_raw"] > _r1["n_raw"],
             f"raw {_r1['n_raw']}->{_r6['n_raw']}, "
             f"kept {_r1['n_kept']}->{_r6['n_kept']}")

    # Спільна підмножина: однаковий n_scored у обох лагів
    _lin = _mk([float(i) * 0.1 + (0.7 if i % 11 == 0 else 0.0)
                for i in range(80)])
    _d1, _ = lag_diff_points(_lin, 1)
    _d6, _ = lag_diff_points(_lin, 6)
    _cg = max(_d1[12][0], _d6[12][0])
    _a = rank_redundancy(_d1, 12, 1, 6, min_gi=_cg)
    _b = rank_redundancy(_d6, 12, 1, 6, min_gi=_cg)
    tc.check("redundancy_common_subset_equal_n",
             _a["n_scored"] == _b["n_scored"],
             f"{_a['n_scored']} vs {_b['n_scored']}")

    # Інтеграція: варіанти, розкид по якорях, вердикт
    _ser = {"BTC.vrp30": _mk(
        [0.0] * 60 + [3.0] * 60), "BTC.atm7": _mk([1.0] * 20)}
    _rd = redundancy_analysis(_ser, 12, 1, 6, lags=(1, 6),
                              name_filter=lambda n: n.endswith(".vrp30"))
    _v = _rd["BTC.vrp30"]
    tc.check("redundancy_analysis_only_filtered_features",
             list(_rd) == ["BTC.vrp30"], str(list(_rd)))
    tc.check("redundancy_analysis_has_all_variants",
             all(k_ in _v for k_ in ("lag1", "lag6", "lag1_common",
                                     "lag6_common", "daily@04_cd1",
                                     "daily_spread_cd0",
                                     "daily_spread_cd1")),
             str(sorted(_v)))
    tc.check("redundancy_daily_all_six_anchors",
             all(f"daily@{h:02d}_cd1" in _v
                 for h in CANONICAL_SLOT_HOURS), str(sorted(_v)))
    tc.check("redundancy_spread_cd0_flagged_not_a_win",
             _v["daily_spread_cd0"]["cd_is_zero"] is True
             and abs(_v["daily_spread_cd0"]["max_redundancy"]
                     - 1.0) < 1e-12,
             str(_v["daily_spread_cd0"]))
    _vd, _txt = redundancy_verdict(_v)
    tc.check("redundancy_verdict_rejects_on_threshold",
             _vd["verdict"] in ("REJECT", "GREY", "ACCEPT", "SKIP")
             and (_vd.get("redundancy_test") is None
                  or _vd["reject_ratio"] == REDUND_REJECT_RATIO),
             f"{_vd['verdict']}: {_txt}")
    _lowr = rank_redundancy([(i, float(i % 7), None) for i in range(30)],
                            12, 1, 6)
    tc.check("redundancy_low_n_flagged",
             _lowr["low_n"] == (_lowr["n_raw"]
                                < REDUND_MIN_RAW_FOR_RATIO),
             f"n_raw={_lowr['n_raw']}, low_n={_lowr['low_n']}")
    _fake_ln = {"lag1_common": {"status": "OK", "redundancy": 1.0,
                                "n_raw": 2, "low_n": True},
                "lag6_common": {"status": "OK", "redundancy": 1.0,
                                "n_raw": 2, "low_n": True}}
    tc.check("redundancy_verdict_lown_blocks_verdict",
             redundancy_verdict(_fake_ln)[0]["verdict"] == "LOW-N", "")
    _fake = {"lag1_common": {"status": "OK", "redundancy": 1.7},
             "lag6_common": {"status": "OK", "redundancy": 5.0}}
    tc.check("redundancy_verdict_reject_above_4x",
             redundancy_verdict(_fake)[0]["verdict"] == "REJECT", "")
    _fake2 = {"lag1_common": {"status": "OK", "redundancy": 1.7},
              "lag6_common": {"status": "OK", "redundancy": 1.8}}
    tc.check("redundancy_verdict_accept_near_ref",
             redundancy_verdict(_fake2)[0]["verdict"] == "ACCEPT", "")
    _fake3 = {"lag1_common": {"status": "OK", "redundancy": 1.7},
              "lag6_common": {"status": "OK", "redundancy": 2.8}}
    tc.check("redundancy_verdict_grey_zone_named",
             redundancy_verdict(_fake3)[0]["verdict"] == "GREY", "")
    tc.check("redundancy_bad_lags_env_refuses",
             _raises(lambda: redundancy_analysis(_ser, 12, 1, 6,
                                                 lags=(0,)), ValueError)
             or _raises(lambda: redundancy_analysis(_ser, 12, 1, 6,
                                                    lags=(0,)),
                        RuntimeError), "")

    # ---- Тест на день тижня (rank_diffs), 23 сер --------------------------
    # scored_indices ДУБЛЮЄ правило оцінюваності з walkforward_rank —
    # прибиваємо їх одне до одного, інакше експозиція тихо розійдеться
    # з чисельником і всі частки будуть неправильні.
    for _n, _mh, _w in ((50, 12, None), (50, 12, 20), (13, 12, None),
                        (5, 12, None), (30, 3, 10)):
        _vals = [float(i % 7) + 0.01 * i for i in range(_n)]
        _h, _ns = walkforward_rank(_vals, _mh, 1, _w)
        tc.check(f"scored_indices_matches_walkforward_n{_n}_w{_w}",
                 len(scored_indices(_n, _mh, _w)) == _ns,
                 f"{len(scored_indices(_n, _mh, _w))} vs {_ns}")

    _st = [datetime(2026, 8, 3, 0, 10, tzinfo=timezone.utc)
           + timedelta(hours=4 * i) for i in range(42)]
    _exp = weekday_exposure(list(range(42)), _st)
    tc.check("weekday_exposure_counts", sum(_exp.values()) == 42
             and all(v == 6 for v in _exp.values()), str(_exp))
    tc.check("weekday_exposure_ignores_out_of_range",
             weekday_exposure([0, 999], _st) == {0: 1}, "")

    # НЕГАТИВНИЙ КОНТРОЛЬ: рівна частка по всіх днях -> H0 не відхиляється.
    _ev_exp = {d: 100 for d in range(7)}
    _ev_al = {d: 10 for d in range(7)}
    _t, _r = weekday_permutation_test(_ev_exp, _ev_al, 400)
    tc.check("weekday_flat_no_effect", _t is not None and _t["p"] > 0.05,
             str(_t and _t["p"]))
    tc.check("weekday_flat_effect_zero",
             _t is not None and _t["effect_rate"] == 0.0, "")

    # ПОЗИТИВНИЙ КОНТРОЛЬ: без нього попередній тест міг би проходити
    # просто через брак потужності.
    _sk_al = dict(_ev_al)
    _sk_al[4] = 60
    _t2, _r = weekday_permutation_test(_ev_exp, _sk_al, 400)
    tc.check("weekday_test_has_power", _t2 is not None and _t2["p"] < 0.01,
             str(_t2 and _t2["p"]))
    tc.check("weekday_test_finds_peak",
             _t2 is not None and _t2["peak_day"] == 4, "")
    tc.check("weekday_verdict_effect",
             weekday_verdict(_t2) == "WEEKDAY-EFFECT", weekday_verdict(_t2))
    tc.check("weekday_verdict_no_effect",
             weekday_verdict(_t) == "NO-EFFECT", weekday_verdict(_t))

    # ЕКСПОЗИЦІЯ: однакова ЧАСТКА при різній кількості знімків НЕ є ефектом.
    # Саме цього не вміла стара розбивка «Пт 91 / Сб 81» на сирих сумах.
    _un_exp = {0: 40, 1: 40, 2: 40, 3: 40, 4: 120, 5: 120, 6: 120}
    _un_al = {d: int(0.1 * n) for d, n in _un_exp.items()}
    _t3, _r = weekday_permutation_test(_un_exp, _un_al, 400)
    tc.check("weekday_unequal_exposure_no_false_effect",
             _t3 is not None and _t3["p"] > 0.05
             and _t3["effect_rate"] < 1e-9,
             str(_t3 and (_t3["p"], _t3["effect_rate"])))

    # LOW-N: мало сирих алертів -> вердикту немає, хай яке мале p.
    _ln_exp = {d: 100 for d in range(7)}
    _ln_al = {d: 0 for d in range(7)}
    _ln_al[4] = 6
    _t4, _r = weekday_permutation_test(_ln_exp, _ln_al, 400)
    tc.check("weekday_low_n_verdict",
             weekday_verdict(_t4) == "LOW-N",
             f"{weekday_verdict(_t4)} p={_t4 and _t4['p']}")

    # Дні з малою експозицією не беруть участі; нуль алертів -> явна відмова.
    _t5, _r5 = weekday_permutation_test({0: 100, 1: 2}, {0: 5, 1: 2}, 100)
    tc.check("weekday_thin_day_refused", _t5 is None and "днів" in _r5,
             str(_r5))
    _t6, _r6 = weekday_permutation_test(_ev_exp, {d: 0 for d in range(7)},
                                        100)
    tc.check("weekday_zero_alerts_refused", _t6 is None, str(_r6))
    _t7, _r7 = weekday_permutation_test(_ev_exp, _ev_al, 0)
    tc.check("weekday_zero_perm_refused", _t7 is None, str(_r7))
    tc.check("weekday_verdict_none", weekday_verdict(None) == "NO-TEST", "")
    tc.check("weekday_p_never_zero", _t2["p"] > 0.0, "")

    # analyze_feature реально заповнює секцію, і мітки беруть diff_stamps.
    _pts = [(datetime(2026, 8, 3, 0, 10, tzinfo=timezone.utc)
             + timedelta(hours=4 * i), float((i * 37) % 11)) for i in range(60)]
    _rf = analyze_feature("BTC.test", _pts, 12, 3.0, 30, 1, 6)
    _wd = _rf["weekday_rank_diffs"]
    tc.check("analyze_feature_has_weekday", isinstance(_wd, dict)
             and "scored_by_weekday" in _wd, "")
    tc.check("weekday_scored_sum_matches",
             sum(_wd["scored_by_weekday"].values()) == _wd["n_scored"],
             f"{_wd['scored_by_weekday']} vs {_wd['n_scored']}")
    tc.check("weekday_raw_sum_matches",
             sum(_wd["raw_by_weekday"].values()) == _wd["n_raw"], "")
    tc.check("weekday_debounced_le_raw",
             _wd["n_debounced"] <= _wd["n_raw"], "")
    tc.check("weekday_scored_matches_spec",
             _wd["n_scored"] == _rf["specs"]["rank_diffs"]["n_scored"],
             f"{_wd['n_scored']} vs "
             f"{_rf['specs']['rank_diffs']['n_scored']}")
    tc.check("weekday_raw_matches_spec",
             _wd["n_raw"] == _rf["specs"]["rank_diffs"]["n_alert"], "")
    tc.check("weekday_debounced_matches_spec",
             _wd["n_debounced"]
             == _rf["specs"]["rank_diffs"]["n_alert_debounced"], "")
    # ЗСУВ НА ОДИН КРОК: одиниця — приріст, тож перша мітка мусить бути
    # ДРУГИМ знімком ряду, а не першим.
    _wd_first = min(int(k) for k in _wd["scored_by_weekday"])
    _sc_idx = scored_indices(len(_pts) - 1, 12, None)
    _expect_first = min(_pts[1:][i][0].weekday() for i in _sc_idx)
    tc.check("weekday_labels_use_diff_stamps",
             _wd_first == _expect_first, f"{_wd_first} vs {_expect_first}")

    _wa = weekday_analysis({"BTC.test": _rf}, 200)
    tc.check("weekday_analysis_per_feature",
             "BTC.test" in _wa["per_feature"], "")
    tc.check("weekday_analysis_pooled_present", "pooled" in _wa, "")
    tc.check("weekday_analysis_thresholds_fixed",
             _wa["thresholds"]["fixed_before_measurement"] is True, "")

    # Групування: atm7 окремо від решти, і жодна фіча не загублена.
    _rf7 = analyze_feature("BTC.atm7", _pts, 12, 3.0, 30, 1, 6)
    _rf9 = analyze_feature("BTC.atm90", _pts, 12, 3.0, 30, 1, 6)
    _wa2 = weekday_analysis({"BTC.atm7": _rf7, "BTC.atm90": _rf9,
                             "ETH.vrp30": _rf}, 200)
    tc.check("weekday_groups_split",
             _wa2["groups"]["atm7"]["features"] == ["BTC.atm7"]
             and sorted(_wa2["groups"]["other"]["features"])
             == ["BTC.atm90", "ETH.vrp30"],
             str({g: v["features"] for g, v in _wa2["groups"].items()}))
    _n_feat = sum(len(v["features"]) for v in _wa2["groups"].values())
    tc.check("weekday_groups_lose_nothing", _n_feat == 3, str(_n_feat))
    tc.check("weekday_group_pool_sums",
             sum(_wa2["groups"]["atm7"]["raw"].values())
             == _wa2["per_feature"]["BTC.atm7"]["n_raw"], "")
    # atm90 НЕ має потрапити в групу atm7 через суфіксне порівняння.
    tc.check("weekday_group_suffix_not_substring",
             "BTC.atm90" not in _wa2["groups"]["atm7"]["features"], "")

    # МАСШТАБ: нормований, тож на ряді з однаковою мінливістю по днях
    # усі значення ~1.0, а на ряді з тихими вихідними — помітно менші.
    _quiet = []
    _t0q = datetime(2026, 8, 3, 0, 10, tzinfo=timezone.utc)
    _rq = _lcg_stream(31337)
    _acc = 0.0
    for _i in range(180):
        _s = _t0q + timedelta(hours=4 * _i)
        _amp = 0.05 if _s.weekday() >= 5 else 1.0
        _acc += _amp * (next(_rq) - 0.5)
        _quiet.append((_s, _acc))
    _rq2 = analyze_feature("BTC.quiet", _quiet, 12, 3.0, 60, 1, 6)
    _sc = _rq2["weekday_rank_diffs"]["med_abs_diff_by_weekday"]
    tc.check("scale_detects_quiet_weekend",
             _sc.get("5") is not None and _sc.get("0") is not None
             and _sc["5"] < 0.3 * _sc["0"], str(_sc))
    _wa3 = weekday_analysis({"BTC.quiet": _rq2}, 100)
    _sr = _wa3["groups"]["other"]["scale_rel"]
    tc.check("scale_rel_normalised",
             0.9 < max(_sr.values()) < 1.6 and min(_sr.values()) < 0.3,
             str(_sr))
    # Нормування має бути ІНВАРІАНТНИМ ДО ОДИНИЦЬ: той самий ряд x1000
    # дає той самий scale_rel (інакше групування фіч у різних одиницях
    # зважувало б їх за величиною, а не за формою).
    _q1000 = [(s, v * 1000.0) for s, v in _quiet]
    _wa4 = weekday_analysis(
        {"BTC.quiet": analyze_feature("BTC.quiet", _q1000, 12, 3.0, 60, 1, 6)},
        100)
    _sr4 = _wa4["groups"]["other"]["scale_rel"]
    tc.check("scale_rel_unit_invariant",
             all(abs(_sr[d] - _sr4[d]) < 1e-9 for d in _sr), "")

    # МЕДІАНА СЛІПА ДО ХВОСТА. Ряд, у якому вихідні мають ТУ САМУ
    # медіану |diff|, але втричі тонший хвіст: med_rel ~1.0 скрізь,
    # а tail_rel/max_rel у Сб/Нд помітно менші. Без цього тесту замір
    # 23 сер на самій медіані виглядав би як спростування
    # гетероскедастичності, хоча він її просто не міг побачити.
    _tl = []
    _t0t = datetime(2026, 8, 3, 0, 10, tzinfo=timezone.utc)
    _rt = _lcg_stream(90210)
    _acc2 = 0.0
    for _i in range(900):
        _s2 = _t0t + timedelta(hours=4 * _i)
        _u = next(_rt)
        _step = (_u - 0.5)
        if _s2.weekday() < 5 and _u > 0.92:
            _step *= 8.0          # товстий хвіст лише в будні
        _acc2 += _step
        _tl.append((_s2, _acc2))
    _rt2 = analyze_feature("BTC.tail", _tl, 12, 3.0, 60, 1, 6)
    _wa5 = weekday_analysis({"BTC.tail": _rt2}, 100)
    _g = _wa5["groups"]["other"]
    # Медіана має лишитись ~рівною (розкид < 0.35 при n ~130 на день).
    _med_flat = (max(_g["scale_rel"].values())
                 - min(_g["scale_rel"].values())) < 0.35
    _tail_split = (_median([_g["max_rel"][d] for d in (5, 6)])
                   < 0.7 * _median([_g["max_rel"][d] for d in range(5)]))
    tc.check("median_blind_to_tail", _med_flat, str(_g["scale_rel"]))
    tc.check("tail_profile_detects_split", _tail_split, str(_g["max_rel"]))
    tc.check("tail_rel_present",
             len(_g["tail_rel"]) == 7 and len(_g["max_rel"]) == 7, "")
    _q1k = weekday_analysis(
        {"BTC.tail": analyze_feature("BTC.tail",
                                     [(s, v * 1000.0) for s, v in _tl],
                                     12, 3.0, 60, 1, 6)}, 100)
    tc.check("tail_rel_unit_invariant",
             all(abs(_g["max_rel"][d]
                     - _q1k["groups"]["other"]["max_rel"][d]) < 1e-9
                 for d in range(7)), "")

    # rank_rel / top10: нейтральні рівні і чутливість.
    tc.check("rank_rel_flat_is_half",
             all(abs(v - 0.5) < 0.06 for v in
                 _rank_rel_by({d: [float(i) for i in range(d, 200, 7)]
                               for d in range(7)}).values()),
             str(_rank_rel_by({d: [float(i) for i in range(d, 200, 7)]
                               for d in range(7)})))
    _big = {0: [10.0] * 30, 1: [1.0] * 30, 2: [1.0] * 30}
    _rr = _rank_rel_by(_big)
    tc.check("rank_rel_detects_shift", _rr[0] > 0.8 and _rr[1] < 0.4,
             str(_rr))
    tc.check("rank_rel_ties_get_mean_rank",
             abs(_rank_rel_by({0: [5.0] * 10, 1: [5.0] * 10})[0] - 0.5)
             < 1e-9, "збіги не мусять зсувати групу вниз")
    tc.check("rank_rel_too_few_refuses", _rank_rel_by({0: [1.0]}) == {}, "")
    _ts = _top_share_by({0: [float(i) for i in range(90)],
                         1: [float(i) for i in range(90, 100)]}, 0.10)
    tc.check("top_share_neutral_and_extreme",
             _ts[1] > 0.9 and _ts[0] < 0.05, str(_ts))
    _flat_ts = _top_share_by({d: [float(i) for i in range(d, 700, 7)]
                              for d in range(7)}, 0.10)
    tc.check("top_share_flat_is_frac",
             all(abs(v - 0.10) < 0.04 for v in _flat_ts.values()),
             str(_flat_ts))
    tc.check("top_share_bad_frac_refuses",
             _top_share_by({0: [1.0, 2.0]}, 0.0) == {}, "")
    # rank/top10 стабільніші за max: на тому самому ряді з тонким
    # хвостом у вихідні вони дають узгоджений сигнал.
    _g5 = _wa5["groups"]["other"]
    tc.check("rank_and_top10_agree_on_split",
             _median([_g5["rank_abs"][d] for d in (5, 6)])
             < _median([_g5["rank_abs"][d] for d in range(5)])
             and _median([_g5["top10_abs"][d] for d in (5, 6)])
             < _median([_g5["top10_abs"][d] for d in range(5)]),
             f"rank={_g5['rank_abs']} top10={_g5['top10_abs']}")

    # ЕПІЗОДИ: згортка корельованих фіч. Дві ІДЕНТИЧНІ фічі мусять дати
    # удвічі менше епізодів, ніж алертів (collapse = 0.5), і РІВНО стільки
    # ж епізодів, скільки одна з них поодинці.
    _dup = analyze_feature("BTC.dupA", _pts, 12, 3.0, 30, 1, 6)
    _dup2 = analyze_feature("BTC.dupB", _pts, 12, 3.0, 30, 1, 6)
    _wa6 = weekday_analysis({"BTC.dupA": _dup, "BTC.dupB": _dup2}, 100)
    _e = _wa6["episodes"]["other"]
    tc.check("episodes_collapse_identical_features",
             _e["n_feature_alerts"] == 2 * _dup["weekday_rank_diffs"]["n_raw"]
             and _e["n_episodes"] == _dup["weekday_rank_diffs"]["n_raw"],
             f"{_e['n_feature_alerts']} / {_e['n_episodes']}")
    tc.check("episodes_collapse_ratio_half",
             abs(_e["collapse"] - 0.5) < 1e-9, str(_e["collapse"]))
    tc.check("episodes_moments_not_doubled",
             _e["n_moments"] == _dup["weekday_rank_diffs"]["n_scored"],
             f"{_e['n_moments']} vs "
             f"{_dup['weekday_rank_diffs']['n_scored']}")
    # НЕЗАЛЕЖНІ фічі згортатись НЕ мусять (негативний контроль до
    # попереднього: інакше згортка «працювала» б, просто все злипаючи).
    _ind = analyze_feature("BTC.indep",
                           [(s_, v * 1.0 + (7 if i % 5 else -3))
                            for i, (s_, v) in enumerate(_pts)],
                           12, 3.0, 30, 1, 6)
    _wa7 = weekday_analysis({"BTC.dupA": _dup, "BTC.indep": _ind}, 100)
    tc.check("episodes_keep_distinct_moments",
             _wa7["episodes"]["other"]["collapse"] > 0.5,
             str(_wa7["episodes"]["other"]["collapse"]))
    tc.check("episodes_verdict_present",
             _wa6["episodes"]["other"]["verdict"] in
             ("LOW-N", "NO-EFFECT", "INCONCLUSIVE", "WEEKDAY-EFFECT",
              "NO-TEST"), "")
    tc.check("episodes_exposure_is_moments",
             sum(_e["scored"].values()) == _e["n_moments"], "")

    # --- ENV-WARN (quiet=True: самотест не сміє смітити в живий лог) ---
    import re as _re
    _env = {"VOLEDGE_CALIB_WINDOW": "60", "VOLEDGE_CALIB_WINDOWW": "60",
            "PATH": "/bin"}
    _unk = warn_unknown_env(KNOWN_ENV, environ=_env, quiet=True)
    tc.check("env_warn_flags_typo", _unk == ["VOLEDGE_CALIB_WINDOWW"],
             str(_unk))
    tc.check("env_warn_ignores_known", "VOLEDGE_CALIB_WINDOW" not in _unk)
    _src = ""
    try:
        with open(os.path.abspath(__file__), "r",
                  encoding="utf-8") as _envfh:
            _src = _envfh.read()
    except OSError:
        _src = ""
    _read = set(_re.findall(
        r'os\.environ\.get\(\s*"(VOLEDGE_[A-Z0-9_]+)"', _src))
    tc.check("known_env_covers_every_read",
             bool(_src) and _read and _read <= set(KNOWN_ENV),
             f"не оголошені: {sorted(_read - set(KNOWN_ENV))}")
    tc.check("known_env_has_no_dead_names",
             bool(_src) and set(KNOWN_ENV) <= _read | {"VOLEDGE_OFFLINE"},
             f"зайві: {sorted(set(KNOWN_ENV) - _read - {'VOLEDGE_OFFLINE'})}")
    # Детекторські змінні НЕ успадковані навмисно: під калібратором
    # main() детектора не виконується, тож VOLEDGE_RANK_K тут no-op і
    # мусить ловитись. Без цього тесту хтось «дочистить» список,
    # додавши RANK_K, і сплутати його з CALIB_RANK_K стане безкарно.
    tc.check("detector_only_vars_are_flagged",
             warn_unknown_env(KNOWN_ENV, environ={"VOLEDGE_RANK_K": "1"},
                              quiet=True) == ["VOLEDGE_RANK_K"])

    print(f"[TEST] passed {tc.passed}/{tc.run}")
    for f_ in tc.failures:
        print(f"[TEST] FAILURE detail: {f_}")
    return tc


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def run_live(min_history, z_thresh, vrp_age_h, window, no_cache,
             rank_k, cooldown, slot_perm, vrp_source,
             wd_perm=WD_PERM_DEFAULT):
    chain_pairs = list_artifacts(CHAIN_DIR, "chain")
    rv_pairs = list_artifacts(RV_DIR, vrp_source)
    if not rv_pairs:
        raise RuntimeError(
            f"[FAIL] нуль артефактів з префіксом {vrp_source!r} у {RV_DIR}")
    print(f"[OVERALL] chain-артефактів: {len(chain_pairs)}, "
          f"{vrp_source}-артефактів: {len(rv_pairs)}")
    if not chain_pairs:
        print(f"[SUMMARY] нуль chain-артефактів у {CHAIN_DIR}")
        return 1

    series, counters = build_series(chain_pairs, rv_pairs, vrp_age_h,
                                    no_cache)
    print(f"[OVERALL] знімків оброблено: {counters['snapshots']}, "
          f"cache hit/miss: {counters['cache_hit']}/{counters['cache_miss']}, "
          f"read_fail: {counters['read_fail']}, "
          f"дірок у рядах: {counters['feature_gaps']}")
    if not series:
        print("[SUMMARY] жодного ряду не побудовано.")
        return 1

    features = {}
    for name in sorted(series):
        features[name] = analyze_feature(name, series[name], min_history,
                                         z_thresh, window, rank_k, cooldown)

    report = {"generated_utc": datetime.now(timezone.utc).isoformat(),
              "z_thresh": z_thresh, "min_history": min_history,
              "window": window, "vrp_max_age_h": vrp_age_h,
              "rank_k": rank_k, "cooldown": cooldown,
              "n_chain": len(chain_pairs), "n_rv": len(rv_pairs),
              "counters": counters, "features": features}

    report["slot_test"] = slot_analysis(series, slot_perm)
    report["slot_perm"] = slot_perm

    report["weekday"] = weekday_analysis(features, wd_perm)
    report["weekday_perm"] = wd_perm

    report["redundancy"] = redundancy_analysis(series, min_history,
                                               rank_k, cooldown)
    report["redundancy_reject_ratio"] = REDUND_REJECT_RATIO
    report["redundancy_verdicts"] = {}
    for _nm, _vars in report["redundancy"].items():
        if _vars.get("_status") == "SKIP":
            report["redundancy_verdicts"][_nm] = {"verdict": "SKIP"}
            continue
        _vd, _txt = redundancy_verdict(_vars)
        _vd["text"] = _txt
        report["redundancy_verdicts"][_nm] = _vd

    print_report(report, z_thresh)
    print_weekday(report["weekday"])
    print_redundancy(report["redundancy"])

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUT_DIR, f"calib_{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n[OVERALL] звіт: {out_path}")
    print(f"[SUMMARY] фіч проаналізовано: {len(features)}, "
          f"знімків: {counters['snapshots']}, "
          f"read_fail: {counters['read_fail']}")
    return 0


def main():
    print("=" * 78)
    print("Vol-Edge :: anomaly_calibrate (READ-ONLY аналіз детектора)")
    print("=" * 78)
    # ПЕРЕД розбором значень: нерозпізнана змінна = тихий дефолт.
    warn_unknown_env(KNOWN_ENV, label="anomaly_calibrate")
    offline_raw = os.environ.get("VOLEDGE_OFFLINE", "0")
    z_raw = os.environ.get("VOLEDGE_Z_THRESH", "3.0")
    mh_raw = os.environ.get("VOLEDGE_MIN_HISTORY", "12")
    age_raw = os.environ.get("VOLEDGE_VRP_MAX_AGE_H", "5")
    src_raw = os.environ.get("VOLEDGE_VRP_SOURCE", "rvroll")
    win_raw = os.environ.get("VOLEDGE_CALIB_WINDOW", "60")
    nc_raw = os.environ.get("VOLEDGE_CALIB_NO_CACHE", "0")
    rk_raw = os.environ.get("VOLEDGE_CALIB_RANK_K", "1")
    cd_raw = os.environ.get("VOLEDGE_CALIB_COOLDOWN", "6")
    sp_raw = os.environ.get("VOLEDGE_CALIB_SLOT_PERM",
                            str(SLOT_PERM_DEFAULT))
    wp_raw = os.environ.get("VOLEDGE_CALIB_WD_PERM", str(WD_PERM_DEFAULT))

    offline = offline_raw.strip() == "1"
    no_cache = nc_raw.strip() == "1"
    try:
        z_thresh = float(z_raw)
        min_history = int(mh_raw)
        vrp_age = float(age_raw)
        window = int(win_raw)
        rank_k = int(rk_raw)
        cooldown = int(cd_raw)
        slot_perm = int(sp_raw)
        wd_perm = int(wp_raw)
    except ValueError:
        raise RuntimeError(f"Невалідні env: Z_THRESH={z_raw!r} "
                           f"MIN_HISTORY={mh_raw!r} "
                           f"VRP_MAX_AGE_H={age_raw!r} "
                           f"CALIB_WINDOW={win_raw!r} "
                           f"CALIB_RANK_K={rk_raw!r} "
                           f"CALIB_COOLDOWN={cd_raw!r} "
                           f"CALIB_SLOT_PERM={sp_raw!r} "
                           f"CALIB_WD_PERM={wp_raw!r}")
    if z_thresh <= 0 or min_history < 3 or vrp_age <= 0:
        raise RuntimeError(f"Поза діапазоном: z_thresh={z_thresh} "
                           f"min_history={min_history} vrp_age={vrp_age}")
    if rank_k < 1:
        raise RuntimeError(f"CALIB_RANK_K={rank_k} має бути >= 1")
    if cooldown < 0:
        raise RuntimeError(f"CALIB_COOLDOWN={cooldown} має бути >= 0")
    if slot_perm < 0:
        raise RuntimeError(f"CALIB_SLOT_PERM={slot_perm} має бути >= 0")
    if wd_perm < 0:
        raise RuntimeError(f"CALIB_WD_PERM={wd_perm} має бути >= 0")
    if window <= min_history:
        raise RuntimeError(f"CALIB_WINDOW={window} має бути > "
                           f"MIN_HISTORY={min_history}")

    print(f"[ENV] VOLEDGE_OFFLINE={offline_raw!r} -> {offline}")
    print(f"[ENV] VOLEDGE_Z_THRESH={z_raw!r} -> {z_thresh}")
    print(f"[ENV] VOLEDGE_MIN_HISTORY={mh_raw!r} -> {min_history}")
    print(f"[ENV] VOLEDGE_VRP_MAX_AGE_H={age_raw!r} -> {vrp_age}")
    vrp_source = src_raw.strip()
    if vrp_source not in ("rvroll", "rv"):
        raise RuntimeError(f"VOLEDGE_VRP_SOURCE={src_raw!r} — очікується "
                           f"'rvroll' (ковзне вікно) або 'rv' (legacy)")
    print(f"[ENV] VOLEDGE_VRP_SOURCE={src_raw!r} -> {vrp_source}")
    print(f"[ENV] VOLEDGE_CALIB_WINDOW={win_raw!r} -> {window}")
    print(f"[ENV] VOLEDGE_CALIB_NO_CACHE={nc_raw!r} -> {no_cache}")
    print(f"[ENV] VOLEDGE_CALIB_RANK_K={rk_raw!r} -> {rank_k}")
    print(f"[ENV] VOLEDGE_CALIB_COOLDOWN={cd_raw!r} -> {cooldown}")
    print(f"[ENV] VOLEDGE_CALIB_SLOT_PERM={sp_raw!r} -> {slot_perm}")
    print(f"[ENV] VOLEDGE_CALIB_WD_PERM={wp_raw!r} -> {wd_perm}")

    print("\n--- ОФЛАЙН-САМОТЕСТИ " + "-" * 56)
    tc = run_self_tests()
    print(f"[SUMMARY] самотести: {tc.run}, пройдено: {tc.passed}, "
          f"провалено: {len(tc.failures)}")
    if tc.failures:
        return 1
    if offline:
        print("[SUMMARY] VOLEDGE_OFFLINE=1 -> live пропущено явно.")
        return 0

    print("\n--- КАЛІБРУВАЛЬНИЙ АНАЛІЗ " + "-" * 51)
    return run_live(min_history, z_thresh, vrp_age, window, no_cache,
                    rank_k, cooldown, slot_perm, vrp_source, wd_perm)


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
