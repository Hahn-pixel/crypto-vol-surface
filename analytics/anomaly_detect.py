# -*- coding: utf-8 -*-
"""
[EN] Anomaly detector against the pipeline's own history: an alert is
raised when the latest increment of a surface feature ranks among the
K most extreme increments of all prior history, with a per-feature
cooldown (debounce). Distribution-free; z-scores are kept as diagnostics
only.

--- Ukrainian original below ---
Vol-Edge :: analytics/anomaly_detect.py  (Модуль 5)

Детектор аномалій проти власної історії знімків.

СХЕМА ДЕТЕКЦІЇ (рев. 22 сер 2026 — заміна z-порогової схеми):

  РАНГОВА ДЕТЕКЦІЯ НА ПРИРОСТАХ + ДЕБАУНС.

  Алерт, якщо ОСТАННІЙ приріст фічі (x[t] - x[t-1]) входить у RANK_K
  найменших або RANK_K найбільших приростів усієї попередньої історії,
  І з моменту попереднього алерту по цій фічі минуло > COOLDOWN кроків.

  Чому НЕ z-поріг (калібрування 22 сер, 168 знімків):
  - Рівні фіч автокорельовані: r_lag1 = 0.90..0.99, n_eff = 1..9 при
    номінальному n=168. Номінальне n оманливе; MAD міряє
    внутрішньорежимне тремтіння, а не мінливість фічі.
  - Розподіл |z| має надважкі хвости: med|z| ~ 0.7 (як у теорії), але
    q99 = 5..18 при теоретичних 2.58, max до 34. Жодне масштабування
    (ковзне вікно, локальна шкала по приростах) цього не виправляє —
    прирости IV справді стрибають.
  - Наслідок: частка алертів 8.6..17.8% замість цільових ~1%, і поріг
    неможливо відкалібрувати — квантиль 1% при n_eff~5 оцінений по
    частці одного спостереження.
  - Рангова схема distribution-free: очікувана частка = 2K/(n+1) ТОЧНО,
    незалежно від форми розподілу. Виміряно: rank_diffs 4.9% сирої,
    2.9% після дебаунсу cd=6, розкид по фічах 1.3..5.2% (проти
    2.6..20.7% у z-схеми). Одне налаштування K працює для всіх фіч.
  - Надмірність (алерти до / після дебаунсу): rank_diffs 1.7x проти
    4.0x у levels_expanding — алерти рангової схеми початково різні
    події, а не повтори одного режимного зсуву.

  z-score ЗБЕРЕЖЕНО в артефакті як ДІАГНОСТИКА (поле "z"), але НЕ керує
  алертами. Поле "n_eff" — ефективний розмір вибірки за AR(1).

Фічі на знімок (на валюту, якщо не вказано інше):
  atm7, atm30, atm90     — ATM IV константних тенорів (interp_tenor);
  psi30                  — ATM skew (SVI-JW), лінійна інтерполяція по tau;
  rr25_30, bf25_30       — legacy-пара для порівняння з psi;
  slope_90_30            — atm90 - atm30 (term slope);
  ethbtc_atm30           — ETH-BTC спред ATM30 (спільна фіча);
  vrp30                  — atm30 ПОТОЧНОГО знімка / RV30_30m з найближчого
                           rv-артефакту не старшого VOLEDGE_VRP_MAX_AGE_H
                           (48 год), інакше явний SKIP.

ВИПРАВЛЕННЯ 22 сер 2026 — ЧИСЕЛЬНИК vrp30:
  БУЛО: iv30 брався з ПОЛЯ rv-артефакту (blk["iv30"]), яке rv_intraday
  зчитує з одного chain-файлу на момент СВОГО прогону (поле
  rv["chain_artifact"]). Оскільки rv-артефакт один на добу, усі шість
  chain-знімків доби давали ІДЕНТИЧНЕ значення vrp30: і чисельник, і
  знаменник оновлювались раз на добу. П'ять приростів із шести були
  ТОЧНИМИ НУЛЯМИ.
  СТАЛО: чисельник = feats[f"{cur}.atm30"] того самого знімка, який
  зараз обробляється. Знаменник поки лишається добовим.
  Це кількісно пояснює всі три симптоми калібрування (n_eff=1.0/1.9;
  rank_diffs 10.7%/9.4%; асиметрія рангів 0/57): 168 знімків несли
  ~28 різних значень, а масові ties ламають рангову схему, для якої
  частка 2K/(n+1) виводилась для розподілу БЕЗ збігів.
  Поле rv["currencies"][cur]["iv30"] БІЛЬШЕ НЕ ВИКОРИСТОВУЄТЬСЯ як
  джерело істини — лише як діагностика в самому rv-артефакті.

DIAG_ONLY (рахуються, але НЕ алертять): BTC.vrp30, ETH.vrp30.
  Причина ЗАЛИШАЄТЬСЯ: знаменник (RV) оновлюється раз/добу, тож фіча
  все ще має добово-ступінчастий масштаб. Знімати з DIAG_ONLY лише
  після підняття частоти збору RV І повторного калібрування.
  Очікуваний ефект цього патчу — падіння частки rank_diffs у діапазон
  решти фіч (1.3..5.2%) і зникнення однобічної рангової асиметрії.
  Якщо симптоми виживуть — діагноз був неповний.

Прапорці якості останнього знімка (не алерти, а прямі [FLAG]-рядки):
  fit_mode != FULL_SVI; durrleman_bad_n > 0; p або c > WING_FLAG_THRESH
  (крило не обмежене даними — діагностика, не сигнал).

Читає: artifacts/chain/chain_*.json(.gz) — ВСІ, як історія;
       artifacts/rv/rv_*.json(.gz) — для VRP.
Пише: artifacts/anomaly/anomaly_YYYYMMDD_HHMMSS.json.

Env-флаги:
  VOLEDGE_OFFLINE=1        -> лише самотести.
  VOLEDGE_RANK_K           -> K рангової схеми, default 1.
  VOLEDGE_COOLDOWN         -> мовчання після алерту, кроків, default 6.
  VOLEDGE_Z_THRESH         -> поріг |z| ДЛЯ ДІАГНОСТИКИ, default 3.0.
  VOLEDGE_MIN_HISTORY      -> мінімум точок історії, default 12.
  VOLEDGE_VRP_MAX_AGE_H    -> макс. вік rv-артефакту, default 5
                              (було 48 за добового rv; ковзний rvroll
                              оновлюється кожні 4 год о :05).
  VOLEDGE_VRP_SOURCE       -> префікс артефактів знаменника vrp30:
                              "rvroll" (ковзне вікно, default) або
                              "rv" (добовий, legacy). Вибір ЯВНИЙ:
                              якщо артефактів немає — відмова, не
                              мовчазний відкат на інше джерело.

Чистий stdlib; мережі немає. Подвійний клік + input() завжди.
"""

