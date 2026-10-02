# -*- coding: utf-8 -*-
"""
[EN] Raw SVI fit of a single volatility-smile slice (Zeliade approach:
linear least squares + Nelder-Mead over (m, log sigma)), vega^2 weights,
Lee bounds, Durrleman butterfly and calendar arbitrage checks, and
delta-space services. The model is valid only inside the quoted
log-moneyness range (+10% padding); outside it the code refuses
explicitly.

--- Ukrainian original below ---
Vol-Edge / core / svi_fit.py

SVI-фіт зрізу поверхні волатильності + сервіси дельта-простору.

Компоненти:
- Raw SVI w(k) = a + b*(rho*(k-m) + sqrt((k-m)^2 + s^2)), фіт методом Zeliade:
  внутрішній зважений LS по (alpha, delta, gamma) з проєкцією на обмеження,
  зовнішній Nelder-Mead по (m, log s). Лише stdlib.
- Умова Durrleman g(k) >= 0 (butterfly no-arbitrage) на щільній сітці.
- Calendar-перевірка між зрізами (total variance неспадна по tau при фікс. k).
- Дельта-солвер "цільова дельта -> k" з явним вибором OTM-гілки для
  premium-adjusted коллів (немонотонність!). Нормалізація F=1, K=e^k.
- Драбина деградації для бідних зрізів: FULL_SVI -> FIXED_S -> PARABOLA -> FLAT.
- Debug counters на кожному кроці; жодного silent fail-open.

Залежності: black76_greeks.py у тій самій папці (норм-функції).
Запуск подвійним кліком: самотести на синтетиці, чекає Enter.
"""

import math
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from black76_greeks import norm_cdf, norm_ppf  # noqa: E402

W_FLOOR = 1e-8  # мінімальна total variance при оцінці моделі


# ----------------------------------------------------------------------------
# Лінійна алгебра 3x3 (stdlib)
# ----------------------------------------------------------------------------

