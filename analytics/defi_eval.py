#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[EN] Read-only DEFI evaluator: Derive option quotes vs Deribit on exact
(expiry, strike, type) matches. Derive IV on its own synthetic forward;
tradability = signed IV difference against bid/ask exceeding the
pre-registered round-trip cost. Pipeline negative control and per-bucket
positive control (-3 vp); verdict is a pure, tested function.

--- Ukrainian original below ---
Vol-Edge :: analytics/defi_eval.py — READ-ONLY оцінювач гіпотези DEFI.

ПИТАННЯ (пре-реєстровано 28 вер, STATE «Пре-реєстрація торгованості»):
чи квотує Derive опціони поза коштами статичної пари Derive/Deribit?
Дві незалежні колонки: T1 (еталон 1 BTC / 10 ETH) і T2 (бюджет $1000,
лише ETH, 1 ETH, застава Deribit у ціні).

ЩО МІРЯЄТЬСЯ
------------
Лише ТОЧНІ збіги (валюта, експірація, страйк, C/P) між рядками
chain-артефакту Deribit і записами defiquotes-артефакту Derive одного
слота. Жодної інтерполяції, жодної SVI-моделі: обидві IV — з котирувань.
Рядки chain — лише OTM-сторона кожного страйка (як іде у фіт), тож ITM
Derive-інструменти не мають пари -> лічильник no_match, не дроп мовчки.

ОДИНИЦІ І ФОРВАРДИ (рішення до першого прогону)
------------------------------------------------
Deribit: IV ПЕРЕРАХОВУЄТЬСЯ тут із сирих bid/ask (монета) як coin*F_B на
НАШОМУ форварді F_B (synthetic з parity), час знімка chain.
ЧОМУ НЕ iv_bid/iv_ask З АРТЕФАКТУ (знайдено негативним контролем 30 вер,
до першого погляду на d_iv): deribit_chain.py множить премію на
index_price (СПОТ), а IV рахує на форварді — у Black-76 для інверсного
опціону USD-ціна = coin*F. Самопара через шлях Derive (coin*F) давала
84 «торговані» інструменти і медіану d_mid 0.115 vp. Розбіжність
артефакт-проти-перерахунку друкується діагностикою (iv_art_minus_ours).
Derive: ціни USDC ділимо на df (дисконт з option_pricing.df), тобто
переводимо у долари на дату експірації — так само, як премія в монеті
на Deribit через F_B є вартістю на дату експірації. IV Derive рахується
на ВЛАСНОМУ форварді Derive F_D (parity: K + (C_mid - P_mid)/df, медіана
3 пар найближче до індексу), на ВЛАСНОМУ tau (час запису).
ЧОМУ СВІЙ ФОРВАРД: між знімками 2+ хв, спот рухається; IV при фіксованому
страйку значно стабільніша за ціну. Порівнюємо РІВЕНЬ ВОЛАТИЛЬНОСТІ.
Базис форвардів (F_D vs F_B) — окрема величина, друкується діагностикою
по кожній експірації; в d_iv не входить.
ПОПРАВКА ДО STATE: «однаковий знак d_iv на коллі й путі = форвард» —
навпаки: похибка форварду дає ПРОТИЛЕЖНІ знаки на C і P. Тут перевірка
знаків неможлива (Deribit-рядки лише OTM), замість неї F_D-F_B прямо.

ПОРІГ НА ІНСТРУМЕНТ (vp), без стелі 12.5% (консервативно, пре-реєстр.):
  T = (0.0003 F + 0.0003 F + 2*0.00015 F + base_fee/size) / vega_vp
      [+ для T2 при купівлі на Deribit: 0.0005*днів*премія_USD / vega_vp]
vega_vp = vega_usd(F_B, K, tau_B, iv_mid_B)/100. Розрахунок обох ніг як
ITM; Derive — [SETTLE-FEE-ASSUMED].

ТОРГОВАНИЙ: max(iv_bid_B - iv_ask_D, iv_bid_D - iv_ask_B) > T (у vp).

КОНТРОЛІ (у тому самому прогоні)
--------------------------------
Уточнення до пре-реєстрації, зроблене ДО першої пари: «негативний на
точних збігах, d_iv ~ 0» у буквальному прочитанні — це сам вимір, не
контроль. Реалізовано як контроль КОНВЕЄРА:
- НЕГАТИВНИЙ: Deribit проти самого себе через ТОЙ САМИЙ шлях Derive
  (ціна -> USD -> IV). Торгованих має бути 0, |медіана d_mid| <= 0.05 vp.
  Провал -> вердикт INVALID по обох колонках.
- ПОЗИТИВНИЙ: синтетичний Derive = Deribit зі зсувом IV на -3 vp (ціни
  перераховані і пропущені через той самий солвер). Бакет, де медіана
  частки торгованих < 5% (поріг PASS), позначається [BLIND]: він не
  бачить навіть 3-vp ефекту і НЕ бере участі ні в PASS, ні в FAIL.
  Усі бакети BLIND -> INVALID.

ВЕРДИКТ (окремо T1 і T2), частка торгованих серед двосторонніх пар
у бакеті на кожному знімку (знімок з < MIN_INSTR парами в бакеті
пропускається з лічильником):
- INVALID: негативний контроль провалено, або всі бакети BLIND;
- INSUFFICIENT: < MIN_DAYS різних діб (PASS/FAIL не друкуються);
- PASS: медіана частки >= 5% у бакеті І бакет проходить у >= 2/3 діб;
- FAIL: медіана частки < 1% у ВСІХ зрячих бакетах;
- INCONCLUSIVE: інакше.
MIN_DAYS=3 і MIN_INSTR=5 — уточнення ДО першої пари.

