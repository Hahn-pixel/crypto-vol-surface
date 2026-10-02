# -*- coding: utf-8 -*-
"""
[EN] Rolling-window realized volatility ending exactly at each snapshot
time - the denominator of the variance risk premium (vrp30) without the
daily step artifact. Live and backfill modes; insufficient coverage is
refused, never rescaled.

--- Ukrainian original below ---
Vol-Edge :: analytics/rv_rolling.py

КОВЗНЕ вікно RV — знаменник для vrp30 без добової ступінчастості.

ПРИЧИНА ІСНУВАННЯ (виміряно 22 сер 2026, слотовий тест у
analytics/anomaly_calibrate.py на 133 парах знімків канонічної сітки):

  Поки RV рахувався по КАЛЕНДАРНИХ добах і оновлювався раз на добу
  (rv_snapshot.sh о 00:40 UTC), приріст vrp30 у слоті 04:10 був
  систематично більший за решту слотів:

    med|d| 04:xx / med|d| решта = 3.125 (BTC), 4.491 (ETH)
    перестановочний тест: p = 0.0060 (BTC), p = 0.0005 (ETH)

  Знакова медіана показала ПИЛКУ, не просто стрибок: різкий підйом на
  оновленні знаменника (+0.0170 BTC / +0.0093 ETH у слоті 04), далі
  сповзання протягом доби (08/12/20 від'ємні). Сума за добу додатна —
  саме це, а не реальний режим, давало односторонній дрейф рангів
  рівнів 0/21 і 0/22.

РІШЕННЯ: RV на КОВЗНОМУ вікні, що закінчується на моменті знімка.
  RV30 = сума квадратів лог-ретернів за останні WINDOW_D діб до
  end_ms, ануалізована. Календарна доба у розрахунку не бере участі
  взагалі, тож поняття «повна/неповна доба» зникає з критичного шляху.
  Перевірка повноти переїжджає з доби на ВІКНО (покриття >= MIN_COVER).

  Кожен прогін кожні 4 год оновлює ~0.55% вікна. Це НЕ додає
  незалежної інформації (r1 лишиться ~0.99) — виправляється саме
  ступінчастість масштабу, і нічого більше.

БЕКФІЛ (обов'язковий, не опція): якщо просто увімкнути ковзне вікно
  вперед, ряд vrp30 матиме пилку в старій половині й гладкість у
  новій, і рангова схема на приростах побачить цей злам як подію.
  Свічки Deribit публічні й доступні назад на роки, тож історію RV
  (на відміну від ланцюга) можна й треба переписати на ВСІ моменти
  chain-знімків. Один запит покриває всю історію: вікно WINDOW_D
  плюс проміжок від першого до останнього знімка.

Режими (VOLEDGE_ROLL_MODE):
  live      — один артефакт на момент «зараз» (для cron кожні 4 год);
  backfill  — по артефакту на КОЖЕН chain-знімок з artifacts/chain.

Артефакт: artifacts/rv/rvroll_YYYYMMDD_HHMMSS.json
  Префікс rvroll_ НЕ перетинається з glob 'rv_*' старих артефактів:
  добовий rv_intraday.py (140 діб, HAR) лишається як є і далі пише
  rv_*.json. Два ряди співіснують; читач обирає префікс явно.

Схема артефакту сумісна з тим, що читає anomaly_detect.vrp_from_rv:
  currencies[cur].status == "OK" і currencies[cur].rv30_by_grid["30m"]
  з полями status/rv30. Поля iv30 НЕМАЄ і бути не повинно — чисельник
  vrp30 з 22 сер береться з atm30 поточного chain-знімка.

Env-флаги:
  VOLEDGE_OFFLINE=1          -> лише самотести, без мережі.
  VOLEDGE_CURRENCIES         -> "BTC,ETH" (default).
  VOLEDGE_ROLL_MODE          -> "live" | "backfill" (default live).
  VOLEDGE_ROLL_WINDOW_D      -> вікно RV30, діб (default 30, мін 7).
  VOLEDGE_ROLL_WINDOW7_D     -> вікно RV7, діб (default 7, мін 2).
  VOLEDGE_ROLL_MIN_COVER     -> мін. покриття вікна ретернами
                                (default 0.96, діапазон 0.5..1.0).
  VOLEDGE_ROLL_PAD_D         -> запас діб до початку вікна (default 3).
  VOLEDGE_ROLL_OVERWRITE=1   -> перезаписувати наявні rvroll-артефакти
                                у backfill (default 0 -> пропуск з
                                лічильником, не мовчазний).

Чистий stdlib. Подвійний клік + input() завжди.
"""