def solve3(A, rhs):
    """Гаусс із частковим півотом для 3x3. Виняток на виродженій матриці."""
    M = [row[:] + [r] for row, r in zip([list(a) for a in A], rhs)]
    n = 3
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-14:
            raise RuntimeError("solve3: вироджена матриця нормальних рівнянь")
        M[col], M[piv] = M[piv], M[col]
        for r in range(col + 1, n):
            f = M[r][col] / M[col][col]
            for c in range(col, n + 1):
                M[r][c] -= f * M[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (M[r][n] - sum(M[r][c] * x[c] for c in range(r + 1, n))) / M[r][r]
    return x


# ----------------------------------------------------------------------------
# SVI-модель зрізу
# ----------------------------------------------------------------------------

class SliceModel:
    """
    Уніфікована модель зрізу total variance w(k).
    kind: 'svi' (params=(a,b,rho,m,s)), 'parabola' (coeffs=(c0,c1,c2)),
          'flat' (level).
    """

    def __init__(self, kind, tau, params=None, coeffs=None, level=None):
        self.kind = kind
        self.tau = float(tau)
        self.params = params
        self.coeffs = coeffs
        self.level = level
        self.k_range = None  # (k_min, k_max) даних; None = необмежена

    def w(self, k: float) -> float:
        if self.kind == "svi":
            a, b, rho, m, s = self.params
            km = k - m
            val = a + b * (rho * km + math.sqrt(km * km + s * s))
        elif self.kind == "parabola":
            c0, c1, c2 = self.coeffs
            val = c0 + c1 * k + c2 * k * k
        elif self.kind == "flat":
            val = self.level
        else:
            raise ValueError(f"SliceModel: невідомий kind={self.kind}")
        return max(val, W_FLOOR)

    def iv(self, k: float) -> float:
        return math.sqrt(self.w(k) / self.tau)

    def g_durrleman(self, k: float, h: float = 1e-4) -> float:
        """g(k) через чисельні похідні — уніфіковано для всіх kind."""
        w0 = self.w(k)
        wp = (self.w(k + h) - self.w(k - h)) / (2.0 * h)
        wpp = (self.w(k + h) - 2.0 * w0 + self.w(k - h)) / (h * h)
        t1 = 1.0 - k * wp / (2.0 * w0)
        return t1 * t1 - 0.25 * wp * wp * (1.0 / w0 + 0.25) + 0.5 * wpp

    def durrleman_violations(self, k_lo: float, k_hi: float, n: int = 201):
        """Список k, де g(k) < 0."""
        bad = []
        for i in range(n):
            k = k_lo + (k_hi - k_lo) * i / (n - 1)
            if self.g_durrleman(k) < 0.0:
                bad.append(k)
        return bad


# ----------------------------------------------------------------------------
# Внутрішній крок Zeliade: зважений LS по (alpha, delta, gamma) при фікс. (m,s)
# ----------------------------------------------------------------------------

def _inner_ls(ks, ws, wgts, m, s, tau):
    """
    w_model = alpha + delta*y + gamma*sqrt(y^2+1), y=(k-m)/s.
    Повертає (alpha, delta, gamma, sse) після проєкції на обмеження:
      gamma >= 0; |delta| <= gamma; alpha >= -sqrt(gamma^2-delta^2);
      Lee: (gamma + |delta|)/s <= 4/tau.
    Проєкція покрокова (не строгий QP) — компроміс stdlib; SSE рахується
    після проєкції, тож зовнішній оптимізатор бачить чесну вартість.
    """
    ys = [(k - m) / s for k in ks]
    zs = [math.sqrt(y * y + 1.0) for y in ys]
    A = [[0.0] * 3 for _ in range(3)]
    rhs = [0.0] * 3
    for y, z, w, g in zip(ys, zs, ws, wgts):
        row = (1.0, y, z)
        for i in range(3):
            rhs[i] += g * row[i] * w
            for j in range(3):
                A[i][j] += g * row[i] * row[j]
    for i in range(3):
        A[i][i] += 1e-12  # регуляризація проти виродження
    alpha, delta, gamma = solve3(A, rhs)

    # Проєкція на обмеження
    eps = 1e-10
    if gamma < eps:
        gamma = eps
    if abs(delta) > gamma:
        delta = math.copysign(gamma - eps, delta)
    lee_cap = 4.0 * s / tau
    tot = gamma + abs(delta)
    if tot > lee_cap:
        scale = lee_cap / tot
        gamma *= scale
        delta *= scale
    root = math.sqrt(max(gamma * gamma - delta * delta, 0.0))
    if alpha < -root:
        alpha = -root + eps

    sse = 0.0
    for y, z, w, g in zip(ys, zs, ws, wgts):
        e = alpha + delta * y + gamma * z - w
        sse += g * e * e
    return alpha, delta, gamma, sse


def _nelder_mead_2d(func, x0, steps, max_iter=300, tol=1e-12):
    """Мінімізація func(x) для x=(x1,x2). Класичний NM без залежностей."""
    pts = [list(x0),
           [x0[0] + steps[0], x0[1]],
           [x0[0], x0[1] + steps[1]]]
    vals = [func(p) for p in pts]
    for _ in range(max_iter):
        order = sorted(range(3), key=lambda i: vals[i])
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[2] - vals[0]) < tol * (abs(vals[0]) + tol):
            break
        cen = [(pts[0][j] + pts[1][j]) / 2.0 for j in range(2)]
        refl = [cen[j] + (cen[j] - pts[2][j]) for j in range(2)]
        f_r = func(refl)
        if f_r < vals[0]:
            exp = [cen[j] + 2.0 * (cen[j] - pts[2][j]) for j in range(2)]
            f_e = func(exp)
            if f_e < f_r:
                pts[2], vals[2] = exp, f_e
            else:
                pts[2], vals[2] = refl, f_r
        elif f_r < vals[1]:
            pts[2], vals[2] = refl, f_r
        else:
            con = [cen[j] + 0.5 * (pts[2][j] - cen[j]) for j in range(2)]
            f_c = func(con)
            if f_c < vals[2]:
                pts[2], vals[2] = con, f_c
            else:  # shrink
                for i in (1, 2):
                    pts[i] = [pts[0][j] + 0.5 * (pts[i][j] - pts[0][j])
                              for j in range(2)]
                    vals[i] = func(pts[i])
    best = min(range(3), key=lambda i: vals[i])
    return pts[best], vals[best]


def _golden_min(func, lo, hi, iters=80):
    """Золотий переріз: мінімізація унімодальної func на [lo, hi]."""
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c = b - gr * (b - a)
    d = a + gr * (b - a)
    fc, fd = func(c), func(d)
    for _ in range(iters):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = func(c)
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = func(d)
    x = (a + b) / 2.0
    return x, func(x)


# ----------------------------------------------------------------------------
# Драбина деградації та головний фіт зрізу
# ----------------------------------------------------------------------------