import glob
import gzip
import json
import math
import os
import re
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_ROOT, _THIS_DIR, os.path.join(_ROOT, "core"),
           os.path.join(_ROOT, "analytics")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from surface_report import interp_tenor
except ImportError:
    from analytics.surface_report import interp_tenor
try:
    from svi_jw import raw_to_jw
except ImportError:
    from core.svi_jw import raw_to_jw
try:
    from env_flags import merge_known, warn_unknown_env
except ImportError:
    from core.env_flags import merge_known, warn_unknown_env

CHAIN_DIR = os.path.join(_ROOT, "artifacts", "chain")
RV_DIR = os.path.join(_ROOT, "artifacts", "rv")
OUT_DIR = os.path.join(_ROOT, "artifacts", "anomaly")

CURRENCIES = ("BTC", "ETH")
TENORS_D = (7.0, 30.0, 90.0)
WING_FLAG_THRESH = 10.0
MAD_SCALE = 1.4826
DIAG_ONLY = ("BTC.vrp30", "ETH.vrp30")
_STAMP_RE = re.compile(r"_(\d{8})_(\d{6})\.json(\.gz)?$")

# Змінні, які читає САМЕ ЦЯ точка входу (усі — у main()). Імпортовані
# surface_report/svi_jw дають лише чисті функції і оточення під час
# прогону детектора не читають, тож їхні змінні сюди НЕ входять: якщо
# хтось виставить VOLEDGE_RV_DAYS перед запуском детектора, вона
# справді нічого не зробить, і попередження про це — правильне.
KNOWN_ENV = merge_known((
    "VOLEDGE_Z_THRESH",
    "VOLEDGE_MIN_HISTORY",
    "VOLEDGE_VRP_MAX_AGE_H",
    "VOLEDGE_VRP_SOURCE",
    "VOLEDGE_RANK_K",
    "VOLEDGE_COOLDOWN",
))


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


def list_artifacts(dirpath, prefix):
    out = []
    for pat in (f"{prefix}_*.json", f"{prefix}_*.json.gz"):
        out.extend(glob.glob(os.path.join(dirpath, pat)))
    pairs = []
    for p in out:
        st = _stamp_of(p)
        if st is None:
            print(f"[SKIP] нерозпізнане ім'я артефакту: {p}")
            continue
        pairs.append((st, p))
    pairs.sort()
    return pairs


# ----------------------------------------------------------------------------
# Фічі одного знімка
# ----------------------------------------------------------------------------