import glob
import json
import math
import re
import os
import sys
import time
import traceback
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_ROOT, _THIS_DIR, os.path.join(_ROOT, "core"),
           os.path.join(_ROOT, "analytics")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from rv_intraday import (API_BASE, ANNUALIZE, BARS_PER_DAY, CHUNK_DAYS,
                             PERP_NAME, SUBSAMPLE_STEPS, http_get_json,
                             subsample_bars)
except ImportError:
    from analytics.rv_intraday import (API_BASE, ANNUALIZE, BARS_PER_DAY,
                                       CHUNK_DAYS, PERP_NAME,
                                       SUBSAMPLE_STEPS, http_get_json,
                                       subsample_bars)
try:
    from env_flags import merge_known, warn_unknown_env
except ImportError:
    from core.env_flags import merge_known, warn_unknown_env

# Змінні, які читає САМЕ ЦЯ точка входу. Канон перевірки — у
# core/env_flags.py: раніше тут був inline-блок усередині main(), який
# не мав quiet, нічого не повертав і принципово не покривався
# самотестом, через що встиг розійтися з копією в tenor_nodes.py.
KNOWN_ENV = merge_known((
    "VOLEDGE_CURRENCIES",
    "VOLEDGE_ROLL_MODE",
    "VOLEDGE_ROLL_WINDOW_D",
    "VOLEDGE_ROLL_WINDOW7_D",
    "VOLEDGE_ROLL_MIN_COVER",
    "VOLEDGE_ROLL_PAD_D",
    "VOLEDGE_ROLL_OVERWRITE",
))

CHAIN_DIR = os.path.join(_ROOT, "artifacts", "chain")
RV_DIR = os.path.join(_ROOT, "artifacts", "rv")

MS_DAY = 86400000
MS_BAR5 = 300000
ART_PREFIX = "rvroll"


# ----------------------------------------------------------------------------
# Завантаження свічок за ЯВНИЙ діапазон
# ----------------------------------------------------------------------------

def fetch_5m_range(currency: str, start_ms: int, end_ms: int) -> dict:
    """
    5-хв свічки PERPETUAL у [start_ms, end_ms] чанками по CHUNK_DAYS.

    Відрізняється від rv_intraday.fetch_5m_candles тим, що кінець
    діапазону ЗАДАЄТЬСЯ явно, а не прибивається до now — без цього
    бекфіл неможливий.
    """
    if end_ms <= start_ms:
        raise ValueError(f"порожній діапазон: [{start_ms},{end_ms}]")
    inst = PERP_NAME[currency]
    chunk_ms = CHUNK_DAYS * MS_DAY
    by_tick = {}
    c = {"chunks": 0, "bars_raw": 0, "bars_dup": 0, "bars_bad": 0}
    lo = start_ms
    while lo < end_ms:
        hi = min(lo + chunk_ms, end_ms)
        r = http_get_json(
            f"{API_BASE}/get_tradingview_chart_data?instrument_name={inst}"
            f"&start_timestamp={lo}&end_timestamp={hi}&resolution=5")
        c["chunks"] += 1
        if r.get("status") != "ok":
            raise RuntimeError(f"candles5m {inst}: status={r.get('status')} "
                               f"на чанку [{lo},{hi}]")
        ticks = r.get("ticks") or []
        closes = r.get("close") or []
        highs = r.get("high") or []
        lows = r.get("low") or []
        if not (len(ticks) == len(closes) == len(highs) == len(lows)):
            raise RuntimeError(
                f"candles5m {inst}: розбіжні довжини масивів у чанку "
                f"[{lo},{hi}]: {len(ticks)}/{len(closes)}/"
                f"{len(highs)}/{len(lows)}")
        for t, cl, h, l in zip(ticks, closes, highs, lows):
            c["bars_raw"] += 1
            t = int(t)
            cl, h, l = float(cl), float(h), float(l)
            if not (cl > 0 and h > 0 and l > 0 and h >= l
                    and math.isfinite(cl)):
                c["bars_bad"] += 1
                continue
            if t in by_tick:
                c["bars_dup"] += 1
            by_tick[t] = (cl, h, l)
        lo = hi
    ts = sorted(by_tick)
    c["bars_kept"] = len(ts)
    return {"ticks": ts,
            "close": [by_tick[t][0] for t in ts],
            "high": [by_tick[t][1] for t in ts],
            "low": [by_tick[t][2] for t in ts],
            "counters": c}


# ----------------------------------------------------------------------------
# Ковзне вікно
# ----------------------------------------------------------------------------