class FitResult:
    def __init__(self):
        self.model = None          # SliceModel
        self.mode = None           # 'FULL_SVI' | 'FIXED_S' | 'PARABOLA' | 'FLAT'
        self.sse = float("nan")
        self.counters = {}         # явні лічильники
        self.durrleman_bad = []    # k-точки з g<0

    def summary(self) -> str:
        c = self.counters
        return (f"[FIT] mode={self.mode} n_in={c.get('n_in')} "
                f"n_used={c.get('n_used')} n_dropped={c.get('n_dropped')} "
                f"sse={self.sse:.3e} durrleman_bad={len(self.durrleman_bad)}")


def fit_slice(ks, ivs, tau, weights=None, fixed_s_hint=None,
              min_full=6, min_fixed=4, min_parab=3) -> FitResult:
    """
    Фіт одного зрізу.
      ks:  список k = ln(K/F)
      ivs: список IV (частки)
      weights: ваги (напр. vega^2 або 1/spread^2); None -> рівні
      fixed_s_hint: s із сусідньої довшої експірації для режиму FIXED_S
    Точки з iv<=0 або non-finite відкидаються з лічильником — явно.
    """
    res = FitResult()
    n_in = len(ks)
    if n_in != len(ivs):
        raise ValueError(f"fit_slice: len(ks)={n_in} != len(ivs)={len(ivs)}")
    if weights is None:
        weights = [1.0] * n_in
    if len(weights) != n_in:
        raise ValueError("fit_slice: довжина weights не збігається")

    kk, ww, gg = [], [], []
    n_dropped_bad = 0
    for k, iv, g in zip(ks, ivs, weights):
        if (iv is None or not math.isfinite(iv) or iv <= 0.0
                or not math.isfinite(k) or g <= 0.0):
            n_dropped_bad += 1
            continue
        kk.append(k)
        ww.append(iv * iv * tau)
        gg.append(g)
    n_used = len(kk)
    res.counters = {"n_in": n_in, "n_used": n_used, "n_dropped": n_dropped_bad}

    if n_used >= min_full:
        res.mode = "FULL_SVI"
        k_lo, k_hi = min(kk), max(kk)
        m0 = kk[ww.index(min(ww))]
        s0 = max(0.05, 0.25 * (k_hi - k_lo))

        def cost(p):
            m, log_s = p
            s = math.exp(log_s)
            if not (1e-4 <= s <= 5.0) or not (k_lo - 1.0 <= m <= k_hi + 1.0):
                return 1e18
            return _inner_ls(kk, ww, gg, m, s, tau)[3]

        best_p, best_v = None, float("inf")
        for m_start in (m0, 0.0):
            p, v = _nelder_mead_2d(cost, [m_start, math.log(s0)], [0.05, 0.3])
            if v < best_v:
                best_p, best_v = p, v
        m_f, s_f = best_p[0], math.exp(best_p[1])
        al, de, ga, sse = _inner_ls(kk, ww, gg, m_f, s_f, tau)
        b = ga / s_f
        rho = de / ga if ga > 0 else 0.0
        res.model = SliceModel("svi", tau, params=(al, b, rho, m_f, s_f))
        res.sse = sse

    elif n_used >= min_fixed and fixed_s_hint is not None and fixed_s_hint > 0:
        res.mode = "FIXED_S"
        k_lo, k_hi = min(kk), max(kk)
        s_f = fixed_s_hint

        def cost_m(m):
            return _inner_ls(kk, ww, gg, m, s_f, tau)[3]

        # грубий скан проти неунімодальності, потім золотий переріз локально
        scan_lo, scan_hi = k_lo - 0.5, k_hi + 0.5
        n_scan = 61
        step = (scan_hi - scan_lo) / (n_scan - 1)
        best_i = min(range(n_scan),
                     key=lambda i: cost_m(scan_lo + i * step))
        m_c = scan_lo + best_i * step
        m_f, sse = _golden_min(cost_m, m_c - step, m_c + step)
        al, de, ga, sse = _inner_ls(kk, ww, gg, m_f, s_f, tau)
        b = ga / s_f
        rho = de / ga if ga > 0 else 0.0
        res.model = SliceModel("svi", tau, params=(al, b, rho, m_f, s_f))
        res.sse = sse

    elif n_used >= min_parab:
        res.mode = "PARABOLA"
        A = [[0.0] * 3 for _ in range(3)]
        rhs = [0.0] * 3
        for k, w, g in zip(kk, ww, gg):
            row = (1.0, k, k * k)
            for i in range(3):
                rhs[i] += g * row[i] * w
                for j in range(3):
                    A[i][j] += g * row[i] * row[j]
        for i in range(3):
            A[i][i] += 1e-12
        c0, c1, c2 = solve3(A, rhs)
        if c2 < 0.0:
            c2 = 0.0  # опуклість донизу заборонена; фіксація явна:
            res.counters["parabola_c2_clamped"] = 1
        res.model = SliceModel("parabola", tau, coeffs=(c0, c1, c2))
        res.sse = sum(g * (res.model.w(k) - w) ** 2
                      for k, w, g in zip(kk, ww, gg))

    elif n_used >= 1:
        res.mode = "FLAT"
        tot_g = sum(gg)
        res.model = SliceModel("flat", tau,
                               level=sum(w * g for w, g in zip(ww, gg)) / tot_g)
        res.sse = sum(g * (res.model.level - w) ** 2
                      for w, g in zip(ww, gg))
    else:
        raise RuntimeError(
            f"fit_slice: нуль придатних точок (n_in={n_in}, "
            f"dropped={n_dropped_bad}) — зріз не підлягає фіту")

    if n_used > 0:
        k_lo_d, k_hi_d = min(kk), max(kk)
        res.model.k_range = (k_lo_d, k_hi_d)
        pad = max(0.02, 0.10 * (k_hi_d - k_lo_d))
        res.durrleman_bad = res.model.durrleman_violations(
            k_lo_d - pad, k_hi_d + pad)
    return res


