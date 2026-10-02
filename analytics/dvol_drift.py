# -*- coding: utf-8 -*-
"""
[EN] Read-only check of a one-sided drift in vrp30 against an
independent numerator (the official Deribit DVOL index), plus a scan of
historical DVOL/RV30 windows to establish how often such drift occurs
by chance.

--- Ukrainian original below ---
Vol-Edge :: analytics/dvol_drift.py

READ-ONLY перевірка одностороннього дрейфу vrp30 проти НЕЗАЛЕЖНОГО
чисельника (офіційний індекс DVOL Deribit).

НІЧОГО не змінює: не пише в artifacts/anomaly/, не чіпає детектор,
не чіпає калібратор. Пише лише власний звіт dvoldrift_*.json.

ПИТАННЯ (пункт 3 «Наступних кроків», 23 сер 2026)
-------------------------------------------------
Рангова асиметрія рівнів BTC.vrp30 = 0 низ / 25 верх, ETH.vrp30 =
0 низ / 23 верх. Пояснення через накопичення слотової пилки
знаменника ВІДКЛИКАНЕ (пилки немає, асиметрія виросла). Лишились два
кандидати:
  1. VRP справді монотонно росте (IV дорожчає відносно RV) — це був би
     РЕАЛЬНИЙ сигнал і перший потенційно торгований режим у проєкті;
  2. є ще одне джерело артефакту, не знайдене.

ЧОМУ САМЕ DVOL
--------------
vrp30 = atm30 / rv30. Обидва складники — НАШІ: atm30 з нашого SVI-фіту,
rv30 з нашого ковзного вікна. Якщо дрейф породжений будь-яким із них,
ми цього з середини не побачимо. DVOL — офіційний 30-денний індекс
волатильності Deribit, порахований БІРЖЕЮ за власною методикою з
власного стакана. Він не поділяє з нами ані фіту, ані сітки, ані
конвенції ATM.

ДЕКОМПОЗИЦІЯ (головна ідея модуля)
----------------------------------
Недостатньо порівняти vrp30 з dvol/rv30: вони ділять ЗНАМЕННИК, тож
дрейф знаменника проліз би в обидва. Тому асиметрія міряється окремо
для П'ЯТИ рядів на тих самих якорях:

  atm30       — наш чисельник
  dvol        — незалежний чисельник
  rv30        — спільний знаменник
  vrp_ours    — atm30 / rv30
  vrp_dvol    — dvol  / rv30

Читання таблиці:
  atm30 і dvol обидва дрейфують вгору, rv30 плаский
      -> IV справді дорожчає. РЕАЛЬНИЙ СИГНАЛ.
  atm30 і dvol плоскі, rv30 дрейфує вниз
      -> дрейф ratio механічний, породжений знаменником.
  atm30 дрейфує, dvol НЕ дрейфує
      -> проблема в НАШОМУ фіті ATM30, не в ринку.
  дрейфують усі три
      -> режимний зсув усього серпня; ratio може бути реальним, але
         сила висновку залежить від довгої історії (див. нижче).

ДОВГА ІСТОРІЯ (те, чого форвардний збір дати не може)
-----------------------------------------------------
DVOL доступний із 2019 року. Тому окремо будується ДОБОВИЙ ряд
dvol / rv30_cc за VOLEDGE_DVOL_HIST_D діб (default 730) і скануються
ковзні вікна довжини нашої історії. Це дає ЕМПІРИЧНУ частку вікон, у
яких асиметрія >= спостереженої. Тобто відповідь на питання «наскільки
рідкісний такий дрейф» береться з РОЗПОДІЛУ, а не з припущення.

ВАЖЛИВО ПРО ДОВГИЙ РЯД: rv30 там рахується close-to-close по ДОБОВИХ
свічках.

ЗАСТЕРЕЖЕННЯ ПРО cc ПЕРЕПИСАНЕ 25 сер (стара редакція ВІДКЛИКАНА).
Було записано: «cc занижує RV ~15% (29.9 проти 35.1 BTC, 26 лип), тому
РІВЕНЬ непорівнюваний, а ФОРМА порівнювана, бо постійний множник на неї
не впливає». Друга частина СПРОСТОВАНА: множник не постійний і навіть
не одного знаку. Живий прогін 24 сер дав ETH RV30_cc = 70.71% проти
RV30_5m = 48.07%, тобто cc ВИЩЕ на 47%.

МЕХАНІЗМ (не міжденні розриви — ринок 24/7, розривів немає за
побудовою; денні закриття беруться з тих самих барів):
    r_cc^2 = (sum r_i)^2 = sum r_i^2 + 2 * sum_{i<j} r_i r_j
Різниця cc проти сітки — це ЗНАК внутрішньоденної автокореляції.
Пиляння -> кросс-члени від'ємні -> cc нижче сітки (липень). Трендові
доби -> додатні -> cc вище (серпень). Плюс cc має 30 доданків проти
1440, тож одна доба домінує суму і сидить у ковзному вікні рівно 30 діб
(сходинка вниз, полиця, сходинка вгору).

ВПЛИВ НА ВИСНОВОК про «~15% вікон» неоднозначний АПРІОРІ:
  + незалежний шум у знаменнику руйнує монотонні пробіги -> виміряні
    15% є НИЖНЬОЮ межею, висновок «не сигнал» від цього лише міцніє;
  - блокова структура артефакту (сходинки на вході/виході екстремальної
    доби) сама виробляє короткі однобічні серії -> завищує.
Тому додано СЕКЦІЮ «Стабільність множника cc», яка (а) міряє ряд
RV30_cc / RV30_grid як діагностику і (б) ПЕРЕПРОГАНЯЄ те саме
сканування зі знаменником на 30-хв сітці. Пункт (б) і є рішенням:
він відповідає на «чи змінюється базова лінія» прямо, без ланцюжка
міркувань через множник.

РІВЕНЬ dvol/rv30_cc у будь-якому разі НЕ порівнюваний з нашим vrp30 і
друкується з позначкою LEVEL-NOT-COMPARABLE.

Env-флаги:
  VOLEDGE_OFFLINE=1        -> лише самотести, без мережі.
  VOLEDGE_MIN_HISTORY      -> мінімум історії для рангів (12).
  VOLEDGE_RANK_K           -> K рангової схеми (1).
  VOLEDGE_VRP_SOURCE       -> префікс артефактів знаменника (rvroll).
  VOLEDGE_VRP_MAX_AGE_H    -> макс. вік артефакту знаменника (5).
  VOLEDGE_DVOL_MAX_AGE_MIN -> макс. розбіжність DVOL-точки і якоря,
                              хвилин (90). Більше -> явний SKIP.
  VOLEDGE_DVOL_HIST_D      -> глибина довгого ряду, діб (730).
  VOLEDGE_DVOL_NO_HIST=1   -> пропустити довгий ряд явно.
  VOLEDGE_CCRATIO_SKIP=1   -> пропустити секцію множника cc явно.
  VOLEDGE_CCRATIO_D        -> глибина 5-хв історії для сітки, діб
                              (default = VOLEDGE_DVOL_HIST_D).
  VOLEDGE_CC_GRID_MIN      -> крок сітки знаменника, хв (30).
  VOLEDGE_CC_CHUNK_D       -> розмір чанка 5-хв свічок, діб (7).
  VOLEDGE_CC_MIN_COVER     -> мін. покриття доби ретернами сітки (0.96).

Чистий stdlib. Подвійний клік + input() завжди.
"""

import json
import math
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_ROOT, _THIS_DIR, os.path.join(_ROOT, "core"),
           os.path.join(_ROOT, "analytics")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from env_flags import merge_known, warn_unknown_env
except ImportError:
    from core.env_flags import merge_known, warn_unknown_env

# Змінні, які читає САМЕ ЦЯ точка входу (усі — у main()). Імпортований
# anomaly_detect читає оточення лише у СВОЄМУ main(), який тут не
# виконується, тож його змінні успадковувати не треба — ті, що
# збігаються (MIN_HISTORY, RANK_K, VRP_*), читає цей модуль сам.
KNOWN_ENV = merge_known((
    "VOLEDGE_MIN_HISTORY",
    "VOLEDGE_RANK_K",
    "VOLEDGE_VRP_SOURCE",
    "VOLEDGE_VRP_MAX_AGE_H",
    "VOLEDGE_DVOL_MAX_AGE_MIN",
    "VOLEDGE_DVOL_HIST_D",
    "VOLEDGE_DVOL_NO_HIST",
    "VOLEDGE_CCRATIO_SKIP",
    "VOLEDGE_CCRATIO_D",
    "VOLEDGE_CC_GRID_MIN",
    "VOLEDGE_CC_CHUNK_D",
    "VOLEDGE_CC_MIN_COVER",
))

try:
    from anomaly_detect import (CHAIN_DIR, RV_DIR, OUT_DIR, _load_json,
                                list_artifacts, features_of_snapshot,
                                vrp_from_rv)
except ImportError:
    from analytics.anomaly_detect import (CHAIN_DIR, RV_DIR, OUT_DIR,
                                          _load_json, list_artifacts,
                                          features_of_snapshot,
                                          vrp_from_rv)
try:
    from surface_report import (http_get_json, API_BASE, PERP_NAME,
                                ANNUALIZE)
except ImportError:
    from analytics.surface_report import (http_get_json, API_BASE,
                                          PERP_NAME, ANNUALIZE)

CURRENCIES = ("BTC", "ETH")
CALIB_CACHE = os.path.join(OUT_DIR, "_calib_cache.json")
# Канонічна UTC-сітка знімків; стара сітка до фіксу CRON_TZ 31 лип.
CANONICAL_SLOT_HOURS = (0, 4, 8, 12, 16, 20)
LEGACY_SLOT_HOURS = (21, 1, 5, 9, 13, 17)
DVOL_MAX_AGE_MIN_DEFAULT = 90
# resolution ендпоінта get_volatility_index_data задається В СЕКУНДАХ.
# "60" — це ОДНА ХВИЛИНА, а не година (коштувало одного прогону:
# 1000 рядків по хвилині покрили 16 год, і з 176 якорів збіглося 5).
DVOL_RES_HOUR = "3600"
DVOL_RES_DAY = "86400"
# Ендпоінт віддає щонайбільше стільки рядків за виклик; більший
# діапазон треба брати сторінками, інакше мовчки отримаємо хвіст.
DVOL_ROWS_CAP = 1000
# Якщо через брак DVOL відпала більша частка якорів — це не «трохи
# дірок», а зламане вирівнювання. Явна відмова, не лічильник.
DVOL_MAX_DROP_FRAC = 0.20
DVOL_HIST_D_DEFAULT = 730
RV_WINDOW_D = 30
# Асиметрія вважається вартою уваги від цієї кількості подій; менше —
# просто мало даних, а не сигнатура.
ASYM_MIN_EVENTS = 10
# Менше оцінених точок — рекорди просто не встигли накопичитись, і
# питання «дрейфує чи ні» не має відповіді. Це ЄДИНА підстава для
# LOW-N; мала кількість подій при достатньому n_scored — це «ні».
MIN_SCORED_FOR_DRIFT = 30
# СМУГИ ОДНОБІЧНОСТІ (|up_frac - 0.5|). Жорсткий поріг 0.9/0.1 на
# неперервній величині виявився хибним 23 сер: ETH.rv30 з 56/12 (18%)
# проскочив у «не дрейфує», хоча очима це виражений дрейф. Це та сама
# помилка, що «поріг на max/min медіан міряє шум». Тепер три смуги, і
# проміжна МІЧЕНА, а не мовчки віднесена до одного з боків.
DRIFT_STRONG_DEV = 0.35   # |uf-0.5| >= 0.35  -> 85%+ або 15%-
DRIFT_MILD_DEV = 0.15     # 0.15..0.35        -> помітно, але не різко
# БАЗОВА ЛІНІЯ НЕ 0.5 (виміряно 23 сер на 736 добах DVOL/RV30_cc):
# медіанна однобічність по історії 29% (BTC) / 46% (ETH). Відношення
# двох автокорельованих рядів схильне до однобічних пробігів саме по
# собі. Тому смуги — це ОПИС ФОРМИ, а не тест значущості; значущість
# дає лише empirical_window_scan.
BASELINE_NOTE = ("однобічність сама по собі не є значущістю: "
                 "медіана по 736 добах 29%/46%, не 50%")

# --- Секція «Стабільність множника cc» (25 сер) ------------------------
# 5-хв свічки тягнуться чанками; 7 діб = 2016 барів, із запасом під
# ліміт рядків ендпоінта. Жорстка стеля сторінок — той самий клас
# захисту, що й у fetch_dvol_series.
CC_CHUNK_D_DEFAULT = 7
CC_MAX_CHUNKS = 400
CC_GRID_MIN_DEFAULT = 30
CC_MIN_COVER_DEFAULT = 0.96
# ПОРОГИ ЗАФІКСОВАНІ ДО ЗАМІРУ (інакше вердикт підганяється під
# отримане). Величина — log(RV_cc / RV_grid); симетрична за знаком,
# на відміну від самого відношення.
# АБСОЛЮТНИЙ ПОРІГ НА IQR БУВ ХИБНИЙ (зловлено на смоуці 25 сер):
# на ЧИСТОМУ i.i.d. шумі при сталому множнику IQR log-відношення вже
# ~0.12, бо cc має 30 доданків проти 1440. Поріг «сталий < 0.10» був би
# недосяжний ЗА ПОБУДОВОЮ — той самий клас, що «поріг на max/min медіан
# міряє шум». Тому судимо НАДЛИШОК над нульовою моделлю, а абсолютний
# IQR лишається лише як друкована довідка.
CC_EXCESS_STABLE = 1.30    # IQR_obs / IQR_null < 1.30 -> множник сталий
CC_EXCESS_REGIME = 2.00    # >= 2.00 -> режимний
CC_NULL_REPS = 200         # реплікацій нульової моделі
CC_NULL_SEED = 20260825
CC_SPEARMAN_STRONG = 0.50  # |rho| з проксі режиму
# Вікон у перепрогоні сканування менше цього — вердикту немає.
CC_MIN_WINDOWS = 100
# Смуга базової лінії, у якій висновок «дрейф не рідкість» стоїть.
CC_BASE_LO = 0.10
CC_BASE_HI = 0.22
CC_BASE_FAIL = 0.05        # нижче — висновок падає


# ----------------------------------------------------------------------------
# Дрібна статистика
# ----------------------------------------------------------------------------

def _median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _f(x, prec=4):
    return "—" if x is None else f"{x:.{prec}f}"