def _interp_slice_field(slices, tenor_d, getter, label):
    """Лінійна інтерполяція по tau довільної зрізової величини.
    getter(slice) -> float|None. (значення|None, причина)."""
    tau_t = tenor_d / 365.0
    pts = []
    for sl in slices:
        if sl.get("status") != "OK":
            continue
        v = getter(sl)
        if v is None:
            continue
        pts.append((sl["tau"], v))
    if len(pts) < 2:
        return None, f"{label}: точок {len(pts)} < 2"
    pts.sort()
    if not (pts[0][0] <= tau_t <= pts[-1][0]):
        return None, (f"{label}: тенор {tenor_d}d поза "
                      f"[{pts[0][0]*365:.1f},{pts[-1][0]*365:.1f}]d")
    for i in range(1, len(pts)):
        if pts[i][0] >= tau_t:
            t0, p0 = pts[i - 1]
            t1, p1 = pts[i]
            w = (tau_t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return p0 + w * (p1 - p0), "OK"
    return None, f"{label}: недосяжна гілка"


def _jw_psi_getter(sl):
    params = sl.get("svi_params")
    if not params or len(params) != 5:
        return None
    try:
        return raw_to_jw(tuple(params), sl["tau"])["psi"]
    except ValueError:
        return None


def _psi_at_tenor(slices, tenor_d):
    return _interp_slice_field(slices, tenor_d, _jw_psi_getter, "psi")


def features_of_snapshot(chain_data):
    """
    (features: {name: float}, skips: {name: reason}, quality: [str]).
    Прапорці якості — лише опис, без чисел історії.
    """
    feats, skips, quality = {}, {}, []
    atm30 = {}
    for blk in chain_data:
        cur = blk.get("currency")
        if cur not in CURRENCIES:
            continue
        slices = blk.get("slices", [])
        for td in TENORS_D:
            t = interp_tenor(slices, td)
            key = f"{cur}.atm{int(td)}"
            if t.get("status") == "OK":
                feats[key] = t["atm_iv"]
                if td == 30.0:
                    atm30[cur] = t["atm_iv"]
            else:
                skips[key] = f"interp_tenor: {t.get('status')}"
        for nm, fld in (("rr25_30", "rr25_pa"), ("bf25_30", "bf25_pa")):
            v, reason = _interp_slice_field(
                slices, 30.0, lambda sl, f=fld: sl.get(f), nm)
            if v is None:
                skips[f"{cur}.{nm}"] = reason
            else:
                feats[f"{cur}.{nm}"] = v
        if f"{cur}.atm90" in feats and f"{cur}.atm30" in feats:
            feats[f"{cur}.slope_90_30"] = (feats[f"{cur}.atm90"]
                                           - feats[f"{cur}.atm30"])
        psi, reason = _psi_at_tenor(slices, 30.0)
        if psi is None:
            skips[f"{cur}.psi30"] = reason
        else:
            feats[f"{cur}.psi30"] = psi
        # якість
        for sl in slices:
            exp = sl.get("expiry")
            if sl.get("status") != "OK":
                quality.append(f"{cur} {exp}: slice status="
                               f"{sl.get('status')}")
                continue
            if sl.get("fit_mode") != "FULL_SVI":
                quality.append(f"{cur} {exp}: fit_mode="
                               f"{sl.get('fit_mode')}")
            if sl.get("durrleman_bad_n", 0) > 0:
                quality.append(f"{cur} {exp}: durr="
                               f"{sl.get('durrleman_bad_n')}")
            params = sl.get("svi_params")
            if params and len(params) == 5:
                try:
                    jw = raw_to_jw(tuple(params), sl["tau"])
                    for wnm in ("p", "c"):
                        if jw[wnm] > WING_FLAG_THRESH:
                            quality.append(
                                f"{cur} {exp}: крило {wnm}="
                                f"{jw[wnm]:.1f} > {WING_FLAG_THRESH} "
                                f"(не обмежене даними)")
                except ValueError as e:
                    quality.append(f"{cur} {exp}: JW-конвертація: {e}")
    if "BTC" in atm30 and "ETH" in atm30:
        feats["ethbtc_atm30"] = atm30["ETH"] - atm30["BTC"]
    else:
        skips["ethbtc_atm30"] = f"atm30 доступний лише для {list(atm30)}"
    return feats, skips, quality


def vrp_from_rv(rv_pairs, snap_time, max_age_h, atm30_by_cur):
    """
    ({'BTC.vrp30':..}, skips).

    ЧИСЕЛЬНИК: atm30_by_cur[cur] — ATM IV тенору 30d ПОТОЧНОГО chain-знімка
    (обов'язковий аргумент; None/відсутність -> явний SKIP, не тихий
    fallback на поле iv30 rv-артефакту).
    ЗНАМЕННИК: RV30 на 30-хв сітці з найближчого rv <= snap_time у межах
    max_age_h; fallback на rv30_5m — явний, з [SKIP]-позначкою.
    """
    feats, skips = {}, {}
    if atm30_by_cur is None:
        for cur in CURRENCIES:
            skips[f"{cur}.vrp30"] = ("чисельник atm30 не переданий "
                                     "(atm30_by_cur=None) — явна відмова")
        return feats, skips
    cand = [(st, p) for st, p in rv_pairs if st <= snap_time]
    if not cand:
        for cur in CURRENCIES:
            skips[f"{cur}.vrp30"] = "нуль rv-артефактів до знімка"
        return feats, skips
    st, path = cand[-1]
    age_h = (snap_time - st).total_seconds() / 3600.0
    if age_h > max_age_h:
        for cur in CURRENCIES:
            skips[f"{cur}.vrp30"] = (f"rv-артефакт застарий: "
                                     f"{age_h:.1f}h > {max_age_h}h")
        return feats, skips
    rv = _load_json(path)
    for cur in CURRENCIES:
        blk = rv.get("currencies", {}).get(cur)
        key = f"{cur}.vrp30"
        if not blk or blk.get("status") != "OK":
            skips[key] = f"rv: валюта {cur} відсутня/не OK"
            continue
        iv30 = atm30_by_cur.get(cur)
        if iv30 is None:
            skips[key] = ("чисельник: atm30 поточного знімка недоступний "
                          "(див. skip по {}.atm30)".format(cur))
            continue
        if iv30 <= 0:
            skips[key] = f"чисельник: atm30={iv30} <= 0"
            continue
        g = (blk.get("rv30_by_grid") or {}).get("30m", {})
        if g.get("status") == "OK":
            rv30 = g["rv30"]
        elif blk.get("rv30_5m") is not None:
            rv30 = blk["rv30_5m"]
            skips[f"{cur}.vrp30_grid"] = ("30m-сітка недоступна, "
                                          "fallback на 5m — явний")
        else:
            skips[key] = "rv: ані 30m, ані 5m RV30"
            continue
        if rv30 <= 0:
            skips[key] = f"rv: rv30={rv30} <= 0"
            continue
        feats[key] = iv30 / rv30
    return feats, skips


# ----------------------------------------------------------------------------
# Робастний z-score
# ----------------------------------------------------------------------------

def robust_z(history: list, x: float):
    """(z|None, reason). median/MAD по history (без x)."""
    n = len(history)
    if n == 0:
        return None, "історія порожня"
    hs = sorted(history)
    med = hs[n // 2] if n % 2 == 1 else 0.5 * (hs[n // 2 - 1] + hs[n // 2])
    devs = sorted(abs(v - med) for v in history)
    mad = devs[n // 2] if n % 2 == 1 else 0.5 * (devs[n // 2 - 1]
                                                 + devs[n // 2])
    if mad <= 0.0:
        return None, f"MAD=0 (константна історія, n={n})"
    return (x - med) / (MAD_SCALE * mad), "OK"


def autocorr_lag1(values):
    """(r|None, reason). Пірсон між x[t] і x[t-1]."""
    n = len(values)
    if n < 4:
        return None, f"точок {n} < 4"
    a, b = values[1:], values[:-1]
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da <= 0.0 or db <= 0.0:
        return None, "нульова дисперсія"
    return num / (da * db), "OK"


def effective_n(n, r1):
    """n_eff за AR(1). None при виродженості."""
    if r1 is None or r1 >= 0.999999 or r1 <= -0.999999:
        return None
    return max(1.0, n * (1.0 - r1) / (1.0 + r1))


def rank_position(hist, x):
    """(n_below, n_above) — скільки значень історії строго менші/більші."""
    n_below = 0
    n_above = 0
    for v in hist:
        if v < x:
            n_below += 1
        elif v > x:
            n_above += 1
    return n_below, n_above


def rank_hits(values, min_history, k):
    """
    [(idx, side)] — індекси, де точка входить у K екстремумів історії.
    side у {'low','high'}. Використовується і для поточної точки, і для
    відтворення історії алертів (потрібно дебаунсу).
    """
    if k < 1:
        raise ValueError(f"rank_k={k} має бути >= 1")
    out = []
    for i in range(len(values)):
        hist = values[:i]
        if len(hist) < min_history:
            continue
        n_below, n_above = rank_position(hist, values[i])
        if n_below < k:
            out.append((i, "low"))
        elif n_above < k:
            out.append((i, "high"))
    return out


def apply_cooldown(idxs, cooldown):
    """
    Дебаунс: після алерту мовчання `cooldown` кроків. Жадібно зліва.
    cooldown <= 0 -> без змін (явно). Детермінований, стану між
    запусками НЕ потребує: історія перераховується щоразу повністю.
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


def detect(series: dict, min_history: int, rank_k: int, cooldown: int,
           z_thresh: float, diag_only=DIAG_ONLY) -> dict:
    """
    series: {feature: [(stamp_iso, value), ...]} хронологічно.

    Рангова детекція на приростах + дебаунс. Повертає {feature: {...}}.
    Ключі результату (сумісні з vol_edge_status.py):
      status  — "OK" | "SKIP"
      value   — останнє значення рівня
      alert   — bool, РІШЕННЯ (ранг + дебаунс + не DIAG_ONLY)
      z       — робастний z останнього ПРИРОСТУ (ДІАГНОСТИКА, не рішення)
      n_history — номінальна довжина історії приростів
      n_eff   — ефективна довжина за AR(1) рівнів
    Плюс рангові поля: diff, rank_below, rank_above, rank_side,
    rank_hit, debounced, diag_only, drift_low, drift_high.
    """
    out = {}
    for name in sorted(series):
        pts = series[name]
        if not pts:
            out[name] = {"status": "SKIP", "reason": "нуль точок"}
            continue
        last_stamp, last_val = pts[-1]
        values = [v for _, v in pts]
        if len(values) < 2:
            out[name] = {"status": "SKIP", "value": last_val,
                         "reason": "менше 2 точок — приріст невизначений"}
            continue

        diffs = [values[i] - values[i - 1] for i in range(1, len(values))]
        hist_diffs = diffs[:-1]
        last_diff = diffs[-1]
        if len(hist_diffs) < min_history:
            out[name] = {"status": "SKIP", "value": last_val,
                         "reason": f"історія приростів {len(hist_diffs)} < "
                                   f"min_history={min_history}"}
            continue

        n_below, n_above = rank_position(hist_diffs, last_diff)
        if n_below < rank_k:
            side = "low"
            rank_hit = True
        elif n_above < rank_k:
            side = "high"
            rank_hit = True
        else:
            side = None
            rank_hit = False

        # Дебаунс: чи не заглушений цей алерт попереднім по тій же фічі.
        hits = rank_hits(diffs, min_history, rank_k)
        kept = set(apply_cooldown([i for i, _ in hits], cooldown))
        last_idx = len(diffs) - 1
        debounced = rank_hit and (last_idx not in kept)

        is_diag = name in diag_only
        alert = bool(rank_hit and not debounced and not is_diag)

        # Діагностика: z приросту, n_eff рівнів, асиметрія дрейфу.
        z, z_reason = robust_z(hist_diffs, last_diff)
        # TIES: рангова схема distribution-free лише для розподілу БЕЗ
        # збігів. Масові нульові прирости (замерзла фіча) роблять частку
        # 2K/(n+1) недійсною. Рахуємо явно — це діагностика, не фільтр.
        n_zero = sum(1 for d in diffs if d == 0.0)
        zeros_frac = n_zero / len(diffs)
        n_ties_last = sum(1 for d in hist_diffs if d == last_diff)
        r1, _ = autocorr_lag1(values)
        n_eff = effective_n(len(values), r1)
        lvl_hits = rank_hits(values, min_history, rank_k)
        drift_low = sum(1 for _, sd in lvl_hits if sd == "low")
        drift_high = sum(1 for _, sd in lvl_hits if sd == "high")

        rec = {"status": "OK", "value": last_val, "diff": last_diff,
               "alert": alert, "rank_hit": rank_hit, "rank_side": side,
               "rank_below": n_below, "rank_above": n_above,
               "debounced": debounced, "diag_only": is_diag,
               "n_history": len(hist_diffs), "n_eff": n_eff,
               "autocorr_lag1": r1,
               "n_zero_diffs": n_zero, "zeros_frac": zeros_frac,
               "n_ties_last": n_ties_last,
               "drift_low": drift_low, "drift_high": drift_high,
               "stamp": last_stamp}
        if z is None:
            rec["z"] = None
            rec["z_reason"] = z_reason
        else:
            rec["z"] = z
            rec["z_exceeds_diag_thresh"] = abs(z) >= z_thresh
        out[name] = rec
    return out


# ----------------------------------------------------------------------------
# Самотести (детерміновано)
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


def run_self_tests() -> TestCounters:
    tc = TestCounters()

    # robust_z
    hist = [0.30 + 0.002 * math.sin(i * 1.7) for i in range(20)]
    z, r = robust_z(hist, 0.35)
    tc.check("z_spike_detected", z is not None and z > 3.0,
             f"z={z} ({r})")
    z2, _ = robust_z(hist, hist[7])
    tc.check("z_normal_small", z2 is not None and abs(z2) < 2.0,
             f"z={z2}")
    z3, r3 = robust_z([0.3] * 15, 0.31)
    tc.check("z_mad_zero_refuses", z3 is None and "MAD=0" in r3, r3)
    # один викид в історії не ламає робастну оцінку
    hist_out = list(hist)
    hist_out[5] = 5.0
    z4, _ = robust_z(hist_out, 0.301)
    tc.check("z_robust_to_outlier", z4 is not None and abs(z4) < 3.0,
             f"z={z4}")

    # --- рангові примітиви ---
    tc.check("rank_position_basic",
             rank_position([1.0, 2.0, 3.0, 4.0], 2.5) == (2, 2), "")
    tc.check("rank_position_ties_ignored",
             rank_position([1.0, 1.0, 1.0], 1.0) == (0, 0), "")
    rh = rank_hits([float(i) for i in range(20)], 12, 1)
    tc.check("rank_hits_trend_all", len(rh) == 20 - 12
             and all(sd == "high" for _, sd in rh), str(len(rh)))

    # Частка рангової схеми НЕ залежить від форми розподілу — ядро
    # рішення відмовитись від z-порогу.
    def _lcg(n_, seed=12345):
        x = seed
        o = []
        for _ in range(n_):
            x = (1103515245 * x + 12345) % 2147483648
            o.append(x / 2147483648.0)
        return o
    u = _lcg(400)
    gaussish = [sum(u[i:i + 4]) - 2.0 for i in range(0, 400, 4)]
    heavy = [v ** 3 * 50.0 for v in gaussish]
    n_g = len(rank_hits(gaussish, 12, 1))
    n_h = len(rank_hits(heavy, 12, 1))
    tc.check("rank_rate_invariant_to_tails", n_g == n_h,
             f"gauss={n_g} heavy={n_h} (мають збігатися точно)")

    tc.check("rank_k_zero_refuses",
             _raises(lambda: rank_hits([1.0] * 20, 12, 0), ValueError), "")

    # --- дебаунс ---
    tc.check("cooldown_collapses_run",
             apply_cooldown([10, 11, 12, 13, 30], 6) == [10, 30],
             str(apply_cooldown([10, 11, 12, 13, 30], 6)))
    tc.check("cooldown_zero_noop",
             apply_cooldown([1, 2, 3], 0) == [1, 2, 3], "")
    tc.check("cooldown_boundary_exclusive",
             apply_cooldown([0, 6, 7], 6) == [0, 7],
             str(apply_cooldown([0, 6, 7], 6)))

    # --- n_eff ---
    r_tr, _ = autocorr_lag1([float(i) for i in range(50)])
    tc.check("autocorr_trend_high", r_tr is not None and r_tr > 0.95,
             f"r={r_tr}")
    tc.check("neff_persistent_small", effective_n(168, 0.95) < 10.0,
             str(effective_n(168, 0.95)))
    tc.check("neff_degenerate_none", effective_n(100, 1.0) is None, "")

    # --- detect: алерт на стрибку приросту ---
    calm = [(f"t{i}", 0.30 + 0.0005 * math.sin(i * 1.7))
            for i in range(30)]
    spike = calm + [("tX", 0.30 + 0.0005 * math.sin(29 * 1.7) + 0.05)]
    d = detect({"a": spike}, min_history=12, rank_k=1, cooldown=6,
               z_thresh=3.0, diag_only=())
    tc.check("detect_rank_alert",
             d["a"]["status"] == "OK" and d["a"]["alert"]
             and d["a"]["rank_side"] == "high", str(d["a"]))

    # спокійна точка алерту не дає
    d2 = detect({"a": calm}, min_history=12, rank_k=1, cooldown=6,
                z_thresh=3.0, diag_only=())
    tc.check("detect_quiet_no_alert",
             d2["a"]["status"] == "OK" and not d2["a"]["alert"],
             str(d2["a"]["alert"]))

    # коротка історія -> явний SKIP
    d3 = detect({"b": [("t0", 1.0), ("t1", 1.1), ("t2", 0.9)]},
                min_history=12, rank_k=1, cooldown=6, z_thresh=3.0)
    tc.check("detect_short_skip", d3["b"]["status"] == "SKIP"
             and "min_history" in d3["b"]["reason"], str(d3["b"]))

    # DIAG_ONLY: ранг спрацював, але алерту немає
    d4 = detect({"BTC.vrp30": spike}, min_history=12, rank_k=1,
                cooldown=6, z_thresh=3.0)
    tc.check("detect_diag_only_no_alert",
             d4["BTC.vrp30"]["rank_hit"] and not d4["BTC.vrp30"]["alert"]
             and d4["BTC.vrp30"]["diag_only"], str(d4["BTC.vrp30"]))

    # дебаунс глушить повтор: два стрибки поспіль -> другий debounced
    twice = calm + [("tX", 0.40), ("tY", 0.52)]
    d5 = detect({"a": twice}, min_history=12, rank_k=1, cooldown=6,
                z_thresh=3.0, diag_only=())
    tc.check("detect_debounce_suppresses",
             d5["a"]["rank_hit"] and d5["a"]["debounced"]
             and not d5["a"]["alert"], str(d5["a"]))
    d6 = detect({"a": twice}, min_history=12, rank_k=1, cooldown=0,
                z_thresh=3.0, diag_only=())
    tc.check("detect_cooldown_zero_lets_through",
             d6["a"]["alert"] and not d6["a"]["debounced"], str(d6["a"]))

    # z присутній як діагностика, але не керує рішенням
    tc.check("detect_z_is_diagnostic_only",
             "z" in d2["a"] and d2["a"]["alert"] is False, "")
    # n_eff МОЖЕ перевищувати n при від'ємній автокореляції — це не
    # аномалія, а властивість формули AR(1). Перевіряємо лише, що
    # значення присутнє й додатне, і що persistent-ряд дає стиснення.
    tc.check("detect_reports_neff",
             d["a"]["n_eff"] is not None and d["a"]["n_eff"] > 0.0,
             str(d["a"]["n_eff"]))
    tc.check("detect_neff_shrinks_when_persistent",
             d5["a"]["n_eff"] is not None
             and d5["a"]["n_eff"] < d5["a"]["n_history"],
             f"n_eff={d5['a']['n_eff']:.2f} n={d5['a']['n_history']}")

    # features_of_snapshot на мінімальній синтетиці
    def mk_slice(exp, tau, iv, psi_sign=-1.0):
        # SVI з майже плоскою усмішкою навколо заданого ATM IV
        a = iv * iv * tau * 0.5
        b = iv * iv * tau * 0.9
        return {"expiry": exp, "status": "OK", "tau": tau,
                "fit_mode": "FULL_SVI", "durrleman_bad_n": 0,
                "svi_params": [a, b, psi_sign * 0.3, 0.05, 0.4],
                "rr25_pa": -0.01 * tau * 365, "bf25_pa": 0.005,
                "rows": []}

    syn_slices = [mk_slice("A", 10 / 365.0, 0.30),
                  mk_slice("B", 45 / 365.0, 0.32),
                  mk_slice("C", 120 / 365.0, 0.34)]
    chain = [{"currency": "BTC", "slices": syn_slices},
             {"currency": "ETH", "slices": [s.copy() for s in syn_slices]}]
    feats, skips, quality = features_of_snapshot(chain)
    tc.check("feats_atm_tenors",
             all(f"BTC.atm{t}" in feats for t in (7, 30, 90))
             or any("atm7" in k for k in skips),
             f"feats={sorted(feats)} skips={skips}")
    tc.check("feats_psi30", "BTC.psi30" in feats and feats["BTC.psi30"] < 0,
             str(feats.get("BTC.psi30")))
    tc.check("feats_rr_interp", "BTC.rr25_30" in feats
             and abs(feats["BTC.rr25_30"] + 0.30) < 1e-9,
             str(feats.get("BTC.rr25_30")))
    tc.check("feats_ethbtc", "ethbtc_atm30" in feats or
             "ethbtc_atm30" in skips, "")

    # прапорець крила
    wing_sl = mk_slice("W", 30 / 365.0, 0.30)
    wing_sl["svi_params"] = [0.001, 0.9, -0.98, 0.0, 0.001]
    _, _, q2 = features_of_snapshot([{"currency": "BTC",
                                      "slices": [wing_sl]}])
    tc.check("quality_wing_flag", any("крило" in s for s in q2), str(q2))

    # vrp_from_rv: застарілий артефакт -> skip (файл не читається взагалі)
    t_new = datetime(2026, 7, 26, tzinfo=timezone.utc)
    t_old = datetime(2026, 7, 20, tzinfo=timezone.utc)
    sk = vrp_from_rv([(t_old, "fake")], t_new, 48, {"BTC": 0.4, "ETH": 0.5})[1]
    tc.check("vrp_stale_skip", "BTC.vrp30" in sk
             and "застарий" in sk["BTC.vrp30"], str(sk))

    # vrp_from_rv: чисельник НЕ переданий -> явна відмова, не тихий
    # fallback на поле iv30 rv-артефакту
    sk_n = vrp_from_rv([(t_old, "fake")], t_new, 48, None)[1]
    tc.check("vrp_no_numerator_refuses",
             "BTC.vrp30" in sk_n and "не переданий" in sk_n["BTC.vrp30"],
             str(sk_n))

    # vrp_from_rv на РЕАЛЬНОМУ тимчасовому артефакті: чисельник береться
    # зі знімка, тож ДВА різні знімки при ОДНОМУ rv дають РІЗНІ vrp.
    _tmp_dir = tempfile.mkdtemp(prefix="voledge_test_")
    _rv_path = os.path.join(_tmp_dir, "rv_20260726_004000.json")
    _rv_doc = {"currencies": {
        "BTC": {"status": "OK", "iv30": 9.99,
                "rv30_by_grid": {"30m": {"status": "OK", "rv30": 0.40}}},
        "ETH": {"status": "OK", "iv30": 9.99,
                "rv30_by_grid": {"30m": {"status": "OK", "rv30": 0.50}}}}}
    with open(_rv_path, "w", encoding="utf-8") as _f:
        json.dump(_rv_doc, _f)
    _rvp = [(datetime(2026, 7, 26, 0, 40, tzinfo=timezone.utc), _rv_path)]
    _t1 = datetime(2026, 7, 26, 4, 10, tzinfo=timezone.utc)
    _t2 = datetime(2026, 7, 26, 8, 10, tzinfo=timezone.utc)
    v1 = vrp_from_rv(_rvp, _t1, 48, {"BTC": 0.44, "ETH": 0.55})[0]
    v2 = vrp_from_rv(_rvp, _t2, 48, {"BTC": 0.48, "ETH": 0.55})[0]
    tc.check("vrp_numerator_is_snapshot_atm30",
             abs(v1["BTC.vrp30"] - 1.10) < 1e-9
             and abs(v2["BTC.vrp30"] - 1.20) < 1e-9,
             f"{v1.get('BTC.vrp30')} / {v2.get('BTC.vrp30')}")
    tc.check("vrp_ignores_artifact_iv30",
             abs(v1["BTC.vrp30"] - 9.99 / 0.40) > 1.0,
             "поле iv30 rv-артефакту не має впливати")
    # відсутній atm30 по одній валюті -> skip лише по ній
    v3, s3 = vrp_from_rv(_rvp, _t1, 48, {"BTC": 0.44})
    tc.check("vrp_missing_atm30_partial_skip",
             "BTC.vrp30" in v3 and "ETH.vrp30" not in v3
             and "ETH.vrp30" in s3, f"{sorted(v3)} / {sorted(s3)}")
    shutil.rmtree(_tmp_dir, ignore_errors=True)

    # --- ties: замерзла фіча (старий баг vrp30) видима в діагностиці ---
    frozen = []
    for i in range(40):
        frozen.append((f"t{i}", 1.00 + 0.01 * (i // 6)))
    d7 = detect({"a": frozen}, min_history=12, rank_k=1, cooldown=6,
                z_thresh=3.0, diag_only=())
    tc.check("detect_reports_zero_diffs",
             d7["a"]["zeros_frac"] > 0.7,
             f"zeros_frac={d7['a']['zeros_frac']:.3f} "
             f"(5 з 6 приростів нульові — сигнатура старого багу)")
    tc.check("detect_reports_ties_count",
             d7["a"]["n_ties_last"] >= 1,
             str(d7["a"]["n_ties_last"]))
    # неперервний ряд збігів не має
    tc.check("detect_no_ties_when_continuous",
             d["a"]["zeros_frac"] == 0.0 and d["a"]["n_ties_last"] == 0,
             f"zeros_frac={d['a']['zeros_frac']}")

    # _stamp_of
    st = _stamp_of("chain_20260726_085531.json.gz")
    tc.check("stamp_parse", st is not None and st.hour == 8, str(st))

    # --- джерела знаменника vrp30 не мають перетинатися ---
    # Змішування добових rv_ і ковзних rvroll_ повернуло б слотову
    # пилку (виміряно 22 сер: p=0.0060/0.0005, ratio 3.1x/4.5x).
    # Глоб "rv_*" не ловить "rvroll_*" лише завдяки підкресленню —
    # тест фіксує це, щоб перейменування не злило теки мовчки.
    _tmp = tempfile.mkdtemp(prefix="voledge_src_")
    for _n in ("rv_20260822_004000.json", "rvroll_20260822_040500.json",
               "rvroll_20260822_080500.json"):
        with open(os.path.join(_tmp, _n), "w", encoding="utf-8") as _f:
            json.dump({}, _f)
    _daily = list_artifacts(_tmp, "rv")
    _roll = list_artifacts(_tmp, "rvroll")
    tc.check("vrp_sources_disjoint",
             len(_daily) == 1 and len(_roll) == 2
             and not (set(p for _s, p in _daily)
                      & set(p for _s, p in _roll)),
             f"rv={len(_daily)} rvroll={len(_roll)}")
    shutil.rmtree(_tmp, ignore_errors=True)

    # --- ENV-WARN (quiet=True: самотест не сміє смітити в живий лог) ---
    _env = {"VOLEDGE_RANK_K": "1", "VOLEDGE_COOLDOWNN": "6",
            "PATH": "/bin"}
    _unk = warn_unknown_env(KNOWN_ENV, environ=_env, quiet=True)
    tc.check("env_warn_flags_typo", _unk == ["VOLEDGE_COOLDOWNN"], str(_unk))
    tc.check("env_warn_ignores_known", "VOLEDGE_RANK_K" not in _unk)
    # Кожна змінна, яку main() ЧИТАЄ, мусить бути в KNOWN_ENV — інакше
    # штатний прогін друкує попередження сам на себе і привчає його
    # ігнорувати. Список звіряється з ФАЙЛОМ, не з пам'яттю автора.
    _src = ""
    try:
        with open(os.path.abspath(__file__), "r",
                  encoding="utf-8") as _envfh:
            _src = _envfh.read()
    except OSError:
        _src = ""
    _read = set(re.findall(r'os\.environ\.get\(\s*"(VOLEDGE_[A-Z0-9_]+)"',
                           _src))
    tc.check("known_env_covers_every_read",
             bool(_src) and _read and _read <= set(KNOWN_ENV),
             f"не оголошені: {sorted(_read - set(KNOWN_ENV))}")
    # Парний негативний контроль: список не роздутий іменами, яких
    # модуль не читає — кожне зайве гасить один клас помилки.
    tc.check("known_env_has_no_dead_names",
             bool(_src) and set(KNOWN_ENV) <= _read | {"VOLEDGE_OFFLINE"},
             f"зайві: {sorted(set(KNOWN_ENV) - _read - {'VOLEDGE_OFFLINE'})}")

    print(f"[TEST] passed {tc.passed}/{tc.run}")
    for f_ in tc.failures:
        print(f"[TEST] FAILURE detail: {f_}")
    return tc


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def run_live(min_history: int, rank_k: int, cooldown: int,
             z_thresh: float, vrp_max_age_h: float,
             vrp_source: str) -> int:
    chain_pairs = list_artifacts(CHAIN_DIR, "chain")
    rv_pairs = list_artifacts(RV_DIR, vrp_source)
    if not rv_pairs:
        raise RuntimeError(
            f"[FAIL] нуль артефактів з префіксом {vrp_source!r} у {RV_DIR}. "
            f"VOLEDGE_VRP_SOURCE вибрано явно — тихого відкату на інше "
            f"джерело немає. Змішувати rvroll_ і rv_ НЕ МОЖНА: добові "
            f"артефакти повернули б слотову пилку (p=0.006/0.0005).")
    print(f"[OVERALL] chain-артефактів: {len(chain_pairs)}, "
          f"{vrp_source}-артефактів: {len(rv_pairs)}")
    if not chain_pairs:
        print(f"[SUMMARY] нуль chain-артефактів у {CHAIN_DIR}")
        return 1

    series = {}
    total_skips = {}
    quality_last = []
    for idx, (st, path) in enumerate(chain_pairs):
        data = _load_json(path)
        feats, skips, quality = features_of_snapshot(data)
        # Чисельник vrp30 — atm30 ЦЬОГО знімка, не поле iv30 rv-артефакту.
        atm30_by_cur = {}
        for cur in CURRENCIES:
            v = feats.get(f"{cur}.atm30")
            if v is not None:
                atm30_by_cur[cur] = v
        vf, vs = vrp_from_rv(rv_pairs, st, vrp_max_age_h, atm30_by_cur)
        feats.update(vf)
        skips.update(vs)
        stamp = st.isoformat()
        for k, v in feats.items():
            series.setdefault(k, []).append((stamp, v))
        if idx == len(chain_pairs) - 1:
            total_skips = skips
            quality_last = quality
            print(f"[OVERALL] останній знімок: {path}")
            print(f"[OVERALL] фіч: {len(feats)}, skip-фіч: {len(skips)}")

    for k, reason in sorted(total_skips.items()):
        print(f"[SKIP] {k}: {reason}")
    for q in quality_last:
        print(f"[FLAG] {q}")

    res = detect(series, min_history, rank_k, cooldown, z_thresh)
    n_alerts = 0
    n_debounced = 0
    n_diag_suppressed = 0
    for name, r in sorted(res.items()):
        if r["status"] != "OK":
            print(f"[SKIP] {name}: {r['reason']}")
            continue
        z_s = f"{r['z']:+.2f}" if r.get("z") is not None else "n/a"
        ne_s = f"{r['n_eff']:.1f}" if r.get("n_eff") is not None else "n/a"
        line = (f"{name} value={r['value']:+.5f} diff={r['diff']:+.5f} "
                f"rank={r['rank_below']}/{r['rank_above']} "
                f"n={r['n_history']} n_eff={ne_s} z={z_s}")
        if r["alert"]:
            n_alerts += 1
            print(f"[ALERT] {line}  ранг={r['rank_side']} K={rank_k}")
        elif r["rank_hit"] and r["diag_only"]:
            n_diag_suppressed += 1
            print(f"[DIAG] {line}  ранг спрацював, але фіча в DIAG_ONLY "
                  f"(алерт придушено явно)")
        elif r["rank_hit"] and r["debounced"]:
            n_debounced += 1
            print(f"[DEBOUNCE] {line}  ранг спрацював, але дебаунс "
                  f"cooldown={cooldown} ще активний")
        else:
            print(f"[OK] {line}")

    # TIES: замерзла фіча (масові нульові прирости) ламає рангову схему.
    for name, r in sorted(res.items()):
        if r.get("status") != "OK":
            continue
        zf = r.get("zeros_frac")
        if zf is not None and zf > 0.10:
            print(f"[FLAG] {name}: нульових приростів "
                  f"{r['n_zero_diffs']} ({zf*100:.1f}%) — рангова частка "
                  f"2K/(n+1) недійсна при масових збігах")

    # Асиметрія рангів на РІВНЯХ — метрика дрейфу, не алерт.
    for name, r in sorted(res.items()):
        if r.get("status") != "OK":
            continue
        lo, hi = r["drift_low"], r["drift_high"]
        tot = lo + hi
        if tot >= 10 and (lo == 0 or hi == 0):
            print(f"[FLAG] {name}: односторонній дрейф рівня "
                  f"({lo} низ / {hi} верх з {tot}) — ряд немонотонним "
                  f"не виглядає")

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUT_DIR, f"anomaly_{stamp}.json")
    payload = {"generated_utc": datetime.now(timezone.utc).isoformat(),
               "scheme": "rank_diffs+cooldown",
               "rank_k": rank_k, "cooldown": cooldown,
               "z_thresh": z_thresh, "z_role": "diagnostic_only",
               "vrp_numerator": "atm30_of_current_chain_snapshot",
               "vrp_denominator": "rv30_30m_of_latest_rv_artifact",
               "min_history": min_history, "diag_only": list(DIAG_ONLY),
               "n_chain": len(chain_pairs), "n_rv": len(rv_pairs),
               "vrp_source": vrp_source,
               "results": res, "skips_last": total_skips,
               "quality_last": quality_last}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\n[OVERALL] артефакт: {out_path}")
    print(f"[SUMMARY] фіч оцінено: "
          f"{sum(1 for r in res.values() if r['status'] == 'OK')}, "
          f"алертів: {n_alerts}, придушено дебаунсом: {n_debounced}, "
          f"придушено DIAG_ONLY: {n_diag_suppressed}, skip: "
          f"{sum(1 for r in res.values() if r['status'] != 'OK')}")
    return 0


def main() -> int:
    print("=" * 72)
    print("Vol-Edge :: anomaly_detect (Модуль 5)")
    print("=" * 72)
    # ПЕРЕД розбором значень: нерозпізнана змінна = тихий дефолт.
    warn_unknown_env(KNOWN_ENV, label="anomaly_detect")
    offline_raw = os.environ.get("VOLEDGE_OFFLINE", "0")
    z_raw = os.environ.get("VOLEDGE_Z_THRESH", "3.0")
    mh_raw = os.environ.get("VOLEDGE_MIN_HISTORY", "12")
    age_raw = os.environ.get("VOLEDGE_VRP_MAX_AGE_H", "5")
    src_raw = os.environ.get("VOLEDGE_VRP_SOURCE", "rvroll")
    rk_raw = os.environ.get("VOLEDGE_RANK_K", "1")
    cd_raw = os.environ.get("VOLEDGE_COOLDOWN", "6")
    offline = offline_raw.strip() == "1"
    try:
        z_thresh = float(z_raw)
        min_history = int(mh_raw)
        vrp_age = float(age_raw)
        rank_k = int(rk_raw)
        cooldown = int(cd_raw)
    except ValueError:
        raise RuntimeError(f"Невалідні env: Z_THRESH={z_raw!r} "
                           f"MIN_HISTORY={mh_raw!r} VRP_MAX_AGE_H={age_raw!r} "
                           f"RANK_K={rk_raw!r} COOLDOWN={cd_raw!r}")
    vrp_source = src_raw.strip()
    if vrp_source not in ("rvroll", "rv"):
        raise RuntimeError(f"VOLEDGE_VRP_SOURCE={src_raw!r} — очікується "
                           f"'rvroll' (ковзне вікно) або 'rv' (legacy)")
    if z_thresh <= 0 or min_history < 3 or vrp_age <= 0:
        raise RuntimeError(f"Поза діапазоном: z_thresh={z_thresh} "
                           f"min_history={min_history} vrp_age={vrp_age}")
    if rank_k < 1:
        raise RuntimeError(f"VOLEDGE_RANK_K={rank_k} має бути >= 1")
    if cooldown < 0:
        raise RuntimeError(f"VOLEDGE_COOLDOWN={cooldown} має бути >= 0")
    print(f"[ENV] VOLEDGE_OFFLINE={offline_raw!r} -> {offline}")
    print(f"[ENV] VOLEDGE_Z_THRESH={z_raw!r} -> {z_thresh}")
    print(f"[ENV] VOLEDGE_MIN_HISTORY={mh_raw!r} -> {min_history}")
    print(f"[ENV] VOLEDGE_VRP_MAX_AGE_H={age_raw!r} -> {vrp_age}")
    print(f"[ENV] VOLEDGE_VRP_SOURCE={src_raw!r} -> {vrp_source}")
    print(f"[ENV] VOLEDGE_RANK_K={rk_raw!r} -> {rank_k}")
    print(f"[ENV] VOLEDGE_COOLDOWN={cd_raw!r} -> {cooldown}")
    print(f"[ENV] DIAG_ONLY (без алертів) -> {list(DIAG_ONLY)}")
    print(f"[ENV] z-score роль -> ДІАГНОСТИКА (не керує алертами)")

    print("\n--- ОФЛАЙН-САМОТЕСТИ " + "-" * 50)
    tc = run_self_tests()
    print(f"[SUMMARY] самотести: {tc.run}, пройдено: {tc.passed}, "
          f"провалено: {len(tc.failures)}")
    if tc.failures:
        return 1
    if offline:
        print("[SUMMARY] VOLEDGE_OFFLINE=1 -> live пропущено явно.")
        return 0

    print("\n--- ДЕТЕКЦІЯ (rank_diffs + cooldown) " + "-" * 35)
    return run_live(min_history, rank_k, cooldown, z_thresh, vrp_age,
                    vrp_source)


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