# ----------------------------------------------------------------------------
# Calendar-перевірка між зрізами
# ----------------------------------------------------------------------------

def calendar_check(models, k_lo=-1.0, k_hi=1.0, n=101):
    """
    models: список SliceModel (буде відсортовано по tau).
    Перевірка ЛИШЕ на перетині діапазонів даних кожної пари (екстраполяція
    крил поза даними не є порушенням). Порожній перетин -> пара пропущена
    з лічильником (явно).
    Повертає (n_violations, worst_gap, info):
      info = {"details": [(tau1, tau2, k, gap), ...], "pairs_skipped": int}
    """
    ms = sorted(models, key=lambda x: x.tau)
    n_viol = 0
    worst = 0.0
    details = []
    pairs_skipped = 0
    for a, b in zip(ms[:-1], ms[1:]):
        lo, hi = k_lo, k_hi
        for m in (a, b):
            kr = getattr(m, "k_range", None)
            if kr is not None:
                lo = max(lo, kr[0])
                hi = min(hi, kr[1])
        if hi <= lo:
            pairs_skipped += 1
            continue
        for i in range(n):
            k = lo + (hi - lo) * i / (n - 1)
            gap = b.w(k) - a.w(k)
            if gap < 0.0:
                n_viol += 1
                if gap < worst:
                    worst = gap
                details.append((a.tau, b.tau, k, gap))
    return n_viol, worst, {"details": details,
                           "pairs_skipped": pairs_skipped}


# ----------------------------------------------------------------------------
# Дельта-простір: F=1, K=e^k
# ----------------------------------------------------------------------------

def _d1_d2_norm(k, sigma, tau):
    st = sigma * math.sqrt(tau)
    d1 = (-k + 0.5 * sigma * sigma * tau) / st
    return d1, d1 - st


def delta_on_slice(model: SliceModel, k: float, kind: str) -> float:
    """
    kind: 'call' | 'put' | 'call_pa' | 'put_pa'. Повертає підписану дельту.
    """
    sigma = model.iv(k)
    d1, d2 = _d1_d2_norm(k, sigma, model.tau)
    if kind == "call":
        return norm_cdf(d1)
    if kind == "put":
        return norm_cdf(d1) - 1.0
    if kind == "call_pa":
        return math.exp(k) * norm_cdf(d2)
    if kind == "put_pa":
        return -math.exp(k) * norm_cdf(-d2)
    raise ValueError(f"delta_on_slice: невідомий kind={kind}")


def _bisect_k(f, lo, hi, tol=1e-10, iters=200):
    f_lo, f_hi = f(lo), f(hi)
    if f_lo * f_hi > 0.0:
        return None
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if hi - lo < tol:
            return mid
        f_mid = f(mid)
        if f_mid == 0.0:
            return mid
        if f_lo * f_mid < 0.0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return 0.5 * (lo + hi)