ДІАГНОСТИКА: n_eff впоперек страйків (lag-1 кореляція best-edge по
страйку в межах експірації і типу), базис форвардів, частка
двосторонніх, розподіл d_mid.

Env: VOLEDGE_DEFI_E_CHAIN_DIR, _DEFI_DIR, _OUT_DIR, _SYNC_S (600),
_NO_WRITE (0), VOLEDGE_OFFLINE (1 -> лише самотести).
Чистий stdlib. Подвійний клік + input() завжди.
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
_REPO_ROOT = os.path.dirname(_THIS_DIR)
_CORE_DIR = os.path.join(_REPO_ROOT, "core")
for _p in (_CORE_DIR, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from black76_greeks import implied_vol, price_usd, vega_usd  # noqa: E402
from env_flags import merge_known, warn_unknown_env  # noqa: E402

DEFAULT_CHAIN_DIR = os.path.join(_REPO_ROOT, "artifacts", "chain")
DEFAULT_DEFI_DIR = os.path.join(_REPO_ROOT, "artifacts", "defi")
DEFAULT_OUT_DIR = os.path.join(_REPO_ROOT, "artifacts", "defieval")

KNOWN_ENV = merge_known((
    "VOLEDGE_DEFI_E_CHAIN_DIR",
    "VOLEDGE_DEFI_E_DEFI_DIR",
    "VOLEDGE_DEFI_E_OUT_DIR",
    "VOLEDGE_DEFI_E_SYNC_S",
    "VOLEDGE_DEFI_E_NO_WRITE",
))

# --- тарифи (виміряно 28 вер, STATE «Кошти торгівлі») ---
FEE_DERIBIT = 0.0003
FEE_DERIVE_TAKER = 0.0003
FEE_SETTLE = 0.00015          # поставка Deribit; Derive — ПРИПУЩЕННЯ
BASE_FEE_USD = 0.5
COLLATERAL_PER_DAY = 0.0005   # Deribit, від'ємне еквіті валюти
SIZE_T1 = {"BTC": 1.0, "ETH": 10.0}
SIZE_T2 = {"ETH": 1.0}        # BTC у T2 виключено ЯВНО

# --- пре-реєстровані пороги вердикту ---
PASS_SHARE = 0.05
FAIL_SHARE = 0.01
DAY_FRAC = 2.0 / 3.0
MIN_DAYS = 3
MIN_INSTR = 5
NEG_TOL_VP = 0.05
POS_SHIFT = 0.03              # -3 vp

BUCKETS = (("d000-003", 0.0, 3.0), ("d004-014", 3.0, 14.0),
           ("d015-045", 14.0, 45.0), ("d046-120", 45.0, 120.0),
           ("d121+", 120.0, 1e9))

CANON_HOURS = (0, 4, 8, 12, 16, 20)
CHAIN_MIN = (5, 15)
DEFI_MIN = (10, 20)
FWD_PAIRS = 3

_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


class EnvError(ValueError):
    pass


class AllDroppedError(RuntimeError):
    pass


# ----------------------------------------------------------------------------
# Оточення
# ----------------------------------------------------------------------------

def env_str(name, default):
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def env_int(name, default, minimum=None):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        x = int(v)
    except ValueError:
        raise EnvError(f"{name}={v!r}: очікується ціле")
    if minimum is not None and x < minimum:
        raise EnvError(f"{name}={x}: мінімум {minimum}")
    return x


# ----------------------------------------------------------------------------
# Розбір
# ----------------------------------------------------------------------------

def fnum(x):
    """Рядок/число -> float; None/порожнє/нечислове -> None (НЕ нуль)."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def deribit_label_to_ymd(label):
    """'30OCT26' -> '20261030'. Без locale (strptime %b залежить від нього)."""
    m = re.fullmatch(r"(\d{1,2})([A-Z]{3})(\d{2})", label)
    if not m or m.group(2) not in _MONTHS:
        raise ValueError(f"нерозпізнана експірація Deribit {label!r}")
    return "20%s%02d%02d" % (m.group(3), _MONTHS[m.group(2)], int(m.group(1)))


def bucket_of(days):
    for name, lo, hi in BUCKETS:
        if lo < days <= hi or (lo == 0.0 and days <= hi):
            return name
    raise ValueError(f"days={days}: поза бакетами")


def load_json(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as _gfh:
            return json.load(_gfh)
    with open(path, "r", encoding="utf-8") as _jfh:
        return json.load(_jfh)


def parse_chain_ts(name):
    m = re.search(r"chain_(\d{8})_(\d{6})\.json(\.gz)?$", name)
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2),
                             "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def parse_defi_ts(name):
    m = re.search(r"defiquotes_(\d{8})T(\d{6})Z[._]json(\.gz)?$", name)
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2),
                             "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def in_slot(ts, minute_band):
    return (ts.hour in CANON_HOURS
            and minute_band[0] <= ts.minute <= minute_band[1])


def pair_artifacts(chain_paths, defi_paths, sync_s, c):
    """Білий список слотів -> пари (chain, defi) того самого слота."""
    chains = {}
    for p in chain_paths:
        ts = parse_chain_ts(os.path.basename(p))
        if ts is None:
            c["chain_bad_name"] += 1
            continue
        if not in_slot(ts, CHAIN_MIN):
            c["chain_off_grid"] += 1
            continue
        chains[(ts.date(), ts.hour)] = (ts, p)
    pairs = []
    for p in defi_paths:
        ts = parse_defi_ts(os.path.basename(p))
        if ts is None:
            c["defi_bad_name"] += 1
            continue
        if not in_slot(ts, DEFI_MIN):
            c["defi_off_grid"] += 1
            continue
        hit = chains.get((ts.date(), ts.hour))
        if hit is None:
            c["defi_no_chain"] += 1
            continue
        if abs((ts - hit[0]).total_seconds()) > sync_s:
            c["pair_out_of_sync"] += 1
            continue
        pairs.append((hit[1], p, ts))
    pairs.sort(key=lambda x: x[2])
    return pairs


# ----------------------------------------------------------------------------
# Витяг ніг
# ----------------------------------------------------------------------------

def deribit_legs(chain_cur, c, iv_diag=None):
    """(ymd, K, cp) -> нога Deribit; IV з coin*F. Лише двосторонні."""
    out = {}
    for sl in chain_cur.get("slices") or []:
        if sl.get("status") != "OK":
            c["b_slice_not_ok"] += 1
            continue
        F, tau = fnum(sl.get("forward")), fnum(sl.get("tau"))
        if not F or not tau or tau <= 0:
            c["b_slice_bad_ft"] += 1
            continue
        ymd = deribit_label_to_ymd(sl["expiry"])
        for r in sl.get("rows") or []:
            c["b_rows"] += 1
            bid, ask = fnum(r.get("bid")), fnum(r.get("ask"))
            if not (bid and ask and bid > 0 and ask > bid):
                c["b_not_two_sided"] += 1
                continue
            K, cp = float(r["K"]), int(r["cp"])
            try:
                ivb = implied_vol(bid * F, F, K, tau, cp)
                iva = implied_vol(ask * F, F, K, tau, cp)
            except (ValueError, RuntimeError):
                c["b_iv_fail"] += 1
                continue
            art = fnum(r.get("iv_bid"))
            if art is not None and iv_diag is not None:
                iv_diag.append((art - ivb) * 100.0)
            key = (ymd, K, cp)
            out[key] = {"F": F, "tau": tau, "K": float(r["K"]),
                        "cp": int(r["cp"]), "bid_coin": bid,
                        "ask_coin": ask, "iv_bid": ivb, "iv_ask": iva,
                        "iv_mid": 0.5 * (ivb + iva), "name": r.get("name")}
    return out


def derive_quotes(defi_cur, c):
    """(ymd, K, cp) -> котирування Derive у доларах на дату експірації."""
    out = {}
    for rec in defi_cur.get("records") or []:
        c["d_records"] += 1
        if rec.get("status") != "ok":
            c["d_status_not_ok"] += 1
            continue
        tr = rec.get("ticker_raw") or {}
        op = tr.get("option_pricing") or {}
        b, a = fnum(tr.get("b")), fnum(tr.get("a"))
        bs, as_ = fnum(tr.get("B")), fnum(tr.get("A"))
        df = fnum(op.get("df"))
        t_ms = fnum(tr.get("t")) or fnum(rec.get("ts_fetch_ms"))
        exp_s = fnum(rec.get("expiry_sec"))
        if not (b and a and bs and as_ and b > 0 and a > b):
            c["d_not_two_sided"] += 1
            continue
        if not (df and 0.5 < df <= 1.0):
            c["d_bad_df"] += 1
            continue
        if not (t_ms and exp_s):
            c["d_bad_time"] += 1
            continue
        tau = (exp_s - t_ms / 1000.0) / (365.0 * 86400.0)
        if tau <= 0:
            c["d_expired"] += 1
            continue
        cp = {"C": 1, "P": -1}.get(rec.get("kind"))
        if cp is None:
            c["d_bad_kind"] += 1
            continue
        key = (rec["expiry_ymd"], float(rec["strike"]), cp)
        out[key] = {"bid": b / df, "ask": a / df, "tau": tau,
                    "index": fnum(tr.get("I")), "K": float(rec["strike"]),
                    "cp": cp}
    return out


def derive_forwards(quotes, c):
    """ymd -> F_D з parity: медіана FWD_PAIRS пар, найближчих до індексу."""
    by_exp = {}
    for (ymd, K, cp), q in quotes.items():
        by_exp.setdefault(ymd, {}).setdefault(K, {})[cp] = q
    out = {}
    for ymd, strikes in by_exp.items():
        cand = []
        for K, sides in strikes.items():
            if 1 in sides and -1 in sides:
                cm = 0.5 * (sides[1]["bid"] + sides[1]["ask"])
                pm = 0.5 * (sides[-1]["bid"] + sides[-1]["ask"])
                idx = sides[1]["index"] or K
                cand.append((abs(K - idx), K + cm - pm))
        if len(cand) < FWD_PAIRS:
            c["d_no_forward_expiry"] += 1
            continue
        cand.sort()
        vals = sorted(v for _, v in cand[:FWD_PAIRS])
        out[ymd] = vals[len(vals) // 2]
    return out


# ----------------------------------------------------------------------------
# Ядро: одна пара інструментів (ЧИСТА функція)
# ----------------------------------------------------------------------------

def threshold_vp(F, vega_vp, size, days=0.0, premium_usd=0.0,
                 collateral=False):
    """Поріг у vp на один контракт номіналу 1 монета."""
    fees = (FEE_DERIBIT + FEE_DERIVE_TAKER + 2.0 * FEE_SETTLE) * F
    cost = fees + BASE_FEE_USD / size
    if collateral:
        cost += COLLATERAL_PER_DAY * days * premium_usd
    return cost / vega_vp


def edges_vp(leg_b, d_bid_usd, d_ask_usd, F_D, tau_D):
    """(edge_buyD_sellB, edge_buyB_sellD, d_mid) у vp, або str-причина."""
    K, cp = leg_b["K"], leg_b["cp"]
    try:
        ivd_b = implied_vol(d_bid_usd, F_D, K, tau_D, cp)
        ivd_a = implied_vol(d_ask_usd, F_D, K, tau_D, cp)
    except (ValueError, RuntimeError):
        return "iv_fail"
    e1 = (leg_b["iv_bid"] - ivd_a) * 100.0
    e2 = (ivd_b - leg_b["iv_ask"]) * 100.0
    d_mid = (0.5 * (ivd_b + ivd_a) - leg_b["iv_mid"]) * 100.0
    return e1, e2, d_mid


def evaluate_instrument(leg_b, d_bid_usd, d_ask_usd, F_D, tau_D, currency):
    """Результат по інструменту для T1 і T2. Чиста функція."""
    res = edges_vp(leg_b, d_bid_usd, d_ask_usd, F_D, tau_D)
    if isinstance(res, str):
        return res
    e1, e2, d_mid = res
    F, tau, K = leg_b["F"], leg_b["tau"], leg_b["K"]
    vega = vega_usd(F, K, tau, leg_b["iv_mid"]) / 100.0
    if not vega > 0:
        return "vega_zero"
    days = tau * 365.0
    out = {"e1": e1, "e2": e2, "best": max(e1, e2), "d_mid": d_mid,
           "days": days, "bucket": bucket_of(days), "vega_vp": vega}
    t1 = threshold_vp(F, vega, SIZE_T1[currency])
    out["T1"] = t1
    out["trade_T1"] = out["best"] > t1
    if currency in SIZE_T2:
        prem = leg_b["ask_coin"] * F
        t2_sellB = threshold_vp(F, vega, SIZE_T2[currency])
        t2_buyB = threshold_vp(F, vega, SIZE_T2[currency], days, prem,
                               collateral=True)
        out["T2"] = (t2_sellB, t2_buyB)
        out["trade_T2"] = (e1 > t2_sellB) or (e2 > t2_buyB)
    return out


def usd_from_iv(leg_b, iv, F, tau):
    return price_usd(F, leg_b["K"], tau, iv, leg_b["cp"])


# ----------------------------------------------------------------------------
# Один знімок
# ----------------------------------------------------------------------------

def _new_tally():
    return {"n": 0, "t1": 0, "t2": 0, "n_eth": 0}


def evaluate_snapshot(chain, defi, c):
    """Повертає словник по бакетах: реальний вимір і обидва контролі."""
    chain_by_cur = {x.get("currency"): x for x in chain}
    snap = {"real": {}, "pos": {}, "neg": {}, "neg_dmid": [],
            "dmid": [], "fwd_gap_bp": [], "neff": [], "iv_art": []}
    for cur, dcur in (defi.get("currencies") or {}).items():
        if cur not in SIZE_T1:
            c["cur_excluded"] += 1
            continue
        ccur = chain_by_cur.get(cur)
        if ccur is None:
            c["cur_no_chain"] += 1
            continue
        legs = deribit_legs(ccur, c, snap["iv_art"])
        quotes = derive_quotes(dcur, c)
        fwd = derive_forwards(quotes, c)
        fb_by_ymd = {k[0]: v["F"] for k, v in legs.items()}
        for ymd, fd in fwd.items():
            if ymd in fb_by_ymd:
                snap["fwd_gap_bp"].append(
                    (cur, ymd, (fd / fb_by_ymd[ymd] - 1.0) * 1e4))
        series = {}
        n_in, n_out = 0, 0
        for key, q in quotes.items():
            leg = legs.get(key)
            if leg is None:
                c["no_match"] += 1
                continue
            if key[0] not in fwd:
                c["match_no_forward"] += 1
                continue
            n_in += 1
            r = evaluate_instrument(leg, q["bid"], q["ask"], fwd[key[0]],
                                    q["tau"], cur)
            if isinstance(r, str):
                c["drop_" + r] += 1
                continue
            n_out += 1
            c["evaluated"] += 1
            tl = snap["real"].setdefault(r["bucket"], _new_tally())
            tl["n"] += 1
            tl["t1"] += int(r["trade_T1"])
            if cur in SIZE_T2:
                tl["n_eth"] += 1
                tl["t2"] += int(r["trade_T2"])
            snap["dmid"].append(r["d_mid"])
            series.setdefault((key[0], key[2]), []).append(
                (key[1], r["best"]))
        if n_in > 0 and n_out == 0:
            raise AllDroppedError(
                f"{cur}: {n_in} пар на вході, 0 оцінено; лічильники: "
                f"{json.dumps(c, ensure_ascii=False, sort_keys=True)}")
        for pts in series.values():
            if len(pts) >= 5:
                pts.sort()
                snap["neff"].append(neff_lag1([v for _, v in pts]))
        # --- контролі на Deribit-ногах того самого знімка ---
        for key, leg in legs.items():
            F, tau = leg["F"], leg["tau"]
            nb = leg["bid_coin"] * F
            na = leg["ask_coin"] * F
            rn = evaluate_instrument(leg, nb, na, F, tau, cur)
            if isinstance(rn, str):
                c["neg_" + rn] += 1
            else:
                tl = snap["neg"].setdefault(rn["bucket"], _new_tally())
                tl["n"] += 1
                tl["t1"] += int(rn["trade_T1"])
                if cur in SIZE_T2:
                    tl["n_eth"] += 1
                    tl["t2"] += int(rn["trade_T2"])
                snap["neg_dmid"].append(rn["d_mid"])
            try:
                pb = usd_from_iv(leg, leg["iv_bid"] - POS_SHIFT, F, tau)
                pa = usd_from_iv(leg, leg["iv_ask"] - POS_SHIFT, F, tau)
            except ValueError:
                c["pos_price_fail"] += 1
                continue
            rp = evaluate_instrument(leg, pb, pa, F, tau, cur)
            if isinstance(rp, str):
                c["pos_" + rp] += 1
                continue
            tl = snap["pos"].setdefault(rp["bucket"], _new_tally())
            tl["n"] += 1
            tl["t1"] += int(rp["trade_T1"])
            if cur in SIZE_T2:
                tl["n_eth"] += 1
                tl["t2"] += int(rp["trade_T2"])
    return snap


def neff_lag1(xs):
    """(r1, n, n_eff) для ряду вздовж страйків."""
    n = len(xs)
    m = sum(xs) / n
    v = sum((x - m) ** 2 for x in xs)
    if v <= 0:
        return (1.0, n, 1.0)
    r = sum((xs[i] - m) * (xs[i + 1] - m) for i in range(n - 1)) / v
    r = max(-0.99, min(0.99, r))
    return (r, n, n * (1.0 - r) / (1.0 + r))


# ----------------------------------------------------------------------------
# Вердикт (ЧИСТА функція)
# ----------------------------------------------------------------------------

def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def final_verdict(bucket_series, blind, neg_ok, n_days):
    """
    bucket_series: {bucket: [(day, share), ...]} — лише знімки з n>=MIN_INSTR.
    blind: множина бакетів, де позитивний контроль не бачить -3 vp.
    Повертає (verdict, {bucket: деталі}).
    """
    detail = {}
    if not neg_ok:
        return "INVALID", {"reason": "negative control failed"}
    seeing = [b for b, s in bucket_series.items() if s and b not in blind]
    for b, s in bucket_series.items():
        med = _median([x for _, x in s])
        days = {}
        for d, x in s:
            days.setdefault(d, []).append(x)
        dpass = sum(1 for xs in days.values() if _median(xs) >= PASS_SHARE)
        detail[b] = {"median_share": med, "n_snap": len(s),
                     "n_days": len(days), "days_pass": dpass,
                     "blind": b in blind}
    if not seeing:
        return "INVALID", dict(detail, reason="all buckets blind or empty")
    if n_days < MIN_DAYS:
        return "INSUFFICIENT", detail
    passing = [b for b in seeing
               if detail[b]["median_share"] >= PASS_SHARE
               and detail[b]["days_pass"] >= DAY_FRAC * detail[b]["n_days"]]
    if passing:
        return "PASS", dict(detail, passing=passing)
    if all(detail[b]["median_share"] < FAIL_SHARE for b in seeing):
        return "FAIL", detail
    return "INCONCLUSIVE", detail


def aggregate(snaps, scheme, c):
    """snaps: [(day, snap)] -> (bucket_series, blind, neg_ok, n_days)."""
    key_t = "t1" if scheme == "T1" else "t2"
    key_n = "n" if scheme == "T1" else "n_eth"
    series, pos_series = {}, {}
    neg_trades, neg_dmid = 0, []
    days = set()
    for day, s in snaps:
        for b, tl in s["real"].items():
            if tl[key_n] < MIN_INSTR:
                c[f"{scheme}_low_n_bucket_snap"] += 1
                continue
            series.setdefault(b, []).append((day, tl[key_t] / tl[key_n]))
            days.add(day)
        for b, tl in s["pos"].items():
            if tl[key_n] >= MIN_INSTR:
                pos_series.setdefault(b, []).append(tl[key_t] / tl[key_n])
        for tl in s["neg"].values():
            neg_trades += tl[key_t]
        neg_dmid.extend(s["neg_dmid"])
    blind = {b for b in series
             if not pos_series.get(b)
             or _median(pos_series[b]) < PASS_SHARE}
    med_neg = _median(neg_dmid)
    neg_ok = (neg_trades == 0 and med_neg is not None
              and abs(med_neg) <= NEG_TOL_VP)
    return series, blind, neg_ok, len(days), {
        "neg_trades": neg_trades, "neg_dmid_median": med_neg,
        "pos_median_share": {b: _median(v) for b, v in pos_series.items()}}


# ----------------------------------------------------------------------------
# Самотести
# ----------------------------------------------------------------------------

class Counters(dict):
    def __missing__(self, k):
        return 0


def _synthetic_leg(F=100000.0, K=100000.0, tau=30 / 365, iv=0.5,
                   half_spread=0.005, cp=1):
    pb = price_usd(F, K, tau, iv - half_spread, cp)
    pa = price_usd(F, K, tau, iv + half_spread, cp)
    return {"F": F, "tau": tau, "K": K, "cp": cp, "bid_coin": pb / F,
            "ask_coin": pa / F, "iv_bid": iv - half_spread,
            "iv_ask": iv + half_spread, "iv_mid": iv, "name": "SYN"}


def run_self_tests():
    res = {"run": 0, "ok": 0, "fail": []}

    def check(name, cond, detail=""):
        res["run"] += 1
        if cond:
            res["ok"] += 1
        else:
            res["fail"].append(f"{name}: {detail}")
        print(f"[TEST] {'OK ' if cond else 'FAIL'} {name} {detail}")

    # [1] самопара: edge = -спред, торгованих немає, d_mid = 0
    L = _synthetic_leg()
    r = evaluate_instrument(L, L["bid_coin"] * L["F"], L["ask_coin"] * L["F"],
                            L["F"], L["tau"], "BTC")
    check("1.1 самопара e1=-1vp", abs(r["e1"] + 1.0) < 1e-6, f"{r['e1']:.6f}")
    check("1.2 самопара d_mid=0", abs(r["d_mid"]) < 1e-6)
    check("1.3 самопара не торгована", not r["trade_T1"])

    # [2] зсув -3 vp бачиться як e1 = 3 - 1 = 2 vp
    pb = usd_from_iv(L, L["iv_bid"] - POS_SHIFT, L["F"], L["tau"])
    pa = usd_from_iv(L, L["iv_ask"] - POS_SHIFT, L["F"], L["tau"])
    r2 = evaluate_instrument(L, pb, pa, L["F"], L["tau"], "BTC")
    check("2.1 позитив e1=2vp", abs(r2["e1"] - 2.0) < 1e-5, f"{r2['e1']:.6f}")
    check("2.2 позитив торгований на 30д (T1~0.79)", r2["trade_T1"],
          f"T1={r2['T1']:.3f}")

    # [3] поріг ATM30 (F=K): T1 BTC ~0.79 vp, T2 ETH купівля на Deribit ~1.7.
    # 0.89 у STATE 28 вер — арифметична помилка (0.2256/sqrt(30/365)=0.787);
    # пре-реєстрована ФОРМУЛА не змінюється, змінюється ілюстрація.
    t1 = r["T1"]
    check("3.1 T1 ATM30 у [0.76,0.82]", 0.76 <= t1 <= 0.82, f"{t1:.3f}")
    Le = _synthetic_leg(F=2700.0, K=2700.0)
    re_ = evaluate_instrument(Le, Le["bid_coin"] * 2700, Le["ask_coin"] * 2700,
                              2700.0, Le["tau"], "ETH")
    check("3.2 T2 sellB у [0.90,1.00]", 0.90 <= re_["T2"][0] <= 1.00,
          f"{re_['T2'][0]:.3f}")
    check("3.3 T2 buyB у [1.65,1.80]", 1.65 <= re_["T2"][1] <= 1.80,
          f"{re_['T2'][1]:.3f}")
    check("3.4 BTC без T2", "T2" not in r)

    # [4] df: дисконтовані ціни після /df дають ту саму IV
    df = 0.995
    q = derive_quotes({"records": [{
        "status": "ok", "kind": "C", "strike": 100000.0,
        "expiry_ymd": "20261030", "expiry_sec": 2000000000.0,
        "ticker_raw": {"b": str(pb * df), "a": str(pa * df), "B": "1",
                       "A": "1", "I": "100000", "t": 1990000000000.0,
                       "option_pricing": {"df": str(df)}}}]}, Counters())
    v = list(q.values())[0]
    check("4.1 /df відновлює ціну", abs(v["bid"] - pb) < 1e-6)

    # [5] форвард з parity
    F0, tau0 = 101000.0, 30 / 365
    recs = []
    for K in (99000.0, 100000.0, 101000.0, 102000.0):
        for kind, cp in (("C", 1), ("P", -1)):
            px = price_usd(F0, K, tau0, 0.5, cp) * 0.99
            recs.append({"status": "ok", "kind": kind, "strike": K,
                         "expiry_ymd": "20261030",
                         "expiry_sec": 2000000000.0,
                         "ticker_raw": {"b": str(px - 1), "a": str(px + 1),
                                        "B": "1", "A": "1", "I": "100500",
                                        "t": 1997408000000.0,
                                        "option_pricing": {"df": "0.99"}}})
    fq = derive_quotes({"records": recs}, Counters())
    fw = derive_forwards(fq, Counters())
    check("5.1 F_D з parity", abs(fw["20261030"] - F0) < 1e-6,
          f"{fw.get('20261030')}")
    c5 = Counters()
    derive_forwards({k: v for k, v in fq.items() if k[1] == 99000.0}, c5)
    check("5.2 мало пар -> лічильник", c5["d_no_forward_expiry"] == 1)

    # [6] розбір і бакети
    check("6.1 label->ymd", deribit_label_to_ymd("30OCT26") == "20261030")
    check("6.2 label day 1", deribit_label_to_ymd("1OCT26") == "20261001")
    check("6.3 bad label raises",
          _raises(lambda: deribit_label_to_ymd("30XYZ26"), ValueError))
    check("6.4 бакети межі", bucket_of(3.0) == "d000-003"
          and bucket_of(3.01) == "d004-014" and bucket_of(30.16) == "d015-045"
          and bucket_of(0.5) == "d000-003" and bucket_of(359) == "d121+")
    check("6.5 defi ім'я з підкресленням",
          parse_defi_ts("defiquotes_20260930T041201Z_json.gz") is not None
          and parse_defi_ts("defiquotes_20260930T041201Z.json.gz")
          is not None)
    check("6.6 chain ім'я", parse_chain_ts("chain_20260930_041002.json")
          .minute == 10)

    # [7] вердикт: пріоритет гілок
    ser_pass = {"b": [("d1", .1), ("d2", .1), ("d3", .1)]}
    ser_fail = {"b": [("d1", 0.0), ("d2", 0.0), ("d3", 0.0)]}
    ser_mid = {"b": [("d1", .02), ("d2", .02), ("d3", .02)]}
    check("7.1 PASS", final_verdict(ser_pass, set(), True, 3)[0] == "PASS")
    check("7.2 FAIL", final_verdict(ser_fail, set(), True, 3)[0] == "FAIL")
    check("7.3 INCONCLUSIVE",
          final_verdict(ser_mid, set(), True, 3)[0] == "INCONCLUSIVE")
    check("7.4 negative провал -> INVALID навіть при PASS-даних",
          final_verdict(ser_pass, set(), False, 3)[0] == "INVALID")
    check("7.5 все сліпе -> INVALID",
          final_verdict(ser_pass, {"b"}, True, 3)[0] == "INVALID")
    check("7.6 мало діб -> INSUFFICIENT",
          final_verdict(ser_pass, set(), True, 2)[0] == "INSUFFICIENT")
    both = {"b": ser_fail["b"], "x": ser_pass["b"]}
    check("7.7 сліпий бакет не дає PASS",
          final_verdict(both, {"x"}, True, 3)[0] == "FAIL")
    few = {"b": [("d1", .1), ("d2", 0.0), ("d3", 0.0), ("d1", .1)]}
    check("7.8 PASS вимагає 2/3 діб",
          final_verdict(few, set(), True, 3)[0] != "PASS")

    # [8] гейт ALL-DROPPED: є вхід, нема виходу -> виняток
    bad_leg = dict(L)
    chain = [{"currency": "BTC", "slices": [{
        "status": "OK", "forward": L["F"], "tau": L["tau"],
        "expiry": "30OCT26", "rows": [{
            "K": L["K"], "cp": 1, "bid": bad_leg["bid_coin"],
            "ask": bad_leg["ask_coin"], "iv_bid": L["iv_bid"],
            "iv_ask": L["iv_ask"], "name": "X"}]}]}]
    drecs = []
    for K in (99000.0, 100000.0, 101000.0):
        for kind in ("C", "P"):
            px = "1e9" if kind == "C" and K == 100000.0 else "1000"
            drecs.append({"status": "ok", "kind": kind, "strike": K,
                          "expiry_ymd": "20261030",
                          "expiry_sec": 2000000000.0,
                          "ticker_raw": {"b": px, "a": str(float(px) + 1),
                                         "B": "1", "A": "1", "I": "100000",
                                         "t": 1997408000000.0,
                                         "option_pricing": {"df": "1"}}})
    defi = {"currencies": {"BTC": {"records": drecs}}}
    check("8.1 ALL-DROPPED піднімає",
          _raises(lambda: evaluate_snapshot(chain, defi, Counters()),
                  AllDroppedError))
    check("8.2 порожня відповідь не піднімає",
          not _raises(lambda: evaluate_snapshot(
              chain, {"currencies": {"BTC": {"records": []}}}, Counters()),
              AllDroppedError))

    # [8b] IV Deribit перераховується з bid/ask, iv_* артефакту ігнорується
    c8 = Counters()
    diag8 = []
    legs8 = deribit_legs({"slices": [{"status": "OK", "forward": L["F"],
                                      "tau": L["tau"], "expiry": "30OCT26",
                                      "rows": [{"K": L["K"], "cp": 1,
                                                "bid": L["bid_coin"],
                                                "ask": L["ask_coin"],
                                                "iv_bid": 0.1,
                                                "iv_ask": 0.2}]}]},
                         c8, diag8)
    lg = list(legs8.values())[0]
    check("8b.1 iv_bid з coin*F", abs(lg["iv_bid"] - L["iv_bid"]) < 1e-7,
          f"{lg['iv_bid']:.6f}")
    check("8b.2 діагностика різниці", abs(diag8[0] - (0.1 - L["iv_bid"])
                                         * 100) < 1e-6)

    # [9] оточення: усе прочитане оголошене + нічого зайвого
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as _sfh:
        src = _sfh.read()
    read = set(re.findall(r'env_(?:str|int)\(\s*"(VOLEDGE_[A-Z0-9_]+)"', src))
    declared = set(KNOWN_ENV) - {"VOLEDGE_OFFLINE"}
    check("9.1 прочитане ⊆ оголошене", read <= declared,
          str(sorted(read - declared)))
    check("9.2 оголошене ⊆ прочитане", declared <= read,
          str(sorted(declared - read)))
    check("9.3 дефолтні теки абсолютні", all(os.path.isabs(p) for p in (
        DEFAULT_CHAIN_DIR, DEFAULT_DEFI_DIR, DEFAULT_OUT_DIR)))

    # [10] пари артефактів: білий список і вікно
    c10 = Counters()
    pr = pair_artifacts(["chain_20260930_041002.json",
                         "chain_20260930_183500.json"],
                        ["defiquotes_20260930T041201Z.json.gz",
                         "defiquotes_20260930T081201Z.json.gz"], 600, c10)
    check("10.1 одна пара", len(pr) == 1, str(pr))
    check("10.2 ручний chain відсіяно", c10["chain_off_grid"] == 1)
    check("10.3 defi без chain лічиться", c10["defi_no_chain"] == 1)

    print(f"[TEST] passed {res['ok']}/{res['run']}")
    for f_ in res["fail"]:
        print(f"[TEST] FAILURE detail: {f_}")
    return res


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

def main():
    print("=" * 72)
    print("Vol-Edge :: analytics/defi_eval (DEFI, READ-ONLY)")
    print("=" * 72)
    warn_unknown_env(KNOWN_ENV, label="defi_eval")
    try:
        chain_dir = os.path.abspath(env_str("VOLEDGE_DEFI_E_CHAIN_DIR",
                                            DEFAULT_CHAIN_DIR))
        defi_dir = os.path.abspath(env_str("VOLEDGE_DEFI_E_DEFI_DIR",
                                           DEFAULT_DEFI_DIR))
        out_dir = os.path.abspath(env_str("VOLEDGE_DEFI_E_OUT_DIR",
                                          DEFAULT_OUT_DIR))
        sync_s = env_int("VOLEDGE_DEFI_E_SYNC_S", 600, minimum=1)
        no_write = env_int("VOLEDGE_DEFI_E_NO_WRITE", 0, minimum=0)
    except EnvError as e:
        print(f"[ENV-ERROR] {e}")
        return 3

    t = run_self_tests()
    if t["fail"]:
        print("[STATUS] самотести провалено — live не запускається")
        return 1
    if os.environ.get("VOLEDGE_OFFLINE") == "1":
        print("[STATUS] OFFLINE: лише самотести")
        return 0

    print(f"chain: {chain_dir}\ndefi:  {defi_dir}\nsync:  {sync_s} с")
    cpaths = sorted(glob.glob(os.path.join(chain_dir, "chain_*.json*")))
    dpaths = sorted(glob.glob(os.path.join(defi_dir, "defiquotes_*json*")))
    c = Counters()
    pairs = pair_artifacts(cpaths, dpaths, sync_s, c)
    print(f"артефактів chain {len(cpaths)}, defi {len(dpaths)}, "
          f"пар {len(pairs)}")
    if dpaths and not pairs:
        raise AllDroppedError(
            f"defi-артефактів {len(dpaths)}, пар 0: {dict(c)}")

    snaps = []
    for cp_, dp_, ts in pairs:
        try:
            chain, defi = load_json(cp_), load_json(dp_)
        except (OSError, ValueError) as e:
            c["read_fail"] += 1
            print(f"[SKIP] читання {os.path.basename(dp_)}: {e!r}")
            continue
        snap = evaluate_snapshot(chain, defi, c)
        snaps.append((ts.date().isoformat(), snap))
        tot = sum(tl["n"] for tl in snap["real"].values())
        print(f"[PAIR] {ts.isoformat()} оцінено {tot}")

    report = {"kind": "defi_eval", "generated_utc":
              datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "n_pairs": len(snaps), "counters": dict(c),
              "flags": ["[SETTLE-FEE-ASSUMED]"], "schemes": {}}

    for scheme in ("T1", "T2"):
        series, blind, neg_ok, n_days, ctrl = aggregate(snaps, scheme, c)
        verdict, detail = final_verdict(series, blind, neg_ok, n_days)
        report["schemes"][scheme] = {"verdict": verdict, "detail": detail,
                                     "controls": ctrl, "n_days": n_days,
                                     "blind": sorted(blind)}
        print(f"\n[{scheme}] діб {n_days}; негативний: торгованих "
              f"{ctrl['neg_trades']}, медіана d_mid "
              f"{ctrl['neg_dmid_median']} -> "
              f"{'OK' if neg_ok else 'FAIL'}")
        for b in sorted(detail):
            if isinstance(detail[b], dict):
                d = detail[b]
                ms = d["median_share"]
                print(f"[{scheme}] {b}: медіана частки "
                      f"{'—' if ms is None else f'{ms:.3f}'} знімків "
                      f"{d['n_snap']} діб {d['n_days']} прох.діб "
                      f"{d['days_pass']} позит.контроль "
                      f"{ctrl['pos_median_share'].get(b)}"
                      f"{' [BLIND]' if d['blind'] else ''}")
        print(f"[{scheme}] ВЕРДИКТ: {verdict}")

    dm = [x for _, s in snaps for x in s["dmid"]]
    gaps = [g for _, s in snaps for g in s["fwd_gap_bp"]]
    neff = [x for _, s in snaps for x in s["neff"]]
    ivart = [x for _, s in snaps for x in s["iv_art"]]
    diag = {"d_mid_median_vp": _median(dm), "n_d_mid": len(dm),
            "iv_art_minus_ours_median_vp": _median(ivart),
            "iv_art_minus_ours_min_vp": min(ivart) if ivart else None,
            "fwd_gap_bp_median": _median([g[2] for g in gaps]),
            "neff_r1_median": _median([x[0] for x in neff]),
            "neff_sum": sum(x[2] for x in neff),
            "n_sum": sum(x[1] for x in neff)}
    report["diagnostics"] = diag
    print(f"\n[DIAG] d_mid медіана {diag['d_mid_median_vp']} vp "
          f"(n={diag['n_d_mid']}); базис F_D/F_B медіана "
          f"{diag['fwd_gap_bp_median']} bp; n_eff впоперек страйків "
          f"{diag['neff_sum']:.1f} з {diag['n_sum']} "
          f"(медіана r1 {diag['neff_r1_median']})")
    print(f"[DIAG] iv_bid артефакту мінус перерахунок coin*F: медіана "
          f"{diag['iv_art_minus_ours_median_vp']} vp, мін "
          f"{diag['iv_art_minus_ours_min_vp']} vp")
    print("[COUNTERS] " + json.dumps(dict(c), ensure_ascii=False,
                                     sort_keys=True))

    if no_write:
        print("[NO-WRITE] звіт не записано")
        return 0
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.abspath(os.path.join(out_dir, f"defieval_{stamp}.json"))
    with open(path, "w", encoding="utf-8") as _ofh:
        json.dump(report, _ofh, ensure_ascii=False, indent=1)
    print(f"[WRITE] {path} ({os.path.getsize(path)} Б)")
    return 0


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    except Exception:
        print("\n[ERROR] Неперехоплений виняток:")
        traceback.print_exc()
        exit_code = 2
    finally:
        try:
            input("\nНатисніть Enter для виходу...")
        except EOFError:
            pass
    sys.exit(exit_code)