def rank_asymmetry(values, min_history, k):
    """
    (n_low, n_high, n_scored) — рангові попадання на РІВНЯХ.

    Точка вважається подією, якщо входить у K найменших або K найбільших
    усієї попередньої історії. Та сама умова, що в детекторі
    (rank_hits на рівнях), тому числа прямо порівнювані зі звітом
    калібрування.
    """
    if k < 1:
        raise ValueError(f"k={k} має бути >= 1")
    n_low = n_high = n_scored = 0
    for i in range(len(values)):
        hist = values[:i]
        if len(hist) < min_history:
            continue
        n_scored += 1
        x = values[i]
        below = sum(1 for v in hist if v < x)
        above = sum(1 for v in hist if v > x)
        if below < k:
            n_low += 1
        elif above < k:
            n_high += 1
    return n_low, n_high, n_scored


def asymmetry_score(n_low, n_high):
    """
    Частка подій, що припала на верхній бік. None, якщо подій немає.
    0.5 = симетрія, 1.0 = всі вгору.
    """
    tot = n_low + n_high
    if tot == 0:
        return None
    return n_high / tot


def binomial_tail_two_sided(n_low, n_high):
    """
    Наближений двобічний p для H0 «сторона рекорду рівноймовірна».

    НАБЛИЖЕННЯ, І ЦЕ ВАЖЛИВО: рекорди в автокорельованому ряді НЕ
    незалежні, тож істинний p більший за цей. Число подано лише як
    орієнтир порядку; рішення ухвалюється за ЕМПІРИЧНИМ скануванням
    довгої історії (empirical_window_scan), де залежність врахована
    самою побудовою.
    """
    n = n_low + n_high
    if n == 0:
        return None
    m = min(n_low, n_high)
    acc = 0.0
    for i in range(m + 1):
        acc += math.comb(n, i)
    p = 2.0 * acc / (2.0 ** n)
    return min(1.0, p)


def empirical_window_scan(values, win, min_history, k, obs_up_frac,
                          obs_events):
    """
    ({...}|None, reason) — як часто ковзне вікно довжини `win` дає
    асиметрію не слабшу за спостережену.

    Це і є чесна відповідь на «наскільки рідкісний такий дрейф»:
    розподіл береться з реальної історії того самого інструмента, а не
    з припущення про незалежність.

    Вікна ПЕРЕКРИВАЮТЬСЯ (крок 1 доба), тож частка — це не p-value в
    строгому сенсі; вона зміщена в бік згладжування. Лічильник
    n_windows друкується поруч, щоб це було видно.
    """
    if win < min_history + 2:
        return None, (f"вікно {win} < min_history+2 ({min_history + 2}) — "
                      f"нічого оцінювати")
    if len(values) < win:
        return None, (f"довгий ряд {len(values)} коротший за вікно {win}")
    if obs_up_frac is None:
        return None, "у спостереженні нуль рангових подій"
    n_windows = 0
    n_ge = 0
    n_skipped_lowevents = 0
    fracs = []
    for s in range(0, len(values) - win + 1):
        seg = values[s:s + win]
        lo, hi, _ns = rank_asymmetry(seg, min_history, k)
        tot = lo + hi
        if tot < ASYM_MIN_EVENTS:
            n_skipped_lowevents += 1
            continue
        n_windows += 1
        fr = hi / tot
        fracs.append(fr)
        # «Не слабша» = така сама однобічність у БУДЬ-ЯКИЙ бік.
        if max(fr, 1.0 - fr) >= max(obs_up_frac, 1.0 - obs_up_frac):
            n_ge += 1
    if n_windows == 0:
        return None, (f"жодне вікно не набрало {ASYM_MIN_EVENTS} подій "
                      f"(пропущено {n_skipped_lowevents})")
    return {"win": win, "n_windows": n_windows,
            "n_skipped_low_events": n_skipped_lowevents,
            "n_ge_observed": n_ge,
            "frac_ge_observed": n_ge / n_windows,
            "median_up_frac": _median(fracs),
            "obs_up_frac": obs_up_frac,
            "obs_events": obs_events,
            "windows_overlap": True}, "OK"


# ----------------------------------------------------------------------------
# Мережа: DVOL та добові свічки
# ----------------------------------------------------------------------------

def parse_dvol_rows(data, counters=None):
    """
    [(dt_utc, close_frac)] з сирих рядків ендпоінта. Виділено окремо,
    щоб парсинг перевірявся офлайн, без мережі.
    """
    c = counters if counters is not None else {}
    for k_ in ("rows", "kept", "dup_stamp", "bad_row"):
        c.setdefault(k_, 0)
    seen = c.setdefault("_seen", set())
    out = []
    for row in data:
        c["rows"] += 1
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            c["bad_row"] += 1
            continue
        try:
            ts = int(row[0])
            close = float(row[4])
        except (TypeError, ValueError):
            c["bad_row"] += 1
            continue
        if ts in seen:
            c["dup_stamp"] += 1
            continue
        seen.add(ts)
        out.append((datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc),
                    close / 100.0))
        c["kept"] += 1
    return out


def fetch_dvol_series(currency, start_ms, end_ms, resolution,
                      _fetcher=None):
    """
    ([(dt_utc, close_frac)], counters) — історія індексу DVOL.

    resolution У СЕКУНДАХ, рядком: DVOL_RES_HOUR / DVOL_RES_DAY.
    Значення переводяться у частки (біржа віддає відсоткові пункти).

    СТОРІНКУВАННЯ ОБОВ'ЯЗКОВЕ: ендпоінт віддає не більше DVOL_ROWS_CAP
    рядків і при перевищенні мовчки повертає лише ХВІСТ діапазону.
    Йдемо назад від end_ms, доки не покриємо start_ms або доки сторінка
    не перестане додавати нові точки (захист від нескінченного циклу).
    """
    step_s = int(resolution)
    if step_s <= 0:
        raise ValueError(f"resolution={resolution!r} має бути > 0 секунд")
    if end_ms <= start_ms:
        raise ValueError(f"end_ms {end_ms} <= start_ms {start_ms}")
    c = {"rows": 0, "kept": 0, "dup_stamp": 0, "bad_row": 0,
         "pages": 0, "hit_cap": 0, "_seen": set()}
    get = _fetcher or http_get_json
    out = []
    cursor = int(end_ms)
    page_ms = DVOL_ROWS_CAP * step_s * 1000
    while cursor > start_ms:
        lo = max(int(start_ms), cursor - page_ms)
        url = (f"{API_BASE}/get_volatility_index_data?currency={currency}"
               f"&start_timestamp={lo}&end_timestamp={cursor}"
               f"&resolution={resolution}")
        r = get(url)
        data = r.get("data")
        c["pages"] += 1
        if not data:
            break
        before = c["kept"]
        out.extend(parse_dvol_rows(data, c))
        if len(data) >= DVOL_ROWS_CAP:
            c["hit_cap"] += 1
        if c["kept"] == before:
            break
        cursor = lo
        if c["pages"] > 50:
            raise RuntimeError(f"DVOL {currency}: >50 сторінок — "
                               f"схоже на нескінченний цикл")
    c.pop("_seen", None)
    if not out:
        raise RuntimeError(f"DVOL {currency}: порожня відповідь "
                           f"(resolution={resolution})")
    out.sort(key=lambda p: p[0])
    return out, c


def check_dvol_coverage(series, first_anchor, last_anchor, currency,
                        max_age_s):
    """
    Явна перевірка, що ряд DVOL накриває діапазон якорів.

    Без неї брак покриття перетворюється на тихе відкидання якорів у
    align_nearest, і модуль доходить до вердикту на кількох точках,
    що виглядає змістовно. Саме так і сталося на першому прогоні.
    """
    if not series:
        raise RuntimeError(f"DVOL {currency}: порожній ряд")
    lo, hi = series[0][0], series[-1][0]
    if lo - first_anchor > timedelta(seconds=max_age_s):
        raise RuntimeError(
            f"DVOL {currency}: ряд починається {lo.isoformat()}, а "
            f"перший якір {first_anchor.isoformat()} — не покрито. "
            f"Найімовірніша причина: resolution у СЕКУНДАХ і/або "
            f"обмеження {DVOL_ROWS_CAP} рядків на сторінку.")
    if last_anchor - hi > timedelta(seconds=max_age_s):
        raise RuntimeError(
            f"DVOL {currency}: ряд закінчується {hi.isoformat()}, а "
            f"останній якір {last_anchor.isoformat()} — не покрито.")
    return {"first": lo.isoformat(), "last": hi.isoformat(),
            "n": len(series)}