def solve_k_for_delta(model: SliceModel, target_abs: float, kind: str,
                      k_lo=None, k_hi=None):
    """
    Знаходить k, де |delta| = target_abs.
    Діапазон пошуку: явні k_lo/k_hi, інакше k_range моделі з 10%-падінгом
    (модель валідна лише в межах даних!), інакше [-2, 2].
    Повертає (k, reason): k=None з причиною, якщо корінь недосяжний У МЕЖАХ
    даних — явно, без екстраполяції крил.
    Для 'call_pa' спершу пік, потім OTM-гілка (праворуч від піка).
    """
    if not (0.0 < target_abs < 1.0):
        return None, f"target={target_abs} поза (0,1)"
    if k_lo is None or k_hi is None:
        kr = getattr(model, "k_range", None)
        if kr is not None:
            pad = max(0.02, 0.10 * (kr[1] - kr[0]))
            k_lo = kr[0] - pad if k_lo is None else k_lo
            k_hi = kr[1] + pad if k_hi is None else k_hi
        else:
            k_lo = -2.0 if k_lo is None else k_lo
            k_hi = 2.0 if k_hi is None else k_hi

    if kind == "call_pa":
        k_peak, neg_peak = _golden_min(
            lambda k: -delta_on_slice(model, k, "call_pa"), k_lo, k_hi)
        peak = -neg_peak
        if target_abs > peak:
            return None, (f"target {target_abs:.4f} > максимум delta_pa "
                          f"{peak:.4f} на [{k_lo:.3f},{k_hi:.3f}] "
                          f"(даних далі немає) — кореня не існує")
        k = _bisect_k(
            lambda k: delta_on_slice(model, k, "call_pa") - target_abs,
            k_peak, k_hi)
        if k is None:
            return None, (f"call_pa: |delta|={target_abs} недосяжна на "
                          f"OTM-гілці [{k_peak:.3f},{k_hi:.3f}] у межах даних")
        return k, "OK"

    f = lambda k: abs(delta_on_slice(model, k, kind)) - target_abs  # noqa: E731
    k = _bisect_k(f, k_lo, k_hi)
    if k is None:
        return None, (f"{kind}: |delta|={target_abs} недосяжна на "
                      f"[{k_lo:.3f},{k_hi:.3f}] у межах даних")
    return k, "OK"


def atm_dns_pa_k(model: SliceModel, iters: int = 50) -> float:
    """ATM delta-neutral straddle (premium-adjusted): d2=0 -> k = -sigma^2*tau/2,
    нерухома точка по sigma(k)."""
    k = 0.0
    for _ in range(iters):
        sigma = model.iv(k)
        k_new = -0.5 * sigma * sigma * model.tau
        if abs(k_new - k) < 1e-14:
            return k_new
        k = k_new
    return k


def rr_bf(model: SliceModel, delta_level: float = 0.25, pa: bool = True):
    """
    RR = iv(call) - iv(put), BF = (iv(call)+iv(put))/2 - iv(ATM-DNS).
    Повертає (rr, bf, деталі) або кидає RuntimeError, якщо страйк недосяжний.
    """
    ck = "call_pa" if pa else "call"
    pk = "put_pa" if pa else "put"
    k_c, r1 = solve_k_for_delta(model, delta_level, ck)
    k_p, r2 = solve_k_for_delta(model, delta_level, pk)
    if k_c is None:
        raise RuntimeError(f"rr_bf: call недосяжний: {r1}")
    if k_p is None:
        raise RuntimeError(f"rr_bf: put недосяжний: {r2}")
    k_atm = atm_dns_pa_k(model) if pa else 0.0
    iv_c, iv_p, iv_a = model.iv(k_c), model.iv(k_p), model.iv(k_atm)
    return (iv_c - iv_p, 0.5 * (iv_c + iv_p) - iv_a,
            {"k_call": k_c, "k_put": k_p, "k_atm": k_atm,
             "iv_call": iv_c, "iv_put": iv_p, "iv_atm": iv_a})


# ----------------------------------------------------------------------------
# Самотести (синтетика — детерміновано, без random)
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
            print(f"  [FAIL] {name}: {detail}")


def _make_synthetic(params, tau, ks):
    a, b, rho, m, s = params
    ivs = []
    for k in ks:
        km = k - m
        w = a + b * (rho * km + math.sqrt(km * km + s * s))
        ivs.append(math.sqrt(w / tau))
    return ivs