def rolling_rv(bars: dict, end_ms: int, window_d: float, step: int,
               min_cover: float) -> dict:
    """
    Ануалізована RV на ковзному вікні (end_ms - window_d діб, end_ms].

    bars — ПОВНА 5-хв стрічка (проріджування робиться тут, щоб сітки
    рахувались з тих самих даних). step у барах по 5 хв.

    Ретерн зараховується, якщо ОБИДВА його кінці в стрічці, розрив
    між ними РІВНО один період сітки, а бар-закриття потрапляє у
    вікно. Усе інше — з лічильником, не тихо.

    Повертає {"status": "OK", "rv": ..., ...} або {"status": причина}.
    """
    if window_d <= 0:
        raise ValueError(f"window_d={window_d} має бути > 0")
    if not (0.5 <= min_cover <= 1.0):
        raise ValueError(f"min_cover={min_cover} поза [0.5, 1.0]")
    if step < 1:
        raise ValueError(f"step={step} має бути >= 1")

    period = step * MS_BAR5
    sub = subsample_bars(bars, step)
    ticks, closes = sub["ticks"], sub["close"]
    start_ms = end_ms - int(window_d * MS_DAY)

    c = {"pairs_seen": 0, "used": 0, "out_of_window": 0,
         "gap_skipped": 0, "bad_price": 0}
    var_sum = 0.0
    for i in range(1, len(ticks)):
        c["pairs_seen"] += 1
        t_prev, t_cur = ticks[i - 1], ticks[i]
        if not (start_ms < t_cur <= end_ms):
            c["out_of_window"] += 1
            continue
        if t_cur - t_prev != period:
            c["gap_skipped"] += 1
            continue
        p0, p1 = closes[i - 1], closes[i]
        if p0 <= 0 or p1 <= 0:
            c["bad_price"] += 1
            continue
        r = math.log(p1 / p0)
        var_sum += r * r
        c["used"] += 1

    expected = int(round(window_d * BARS_PER_DAY / step))
    cover = c["used"] / expected if expected > 0 else 0.0
    c["expected"] = expected
    c["cover"] = cover
    if expected <= 0:
        return {"status": "expected_zero", "counters": c}
    if cover < min_cover:
        return {"status": f"low_cover: {cover:.4f} < {min_cover}",
                "counters": c}
    rv = math.sqrt(var_sum * (ANNUALIZE / window_d))
    return {"status": "OK", "rv": rv, "var_sum": var_sum,
            "window_d": window_d, "grid_min": step * 5,
            "start_ms": start_ms, "end_ms": end_ms, "counters": c}


def rolling_by_grid(bars: dict, end_ms: int, window_d: float,
                    min_cover: float) -> dict:
    """RV на ковзному вікні по всіх сітках SUBSAMPLE_STEPS (5/15/30 хв)."""
    out = {}
    for step in SUBSAMPLE_STEPS:
        res = rolling_rv(bars, end_ms, window_d, step, min_cover)
        key = f"{step * 5}m"
        if res["status"] == "OK":
            out[key] = {"status": "OK", "rv30": res["rv"],
                        "n_rets": res["counters"]["used"],
                        "cover": res["counters"]["cover"],
                        "counters": res["counters"]}
        else:
            out[key] = {"status": res["status"],
                        "counters": res.get("counters", {})}
    return out


# ----------------------------------------------------------------------------
# Артефакти
# ----------------------------------------------------------------------------