def fetch_daily_closes(currency, days):
    """([(dt_utc, close)], counters) — добові закриття перпетуала."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int((days + 5) * 24 * 3600 * 1000)
    inst = PERP_NAME[currency]
    r = http_get_json(
        f"{API_BASE}/get_tradingview_chart_data?instrument_name={inst}"
        f"&start_timestamp={start_ms}&end_timestamp={end_ms}"
        f"&resolution=1D")
    if r.get("status") != "ok" or not r.get("close"):
        raise RuntimeError(f"candles {inst}: status={r.get('status')}")
    ticks = r.get("ticks") or []
    closes = r["close"]
    if len(ticks) != len(closes):
        raise RuntimeError(f"candles {inst}: ticks {len(ticks)} != "
                           f"closes {len(closes)} — сітка неузгоджена")
    out = [(datetime.fromtimestamp(int(t) / 1000.0, tz=timezone.utc),
            float(cl)) for t, cl in zip(ticks, closes)]
    out.sort(key=lambda p: p[0])
    return out, {"rows": len(out)}


def rv30_cc_series(closes, window=RV_WINDOW_D):
    """
    [(dt, rv30_annualized)] — trailing close-to-close RV.

    ЗАСТЕРЕЖЕННЯ ПЕРЕПИСАНЕ 25 сер. Стара редакція («cc занижує ~15%,
    для ФОРМИ постійний множник байдужий») ВІДКЛИКАНА: множник не
    сталий і міняє знак разом із режимом (24 сер ETH: cc 70.71 проти
    сітки 48.07). Причина — знак внутрішньоденної автокореляції, а не
    міжденні розриви, яких на ринку 24/7 немає. Рівень непорівнюваний
    у будь-якому разі; ФОРМА перевіряється прямо — секцією
    «Стабільність множника cc» (перепрогін сканування на 30-хв
    знаменнику), а не припущенням про множник.
    """
    if window < 2:
        raise ValueError(f"window={window} має бути >= 2")
    out = []
    for i in range(len(closes)):
        if i < window:
            continue
        acc = 0.0
        ok = True
        for j in range(i - window + 1, i + 1):
            p0, p1 = closes[j - 1][1], closes[j][1]
            if p0 <= 0 or p1 <= 0:
                ok = False
                break
            acc += math.log(p1 / p0) ** 2
        if not ok:
            continue
        out.append((closes[i][0], math.sqrt(acc / window * ANNUALIZE)))
    return out


def align_nearest(series, target, max_age_s):
    """
    (value|None, delta_s|None) — найближча точка ряду до `target`.

    Береться найближча В БУДЬ-ЯКИЙ БІК, бо DVOL — незалежне
    вимірювання, а не наш знаменник: для нього немає причини вимагати
    «не з майбутнього». Перевищення max_age_s -> явна відмова.
    """
    if not series:
        return None, None
    best = None
    best_d = None
    for st, v in series:
        d = abs((st - target).total_seconds())
        if best_d is None or d < best_d:
            best, best_d = v, d
    if best_d is None or best_d > max_age_s:
        return None, best_d
    return best, best_d




# ----------------------------------------------------------------------------
# Стабільність множника cc (25 сер): 5-хв свічки -> сітка -> відношення
# ----------------------------------------------------------------------------

def fetch_5m_candles_paged(currency, days, chunk_days=CC_CHUNK_D_DEFAULT,
                           _fetcher=None, _now_ms=None):
    """
    ({"ticks": [ms], "close": [...]}, counters) — 5-хв свічки перпетуала
    за `days` діб, чанками по chunk_days.

    Власна реалізація, а не імпорт rv_intraday.fetch_5m_candles: там
    глибина зашита під VOLEDGE_RV5_DAYS і чанк неконфігурований, а тут
    треба 730 діб. High/Low не тягнемо — Parkinson у цій секції не
    потрібен, а це вдвічі менше пам'яті на 210 тис. барів.
    """
    if days < 1:
        raise ValueError(f"days={days} має бути >= 1")
    if chunk_days < 1:
        raise ValueError(f"chunk_days={chunk_days} має бути >= 1")
    inst = PERP_NAME[currency]
    get = _fetcher or http_get_json
    end_ms = int(_now_ms if _now_ms is not None else time.time() * 1000)
    start_ms = end_ms - int((days + 1) * 24 * 3600 * 1000)
    chunk_ms = int(chunk_days * 24 * 3600 * 1000)
    by_tick = {}
    c = {"chunks": 0, "bars_raw": 0, "bars_dup": 0, "bars_bad": 0}
    lo = start_ms
    while lo < end_ms:
        hi = min(lo + chunk_ms, end_ms)
        r = get(f"{API_BASE}/get_tradingview_chart_data?"
                f"instrument_name={inst}&start_timestamp={lo}"
                f"&end_timestamp={hi}&resolution=5")
        c["chunks"] += 1
        if c["chunks"] > CC_MAX_CHUNKS:
            raise RuntimeError(f"candles5m {inst}: >{CC_MAX_CHUNKS} чанків "
                               f"— схоже на нескінченний цикл")
        if r.get("status") != "ok":
            raise RuntimeError(f"candles5m {inst}: status={r.get('status')} "
                               f"на чанку [{lo},{hi}]")
        ticks = r.get("ticks") or []
        closes = r.get("close") or []
        if len(ticks) != len(closes):
            raise RuntimeError(f"candles5m {inst}: ticks {len(ticks)} != "
                               f"closes {len(closes)} у чанку [{lo},{hi}]")
        for t, cl in zip(ticks, closes):
            c["bars_raw"] += 1
            t = int(t)
            cl = float(cl)
            if not (cl > 0 and math.isfinite(cl)):
                c["bars_bad"] += 1
                continue
            if t in by_tick:
                c["bars_dup"] += 1
            by_tick[t] = cl
        lo = hi
    ticks_sorted = sorted(by_tick)
    c["bars_kept"] = len(ticks_sorted)
    if len(ticks_sorted) < 2:
        raise RuntimeError(f"candles5m {inst}: узято {len(ticks_sorted)} "
                           f"барів — рахувати нема чого")
    return ({"ticks": ticks_sorted,
             "close": [by_tick[t] for t in ticks_sorted]}, c)


def grid_daily_from_5m(bars, grid_min=CC_GRID_MIN_DEFAULT,
                       min_cover=CC_MIN_COVER_DEFAULT, _now_ms=None):
    """
    ({"days": [...], "var": [...], "close": [...]}, counters).

    Бар потрапляє в сітку, якщо його штамп кратний grid_min хвилинам.
    Добова варіація = сума квадратів лог-ретернів МІЖ СУСІДНІМИ вузлами
    сітки; ретерн належить добі свого правого кінця.

    КЛЮЧОВЕ ДЛЯ ЦІЄЇ СЕКЦІЇ: денні закриття для cc беруться З ЦИХ ЖЕ
    вузлів, а не з окремого 1D-ендпоінта. Інакше у відношення
    RV_cc/RV_grid заліз би ще й зсув сітки/джерела, і ми міряли б не
    множник, а розбіжність двох різних вибірок.
    """
    if grid_min < 5 or grid_min % 5 != 0:
        raise ValueError(f"grid_min={grid_min} має бути кратний 5 і >= 5")
    if not (0.0 < min_cover <= 1.0):
        raise ValueError(f"min_cover={min_cover} має бути в (0, 1]")
    step_ms = grid_min * 60 * 1000
    expected = (24 * 60) // grid_min
    ticks, closes = bars["ticks"], bars["close"]
    c = {"bars_in": len(ticks), "nodes": 0, "days_seen": 0,
         "days_full": 0, "days_incomplete": 0, "day_today_dropped": 0,
         "rets_used": 0, "expected_per_day": expected}
    nodes = [(t, p) for t, p in zip(ticks, closes) if t % step_ms == 0]
    c["nodes"] = len(nodes)
    if len(nodes) < 2:
        raise RuntimeError(f"grid_daily: вузлів {len(nodes)} при кроці "
                           f"{grid_min} хв — рахувати нема чого")
    acc = {}
    for i in range(1, len(nodes)):
        t0, p0 = nodes[i - 1]
        t1, p1 = nodes[i]
        day = datetime.fromtimestamp(t1 / 1000.0,
                                     tz=timezone.utc).strftime("%Y-%m-%d")
        a = acc.get(day)
        if a is None:
            a = {"var": 0.0, "n": 0, "close": p1}
            acc[day] = a
        r = math.log(p1 / p0)
        a["var"] += r * r
        a["n"] += 1
        a["close"] = p1
    now_ms = int(_now_ms if _now_ms is not None else time.time() * 1000)
    today = datetime.fromtimestamp(now_ms / 1000.0,
                                   tz=timezone.utc).strftime("%Y-%m-%d")
    out_d, out_v, out_c = [], [], []
    for day in sorted(acc):
        c["days_seen"] += 1
        if day == today:
            c["day_today_dropped"] += 1
            continue
        a = acc[day]
        if a["n"] < min_cover * expected:
            c["days_incomplete"] += 1
            continue
        c["days_full"] += 1
        c["rets_used"] += a["n"]
        out_d.append(day)
        out_v.append(a["var"])
        out_c.append(a["close"])
    if len(out_d) < RV_WINDOW_D + 2:
        raise RuntimeError(f"grid_daily: повних діб {len(out_d)} — менше "
                           f"вікна {RV_WINDOW_D}+2")
    return ({"days": out_d, "var": out_v, "close": out_c}, c)


def rv_trailing_grid(days, var, window=RV_WINDOW_D):
    """[(day, rv_ann)] — trailing RV із добових варіацій сітки."""
    if window < 2:
        raise ValueError(f"window={window} має бути >= 2")
    out = []
    for i in range(window - 1, len(days)):
        mv = sum(var[i - window + 1:i + 1]) / window
        out.append((days[i], math.sqrt(mv * ANNUALIZE)))
    return out


def rv_trailing_cc(days, closes, window=RV_WINDOW_D):
    """
    [(day, rv_ann)] — trailing close-to-close з ТИХ САМИХ денних
    закриттів, що й сітка. Перша оцінка доступна на window-й ретерн,
    тобто на індексі window (потрібен ще один попередній close).
    """
    if window < 2:
        raise ValueError(f"window={window} має бути >= 2")
    out = []
    for i in range(window, len(days)):
        acc = 0.0
        for j in range(i - window + 1, i + 1):
            acc += math.log(closes[j] / closes[j - 1]) ** 2
        out.append((days[i], math.sqrt(acc / window * ANNUALIZE)))
    return out


def count_day_gaps(days):
    """
    Скільки разів у списку YYYY-MM-DD сусідні дати не йдуть підряд.

    Потрібне, бо rv_trailing_* беруть ОСТАННІ window записів, а не
    останні window КАЛЕНДАРНИХ діб: при дірці вікно мовчки розтягується.
    Тихо це залишати не можна — лічильник і [FLAG] у друку.
    """
    n = 0
    for a, b in zip(days, days[1:]):
        da = datetime.strptime(a, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        db = datetime.strptime(b, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if (db - da).days != 1:
            n += 1
    return n


def spearman(xs, ys):
    """
    rho Спірмена, збіги отримують СЕРЕДНІЙ ранг. None, якщо точок < 3
    або один із рядів константний (rho невизначений, а не 0).
    """
    if len(xs) != len(ys):
        raise ValueError(f"довжини {len(xs)} != {len(ys)}")
    n = len(xs)
    if n < 3:
        return None

    def _ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for m in range(i, j + 1):
                rk[order[m]] = avg
            i = j + 1
        return rk

    rx, ry = _ranks(xs), _ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _quantile_sorted(xs, q):
    """Лінійна інтерполяція між порядковими статистиками."""
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def cc_ratio_panel(days, var, closes, window=RV_WINDOW_D):
    """
    ({...}, [(day, rv_cc, rv_grid, log_ratio, conc_grid)]) — ряд
    відношення і його статистики.

    conc_grid — частка найбільшої добової варіації СІТКИ у сумі вікна:
    проксі «одна доба домінує суму». ВАЖЛИВО (виправлено 26 сер):
    раніше conc рахувався з cc-квадратів, тобто ділив чисельник із
    самою величиною log(cc/grid), яку мав пояснювати, — будь-яка
    доба-викид піднімала обидва ряди й давала rho з нічого. Сітка
    незалежна від чисельника відношення. Другий проксі — рівень RV_grid
    (він теж із сітки, тож теж чистий).
    """
    grid = dict(rv_trailing_grid(days, var, window))
    ccs = dict(rv_trailing_cc(days, closes, window))
    idx = {d: i for i, d in enumerate(days)}
    rows = []
    n_drop_nocc = 0
    n_drop_zero = 0
    for d in days:
        g, cc = grid.get(d), ccs.get(d)
        if g is None or cc is None:
            n_drop_nocc += 1
            continue
        if g <= 0 or cc <= 0:
            # cc == 0 буває на ідеально поверненому вікні; log
            # невизначений, але ТИХО викидати не можна — це якраз
            # найнижчий край множника.
            n_drop_zero += 1
            continue
        i = idx[d]
        wv = var[i - window + 1:i + 1]
        tot = sum(wv)
        conc = (max(wv) / tot) if tot > 0 else None
        rows.append((d, cc, g, math.log(cc / g), conc))
    lr = sorted(r[3] for r in rows)
    stats = {"n": len(rows),
             "median_ratio": (math.exp(_quantile_sorted(lr, 0.5))
                              if lr else None),
             "median_log": _quantile_sorted(lr, 0.5),
             "q25_log": _quantile_sorted(lr, 0.25),
             "q75_log": _quantile_sorted(lr, 0.75),
             "min_ratio": (math.exp(lr[0]) if lr else None),
             "max_ratio": (math.exp(lr[-1]) if lr else None)}
    stats["iqr_log"] = (None if not lr
                        else stats["q75_log"] - stats["q25_log"])
    stats["frac_above_one"] = ((sum(1 for x in lr if x > 0.0) / len(lr))
                               if lr else None)
    stats["n_drop_no_pair"] = n_drop_nocc
    stats["n_drop_zero_rv"] = n_drop_zero
    stats["conc_source"] = "grid_var"
    stats["rho_conc_grid"] = spearman(
        [r[3] for r in rows if r[4] is not None],
        [r[4] for r in rows if r[4] is not None])
    stats["rho_level"] = spearman([r[3] for r in rows],
                                  [r[2] for r in rows])
    return stats, rows


def _gauss_stream(seed):
    """Box-Muller поверх LCG: детермінований, без random і без numpy."""
    x = seed & 0x7FFFFFFF
    while True:
        vals = []
        for _ in range(2):
            x = (1103515245 * x + 12345) % 2147483648
            vals.append(max(x / 2147483648.0, 1e-12))
        r = math.sqrt(-2.0 * math.log(vals[0]))
        th = 2.0 * math.pi * vals[1]
        yield r * math.cos(th)
        yield r * math.sin(th)


def cc_null_iqr(var, window=RV_WINDOW_D, reps=CC_NULL_REPS,
                seed=CC_NULL_SEED):
    """
    (median_iqr, [iqr...]) — розкид log(RV_cc/RV_grid) під нульовою
    моделлю «множник СТАЛИЙ, уся розбіжність — шум оцінювача».

    Конструкція: добова варіація сітки береться РЕАЛЬНА і не чіпається;
    добовий cc-ретерн симулюється як sqrt(var_d) * z, z ~ N(0,1), тобто
    без внутрішньоденної автокореляції взагалі. Тоді E[r_cc^2] = var_d
    точно — множник тотожно 1 в очікуванні, а весь спостережений розкид
    породжений тим, що cc має 30 доданків проти 1440.

    БЕЗ ЦЬОГО КОНТРОЛЮ вердикт про «сталість» вимірював би шум: на
    i.i.d. даних IQR уже ~0.12.
    """
    n = len(var)
    if n < window + 2 or reps < 1:
        return None, []
    grid_sq = []
    for i in range(window - 1, n):
        grid_sq.append(sum(var[i - window + 1:i + 1]) / window)
    g = _gauss_stream(seed)
    iqrs = []
    for _ in range(reps):
        sq = [var[i] * (next(g) ** 2) for i in range(n)]
        lr = []
        for pos, i in enumerate(range(window - 1, n)):
            cc = sum(sq[i - window + 1:i + 1]) / window
            gv = grid_sq[pos]
            if cc <= 0 or gv <= 0:
                continue
            lr.append(0.5 * math.log(cc / gv))
        if len(lr) < 4:
            continue
        lr.sort()
        iqrs.append(_quantile_sorted(lr, 0.75) - _quantile_sorted(lr, 0.25))
    if not iqrs:
        return None, []
    iqrs_sorted = sorted(iqrs)
    return _quantile_sorted(iqrs_sorted, 0.5), iqrs


def cc_multiplier_verdict(stats):
    """
    (code, text). Пороги зафіксовані у константах ДО заміру.
    """
    n = stats.get("n") or 0
    if n < RV_WINDOW_D * 2:
        return "LOW-N", (f"точок {n} < {RV_WINDOW_D * 2} — ряд відношення "
                         f"надто короткий, вердикту немає")
    iqr = stats.get("iqr_log")
    if iqr is None:
        return "LOW-N", "IQR не обчислився"
    null_iqr = stats.get("null_iqr_log")
    if null_iqr is None or null_iqr <= 0.0:
        return "LOW-N", ("нульова модель не порахувалась — без неї "
                         "абсолютний IQR міряє шум оцінювача, а не "
                         "множник")
    exc = iqr / null_iqr
    if "rho_conc" in stats:
        raise ValueError("stats містить застарілий ключ rho_conc "
                         "(проксі з cc-квадратів); очікується "
                         "rho_conc_grid")
    rhos = [abs(r) for r in (stats.get("rho_conc_grid"),
                             stats.get("rho_level")) if r is not None]
    rho_max = max(rhos) if rhos else 0.0
    if exc < CC_EXCESS_STABLE and rho_max < CC_SPEARMAN_STRONG:
        return "STABLE", (
            f"надлишок IQR над нульовою моделлю {exc:.2f}x < "
            f"{CC_EXCESS_STABLE}x і |rho| {rho_max:.2f} — розкид "
            f"пояснюється шумом оцінювача, множник сталий, стара теза "
            f"була б справедлива")
    if exc >= CC_EXCESS_REGIME or rho_max >= CC_SPEARMAN_STRONG:
        return "REGIME-DEPENDENT", (
            f"надлишок {exc:.2f}x (поріг {CC_EXCESS_REGIME}x), |rho| до "
            f"{rho_max:.2f} (поріг {CC_SPEARMAN_STRONG}) — розкид більший, "
            f"ніж дає шум; множник ходить із режимом, теза «постійний "
            f"множник не впливає на форму» мертва")
    return "GREY", (
        f"надлишок {exc:.2f}x між {CC_EXCESS_STABLE}x і "
        f"{CC_EXCESS_REGIME}x, |rho| {rho_max:.2f} — проміжна смуга, "
        f"однозначної відповіді немає")


def baseline_shift_verdict(sc_cc, sc_grid):
    """
    (code, text) — чи вистоює висновок «дрейф трапляється у ~15% вікон»
    при заміні знаменника на 30-хв сітку.

    ЦЕ І Є РІШЕННЯ секції: пряме порівняння базових ліній, без
    міркування через множник.
    """
    if sc_grid is None:
        return "LOW-N", "сканування на сітці не відбулося"
    nw = sc_grid.get("n_windows", 0)
    if nw < CC_MIN_WINDOWS:
        return "LOW-N", (f"вікон на сітці {nw} < {CC_MIN_WINDOWS} — "
                         f"глибина 5-хв історії не дає вердикту")
    fg = sc_grid["frac_ge_observed"]
    fc = sc_cc["frac_ge_observed"] if sc_cc else None
    tail = "" if fc is None else f" (на cc було {fc * 100:.1f}%)"
    if fg <= CC_BASE_FAIL:
        return "CONCLUSION-FALLS", (
            f"базова лінія на сітці {fg * 100:.1f}% <= "
            f"{CC_BASE_FAIL * 100:.0f}%{tail} — дрейф РІДКІСНИЙ, "
            f"висновок «не сигнал» більше не тримається")
    if CC_BASE_LO <= fg <= CC_BASE_HI:
        return "CONCLUSION-HOLDS", (
            f"базова лінія на сітці {fg * 100:.1f}% у смузі "
            f"{CC_BASE_LO * 100:.0f}-{CC_BASE_HI * 100:.0f}%{tail} — "
            f"висновок «дрейф не рідкість» стоїть")
    # НАПРЯМКОВІ гілки (виправлено 26 сер). Раніше обидва боки смуги
    # злипались в один ярлик, і BTC із 6.4% (НИЖЧЕ смуги, майже на
    # порозі відмови) друкувався як «дрейф і поготів не рідкість» —
    # тобто рівно навпаки до даних.
    if fg < CC_BASE_LO:
        return "CONCLUSION-WEAKER", (
            f"базова лінія на сітці {fg * 100:.1f}% НИЖЧЕ смуги "
            f"{CC_BASE_LO * 100:.0f}-{CC_BASE_HI * 100:.0f}%{tail}, але "
            f"вище порогу відмови {CC_BASE_FAIL * 100:.0f}% — дрейф на "
            f"правильному знаменнику РІДШИЙ, ніж здавалось на cc; "
            f"висновок «не сигнал» тримається, але запас малий")
    return "CONCLUSION-HOLDS-WIDER", (
        f"базова лінія на сітці {fg * 100:.1f}% ВИЩЕ смуги "
        f"{CC_BASE_LO * 100:.0f}-{CC_BASE_HI * 100:.0f}%{tail} — дрейф і "
        f"поготів не рідкість")


# ----------------------------------------------------------------------------
# Побудова вирівняних рядів на наших якорях
# ----------------------------------------------------------------------------

def _load_calib_cache():
    """Кеш фіч калібратора, якщо є. READ-ONLY, ніколи не пишемо."""
    if not os.path.exists(CALIB_CACHE):
        return {}, {"cache_file": 0}
    try:
        with open(CALIB_CACHE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"[SKIP] кеш калібратора нечитабельний ({e}) — "
              f"рахуємо фічі заново")
        return {}, {"cache_file": 0}
    ent = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(ent, dict):
        ent = raw if isinstance(raw, dict) else {}
    return ent, {"cache_file": 1}


def build_anchor_series(chain_pairs, rv_pairs, dvol_by_cur, vrp_age_h,
                        dvol_max_age_s):
    """
    ({cur: {name: [(dt, val)]}}, counters).

    П'ять рядів на валюту: atm30, dvol, rv30, vrp_ours, vrp_dvol.
    Якір — час chain-знімка. Точки старої сітки відкидаються явно:
    вони зібрані до фіксу CRON_TZ і змішувати їх із канонічними в
    аналізі форми не можна.
    """
    cache, ccnt = _load_calib_cache()
    counters = {"snapshots": 0, "legacy_grid": 0, "off_grid": 0,
                "cache_hit": 0, "cache_miss": 0, "read_fail": 0,
                "dvol_too_far": 0, "no_atm30": 0, "no_rv30": 0}
    counters.update(ccnt)
    series = {cur: {k: [] for k in ("atm30", "dvol", "rv30",
                                    "vrp_ours", "vrp_dvol")}
              for cur in CURRENCIES}

    for st, path in chain_pairs:
        counters["snapshots"] += 1
        if st.hour in LEGACY_SLOT_HOURS:
            counters["legacy_grid"] += 1
            continue
        if st.hour not in CANONICAL_SLOT_HOURS:
            counters["off_grid"] += 1
            continue
        key = os.path.basename(path)
        feats = cache.get(key)
        if feats is not None:
            counters["cache_hit"] += 1
        else:
            try:
                data = _load_json(path)
                feats, _sk, _q = features_of_snapshot(data)
                counters["cache_miss"] += 1
            except Exception as e:
                counters["read_fail"] += 1
                print(f"[SKIP] знімок {key}: {e}")
                continue

        atm30_by_cur = {}
        for cur in CURRENCIES:
            v = feats.get(f"{cur}.atm30")
            if v is not None:
                atm30_by_cur[cur] = v
        try:
            vf, _vs = vrp_from_rv(rv_pairs, st, vrp_age_h, atm30_by_cur)
        except TypeError as e:
            raise RuntimeError(
                f"[FAIL] контракт vrp_from_rv змінився: {e}") from e

        for cur in CURRENCIES:
            atm30 = atm30_by_cur.get(cur)
            if atm30 is None or atm30 <= 0:
                counters["no_atm30"] += 1
                continue
            vrp_ours = vf.get(f"{cur}.vrp30")
            if vrp_ours is None or vrp_ours <= 0:
                counters["no_rv30"] += 1
                continue
            # Знаменник відновлюється з нашої ж пари — жодного
            # повторного читання артефакту, жодного розходження.
            rv30 = atm30 / vrp_ours
            dv, _d = align_nearest(dvol_by_cur.get(cur, []), st,
                                   dvol_max_age_s)
            if dv is None or dv <= 0:
                counters["dvol_too_far"] += 1
                counters[f"dvol_too_far_{cur}"] = counters.get(
                    f"dvol_too_far_{cur}", 0) + 1
                continue
            s = series[cur]
            s["atm30"].append((st, atm30))
            s["dvol"].append((st, dv))
            s["rv30"].append((st, rv30))
            s["vrp_ours"].append((st, vrp_ours))
            s["vrp_dvol"].append((st, dv / rv30))
            counters[f"kept_{cur}"] = counters.get(f"kept_{cur}", 0) + 1

    # НЕ ЛІЧИЛЬНИК, А ВІДМОВА: якщо через брак DVOL відпала більша
    # частка якорів, ряд не «трохи дірявий» — вирівнювання зламане, і
    # будь-який вердикт далі буде порахований на випадковому хвості.
    for cur in CURRENCIES:
        kept = counters.get(f"kept_{cur}", 0)
        drop = counters.get(f"dvol_too_far_{cur}", 0)
        tot = kept + drop
        if tot and (drop / tot) > DVOL_MAX_DROP_FRAC:
            raise RuntimeError(
                f"[FAIL] {cur}: через брак DVOL відпало {drop} з {tot} "
                f"якорів ({drop / tot:.0%} > "
                f"{DVOL_MAX_DROP_FRAC:.0%}). Вирівнювання зламане — "
                f"перевірте resolution (СЕКУНДИ) і сторінкування.")
    return series, counters


def decompose(series_by_cur, min_history, k):
    """{cur: {name: {n_low, n_high, up_frac, p_binom_approx, ...}}}"""
    out = {}
    for cur in sorted(series_by_cur):
        rows = {}
        for name in ("atm30", "dvol", "rv30", "vrp_ours", "vrp_dvol"):
            pts = series_by_cur[cur][name]
            vals = [v for _s, v in pts]
            lo, hi, ns = rank_asymmetry(vals, min_history, k)
            rows[name] = {
                "n": len(vals), "n_scored": ns,
                "n_low": lo, "n_high": hi,
                "up_frac": asymmetry_score(lo, hi),
                "p_binom_approx": binomial_tail_two_sided(lo, hi),
                "low_events": (lo + hi) < ASYM_MIN_EVENTS,
                "first": vals[0] if vals else None,
                "last": vals[-1] if vals else None,
                "median": _median(vals)}
            rows[name]["drift_label"] = drift_label(rows[name])
        out[cur] = rows
    return out


def drift_label(row):
    """
    "UP" | "DOWN" | "UP?" | "DOWN?" | "NONE" | "LOW-N" — форма ряду.

    Знак «?» = проміжна смуга: однобічність помітна, але не різка.
    Такі ряди НЕ мовчки зараховуються до одного з боків — вердикт, що
    на них спирається, позначається як попередній.
    """
    if row["n_scored"] < MIN_SCORED_FOR_DRIFT:
        return "LOW-N"
    uf = row["up_frac"]
    if uf is None:
        return "NONE"
    if (row["n_low"] + row["n_high"]) < ASYM_MIN_EVENTS:
        return "NONE"
    dev = uf - 0.5
    side = "UP" if dev > 0 else "DOWN"
    if abs(dev) >= DRIFT_STRONG_DEV:
        return side
    if abs(dev) >= DRIFT_MILD_DEV:
        return side + "?"
    return "NONE"


def verdict_from_rows(rows):
    """
    (код, текст) — механічне читання таблиці декомпозиції.

    Ніяких зважувань «на око»: правила ті самі, що в шапці модуля,
    і вони записані ДО того, як побачені числа.
    """
    labels = {n: drift_label(rows[n])
              for n in ("atm30", "dvol", "rv30")}
    if any(v == "LOW-N" for v in labels.values()):
        return "LOW-N", (f"оцінених точок < {MIN_SCORED_FOR_DRIFT} хоча "
                         f"б в одному ряді — рекорди не встигли "
                         f"накопичитись, декомпозиція не читається")

    def _dir(lbl):
        if lbl.startswith("UP"):
            return 1
        if lbl.startswith("DOWN"):
            return -1
        return 0

    def _strong(lbl):
        return lbl in ("UP", "DOWN")

    d_atm, d_dv, d_rv = (_dir(labels["atm30"]), _dir(labels["dvol"]),
                         _dir(labels["rv30"]))
    tentative = any(v.endswith("?") for v in labels.values())
    suffix = ""
    if tentative:
        mild = ", ".join(f"{n}={v}" for n, v in sorted(labels.items())
                         if v.endswith("?"))
        suffix = (f" [ПОПЕРЕДНЬО: проміжна смуга у {mild} — "
                  f"вердикт тримається на межі]")

    def _r(code, txt):
        return (code + ("?" if tentative else ""), txt + suffix)

    # РОЗБІЖНІСТЬ ЧИСЕЛЬНИКІВ перевіряється ПЕРШОЮ і ЗА НАПРЯМКОМ.
    # Помилка 23 сер: правило «atm30 дрейфує, dvol ні» було
    # безнапрямковим і оголосило OUR-FIT для BTC, де обидва йшли ВНИЗ,
    # просто з різною силою. Розбіжність — це протилежні знаки або
    # різкий дрейф одного проти повної плоскості іншого.
    clash = (d_atm * d_dv == -1
             or (_strong(labels["atm30"]) and d_dv == 0)
             or (_strong(labels["dvol"]) and d_atm == 0))
    if clash and _strong(labels["atm30"]) and not _strong(labels["dvol"]):
        return _r("OUR-FIT", "наш atm30 дрейфує різко, незалежний DVOL "
                             "ні або в інший бік: підозра на наш фіт")
    if clash and _strong(labels["dvol"]) and not _strong(labels["atm30"]):
        return _r("ODD", "DVOL дрейфує різко, наш atm30 ні або в інший "
                         "бік: розбіжність методик, розбирати окремо")
    if clash:
        return _r("CLASH", "чисельники дрейфують у протилежні боки — "
                           "порівнювати ratio без цього не можна")

    # Далі — атрибуція дрейфу ratio = чисельник / знаменник.
    # Вгору його штовхає зростання чисельника АБО спад знаменника.
    num_up = d_atm > 0 or d_dv > 0
    den_down = d_rv < 0
    if num_up and d_rv > 0:
        return _r("CO-MOVE", "чисельники І знаменник ростуть разом — "
                             "дрейф ratio не пояснюється складниками, "
                             "вони компенсуються")
    if num_up and not den_down:
        return _r("REAL", "чисельники ростуть, знаменник ні — "
                          "IV справді дорожчає відносно RV")
    if den_down and not num_up:
        return _r("DENOM", "чисельники не ростуть, знаменник спадає — "
                           "дрейф ratio механічний")
    if num_up and den_down:
        return _r("REGIME", "чисельники ростуть І знаменник спадає — "
                            "режимний зсув; силу висновку дає лише "
                            "довга історія")
    return _r("FLAT", "жоден зі складників не дрейфує так, щоб "
                      "пояснити дрейф ratio")


# ----------------------------------------------------------------------------
# Друк
# ----------------------------------------------------------------------------

def print_decomposition(dec, min_history, k):
    print("\n" + "=" * 78)
    print("ДЕКОМПОЗИЦІЯ ОДНОСТОРОННЬОГО ДРЕЙФУ (ранги на РІВНЯХ)")
    print("=" * 78)
    print(f"  Подія = точка входить у K={k} екстремумів історії "
          f"(min_history={min_history}).")
    print(f"  Умова та сама, що в детекторі, тож числа порівнювані зі "
          f"звітом калібрування.")
    print(f"  p_binom — НАБЛИЖЕННЯ (рекорди залежні); рішення дає "
          f"емпіричне сканування.")
    print(f"  форма: UP/DOWN різко, UP?/DOWN? проміжна смуга, "
          f"NONE без однобічності.")
    print(f"  УВАГА: {BASELINE_NOTE}.")
    for cur in sorted(dec):
        rows = dec[cur]
        print(f"\n  {cur}")
        print(f"    {'ряд':<12}{'n':>5}{'низ':>6}{'верх':>6}"
              f"{'верх%':>8}{'форма':>8}{'p~':>10}"
              f"{'перша':>11}{'остання':>11}")
        for name in ("atm30", "dvol", "rv30", "vrp_ours", "vrp_dvol"):
            r = rows[name]
            uf = r["up_frac"]
            if r["n_scored"] < MIN_SCORED_FOR_DRIFT:
                mark = "  [LOW-N: мало оцінених точок]"
            elif r["low_events"]:
                mark = "  [мало подій = не дрейфує]"
            else:
                mark = ""
            print(f"    {name:<12}{r['n']:>5}{r['n_low']:>6}"
                  f"{r['n_high']:>6}"
                  f"{('—' if uf is None else f'{uf * 100:.0f}%'):>8}"
                  f"{drift_label(r):>8}"
                  f"{_f(r['p_binom_approx'], 4):>10}"
                  f"{_f(r['first'], 4):>11}{_f(r['last'], 4):>11}{mark}")
        code, txt = verdict_from_rows(rows)
        print(f"    [ДЕКОМПОЗИЦІЯ {code}] {txt}")


def print_history(hist):
    if not hist:
        return
    print("\n" + "=" * 78)
    print("ДОВГА ІСТОРІЯ DVOL/RV30_cc: наскільки рідкісний такий дрейф")
    print("=" * 78)
    print("  РІВЕНЬ НЕ ПОРІВНЮВАНИЙ з нашим vrp30: знаменник тут "
          "close-to-close")
    print("  по добових свічках. УВАГА (25 сер): теза «cc = постійний "
          "множник,")
    print("  тож ФОРМА порівнювана» ВІДКЛИКАНА — множник ходить із "
          "режимом.")
    print("  Чи вистоює базова лінія — див. секцію «Множник cc» нижче.")
    for cur in sorted(hist):
        h = hist[cur]
        print(f"\n  {cur}")
        if h.get("status") != "OK":
            print(f"    [SKIP] {h.get('reason')}")
            continue
        print(f"    добових точок: {h['n_points']}, "
              f"вікно сканування: {h['scan']['win']} діб")
        sc = h["scan"]
        print(f"    вікон оцінено: {sc['n_windows']} "
              f"(пропущено з <{ASYM_MIN_EVENTS} подій: "
              f"{sc['n_skipped_low_events']}); вікна перекриваються")
        print(f"    медіанна однобічність по історії: "
              f"{sc['median_up_frac'] * 100:.0f}%")
        print(f"    спостережено зараз: {sc['obs_up_frac'] * 100:.0f}% "
              f"на {sc['obs_events']} подіях")
        print(f"    вікон із не слабшою однобічністю: "
              f"{sc['n_ge_observed']}/{sc['n_windows']} = "
              f"{sc['frac_ge_observed'] * 100:.1f}%")
        if sc["frac_ge_observed"] <= 0.05:
            print("    [FLAG] такий дрейф рідкісний у власній історії "
                  "інструмента — на користь РЕАЛЬНОСТІ сигналу.")
        else:
            print("    [OK] такий дрейф трапляється регулярно — сам по "
                  "собі він НЕ рідкість і сигналом не є.")


def print_cc_ratio(cc):
    print("\n" + "=" * 78)
    print("МНОЖНИК cc: чи сталий RV30_cc / RV30_grid (25 сер)")
    print("=" * 78)
    print("  Обидві оцінки — з ОДНИХ 5-хв барів і тих самих денних "
          "закриттів,")
    print("  тож відношення міряє саме множник, а не розбіжність "
          "джерел.")
    print("  Судимо НАДЛИШОК над нульовою моделлю "
          "(множник сталий, розкид = шум оцінювача):")
    print(f"  < {CC_EXCESS_STABLE}x сталий, >= {CC_EXCESS_REGIME}x режимний; "
          f"|rho| >= {CC_SPEARMAN_STRONG} -> режимний;")
    print(f"  вікон у перепрогоні < {CC_MIN_WINDOWS} -> [LOW-N]. "
          f"Пороги зафіксовані до заміру.")
    for cur in sorted(cc):
        h = cc[cur]
        print(f"\n  {cur}")
        if h.get("status") != "OK":
            print(f"    [SKIP] {h.get('reason')}")
            continue
        s = h["stats"]
        print(f"    добових точок сітки: {h['n_days']}, "
              f"крок {h['grid_min']} хв, точок відношення: {s['n']}, "
              f"дірок у ряді: {h.get('day_gaps', 0)}")
        print(f"    відкинуто: без пари {s.get('n_drop_no_pair', 0)}, "
              f"нульова оцінка {s.get('n_drop_zero_rv', 0)}")
        print(f"    медіана cc/grid: {_f(s['median_ratio'], 3)}  "
              f"(min {_f(s['min_ratio'], 3)}, max {_f(s['max_ratio'], 3)})")
        print(f"    частка точок з cc > grid: "
              f"{(s['frac_above_one'] or 0.0) * 100:.0f}%  "
              f"— знак множника {'МІНЯЄТЬСЯ' if 0.05 < (s['frac_above_one'] or 0) < 0.95 else 'сталий'}")
        print(f"    IQR log-відношення: {_f(s['iqr_log'], 3)}  "
              f"(q25 {_f(s['q25_log'], 3)}, q75 {_f(s['q75_log'], 3)})")
        print(f"    нульова модель ({s.get('null_reps')} реплікацій): "
              f"IQR {_f(s.get('null_iqr_log'), 3)}, надлишок "
              f"{_f(s.get('excess_iqr'), 2)}x")
        print(f"    rho з концентрацією однієї доби "
              f"(проксі з {s.get('conc_source', '?')}): "
              f"{_f(s.get('rho_conc_grid'), 2)}; rho з рівнем RV: "
              f"{_f(s['rho_level'], 2)}")
        code, txt = h["verdict"]
        print(f"    [МНОЖНИК {code}] {txt}")
        sg = h.get("scan_grid")
        if sg is None:
            print(f"    [SKIP] перепрогін сканування: {h.get('scan_why')}")
        else:
            print(f"    перепрогін сканування на 30-хв знаменнику: "
                  f"вікон {sg['n_windows']}, "
                  f"медіанна однобічність "
                  f"{sg['median_up_frac'] * 100:.0f}%, "
                  f"не слабших {sg['n_ge_observed']}/{sg['n_windows']} = "
                  f"{sg['frac_ge_observed'] * 100:.1f}%")
        bcode, btxt = h["baseline"]
        print(f"    [БАЗОВА ЛІНІЯ {bcode}] {btxt}")


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


def _lcg(seed):
    x = seed & 0x7FFFFFFF
    while True:
        x = (1103515245 * x + 12345) % 2147483648
        yield x / 2147483648.0


def run_self_tests():
    tc = TestCounters()
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)

    # --- rank_asymmetry ---
    up = [float(i) for i in range(40)]
    lo, hi, ns = rank_asymmetry(up, 12, 1)
    tc.check("rank_asym_monotone_up_is_all_high",
             lo == 0 and hi == ns and ns == 28, f"{lo}/{hi}/{ns}")
    dn = [-float(i) for i in range(40)]
    lo, hi, ns = rank_asymmetry(dn, 12, 1)
    tc.check("rank_asym_monotone_down_is_all_low",
             hi == 0 and lo == ns, f"{lo}/{hi}/{ns}")
    tc.check("rank_asym_k_zero_refuses",
             _raises(lambda: rank_asymmetry(up, 12, 0), ValueError), "")
    rng = _lcg(11)
    noise = [next(rng) for _ in range(400)]
    lo, hi, ns = rank_asymmetry(noise, 12, 1)
    tc.check("rank_asym_noise_is_balanced",
             0.25 < (hi / (lo + hi)) < 0.75, f"{lo} низ / {hi} верх")
    tc.check("asymmetry_score_none_on_zero_events",
             asymmetry_score(0, 0) is None, "")
    tc.check("asymmetry_score_all_high",
             asymmetry_score(0, 7) == 1.0, "")

    # --- біноміальне наближення ---
    tc.check("binom_zero_events_none",
             binomial_tail_two_sided(0, 0) is None, "")
    tc.check("binom_symmetric_is_one",
             abs(binomial_tail_two_sided(5, 5) - 1.0) < 1e-9,
             _f(binomial_tail_two_sided(5, 5), 6))
    tc.check("binom_extreme_is_tiny",
             binomial_tail_two_sided(0, 25) < 1e-6,
             _f(binomial_tail_two_sided(0, 25), 10))
    tc.check("binom_monotone_in_asymmetry",
             binomial_tail_two_sided(0, 20)
             < binomial_tail_two_sided(5, 15), "")

    # --- rv30_cc ---
    cl = [(t0 + timedelta(days=i), 100.0 * (1.01 ** i))
          for i in range(60)]
    rv = rv30_cc_series(cl, 30)
    tc.check("rv30_cc_length",
             len(rv) == len(cl) - 30, f"{len(rv)} vs {len(cl) - 30}")
    exp = math.sqrt(math.log(1.01) ** 2 * ANNUALIZE)
    tc.check("rv30_cc_constant_growth_value",
             abs(rv[0][1] - exp) < 1e-9, f"{rv[0][1]:.6f} vs {exp:.6f}")
    tc.check("rv30_cc_window_refuses",
             _raises(lambda: rv30_cc_series(cl, 1), ValueError), "")
    cl_bad = list(cl)
    cl_bad[10] = (cl_bad[10][0], 0.0)
    tc.check("rv30_cc_nonpositive_price_dropped_not_nan",
             all(math.isfinite(v) for _s, v in rv30_cc_series(cl_bad, 30)),
             "")

    # --- align_nearest ---
    ser = [(t0 + timedelta(hours=i), float(i)) for i in range(10)]
    v, d = align_nearest(ser, t0 + timedelta(hours=3, minutes=10), 3600)
    tc.check("align_picks_nearest", v == 3.0 and abs(d - 600) < 1,
             f"v={v} d={d}")
    v, d = align_nearest(ser, t0 + timedelta(hours=3, minutes=50), 3600)
    tc.check("align_picks_nearest_from_future", v == 4.0, f"v={v}")
    v, d = align_nearest(ser, t0 + timedelta(days=5), 3600)
    tc.check("align_refuses_when_too_far", v is None and d is not None,
             f"v={v} d={d}")
    v, d = align_nearest([], t0, 3600)
    tc.check("align_empty_series_refuses", v is None and d is None, "")

    # --- декомпозиція: три сценарії, побудовані навмисно ---
    def mkser(atm, dvol, rv):
        st = [t0 + timedelta(hours=4 * i) for i in range(len(atm))]
        return {"atm30": list(zip(st, atm)), "dvol": list(zip(st, dvol)),
                "rv30": list(zip(st, rv)),
                "vrp_ours": list(zip(st, [a / r for a, r
                                          in zip(atm, rv)])),
                "vrp_dvol": list(zip(st, [d / r for d, r
                                          in zip(dvol, rv)]))}

    n = 60
    rng = _lcg(5)
    jit = [0.001 * (next(rng) - 0.5) for _ in range(n)]
    flat_rv = [0.30 + j for j in jit]
    rng2 = _lcg(9)
    jit2 = [0.001 * (next(rng2) - 0.5) for _ in range(n)]
    rise = [0.30 + 0.002 * i + j for i, j in enumerate(jit2)]

    dec = decompose({"X": mkser(rise, rise, flat_rv)}, 12, 1)
    code, _t = verdict_from_rows(dec["X"])
    tc.check("decompose_real_signal", code == "REAL", code)

    fall_rv = [0.40 - 0.002 * i + j for i, j in enumerate(jit2)]
    dec = decompose({"X": mkser(flat_rv, flat_rv, fall_rv)}, 12, 1)
    code, _t = verdict_from_rows(dec["X"])
    tc.check("decompose_denominator_artifact", code == "DENOM", code)

    dec = decompose({"X": mkser(rise, flat_rv, flat_rv)}, 12, 1)
    code, _t = verdict_from_rows(dec["X"])
    tc.check("decompose_our_fit_suspect", code == "OUR-FIT", code)

    dec = decompose({"X": mkser(flat_rv, flat_rv, flat_rv)}, 12, 1)
    code, _t = verdict_from_rows(dec["X"])
    tc.check("decompose_flat_when_nothing_drifts", code == "FLAT", code)

    # Усе росте разом -> складники компенсуються, а не «режим».
    # Старе очікування REGIME було безнапрямковим і тому хибним.
    dec = decompose({"X": mkser(rise, rise, rise)}, 12, 1)
    code, _t = verdict_from_rows(dec["X"])
    tc.check("decompose_comove_when_all_rise",
             code.startswith("CO-MOVE"), code)

    short = mkser(rise[:16], rise[:16], flat_rv[:16])
    dec = decompose({"X": short}, 12, 1)
    code, _t = verdict_from_rows(dec["X"])
    tc.check("decompose_lown_blocks_verdict", code == "LOW-N", code)

    # НЕГАТИВНИЙ КОНТРОЛЬ ДЛЯ САМОЇ ДЕКОМПОЗИЦІЇ: якщо вона на чистому
    # шумі оголошує дрейф, усі її вердикти нічого не варті.
    rngn = _lcg(77)
    pure = [0.30 + 0.01 * (next(rngn) - 0.5) for _ in range(n)]
    rngn2 = _lcg(78)
    pure2 = [0.30 + 0.01 * (next(rngn2) - 0.5) for _ in range(n)]
    rngn3 = _lcg(79)
    pure3 = [0.30 + 0.01 * (next(rngn3) - 0.5) for _ in range(n)]
    dec = decompose({"X": mkser(pure, pure2, pure3)}, 12, 1)
    code, _t = verdict_from_rows(dec["X"])
    tc.check("decompose_noise_is_not_a_drift",
             code in ("FLAT", "LOW-N"), code)

    # --- емпіричне сканування ---
    trend = [float(i) + 0.3 * next(_lcg(3)) for i in range(400)]
    res, why = empirical_window_scan(trend, 60, 12, 1, 1.0, 25)
    tc.check("scan_trend_finds_many_similar_windows",
             res is not None and res["frac_ge_observed"] > 0.5,
             why if res is None else _f(res["frac_ge_observed"], 3))
    rngs = _lcg(21)
    flat = [next(rngs) for _ in range(900)]
    # Коротке вікно на плоскому ряді НЕ набирає подій — і це правильна
    # відмова, а не збій: рекорди породжує дрейф, якого тут немає.
    res, why = empirical_window_scan(flat, 60, 12, 1, 1.0, 25)
    tc.check("scan_flat_short_window_refuses_low_events",
             res is None and "подій" in str(why), str(why))
    # На довгому вікні події накопичуються, і повна однобічність
    # виявляється рідкістю — саме це і має показувати сканування.
    res, why = empirical_window_scan(flat, 400, 12, 1, 1.0, 25)
    tc.check("scan_flat_series_rarely_fully_onesided",
             res is not None and res["frac_ge_observed"] < 0.10,
             why if res is None else _f(res["frac_ge_observed"], 3))
    res, why = empirical_window_scan(flat, 10, 12, 1, 1.0, 25)
    tc.check("scan_refuses_window_below_min_history",
             res is None, str(why))
    res, why = empirical_window_scan(flat[:20], 60, 12, 1, 1.0, 25)
    tc.check("scan_refuses_short_history", res is None, str(why))
    res, why = empirical_window_scan(flat, 60, 12, 1, None, 0)
    tc.check("scan_refuses_when_no_observed_events", res is None,
             str(why))

    # --- контракти імпортованих функцій ---
    import inspect
    sig = list(inspect.signature(vrp_from_rv).parameters)
    tc.check("vrp_from_rv_arity_is_4", len(sig) == 4, str(sig))

    # --- DVOL: парсинг, сторінкування, покриття -------------------------
    # РЕГРЕСІЙНІ ТЕСТИ НА БАГ 23 сер: resolution у СЕКУНДАХ, і ендпоінт
    # мовчки віддає лише хвіст діапазону при перевищенні ліміту рядків.
    tc.check("dvol_res_hour_is_seconds", DVOL_RES_HOUR == "3600",
             DVOL_RES_HOUR)
    tc.check("dvol_res_day_is_seconds", DVOL_RES_DAY == "86400",
             DVOL_RES_DAY)

    _rows = [[1000 + 3600000 * i, 1, 2, 3, 40.0 + i] for i in range(5)]
    _c = {}
    _p = parse_dvol_rows(_rows, _c)
    tc.check("dvol_parse_converts_percent_to_frac",
             abs(_p[0][1] - 0.40) < 1e-12, _f(_p[0][1], 4))
    tc.check("dvol_parse_counts_all", _c["kept"] == 5
             and _c["rows"] == 5, str(_c))
    _c2 = {}
    parse_dvol_rows([[1, 2], "junk", [1000, 1, 2, 3, "x"]], _c2)
    tc.check("dvol_parse_bad_rows_counted",
             _c2["bad_row"] == 3 and _c2["kept"] == 0, str(_c2))
    _c3 = {}
    parse_dvol_rows(_rows + _rows, _c3)
    tc.check("dvol_parse_dedupes_stamps",
             _c3["kept"] == 5 and _c3["dup_stamp"] == 5, str(_c3))

    # Фальшивий ендпоінт: віддає максимум DVOL_ROWS_CAP рядків і лише
    # ХВІСТ запитаного вікна — рівно так, як повівся справжній.
    step_ms = 3600 * 1000

    def _fake(url):
        import urllib.parse as _up
        q = _up.parse_qs(_up.urlparse(url).query)
        lo = int(q["start_timestamp"][0])
        hi = int(q["end_timestamp"][0])
        res_ms = int(q["resolution"][0]) * 1000
        stamps = list(range(lo - lo % res_ms + res_ms, hi + 1, res_ms))
        stamps = stamps[-DVOL_ROWS_CAP:]
        return {"data": [[t, 1, 2, 3, 40.0] for t in stamps]}

    _end = 2000 * step_ms
    _start = 0
    _ser, _cc = fetch_dvol_series("BTC", _start, _end, "3600",
                                  _fetcher=_fake)
    tc.check("dvol_pagination_covers_full_range",
             len(_ser) >= 1900 and _cc["pages"] >= 2,
             f"n={len(_ser)} pages={_cc['pages']}")
    tc.check("dvol_pagination_flags_cap_hit", _cc["hit_cap"] >= 1,
             str(_cc["hit_cap"]))
    tc.check("dvol_pagination_sorted_ascending",
             all(_ser[i][0] <= _ser[i + 1][0]
                 for i in range(len(_ser) - 1)), "")
    tc.check("dvol_bad_resolution_refuses",
             _raises(lambda: fetch_dvol_series("BTC", 0, 10, "0",
                                               _fetcher=_fake),
                     ValueError), "")
    tc.check("dvol_reversed_range_refuses",
             _raises(lambda: fetch_dvol_series("BTC", 10, 0, "3600",
                                               _fetcher=_fake),
                     ValueError), "")

    # Покриття: короткий ряд проти широкого діапазону якорів = ВІДМОВА,
    # а не тихе відкидання якорів у align_nearest.
    _short = [(t0 + timedelta(hours=i), 0.4) for i in range(16)]
    tc.check("coverage_refuses_when_series_too_short",
             _raises(lambda: check_dvol_coverage(
                 _short, t0 - timedelta(days=20),
                 t0 + timedelta(hours=15), "BTC", 5400),
                 RuntimeError), "")
    tc.check("coverage_refuses_when_series_ends_early",
             _raises(lambda: check_dvol_coverage(
                 _short, t0, t0 + timedelta(days=20), "BTC", 5400),
                 RuntimeError), "")
    _cov = check_dvol_coverage(_short, t0 + timedelta(hours=1),
                               t0 + timedelta(hours=14), "BTC", 5400)
    tc.check("coverage_ok_when_range_inside", _cov["n"] == 16,
             str(_cov["n"]))
    tc.check("coverage_empty_series_refuses",
             _raises(lambda: check_dvol_coverage([], t0, t0, "BTC", 60),
                     RuntimeError), "")
    tc.check("drop_frac_threshold_is_tight",
             0.0 < DVOL_MAX_DROP_FRAC <= 0.25,
             str(DVOL_MAX_DROP_FRAC))

    # --- РЕГРЕСІЯ НА ПОМИЛКУ КЛАСИФІКАТОРА 23 сер ----------------------
    # Старий жорсткий поріг 0.9/0.1 відніс ETH.rv30 (56 низ / 12 верх =
    # 18% верх) до «не дрейфує» і видав FLAT, хоча очима це виражений
    # спадний дрейф. Смуги мають ловити такі випадки як проміжні.
    def _row(lo, hi, n_scored=141):
        return {"n_scored": n_scored, "n_low": lo, "n_high": hi,
                "up_frac": asymmetry_score(lo, hi)}

    tc.check("label_eth_rv30_18pct_is_not_none",
             drift_label(_row(56, 12)) == "DOWN?",
             drift_label(_row(56, 12)))
    tc.check("label_btc_rv30_0pct_is_strong_down",
             drift_label(_row(53, 0)) == "DOWN",
             drift_label(_row(53, 0)))
    tc.check("label_vrp_100pct_is_strong_up",
             drift_label(_row(0, 33)) == "UP",
             drift_label(_row(0, 33)))
    tc.check("label_btc_atm30_26pct_is_mild_down",
             drift_label(_row(14, 5)) == "DOWN?",
             drift_label(_row(14, 5)))
    tc.check("label_dvol_36pct_is_none",
             drift_label(_row(7, 4)) == "NONE",
             drift_label(_row(7, 4)))
    tc.check("label_lown_when_few_scored",
             drift_label(_row(5, 0, n_scored=10)) == "LOW-N",
             drift_label(_row(5, 0, n_scored=10)))
    tc.check("label_few_events_is_none_not_lown",
             drift_label(_row(2, 1)) == "NONE",
             drift_label(_row(2, 1)))
    tc.check("label_bands_ordered",
             DRIFT_MILD_DEV < DRIFT_STRONG_DEV < 0.5,
             f"{DRIFT_MILD_DEV}/{DRIFT_STRONG_DEV}")

    # Реальні рядки ETH від 23 сер більше НЕ дають FLAT.
    _eth = {"atm30": _row(24, 6), "dvol": _row(14, 7),
            "rv30": _row(56, 12), "vrp_ours": _row(0, 22),
            "vrp_dvol": _row(0, 32)}
    _code, _txt = verdict_from_rows(_eth)
    tc.check("eth_23aug_no_longer_flat", not _code.startswith("FLAT"),
             f"{_code}: {_txt[:60]}")
    tc.check("eth_23aug_marked_tentative", _code.endswith("?"), _code)
    _btc = {"atm30": _row(14, 5), "dvol": _row(7, 4),
            "rv30": _row(53, 0), "vrp_ours": _row(1, 27),
            "vrp_dvol": _row(0, 33)}
    _code_b, _txt_b = verdict_from_rows(_btc)
    tc.check("btc_23aug_still_denominator",
             _code_b.startswith("DENOM"), _code_b)

    # Напрямкові правила: OUR-FIT лише при РІЗКІЙ розбіжності знаків.
    _clash = {"atm30": _row(0, 30), "dvol": _row(30, 0),
              "rv30": _row(20, 20), "vrp_ours": _row(0, 20),
              "vrp_dvol": _row(0, 20)}
    tc.check("opposite_numerators_flagged_clash",
             verdict_from_rows(_clash)[0].startswith("CLASH"),
             verdict_from_rows(_clash)[0])
    _ourfit = {"atm30": _row(0, 30), "dvol": _row(9, 8),
               "rv30": _row(20, 20), "vrp_ours": _row(0, 20),
               "vrp_dvol": _row(0, 20)}
    tc.check("strong_atm_vs_flat_dvol_is_our_fit",
             verdict_from_rows(_ourfit)[0].startswith("OUR-FIT"),
             verdict_from_rows(_ourfit)[0])
    _same = {"atm30": _row(20, 3), "dvol": _row(18, 4),
             "rv30": _row(20, 20), "vrp_ours": _row(0, 20),
             "vrp_dvol": _row(0, 20)}
    tc.check("same_direction_numerators_not_our_fit",
             not verdict_from_rows(_same)[0].startswith("OUR-FIT"),
             verdict_from_rows(_same)[0])
    _real = {"atm30": _row(0, 30), "dvol": _row(0, 28),
             "rv30": _row(20, 20), "vrp_ours": _row(0, 20),
             "vrp_dvol": _row(0, 20)}
    tc.check("both_numerators_up_is_real",
             verdict_from_rows(_real)[0].startswith("REAL"),
             verdict_from_rows(_real)[0])
    _reg = {"atm30": _row(0, 30), "dvol": _row(0, 28),
            "rv30": _row(30, 0), "vrp_ours": _row(0, 20),
            "vrp_dvol": _row(0, 20)}
    tc.check("up_numerators_and_down_denom_is_regime",
             verdict_from_rows(_reg)[0].startswith("REGIME"),
             verdict_from_rows(_reg)[0])


    # ------------------------------------------------------------------
    # Секція «Множник cc» (25 сер)
    # ------------------------------------------------------------------
    DAY_MS = 24 * 3600 * 1000
    GRID_MS = 30 * 60 * 1000
    # База: 2026-01-01 00:00 UTC, рівно на вузлі сітки.
    _base = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
                * 1000)

    def _mk_bars(n_days, day_fn, start=_base, p0=100.0):
        """5-хв бари: day_fn(day_idx, node_idx) -> лог-ретерн ВУЗЛА."""
        ticks, closes = [start], [p0]
        p = p0
        for d in range(n_days):
            for j in range(48):
                p *= math.exp(day_fn(d, j))
                ticks.append(start + d * DAY_MS + (j + 1) * GRID_MS)
                closes.append(p)
        return {"ticks": ticks, "close": closes}

    _now = _base + (40 + 1) * DAY_MS + 3600 * 1000

    # МЕХАНІЗМ, ПОЗИТИВНИЙ КОНТРОЛЬ: трендова доба (усі ретерни одного
    # знаку) дає cc ВИЩЕ сітки рівно у sqrt(48) раз за побудовою:
    # cc^2 = (48r)^2, grid = 48 r^2.
    _trend = _mk_bars(40, lambda d, j: 0.0005)
    _g, _gc = grid_daily_from_5m(_trend, 30, 0.96, _now_ms=_now)
    tc.check("grid_daily_counts_nodes", _gc["expected_per_day"] == 48
             and _gc["days_full"] >= 38,
             f"full={_gc['days_full']} exp={_gc['expected_per_day']}")
    _st, _rows = cc_ratio_panel(_g["days"], _g["var"], _g["close"], 30)
    tc.check("trending_day_makes_cc_high",
             _st["median_ratio"] is not None
             and abs(_st["median_ratio"] - math.sqrt(48.0)) < 0.05,
             f"{_f(_st['median_ratio'], 3)} проти {math.sqrt(48.0):.3f}")

    # МЕХАНІЗМ, НЕГАТИВНИЙ КОНТРОЛЬ: пиляння +r/-r дає cc НУЛЬ, тобто
    # множник у ТОЙ САМИЙ бік, що й «cc занижує». Разом із попереднім
    # тестом це і є доказ, що знак множника задає внутрішньоденна
    # автокореляція, а не міжденні розриви.
    _chop = _mk_bars(40, lambda d, j: 0.0005 * (1 if j % 2 == 0 else -1)
                     + 0.00002)
    _g2, _ = grid_daily_from_5m(_chop, 30, 0.96, _now_ms=_now)
    _st2, _ = cc_ratio_panel(_g2["days"], _g2["var"], _g2["close"], 30)
    tc.check("chopping_day_makes_cc_low",
             _st2["median_ratio"] is not None
             and _st2["median_ratio"] < 0.50,
             f"{_f(_st2['median_ratio'], 4)}")
    # ІДЕАЛЬНЕ пиляння дає cc == 0; точка не мовчить, а рахується.
    _chop0 = _mk_bars(40, lambda d, j: 0.0005 * (1 if j % 2 == 0 else -1))
    _g0, _ = grid_daily_from_5m(_chop0, 30, 0.96, _now_ms=_now)
    _st0, _ = cc_ratio_panel(_g0["days"], _g0["var"], _g0["close"], 30)
    tc.check("zero_cc_points_are_counted_not_silent",
             _st0["n"] == 0 and _st0["n_drop_zero_rv"] > 0,
             f"n={_st0['n']} drop_zero={_st0['n_drop_zero_rv']}")
    tc.check("cc_verdict_low_n_on_empty_panel",
             cc_multiplier_verdict(_st0)[0] == "LOW-N")
    tc.check("ratio_sign_flips_between_regimes",
             _st["median_ratio"] > 1.0 > _st2["median_ratio"],
             f"{_f(_st['median_ratio'], 2)} / {_f(_st2['median_ratio'], 3)}")

    # Неповна доба відкидається, сьогоднішня — теж.
    _part = _mk_bars(40, lambda d, j: 0.0005)
    _keep = [(t, p) for t, p in zip(_part["ticks"], _part["close"])
             if not (_base + 5 * DAY_MS <= t < _base + 6 * DAY_MS
                     and (t // GRID_MS) % 2 == 0)]
    _part = {"ticks": [t for t, _p in _keep],
             "close": [p for _t, p in _keep]}
    _g3, _gc3 = grid_daily_from_5m(_part, 30, 0.96, _now_ms=_now)
    tc.check("grid_daily_drops_incomplete_day",
             _gc3["days_incomplete"] >= 1,
             f"incomplete={_gc3['days_incomplete']}")
    _now_in = _base + 20 * DAY_MS + 3600 * 1000
    _today_in = datetime.fromtimestamp(_now_in / 1000.0,
                                       tz=timezone.utc).strftime("%Y-%m-%d")
    _g4, _gc4 = grid_daily_from_5m(_trend, 30, 0.96, _now_ms=_now_in)
    tc.check("grid_daily_drops_today",
             _gc4["day_today_dropped"] == 1
             and _today_in not in _g4["days"],
             f"dropped={_gc4['day_today_dropped']} today={_today_in}")
    tc.check("grid_min_must_be_multiple_of_5",
             _raises(lambda: grid_daily_from_5m(_trend, 7, 0.96), ValueError))

    # Дірки в добовому ряді рахуються, а не ігноруються.
    tc.check("count_day_gaps_zero_on_contiguous",
             count_day_gaps(["2026-01-01", "2026-01-02", "2026-01-03"]) == 0)
    tc.check("count_day_gaps_finds_hole",
             count_day_gaps(["2026-01-01", "2026-01-03", "2026-01-04"]) == 1)

    # rv_trailing_*: вікно і довжина рядів.
    _dd = [f"2026-02-{i + 1:02d}" for i in range(20)]
    _vv = [0.0001] * 20
    _rg = rv_trailing_grid(_dd, _vv, 5)
    tc.check("rv_trailing_grid_length", len(_rg) == 16, str(len(_rg)))
    tc.check("rv_trailing_grid_value",
             abs(_rg[0][1] - math.sqrt(0.0001 * ANNUALIZE)) < 1e-12)
    _cl = [100.0 * (1.01 ** i) for i in range(20)]
    _rc = rv_trailing_cc(_dd, _cl, 5)
    tc.check("rv_trailing_cc_length", len(_rc) == 15, str(len(_rc)))
    tc.check("rv_trailing_cc_needs_extra_close",
             _raises(lambda: rv_trailing_cc(_dd[:5], _cl[:5], 5),
                     Exception) or len(rv_trailing_cc(_dd, _cl, 5)) == 15)

    # spearman
    tc.check("spearman_perfect", abs(spearman([1, 2, 3, 4],
                                              [10, 20, 30, 40]) - 1.0) < 1e-12)
    tc.check("spearman_reversed", abs(spearman([1, 2, 3, 4],
                                               [4, 3, 2, 1]) + 1.0) < 1e-12)
    tc.check("spearman_ties_mean_rank",
             abs(spearman([1, 1, 2, 2], [1, 2, 3, 4])
                 - 2.0 / math.sqrt(5.0)) < 1e-9,
             _f(spearman([1, 1, 2, 2], [1, 2, 3, 4]), 4))
    tc.check("spearman_constant_is_none",
             spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None)
    tc.check("spearman_short_is_none", spearman([1, 2], [1, 2]) is None)
    tc.check("spearman_length_mismatch_raises",
             _raises(lambda: spearman([1, 2, 3], [1, 2]), ValueError))

    # Вердикт множника: пороги зафіксовані, гілки не друкуються разом.
    _stab = {"n": 200, "iqr_log": 0.05, "null_iqr_log": 0.05,
             "rho_conc_grid": 0.1, "rho_level": 0.2}
    tc.check("cc_verdict_stable",
             cc_multiplier_verdict(_stab)[0] == "STABLE",
             cc_multiplier_verdict(_stab)[0])
    _reg = {"n": 200, "iqr_log": 0.40, "null_iqr_log": 0.10,
            "rho_conc_grid": 0.1, "rho_level": 0.1}
    tc.check("cc_verdict_regime_by_iqr",
             cc_multiplier_verdict(_reg)[0] == "REGIME-DEPENDENT")
    _reg2 = {"n": 200, "iqr_log": 0.05, "null_iqr_log": 0.05,
             "rho_conc_grid": 0.7, "rho_level": 0.1}
    tc.check("cc_verdict_regime_by_rho",
             cc_multiplier_verdict(_reg2)[0] == "REGIME-DEPENDENT",
             "висока rho сама по собі достатня")
    _grey = {"n": 200, "iqr_log": 0.18, "null_iqr_log": 0.12,
             "rho_conc_grid": 0.2, "rho_level": 0.1}
    tc.check("cc_verdict_grey_band",
             cc_multiplier_verdict(_grey)[0] == "GREY")
    tc.check("cc_verdict_low_n",
             cc_multiplier_verdict({"n": 10, "iqr_log": 0.01,
                                    "null_iqr_log": 0.01})[0] == "LOW-N")

    # Базова лінія: LOW-N має вигравати у «висновок стоїть», інакше
    # секція видасть вердикт на десятку вікон.
    _sc_cc = {"frac_ge_observed": 0.148, "n_windows": 526}
    tc.check("baseline_low_n_when_few_windows",
             baseline_shift_verdict(
                 _sc_cc, {"frac_ge_observed": 0.15,
                          "n_windows": CC_MIN_WINDOWS - 1})[0] == "LOW-N")
    tc.check("baseline_holds_in_band",
             baseline_shift_verdict(
                 _sc_cc, {"frac_ge_observed": 0.15,
                          "n_windows": 300})[0] == "CONCLUSION-HOLDS")
    tc.check("baseline_falls_when_rare",
             baseline_shift_verdict(
                 _sc_cc, {"frac_ge_observed": 0.02,
                          "n_windows": 300})[0] == "CONCLUSION-FALLS")
    tc.check("baseline_holds_wider_above_band",
             baseline_shift_verdict(
                 _sc_cc, {"frac_ge_observed": 0.45,
                          "n_windows": 300})[0] == "CONCLUSION-HOLDS-WIDER")
    tc.check("baseline_none_scan_is_low_n",
             baseline_shift_verdict(_sc_cc, None)[0] == "LOW-N")
    # РЕГРЕСІЯ 26 сер: 6.4% BTC — нижче смуги, вище порогу відмови.
    # Стара гілка давала HOLDS-WIDER, тобто протилежне до даних.
    _below = baseline_shift_verdict(_sc_cc, {"frac_ge_observed": 0.064,
                                             "n_windows": 300})
    tc.check("baseline_below_band_is_weaker",
             _below[0] == "CONCLUSION-WEAKER", _below[0])
    tc.check("baseline_below_band_text_says_rarer",
             "РІДШИЙ" in _below[1])
    tc.check("baseline_above_band_text_says_not_rare",
             "поготів" in baseline_shift_verdict(
                 _sc_cc, {"frac_ge_observed": 0.45, "n_windows": 300})[1])
    tc.check("baseline_sides_differ",
             baseline_shift_verdict(_sc_cc, {"frac_ge_observed": 0.064,
                                             "n_windows": 300})[0]
             != baseline_shift_verdict(_sc_cc, {"frac_ge_observed": 0.45,
                                                "n_windows": 300})[0],
             "боки смуги мусять мати РІЗНІ коди")
    tc.check("baseline_eth_113_holds",
             baseline_shift_verdict(_sc_cc, {"frac_ge_observed": 0.113,
                                             "n_windows": 300})[0]
             == "CONCLUSION-HOLDS")

    # Проксі режиму: з варіації СІТКИ, не з cc-квадратів.
    _pd = [f"2026-01-{i + 1:02d}" for i in range(12)]
    _pv = [1e-4] * 12
    _pv[9] = 9e-4
    _pc = [100.0]
    for _i in range(1, 12):
        _pc.append(_pc[-1] * (1.01 if _i % 2 else 0.99))
    _ps, _pr = cc_ratio_panel(_pd, _pv, _pc, window=5)
    _exp = 9e-4 / (9e-4 + 4e-4)
    _got = [r[4] for r in _pr if r[0] == "2026-01-12"]
    tc.check("conc_proxy_from_grid_var",
             _got and abs(_got[0] - _exp) < 1e-12,
             f"{_got} vs {_exp:.6f}")
    tc.check("conc_source_declared",
             _ps.get("conc_source") == "grid_var")
    tc.check("panel_has_no_legacy_rho_conc",
             "rho_conc" not in _ps and "rho_conc_grid" in _ps)
    # Той самий var, ІНШІ закриття -> conc НЕ зрушив (проксі чистий).
    _pc2 = [100.0]
    for _i in range(1, 12):
        _pc2.append(_pc2[-1] * (1.05 if _i == 10 else 1.001))
    _pr2 = cc_ratio_panel(_pd, _pv, _pc2, window=5)[1]
    tc.check("conc_proxy_independent_of_closes",
             [r[4] for r in _pr] == [r[4] for r in _pr2],
             "conc не сміє залежати від чисельника відношення")
    tc.check("legacy_rho_conc_key_rejected",
             _raises(lambda: cc_multiplier_verdict(
                 {"n": 300, "iqr_log": 0.12, "null_iqr_log": 0.12,
                  "rho_conc": 0.1}), ValueError),
             "старий ключ мусить падати, а не тихо не читатись")

    # Сторінкування 5-хв свічок: чанки, дедуплікація, стеля.
    _calls = {"n": 0}

    def _fake5m(url):
        _calls["n"] += 1
        lo = int(url.split("start_timestamp=")[1].split("&")[0])
        hi = int(url.split("end_timestamp=")[1].split("&")[0])
        t = lo - (lo % (5 * 60 * 1000)) - 5 * 60 * 1000
        ticks, closes = [], []
        while t < hi:
            ticks.append(t)
            closes.append(100.0)
            t += 5 * 60 * 1000
        return {"status": "ok", "ticks": ticks, "close": closes}

    _b, _bc = fetch_5m_candles_paged("BTC", 30, 7, _fetcher=_fake5m,
                                     _now_ms=_base)
    tc.check("candles_paged_chunks", _bc["chunks"] == 5,
             f"chunks={_bc['chunks']}")
    tc.check("candles_paged_dedup",
             len(_b["ticks"]) == len(set(_b["ticks"])) and _bc["bars_dup"] > 0,
             f"kept={_bc['bars_kept']} dup={_bc['bars_dup']}")
    tc.check("candles_paged_sorted",
             _b["ticks"] == sorted(_b["ticks"]))
    tc.check("candles_paged_bad_status_raises",
             _raises(lambda: fetch_5m_candles_paged(
                 "BTC", 30, 7,
                 _fetcher=lambda u: {"status": "err"}), RuntimeError))
    tc.check("candles_paged_length_mismatch_raises",
             _raises(lambda: fetch_5m_candles_paged(
                 "BTC", 30, 7,
                 _fetcher=lambda u: {"status": "ok", "ticks": [1, 2],
                                     "close": [1.0]}), RuntimeError))
    tc.check("candles_paged_chunk_cap",
             _raises(lambda: fetch_5m_candles_paged(
                 "BTC", 4000, 1, _fetcher=_fake5m, _now_ms=_base),
                 RuntimeError))

    # Квантиль: інтерполяція, не «найближче значення».
    tc.check("quantile_interpolates",
             abs(_quantile_sorted([0.0, 1.0, 2.0, 3.0], 0.5) - 1.5) < 1e-12)
    tc.check("quantile_single_point",
             _quantile_sorted([7.0], 0.9) == 7.0)


    # --- Нульова модель шуму cc (25 сер) ---
    # АБСОЛЮТНИЙ ПОРІГ БУВ БИ НЕДОСЯЖНИЙ: на i.i.d. ряді IQR ~0.12 сам
    # по собі. Ці тести фіксують, що ми міряємо НАДЛИШОК над цим фоном.
    _gs = _gauss_stream(7)
    _draw = [next(_gs) for _ in range(4000)]
    _m = sum(_draw) / len(_draw)
    _sd = math.sqrt(sum((x - _m) ** 2 for x in _draw) / len(_draw))
    tc.check("gauss_stream_moments", abs(_m) < 0.06 and abs(_sd - 1.0) < 0.06,
             f"m={_m:.3f} sd={_sd:.3f}")
    tc.check("gauss_stream_deterministic",
             [next(_gauss_stream(11)) for _ in range(5)]
             == [next(_gauss_stream(11)) for _ in range(5)])

    _var_flat = [0.0001] * 400
    _nq, _nall = cc_null_iqr(_var_flat, 30, reps=40, seed=3)
    tc.check("null_iqr_is_positive_on_iid",
             _nq is not None and 0.05 < _nq < 0.40, f"{_f(_nq, 3)}")
    tc.check("null_iqr_reps_counted", len(_nall) == 40, str(len(_nall)))
    tc.check("null_iqr_refuses_short_series",
             cc_null_iqr([0.0001] * 10, 30, reps=5)[0] is None)
    # Той самий сид -> той самий результат (вердикт не має плавати).
    tc.check("null_iqr_reproducible",
             cc_null_iqr(_var_flat, 30, reps=10, seed=5)[0]
             == cc_null_iqr(_var_flat, 30, reps=10, seed=5)[0])

    # Вердикт: надлишок 1.0x на чистому шумі -> STABLE, а не GREY.
    _n_stab = {"n": 300, "iqr_log": 0.12, "null_iqr_log": 0.12,
               "rho_conc_grid": 0.1, "rho_level": 0.1}
    tc.check("verdict_stable_when_iqr_equals_null",
             cc_multiplier_verdict(_n_stab)[0] == "STABLE",
             cc_multiplier_verdict(_n_stab)[0])
    _n_reg = {"n": 300, "iqr_log": 0.30, "null_iqr_log": 0.12,
              "rho_conc_grid": 0.1, "rho_level": 0.1}
    tc.check("verdict_regime_on_excess",
             cc_multiplier_verdict(_n_reg)[0] == "REGIME-DEPENDENT",
             f"{0.30 / 0.12:.2f}x")
    _n_missing = {"n": 300, "iqr_log": 0.12, "null_iqr_log": None}
    tc.check("verdict_low_n_without_null",
             cc_multiplier_verdict(_n_missing)[0] == "LOW-N",
             "без контролю вердикту немає")

    # --- Перевірка змінних оточення (quiet=True: самотест не сміє
    # смітити в живий лог) ---
    import re as _envre
    _unk = warn_unknown_env(
        KNOWN_ENV,
        environ={"VOLEDGE_DVOL_HIST_D": "730", "VOLEDGE_DVOL_HIST_DAYS": "730"},
        quiet=True)
    tc.check("env_warn_flags_typo", _unk == ["VOLEDGE_DVOL_HIST_DAYS"],
             str(_unk))
    tc.check("env_warn_ignores_known", "VOLEDGE_DVOL_HIST_D" not in _unk)
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

def run_cc_section(dec, series, min_history, rank_k, cc_days,
                   grid_min, chunk_d, min_cover):
    """
    {cur: {...}} — секція «Стабільність множника cc».

    Для кожної валюти: 5-хв свічки -> добова сітка -> ряд відношення
    RV_cc/RV_grid -> вердикт про сталість множника; далі перепрогін
    empirical_window_scan зі знаменником на сітці і порівняння базової
    лінії з тією, що дав cc.
    """
    out = {}
    for cur in CURRENCIES:
        try:
            bars, bc = fetch_5m_candles_paged(cur, cc_days, chunk_d)
            grid, gc = grid_daily_from_5m(bars, grid_min, min_cover)
            print(f"[CC] {cur}: чанків {bc['chunks']}, барів "
                  f"{bc['bars_kept']} (битих {bc['bars_bad']}, "
                  f"дублів {bc['bars_dup']}), вузлів сітки {gc['nodes']}, "
                  f"повних діб {gc['days_full']} (неповних "
                  f"{gc['days_incomplete']}, сьогодні "
                  f"{gc['day_today_dropped']})")
            stats, rows = cc_ratio_panel(grid["days"], grid["var"],
                                         grid["close"], RV_WINDOW_D)
            nq, _nall = cc_null_iqr(grid["var"], RV_WINDOW_D)
            stats["null_iqr_log"] = nq
            stats["null_reps"] = CC_NULL_REPS
            stats["excess_iqr"] = ((stats["iqr_log"] / nq)
                                   if (nq and stats["iqr_log"] is not None
                                       and nq > 0) else None)
            gaps = count_day_gaps(grid["days"])
            if gaps:
                print(f"[CC] {cur}: [FLAG] дірок у добовому ряді: "
                      f"{gaps} — trailing-вікна навколо них накривають "
                      f"більше {RV_WINDOW_D} календарних діб")
            rec = {"status": "OK", "n_days": gc["days_full"],
                   "grid_min": grid_min, "stats": stats,
                   "day_gaps": gaps,
                   "verdict": cc_multiplier_verdict(stats),
                   "counters": {"bars": bc, "grid": gc},
                   "_rv_rows": rv_trailing_grid(grid["days"],
                                                grid["var"],
                                                RV_WINDOW_D)}
            out[cur] = rec
        except Exception as e:
            out[cur] = {"status": "SKIP", "reason": str(e)}
            print(f"[CC] {cur}: SKIP ({e})")
    return out


def attach_cc_scan(cc, dec, series, dvol_daily, min_history, rank_k,
                   scan_cc_by_cur):
    """
    Перепрогін сканування зі знаменником на 30-хв сітці + вердикт про
    базову лінію. Виділено окремо, щоб мережа і статистика не сиділи в
    одній функції: сюди приходять уже готові ряди.
    """
    for cur in CURRENCIES:
        rec = cc.get(cur)
        if not rec or rec.get("status") != "OK":
            continue
        rec["scan_grid"] = None
        rec["scan_why"] = "немає DVOL-ряду для перепрогону"
        rec["baseline"] = ("LOW-N", "перепрогін не відбувся")
        dser = dvol_daily.get(cur)
        rvrows = rec.pop("_rv_rows", None)
        if dser is None or not rvrows:
            continue
        rvmap = dict(rvrows)
        ratio = []
        miss = 0
        for s, dv in dser:
            g = rvmap.get(s.strftime("%Y-%m-%d"))
            if g is None or g <= 0 or dv <= 0:
                miss += 1
                continue
            ratio.append(dv / g)
        rec["n_pairs_grid"] = len(ratio)
        rec["n_miss_grid"] = miss
        obs = dec[cur]["vrp_ours"]
        win = len(series[cur]["vrp_ours"])
        sc, why = empirical_window_scan(ratio, win, min_history, rank_k,
                                        obs["up_frac"],
                                        obs["n_low"] + obs["n_high"])
        rec["scan_grid"] = sc
        rec["scan_why"] = why
        rec["baseline"] = baseline_shift_verdict(
            scan_cc_by_cur.get(cur), sc)
    return cc


def run_live(min_history, rank_k, vrp_source, vrp_age_h, dvol_age_s,
             hist_days, do_hist, do_cc, cc_days, grid_min, chunk_d,
             min_cover):
    chain_pairs = list_artifacts(CHAIN_DIR, "chain")
    rv_pairs = list_artifacts(RV_DIR, vrp_source)
    if not chain_pairs:
        print(f"[SUMMARY] нуль chain-артефактів у {CHAIN_DIR}")
        return 1
    if not rv_pairs:
        raise RuntimeError(f"[FAIL] нуль артефактів {vrp_source!r} "
                           f"у {RV_DIR}")
    print(f"[OVERALL] chain: {len(chain_pairs)}, "
          f"{vrp_source}: {len(rv_pairs)}")

    first_st = chain_pairs[0][0]
    last_st = chain_pairs[-1][0]
    start_ms = int((first_st - timedelta(hours=6)).timestamp() * 1000)
    end_ms = int((last_st + timedelta(hours=6)).timestamp() * 1000)

    anchors = [st for st, _p in chain_pairs
               if st.hour in CANONICAL_SLOT_HOURS]
    if not anchors:
        print("[SUMMARY] нуль якорів канонічної сітки")
        return 1
    dvol_by_cur = {}
    for cur in CURRENCIES:
        ser, c = fetch_dvol_series(cur, start_ms, end_ms, DVOL_RES_HOUR)
        cov = check_dvol_coverage(ser, anchors[0], anchors[-1], cur,
                                  dvol_age_s)
        dvol_by_cur[cur] = ser
        print(f"[DVOL] {cur}: сторінок {c['pages']}, рядків {c['rows']}, "
              f"узято {c['kept']}, дублікатів {c['dup_stamp']}, "
              f"биті {c['bad_row']}, впертось у ліміт {c['hit_cap']}")
        print(f"[DVOL] {cur}: покриття {cov['first']} .. {cov['last']}")

    series, counters = build_anchor_series(chain_pairs, rv_pairs,
                                           dvol_by_cur, vrp_age_h,
                                           dvol_age_s)
    print(f"[OVERALL] знімків {counters['snapshots']}, "
          f"стара сітка {counters['legacy_grid']}, "
          f"поза сіткою {counters['off_grid']}, "
          f"cache hit/miss {counters['cache_hit']}/"
          f"{counters['cache_miss']}, read_fail {counters['read_fail']}")
    print(f"[OVERALL] відкинуто: DVOL задалеко "
          f"{counters['dvol_too_far']}, без atm30 "
          f"{counters['no_atm30']}, без rv30 {counters['no_rv30']}")

    dec = decompose(series, min_history, rank_k)
    print_decomposition(dec, min_history, rank_k)

    hist = {}
    dvol_daily = {}
    scan_cc_by_cur = {}
    if do_hist:
        for cur in CURRENCIES:
            try:
                dser, dc = fetch_dvol_series(
                    cur,
                    int((datetime.now(timezone.utc)
                         - timedelta(days=hist_days + 5)).timestamp()
                        * 1000),
                    int(datetime.now(timezone.utc).timestamp() * 1000),
                    DVOL_RES_DAY)
                closes, cc = fetch_daily_closes(cur, hist_days + 40)
                rvser = rv30_cc_series(closes, RV_WINDOW_D)
                rvmap = {s.date(): v for s, v in rvser}
                ratio = []
                miss = 0
                for s, dv in dser:
                    rv = rvmap.get(s.date())
                    if rv is None or rv <= 0 or dv <= 0:
                        miss += 1
                        continue
                    ratio.append(dv / rv)
                print(f"[HIST] {cur}: сторінок {dc['pages']}, "
                      f"DVOL діб {dc['kept']}, "
                      f"свічок {cc['rows']}, RV-точок {len(rvser)}, "
                      f"пар {len(ratio)}, без пари {miss}")
                obs = dec[cur]["vrp_ours"]
                win = len(series[cur]["vrp_ours"])
                sc, why = empirical_window_scan(
                    ratio, win, min_history, rank_k,
                    obs["up_frac"], obs["n_low"] + obs["n_high"])
                if sc is None:
                    hist[cur] = {"status": "SKIP", "reason": why}
                else:
                    hist[cur] = {"status": "OK", "n_points": len(ratio),
                                 "scan": sc}
                dvol_daily[cur] = dser
                scan_cc_by_cur[cur] = sc
            except Exception as e:
                hist[cur] = {"status": "SKIP", "reason": str(e)}
                print(f"[HIST] {cur}: SKIP ({e})")
        print_history(hist)
    else:
        print("\n[SKIP] довгий ряд пропущено явно "
              "(VOLEDGE_DVOL_NO_HIST=1)")

    cc = {}
    if do_cc:
        print("\n--- МНОЖНИК cc " + "-" * 61)
        cc = run_cc_section(dec, series, min_history, rank_k, cc_days,
                            grid_min, chunk_d, min_cover)
        attach_cc_scan(cc, dec, series, dvol_daily, min_history, rank_k,
                       scan_cc_by_cur)
        print_cc_ratio(cc)
    else:
        print("\n[SKIP] секція множника cc пропущена явно "
              "(VOLEDGE_CCRATIO_SKIP=1)")

    report = {"generated_utc": datetime.now(timezone.utc).isoformat(),
              "min_history": min_history, "rank_k": rank_k,
              "vrp_source": vrp_source, "vrp_max_age_h": vrp_age_h,
              "dvol_max_age_s": dvol_age_s,
              "counters": counters, "decomposition": dec,
              "verdicts": {cur: dict(zip(("code", "text"),
                                         verdict_from_rows(dec[cur])))
                           for cur in dec},
              "history": hist, "cc_multiplier": cc}
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUT_DIR, f"dvoldrift_{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n[OVERALL] звіт: {out_path}")
    print(f"[SUMMARY] валют: {len(dec)}, вердикти: "
          + ", ".join(f"{c}={report['verdicts'][c]['code']}"
                      for c in sorted(dec)))
    if cc:
        print("[SUMMARY] множник cc: "
              + ", ".join(
                  f"{c}={cc[c]['verdict'][0]}/"
                  f"{cc[c].get('baseline', ('—',))[0]}"
                  if cc[c].get("status") == "OK" else f"{c}=SKIP"
                  for c in sorted(cc)))
    return 0


def main():
    print("=" * 78)
    print("Vol-Edge :: dvol_drift (READ-ONLY перевірка дрейфу vrp30)")
    print("=" * 78)
    # ПЕРЕД розбором значень: нерозпізнана змінна = тихий дефолт.
    # Локальна копія цієї логіки прибрана 24 сер — вона була третьою
    # в проєкті і третьою за формою (без quiet, без повернення).
    warn_unknown_env(KNOWN_ENV, label="dvol_drift")
    off_raw = os.environ.get("VOLEDGE_OFFLINE", "0")
    mh_raw = os.environ.get("VOLEDGE_MIN_HISTORY", "12")
    rk_raw = os.environ.get("VOLEDGE_RANK_K", "1")
    src_raw = os.environ.get("VOLEDGE_VRP_SOURCE", "rvroll")
    age_raw = os.environ.get("VOLEDGE_VRP_MAX_AGE_H", "5")
    dva_raw = os.environ.get("VOLEDGE_DVOL_MAX_AGE_MIN",
                             str(DVOL_MAX_AGE_MIN_DEFAULT))
    hd_raw = os.environ.get("VOLEDGE_DVOL_HIST_D",
                            str(DVOL_HIST_D_DEFAULT))
    nh_raw = os.environ.get("VOLEDGE_DVOL_NO_HIST", "0")
    ccs_raw = os.environ.get("VOLEDGE_CCRATIO_SKIP", "0")
    ccd_raw = os.environ.get("VOLEDGE_CCRATIO_D", "")
    ccg_raw = os.environ.get("VOLEDGE_CC_GRID_MIN",
                             str(CC_GRID_MIN_DEFAULT))
    ccc_raw = os.environ.get("VOLEDGE_CC_CHUNK_D",
                             str(CC_CHUNK_D_DEFAULT))
    ccv_raw = os.environ.get("VOLEDGE_CC_MIN_COVER",
                             str(CC_MIN_COVER_DEFAULT))

    offline = off_raw.strip() == "1"
    do_hist = nh_raw.strip() != "1"
    do_cc = ccs_raw.strip() != "1"
    try:
        min_history = int(mh_raw)
        rank_k = int(rk_raw)
        vrp_age = float(age_raw)
        dvol_age_min = float(dva_raw)
        hist_days = int(hd_raw)
        cc_days = int(ccd_raw) if ccd_raw.strip() else hist_days
        grid_min = int(ccg_raw)
        chunk_d = int(ccc_raw)
        min_cover = float(ccv_raw)
    except ValueError:
        raise RuntimeError(f"Невалідні env: MIN_HISTORY={mh_raw!r} "
                           f"RANK_K={rk_raw!r} "
                           f"VRP_MAX_AGE_H={age_raw!r} "
                           f"DVOL_MAX_AGE_MIN={dva_raw!r} "
                           f"DVOL_HIST_D={hd_raw!r} "
                           f"CCRATIO_D={ccd_raw!r} "
                           f"CC_GRID_MIN={ccg_raw!r} "
                           f"CC_CHUNK_D={ccc_raw!r} "
                           f"CC_MIN_COVER={ccv_raw!r}")
    if min_history < 3:
        raise RuntimeError(f"MIN_HISTORY={min_history} має бути >= 3")
    if rank_k < 1:
        raise RuntimeError(f"RANK_K={rank_k} має бути >= 1")
    if vrp_age <= 0 or dvol_age_min <= 0:
        raise RuntimeError(f"Вік має бути > 0: vrp={vrp_age} "
                           f"dvol_min={dvol_age_min}")
    if hist_days < 90:
        raise RuntimeError(f"DVOL_HIST_D={hist_days} < 90 — надто "
                           f"коротка історія для сканування")
    if do_cc:
        if cc_days < 90:
            raise RuntimeError(f"CCRATIO_D={cc_days} < 90 — надто "
                               f"коротка історія для перепрогону")
        if grid_min < 5 or grid_min % 5 != 0:
            raise RuntimeError(f"CC_GRID_MIN={grid_min} має бути "
                               f"кратний 5 і >= 5")
        if chunk_d < 1 or chunk_d > 30:
            raise RuntimeError(f"CC_CHUNK_D={chunk_d} має бути 1..30")
        if not (0.0 < min_cover <= 1.0):
            raise RuntimeError(f"CC_MIN_COVER={min_cover} має бути "
                               f"в (0, 1]")
    vrp_source = src_raw.strip()
    if vrp_source not in ("rvroll", "rv"):
        raise RuntimeError(f"VOLEDGE_VRP_SOURCE={src_raw!r} — очікується "
                           f"'rvroll' або 'rv'")

    print(f"[ENV] VOLEDGE_OFFLINE={off_raw!r} -> {offline}")
    print(f"[ENV] VOLEDGE_MIN_HISTORY={mh_raw!r} -> {min_history}")
    print(f"[ENV] VOLEDGE_RANK_K={rk_raw!r} -> {rank_k}")
    print(f"[ENV] VOLEDGE_VRP_SOURCE={src_raw!r} -> {vrp_source}")
    print(f"[ENV] VOLEDGE_VRP_MAX_AGE_H={age_raw!r} -> {vrp_age}")
    print(f"[ENV] VOLEDGE_DVOL_MAX_AGE_MIN={dva_raw!r} -> "
          f"{dvol_age_min}")
    print(f"[ENV] VOLEDGE_DVOL_HIST_D={hd_raw!r} -> {hist_days}")
    print(f"[ENV] VOLEDGE_DVOL_NO_HIST={nh_raw!r} -> {not do_hist}")
    print(f"[ENV] VOLEDGE_CCRATIO_SKIP={ccs_raw!r} -> {not do_cc}")
    print(f"[ENV] VOLEDGE_CCRATIO_D={ccd_raw!r} -> {cc_days}")
    print(f"[ENV] VOLEDGE_CC_GRID_MIN={ccg_raw!r} -> {grid_min}")
    print(f"[ENV] VOLEDGE_CC_CHUNK_D={ccc_raw!r} -> {chunk_d}")
    print(f"[ENV] VOLEDGE_CC_MIN_COVER={ccv_raw!r} -> {min_cover}")


    print("\n--- ОФЛАЙН-САМОТЕСТИ " + "-" * 56)
    tc = run_self_tests()
    print(f"[SUMMARY] самотести: {tc.run}, пройдено: {tc.passed}, "
          f"провалено: {len(tc.failures)}")
    if tc.failures:
        return 1
    if offline:
        print("[SUMMARY] VOLEDGE_OFFLINE=1 -> live пропущено явно.")
        return 0

    print("\n--- ПЕРЕВІРКА ДРЕЙФУ " + "-" * 56)
    return run_live(min_history, rank_k, vrp_source, vrp_age,
                    dvol_age_min * 60.0, hist_days, do_hist, do_cc,
                    cc_days, grid_min, chunk_d, min_cover)


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