def run_self_tests() -> TestCounters:
    tc = TestCounters()
    tau = 30.0 / 365.0

    print("[1] Відновлення відомих SVI-параметрів (без шуму)")
    true_params = (0.012, 0.30, 0.15, -0.02, 0.18)
    ks9 = [-0.5, -0.35, -0.2, -0.1, 0.0, 0.1, 0.2, 0.35, 0.5]
    ivs9 = _make_synthetic(true_params, tau, ks9)
    fr = fit_slice(ks9, ivs9, tau)
    print("  " + fr.summary())
    tc.check("recovery: mode", fr.mode == "FULL_SVI", fr.mode)
    max_err = max(abs(fr.model.iv(k) - iv) for k, iv in zip(ks9, ivs9))
    tc.check("recovery: max |iv_fit - iv_true| < 0.15 vp",
             max_err < 0.0015, f"max_err={max_err:.6f}")
    tc.check("recovery: durrleman чистий", len(fr.durrleman_bad) == 0,
             f"bad={len(fr.durrleman_bad)}")

    print("[2] Durrleman: детекція арбітражних параметрів")
    bad_model = SliceModel("svi", tau, params=(-0.005, 0.6, 0.95, 0.0, 0.03))
    bad = bad_model.durrleman_violations(-0.8, 0.8)
    tc.check("durrleman: ловить g<0", len(bad) > 0, "порушень не знайдено")
    good_model = SliceModel("svi", tau, params=true_params)
    good = good_model.durrleman_violations(-0.8, 0.8)
    tc.check("durrleman: чистий на здорових параметрах", len(good) == 0,
             f"bad={len(good)}")

    print("[3] Дельта-солвер: round-trip та OTM-гілка")
    model = fr.model
    for lvl in (0.10, 0.25):
        for kind in ("call_pa", "put_pa", "call", "put"):
            k, reason = solve_k_for_delta(model, lvl, kind)
            if k is None:
                # 10d на високоволатильній синтетиці легітимно поза даними;
                # 25d зобов'язаний знаходитись
                tc.check(f"solver {kind} {lvl}: явна відмова допустима "
                         f"лише для 10d", lvl == 0.10, reason)
                continue
            d = abs(delta_on_slice(model, k, kind))
            tc.check(f"solver {kind} {lvl}: |delta| точна",
                     abs(d - lvl) < 1e-7, f"d={d:.8f}")
    k25c, _ = solve_k_for_delta(model, 0.25, "call_pa")
    k_peak, negp = _golden_min(
        lambda k: -delta_on_slice(model, k, "call_pa"), -2.0, 2.0)
    tc.check("solver call_pa: корінь на OTM-гілці (k > k_peak)",
             k25c is not None and k25c > k_peak,
             f"k25c={k25c}, k_peak={k_peak:.4f}")
    k_none, reason = solve_k_for_delta(model, 0.99, "call_pa")
    tc.check("solver call_pa: неіснуючий target -> явний None",
             k_none is None and "не існує" in reason, f"reason={reason}")
    narrow = SliceModel("svi", 2.6/365.0,
                        params=(0.0002, 5.0, -0.3, 0.0, 0.01))
    narrow.k_range = (-0.03, 0.03)
    k_out, reason_out = solve_k_for_delta(narrow, 0.10, "put_pa")
    tc.check("solver: 10d поза вузькими даними -> явна відмова",
             k_out is None, f"k={k_out}, reason={reason_out}")

    print("[4] RR/BF: плоска вола -> RR = 0; k-симетрична усмішка -> RR != 0")
    flat_model = SliceModel("flat", tau, level=0.06)
    rr_f, bf_f, _ = rr_bf(flat_model, 0.25, pa=False)
    tc.check("rr flat vol == 0", abs(rr_f) < 1e-9, f"rr={rr_f:.2e}")
    tc.check("bf flat vol == 0", abs(bf_f) < 1e-9, f"bf={bf_f:.2e}")
    sym_model = SliceModel("svi", tau, params=(0.010, 0.25, 0.0, 0.0, 0.20))
    rr_v, bf_v, det_v = rr_bf(sym_model, 0.25, pa=False)
    tc.check("rr k-symmetric != 0 (дельта-страйки асиметричні)",
             rr_v > 0.01, f"rr={rr_v:.6f}")
    tc.check("bf symmetric > 0", bf_v > 0.0, f"bf={bf_v:.6f}")
    rr_pa, bf_pa, det_pa = rr_bf(sym_model, 0.25, pa=True)
    tc.check("rr_pa: скінченний і розумний", abs(rr_pa) < 0.05,
             f"rr_pa={rr_pa:.6f}")
    print(f"  RR25 vanilla={rr_v*100:.3f} vp, RR25 pa={rr_pa*100:.3f} vp "
          f"(розбіжність — ефект конвенції дельт, це очікувано)")

    print("[5] Calendar-перевірка")
    m1 = SliceModel("svi", 7.0/365.0, params=(0.004, 0.20, 0.0, 0.0, 0.15))
    m2 = SliceModel("svi", 30.0/365.0, params=(0.014, 0.30, 0.0, 0.0, 0.18))
    nv, worst, _ = calendar_check([m1, m2])
    tc.check("calendar: здорова пара без порушень", nv == 0,
             f"nv={nv}, worst={worst:.3e}")
    m3 = SliceModel("svi", 30.0/365.0, params=(0.001, 0.05, 0.0, 0.0, 0.15))
    nv2, worst2, _ = calendar_check([m1, m3])
    tc.check("calendar: перетин детектується", nv2 > 0, f"nv={nv2}")
    m4 = SliceModel("flat", 7.0/365.0, level=0.004)
    m4.k_range = (-0.05, 0.05)
    m5 = SliceModel("flat", 30.0/365.0, level=0.001)
    m5.k_range = (0.30, 0.60)
    nv3, _, info3 = calendar_check([m4, m5])
    tc.check("calendar: порожній перетин -> явний skip",
             nv3 == 0 and info3["pairs_skipped"] == 1,
             f"nv={nv3}, skipped={info3['pairs_skipped']}")

    print("[6] Драбина деградації")
    ks4 = [-0.2, -0.05, 0.05, 0.2]
    ivs4 = _make_synthetic(true_params, tau, ks4)
    fr4 = fit_slice(ks4, ivs4, tau, fixed_s_hint=0.18)
    print("  " + fr4.summary())
    tc.check("degrade: 4 точки + hint -> FIXED_S", fr4.mode == "FIXED_S",
             fr4.mode)
    err4 = max(abs(fr4.model.iv(k) - iv) for k, iv in zip(ks4, ivs4))
    tc.check("degrade FIXED_S: точність", err4 < 0.003, f"err={err4:.6f}")
    fr4b = fit_slice(ks4, ivs4, tau)  # без hint -> PARABOLA
    tc.check("degrade: 4 точки без hint -> PARABOLA",
             fr4b.mode == "PARABOLA", fr4b.mode)
    ks2 = [-0.05, 0.05]
    fr2 = fit_slice(ks2, _make_synthetic(true_params, tau, ks2), tau)
    tc.check("degrade: 2 точки -> FLAT", fr2.mode == "FLAT", fr2.mode)
    ivs_bad = [0.7, -1.0, float("nan"), 0.72, 0.71, 0.69, 0.7, 0.73, 0.75]
    fr_drop = fit_slice(ks9, ivs_bad, tau, fixed_s_hint=0.18)
    tc.check("degrade: сміттєві точки відкинуті з лічильником",
             fr_drop.counters["n_dropped"] == 2,
             f"dropped={fr_drop.counters['n_dropped']}")

    print("[7] ATM-DNS pa: d2(k_atm) ~= 0")
    k_atm = atm_dns_pa_k(model)
    sigma_atm = model.iv(k_atm)
    _, d2 = _d1_d2_norm(k_atm, sigma_atm, model.tau)
    tc.check("atm_dns_pa: d2 ~ 0", abs(d2) < 1e-8, f"d2={d2:.2e}")

    return tc


def main() -> int:
    print("=" * 64)
    print("Vol-Edge :: svi_fit :: самотести")
    print("=" * 64)
    tc = run_self_tests()
    print("-" * 64)
    print(f"[SUMMARY] тестів: {tc.run}, пройдено: {tc.passed}, "
          f"провалено: {len(tc.failures)}")
    if tc.failures:
        print("[SUMMARY] ПРОВАЛЕНІ:")
        for f in tc.failures:
            print(f"  - {f}")
        return 1
    print("[SUMMARY] Усі тести пройдено. Готовий до модуля 3 (deribit_chain).")
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