def _stamp_of_chain(path):
    base = os.path.basename(path)
    core = base.split(".json")[0]
    parts = core.split("_")
    if len(parts) < 3:
        return None
    try:
        return datetime.strptime(parts[-2] + parts[-1],
                                 "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def chain_snapshot_times(chain_dir=CHAIN_DIR):
    """Відсортовані моменти всіх chain-знімків. Нерозпізнані — з [SKIP]."""
    paths = []
    for pat in ("chain_*.json", "chain_*.json.gz"):
        paths.extend(glob.glob(os.path.join(chain_dir, pat)))
    out = []
    for p in sorted(paths):
        st = _stamp_of_chain(p)
        if st is None:
            print(f"[SKIP] нерозпізнане ім'я chain-артефакту: {p}")
            continue
        out.append(st)
    out.sort()
    return out


def artifact_path(dt_utc, art_dir=RV_DIR):
    return os.path.join(art_dir,
                        f"{ART_PREFIX}_{dt_utc.strftime('%Y%m%d_%H%M%S')}.json")


def build_payload(dt_utc, per_cur, params):
    return {"generated_utc": datetime.now(timezone.utc).isoformat(),
            "snapshot_utc": dt_utc.isoformat(),
            "scheme": "rolling_window",
            "params": params,
            "currencies": per_cur}


def compute_for_time(bars_by_cur, dt_utc, window_d, window7_d, min_cover):
    """{cur: блок артефакту} для одного моменту. Помилок не ковтає."""
    end_ms = int(dt_utc.timestamp() * 1000)
    per_cur = {}
    for cur, bars in bars_by_cur.items():
        grid = rolling_by_grid(bars, end_ms, window_d, min_cover)
        r7 = rolling_rv(bars, end_ms, window7_d, 1, min_cover)
        ok30 = grid.get("30m", {}).get("status") == "OK"
        blk = {"status": "OK" if ok30 else "NO_RV30",
               "rv30_by_grid": grid,
               "rv7_5m": r7["rv"] if r7["status"] == "OK" else None,
               "rv7_status": r7["status"],
               "bars_counters": bars.get("counters", {})}
        if not ok30:
            blk["reason"] = grid.get("30m", {}).get("status")
        per_cur[cur] = blk
    return per_cur


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


def _synthetic_bars(n_days, sigma_ann, end_ms, seed=4242):
    """5-хв стрічка з відомою ануалізованою волатильністю (детерміновано).
    Кінець рівно на end_ms; ticks вирівняні по 5 хв."""
    n = n_days * BARS_PER_DAY
    dt = 1.0 / (ANNUALIZE * BARS_PER_DAY)
    sd = sigma_ann * math.sqrt(dt)
    x = seed
    ticks, closes = [], []
    p = 100.0
    start = end_ms - n * MS_BAR5
    for i in range(n + 1):
        ticks.append(start + i * MS_BAR5)
        closes.append(p)
        # Box-Muller на LCG — детерміновано, без random
        x = (1103515245 * x + 12345) % 2147483648
        u1 = (x + 1) / 2147483649.0
        x = (1103515245 * x + 12345) % 2147483648
        u2 = (x + 1) / 2147483649.0
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        p = p * math.exp(sd * z)
    return {"ticks": ticks, "close": closes,
            "high": list(closes), "low": list(closes),
            "counters": {"bars_kept": len(ticks)}}


def run_self_tests() -> TestCounters:
    tc = TestCounters()
    end_ms = 1756000000000 - (1756000000000 % MS_BAR5)

    # 1. відтворення закладеної sigma на 5-хв сітці
    bars = _synthetic_bars(45, 0.60, end_ms)
    r = rolling_rv(bars, end_ms, 30.0, 1, 0.96)
    tc.check("rv_recovers_sigma",
             r["status"] == "OK" and abs(r["rv"] - 0.60) < 0.06,
             f"status={r['status']} rv={r.get('rv')}")

    # 2. вікно ВІДРІЗАЄ старі дані: n_rets = рівно 30*288
    tc.check("window_exact_count",
             r["counters"]["used"] == 30 * BARS_PER_DAY,
             f"{r['counters']['used']} vs {30 * BARS_PER_DAY}")
    tc.check("window_cover_one",
             abs(r["counters"]["cover"] - 1.0) < 1e-12,
             str(r["counters"]["cover"]))

    # 3. КЛЮЧОВЕ: дзеркало слотового тесту з anomaly_calibrate.
    # Беремо 20 діб по 6 знімків, рахуємо |приріст| RV між сусідніми
    # знімками і групуємо за слотом доби. У добовій схемі один слот
    # був у 3-4.5 рази більший за решту; у ковзній усі слоти мусять
    # бути рівноцінні. Це і є критерій приймання модуля.
    long_bars = _synthetic_bars(60, 0.60, end_ms)
    n_slots, n_days_test = 6, 20
    seq = []
    for k in range(n_slots * n_days_test):
        t_k = end_ms - k * 4 * 3600000
        rk = rolling_rv(long_bars, t_k, 30.0, 6, 0.96)
        seq.append((k, rk["rv"] if rk["status"] == "OK" else None))
    seq.reverse()
    by_slot = {}
    for i in range(1, len(seq)):
        k_prev, v_prev = seq[i - 1]
        k_cur, v_cur = seq[i]
        if v_prev is None or v_cur is None:
            continue
        by_slot.setdefault(k_cur % n_slots, []).append(abs(v_cur - v_prev))
    meds = []
    for s in sorted(by_slot):
        xs = sorted(by_slot[s])
        meds.append(xs[len(xs) // 2])
    slot_ratio = (max(meds) / min(meds)) if meds and min(meds) > 0 \
        else float("inf")
    # Метрика — ТА САМА, що в anomaly_calibrate: перестановочний тест
    # H0 «розподіл |приросту| однаковий по слотах». Порівнювати max/min
    # шести медіан по ~20 точок не можна: на випадковому блуканні
    # 2-2.5x виникає сам собою, тобто такий поріг міряв би шум, а не
    # слотовий ефект. Тут потрібне НЕвідхилення H0.
    def _slot_perm_p(groups, n_perm=400, seed=20260822):
        sizes = [(s, len(groups[s])) for s in sorted(groups)]
        pool = []
        for s in sorted(groups):
            pool.extend(groups[s])

        def _stat(pl):
            ms = []
            pos = 0
            for _s, n_ in sizes:
                seg = sorted(pl[pos:pos + n_])
                ms.append(seg[len(seg) // 2] if seg else 0.0)
                pos += n_
            return max(ms) - min(ms)

        obs = _stat(pool)
        x = seed
        n_ge = 0
        for _ in range(n_perm):
            sh = list(pool)
            for i in range(len(sh) - 1, 0, -1):
                x = (1103515245 * x + 12345) % 2147483648
                j = int((x / 2147483648.0) * (i + 1))
                j = min(j, i)
                sh[i], sh[j] = sh[j], sh[i]
            if _stat(sh) >= obs:
                n_ge += 1
        return (n_ge + 1) / (n_perm + 1)

    slot_p = _slot_perm_p(by_slot)
    tc.check("rolling_no_slot_effect",
             len(meds) == n_slots and slot_p > 0.05,
             f"p = {slot_p:.4f} (H0 не відхиляється), "
             f"max/min медіан = {slot_ratio:.2f}; "
             f"добова схема давала p = 0.0060 / 0.0005")

    # НЕГАТИВНИЙ КОНТРОЛЬ: без нього попередній тест міг би проходити
    # просто через брак потужності. Підсуваємо ту саму послідовність,
    # але з добовою сходинкою у слоті 1 (аналог оновлення rv о 00:40) —
    # тест ЗОБОВ'ЯЗАНИЙ її побачити.
    by_slot_step = {}
    for s in by_slot:
        by_slot_step[s] = list(by_slot[s])
    bump = 3.0 * max(meds)
    by_slot_step[1] = [v + bump for v in by_slot_step[1]]
    p_step = _slot_perm_p(by_slot_step)
    tc.check("slot_test_has_power",
             p_step < 0.05,
             f"p = {p_step:.4f} на штучній добовій сходинці "
             f"(тест мусить її ловити, інакше попередній тест порожній)")

    # 4. сітки 5/15/30 хв дають близькі оцінки (плато субсемплінгу)
    g = rolling_by_grid(bars, end_ms, 30.0, 0.96)
    ok_all = all(g[k]["status"] == "OK" for k in ("5m", "15m", "30m"))
    tc.check("grids_all_ok", ok_all, str({k: g[k]["status"] for k in g}))
    if ok_all:
        vals = [g[k]["rv30"] for k in ("5m", "15m", "30m")]
        tc.check("grids_close", (max(vals) - min(vals)) < 0.12,
                 f"{[round(v, 4) for v in vals]}")
        tc.check("grid_30m_count",
                 g["30m"]["n_rets"] == 30 * BARS_PER_DAY // 6,
                 str(g["30m"]["n_rets"]))

    # 5. дірки в стрічці -> gap_skipped і падіння покриття, не тихо
    holed = {"ticks": [], "close": [], "high": [], "low": []}
    for i, t in enumerate(bars["ticks"]):
        if 9000 <= i < 9600:   # всередині 30-денного вікна
            continue
        holed["ticks"].append(t)
        holed["close"].append(bars["close"][i])
        holed["high"].append(bars["high"][i])
        holed["low"].append(bars["low"][i])
    rh = rolling_rv(holed, end_ms, 30.0, 1, 0.96)
    tc.check("holes_lower_cover", rh["status"].startswith("low_cover"),
             rh["status"])
    tc.check("holes_counted",
             rh["counters"]["gap_skipped"] >= 1
             and rh["counters"]["used"] < 30 * BARS_PER_DAY,
             f"gap={rh['counters']['gap_skipped']} "
             f"used={rh['counters']['used']}")
    # той самий ряд із м'яким порогом проходить — відмова саме за
    # покриттям, а не через поломку
    rh2 = rolling_rv(holed, end_ms, 30.0, 1, 0.5)
    tc.check("holes_pass_with_loose_cover", rh2["status"] == "OK",
             rh2["status"])

    # 6. закоротка історія -> явна відмова, не мовчазна оцінка
    short = _synthetic_bars(10, 0.60, end_ms)
    rs = rolling_rv(short, end_ms, 30.0, 1, 0.96)
    tc.check("short_history_refuses", rs["status"].startswith("low_cover"),
             rs["status"])

    # 7. валідація аргументів
    tc.check("bad_window_refuses",
             _raises(lambda: rolling_rv(bars, end_ms, 0.0, 1, 0.96),
                     ValueError), "")
    tc.check("bad_cover_refuses",
             _raises(lambda: rolling_rv(bars, end_ms, 30.0, 1, 1.5),
                     ValueError), "")
    tc.check("bad_step_refuses",
             _raises(lambda: rolling_rv(bars, end_ms, 30.0, 0, 0.96),
                     ValueError), "")
    tc.check("empty_range_refuses",
             _raises(lambda: fetch_5m_range("BTC", 100, 100), ValueError), "")

    # 8. детермінованість
    r_a = rolling_rv(bars, end_ms, 30.0, 1, 0.96)
    r_b = rolling_rv(bars, end_ms, 30.0, 1, 0.96)
    tc.check("deterministic", r_a["rv"] == r_b["rv"], str(r_a["rv"]))

    # 9. ануалізація: RV7 і RV30 на однорідному ряді збігаються
    r7 = rolling_rv(bars, end_ms, 7.0, 1, 0.96)
    tc.check("annualization_consistent",
             r7["status"] == "OK" and abs(r7["rv"] - r["rv"]) < 0.12,
             f"rv7={r7.get('rv')} rv30={r['rv']}")

    # 10. схема артефакту — та, яку читає anomaly_detect.vrp_from_rv
    dt_fix = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc)
    per_cur = compute_for_time({"BTC": bars}, dt_fix, 30.0, 7.0, 0.96)
    blk = per_cur["BTC"]
    tc.check("payload_shape_readable_by_detector",
             blk["status"] == "OK"
             and blk["rv30_by_grid"]["30m"]["status"] == "OK"
             and isinstance(blk["rv30_by_grid"]["30m"]["rv30"], float),
             str(blk["status"]))
    tc.check("payload_has_no_iv30", "iv30" not in blk,
             "чисельник vrp30 береться з chain-знімка, не звідси")

    # 11. ім'я артефакту не перетинається з glob 'rv_*'
    p = artifact_path(datetime(2026, 8, 22, 8, 10, tzinfo=timezone.utc),
                      "/tmp")
    base = os.path.basename(p)
    tc.check("artifact_name_disjoint_from_rv",
             base == "rvroll_20260822_081000.json"
             and not base.startswith("rv_"), base)

    # 12. розбір імені chain-артефакту (обидва розширення)
    s1 = _stamp_of_chain("chain_20260822_081001.json")
    s2 = _stamp_of_chain("chain_20260822_081001.json.gz")
    tc.check("chain_stamp_parse",
             s1 is not None and s1 == s2 and s1.hour == 8, str(s1))
    tc.check("chain_stamp_bad_name",
             _stamp_of_chain("chain_broken.json") is None, "")

    # --- ENV-WARN (quiet=True: самотест не сміє смітити в живий лог) ---
    _env = {"VOLEDGE_ROLL_MODE": "backfill", "VOLEDGE_RVROLL_MODE": "backfill"}
    _unk = warn_unknown_env(KNOWN_ENV, environ=_env, quiet=True)
    # Рівно та змінна, що коштувала двох прогонів бекфілу 22 сер.
    tc.check("env_warn_catches_rvroll_mode_typo",
             _unk == ["VOLEDGE_RVROLL_MODE"], str(_unk))
    tc.check("env_warn_ignores_known", "VOLEDGE_ROLL_MODE" not in _unk)
    _src = ""
    try:
        with open(os.path.abspath(__file__), "r",
                  encoding="utf-8") as _envfh:
            _src = _envfh.read()
    except OSError:
        _src = ""
    _read = set(re.findall(
        r'os\.environ\.get\(\s*"(VOLEDGE_[A-Z0-9_]+)"', _src))
    tc.check("known_env_covers_every_read",
             bool(_src) and _read and _read <= set(KNOWN_ENV),
             f"не оголошені: {sorted(_read - set(KNOWN_ENV))}")
    tc.check("known_env_has_no_dead_names",
             bool(_src) and set(KNOWN_ENV) <= _read | {"VOLEDGE_OFFLINE"},
             f"зайві: {sorted(set(KNOWN_ENV) - _read - {'VOLEDGE_OFFLINE'})}")

    print(f"[TEST] passed {tc.passed}/{tc.run}")
    for f_ in tc.failures:
        print(f"[TEST] FAILURE detail: {f_}")
    return tc


# ----------------------------------------------------------------------------
# Live / backfill
# ----------------------------------------------------------------------------

def run_live(currencies, window_d, window7_d, min_cover, pad_d) -> int:
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    end_ms -= end_ms % MS_BAR5          # останній закритий 5-хв бар
    dt_end = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc)
    start_ms = end_ms - int((window_d + pad_d) * MS_DAY)
    print(f"[OVERALL] режим live, кінець вікна {dt_end.isoformat()}")

    bars_by_cur = {}
    for cur in currencies:
        b = fetch_5m_range(cur, start_ms, end_ms)
        print(f"[OVERALL] {cur}: чанків {b['counters']['chunks']}, "
              f"барів {b['counters']['bars_kept']}, "
              f"дублів {b['counters']['bars_dup']}, "
              f"биттих {b['counters']['bars_bad']}")
        bars_by_cur[cur] = b

    per_cur = compute_for_time(bars_by_cur, dt_end, window_d, window7_d,
                               min_cover)
    params = {"window_d": window_d, "window7_d": window7_d,
              "min_cover": min_cover, "pad_d": pad_d, "mode": "live"}
    os.makedirs(RV_DIR, exist_ok=True)
    out_path = artifact_path(dt_end)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(build_payload(dt_end, per_cur, params), f,
                  ensure_ascii=False, indent=1)

    n_ok = 0
    for cur, blk in sorted(per_cur.items()):
        g = blk["rv30_by_grid"].get("30m", {})
        if blk["status"] == "OK":
            n_ok += 1
            print(f"[OK] {cur}: RV30_30m={g['rv30']:.4f} "
                  f"n_rets={g['n_rets']} cover={g['cover']:.4f} "
                  f"RV7_5m={blk['rv7_5m']:.4f}")
        else:
            print(f"[SKIP] {cur}: {blk.get('reason')}")
    print(f"[OVERALL] артефакт: {out_path}")
    print(f"[SUMMARY] валют OK: {n_ok}/{len(per_cur)}")
    return 0 if n_ok == len(per_cur) else 1


def run_backfill(currencies, window_d, window7_d, min_cover, pad_d,
                 overwrite) -> int:
    times = chain_snapshot_times()
    if not times:
        print(f"[SUMMARY] нуль chain-знімків у {CHAIN_DIR}")
        return 1
    print(f"[OVERALL] режим backfill, chain-знімків: {len(times)}, "
          f"від {times[0].isoformat()} до {times[-1].isoformat()}")

    start_ms = int(times[0].timestamp() * 1000) \
        - int((window_d + pad_d) * MS_DAY)
    end_ms = int(times[-1].timestamp() * 1000) + MS_BAR5
    span_d = (end_ms - start_ms) / MS_DAY
    print(f"[OVERALL] один запит на валюту покриває {span_d:.1f} діб")

    bars_by_cur = {}
    for cur in currencies:
        b = fetch_5m_range(cur, start_ms, end_ms)
        print(f"[OVERALL] {cur}: чанків {b['counters']['chunks']}, "
              f"барів {b['counters']['bars_kept']}, "
              f"дублів {b['counters']['bars_dup']}, "
              f"биттих {b['counters']['bars_bad']}")
        bars_by_cur[cur] = b

    os.makedirs(RV_DIR, exist_ok=True)
    params = {"window_d": window_d, "window7_d": window7_d,
              "min_cover": min_cover, "pad_d": pad_d, "mode": "backfill"}
    c = {"written": 0, "exists_skipped": 0, "partial": 0, "failed": 0}
    for dt in times:
        out_path = artifact_path(dt)
        if os.path.exists(out_path) and not overwrite:
            c["exists_skipped"] += 1
            continue
        per_cur = compute_for_time(bars_by_cur, dt, window_d, window7_d,
                                   min_cover)
        n_ok = sum(1 for b in per_cur.values() if b["status"] == "OK")
        if n_ok == 0:
            c["failed"] += 1
            reasons = {k: v.get("reason") for k, v in per_cur.items()}
            print(f"[SKIP] {dt.isoformat()}: жодної валюти — {reasons}")
            continue
        if n_ok < len(per_cur):
            c["partial"] += 1
            print(f"[SKIP] {dt.isoformat()}: частково, OK "
                  f"{n_ok}/{len(per_cur)}")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(build_payload(dt, per_cur, params), f,
                      ensure_ascii=False, indent=1)
        c["written"] += 1

    print(f"[SUMMARY] записано: {c['written']}, вже існували: "
          f"{c['exists_skipped']}, часткові: {c['partial']}, "
          f"провалено: {c['failed']}, усього моментів: {len(times)}")
    return 0 if c["failed"] == 0 else 1


def main() -> int:
    print("=" * 72)
    print("Vol-Edge :: rv_rolling (ковзне вікно RV)")
    print("=" * 72)
    # ПЕРЕД розбором значень: нерозпізнана змінна = тихий дефолт. Саме
    # так 22 сер бекфіл двічі відпрацював у режимі live — обгортка
    # експортувала VOLEDGE_RVROLL_MODE, а модуль читає
    # VOLEDGE_ROLL_MODE. Раніше попередження друкувалось ПІСЛЯ
    # валідації, тобто падіння на іншій змінній ховало його.
    warn_unknown_env(KNOWN_ENV, label="rv_rolling")
    offline_raw = os.environ.get("VOLEDGE_OFFLINE", "0")
    cur_raw = os.environ.get("VOLEDGE_CURRENCIES", "BTC,ETH")
    mode_raw = os.environ.get("VOLEDGE_ROLL_MODE", "live")
    w_raw = os.environ.get("VOLEDGE_ROLL_WINDOW_D", "30")
    w7_raw = os.environ.get("VOLEDGE_ROLL_WINDOW7_D", "7")
    cov_raw = os.environ.get("VOLEDGE_ROLL_MIN_COVER", "0.96")
    pad_raw = os.environ.get("VOLEDGE_ROLL_PAD_D", "3")
    ow_raw = os.environ.get("VOLEDGE_ROLL_OVERWRITE", "0")

    offline = offline_raw.strip() == "1"
    overwrite = ow_raw.strip() == "1"
    mode = mode_raw.strip().lower()
    try:
        window_d = float(w_raw)
        window7_d = float(w7_raw)
        min_cover = float(cov_raw)
        pad_d = float(pad_raw)
    except ValueError:
        raise RuntimeError(f"Невалідні env: ROLL_WINDOW_D={w_raw!r} "
                           f"ROLL_WINDOW7_D={w7_raw!r} "
                           f"ROLL_MIN_COVER={cov_raw!r} "
                           f"ROLL_PAD_D={pad_raw!r}")
    if mode not in ("live", "backfill"):
        raise RuntimeError(f"VOLEDGE_ROLL_MODE={mode_raw!r} — має бути "
                           f"'live' або 'backfill'")
    if window_d < 7:
        raise RuntimeError(f"ROLL_WINDOW_D={window_d} < 7")
    if window7_d < 2:
        raise RuntimeError(f"ROLL_WINDOW7_D={window7_d} < 2")
    if not (0.5 <= min_cover <= 1.0):
        raise RuntimeError(f"ROLL_MIN_COVER={min_cover} поза [0.5, 1.0]")
    if pad_d < 0:
        raise RuntimeError(f"ROLL_PAD_D={pad_d} < 0")
    currencies = [c.strip().upper() for c in cur_raw.split(",")
                  if c.strip()]
    for cur in currencies:
        if cur not in PERP_NAME:
            raise RuntimeError(f"Невідома валюта {cur!r} у "
                               f"VOLEDGE_CURRENCIES")


    print(f"[ENV] VOLEDGE_OFFLINE={offline_raw!r} -> {offline}")
    print(f"[ENV] VOLEDGE_CURRENCIES={cur_raw!r} -> {currencies}")
    print(f"[ENV] VOLEDGE_ROLL_MODE={mode_raw!r} -> {mode}")
    print(f"[ENV] VOLEDGE_ROLL_WINDOW_D={w_raw!r} -> {window_d}")
    print(f"[ENV] VOLEDGE_ROLL_WINDOW7_D={w7_raw!r} -> {window7_d}")
    print(f"[ENV] VOLEDGE_ROLL_MIN_COVER={cov_raw!r} -> {min_cover}")
    print(f"[ENV] VOLEDGE_ROLL_PAD_D={pad_raw!r} -> {pad_d}")
    print(f"[ENV] VOLEDGE_ROLL_OVERWRITE={ow_raw!r} -> {overwrite}")

    print("\n--- ОФЛАЙН-САМОТЕСТИ " + "-" * 50)
    tc = run_self_tests()
    print(f"[SUMMARY] самотести: {tc.run}, пройдено: {tc.passed}, "
          f"провалено: {len(tc.failures)}")
    if tc.failures:
        return 1
    if offline:
        print("[SUMMARY] VOLEDGE_OFFLINE=1 -> live пропущено явно.")
        return 0

    print(f"\n--- ПРОГІН ({mode}) " + "-" * 48)
    t0 = time.time()
    if mode == "live":
        rc = run_live(currencies, window_d, window7_d, min_cover, pad_d)
    else:
        rc = run_backfill(currencies, window_d, window7_d, min_cover,
                          pad_d, overwrite)
    print(f"[OVERALL] час прогону: {time.time() - t0:.1f} с")
    return rc


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
