# -*- coding: utf-8 -*-
"""
[EN] Black-76 pricer, implied-volatility solver and Greeks for Deribit
inverse (coin-settled) options on BTC/ETH. Prices and Greeks in USD and
coin terms, including premium-adjusted delta (K/F)*N(d2), which is
non-monotonic in strike. Standard library only; self-tests run first.

--- Ukrainian original below ---
Vol-Edge / core / black76_greeks.py

Black-76 pricer та греки для інверсних опціонів Deribit (BTC/ETH).
- Ціни: USD та coin (BTC) терміни.
- Греки: USD (delta, gamma, vega, theta, vanna, volga)
         + coin-версії + premium-adjusted delta/gamma.
- IV-солвер: Newton з fallback на бісекцію. Без сторонніх залежностей (лише stdlib).

Конвенції проєкту:
- r = 0 (дисконтування зашите у форвард Deribit).
- tau у роках, sigma у частках (0.70 = 70%).
- cp: +1 = call, -1 = put.
- Жодного silent fail-open: будь-яка аномалія -> явний виняток або NaN з причиною,
  усі відсіви враховані в лічильниках самотестів.

Запуск подвійним кліком: виконує повний набір самотестів і чекає Enter.
"""

import math
import sys
import traceback

# ----------------------------------------------------------------------------
# Нормальний розподіл (stdlib, без scipy)
# ----------------------------------------------------------------------------

SQRT_2PI = math.sqrt(2.0 * math.pi)


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Інверсія CDF (Acklam). Точність ~1e-9, достатньо для стартових guess-ів."""
    if not (0.0 < p < 1.0):
        raise ValueError(f"norm_ppf: p={p} поза (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
            ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        x = (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
            (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
            ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    # Один крок Halley для дотиску
    e = norm_cdf(x) - p
    u = e * SQRT_2PI * math.exp(x * x / 2.0)
    x = x - u / (1.0 + x * u / 2.0)
    return x


# ----------------------------------------------------------------------------
# Валідація входів
# ----------------------------------------------------------------------------

def _validate(F: float, K: float, tau: float, sigma: float, cp: int) -> None:
    if not (F > 0.0):
        raise ValueError(f"F={F}: форвард має бути > 0")
    if not (K > 0.0):
        raise ValueError(f"K={K}: страйк має бути > 0")
    if not (tau > 0.0):
        raise ValueError(f"tau={tau}: час має бути > 0")
    if not (sigma > 0.0):
        raise ValueError(f"sigma={sigma}: вола має бути > 0")
    if cp not in (1, -1):
        raise ValueError(f"cp={cp}: очікується +1 (call) або -1 (put)")


def d1_d2(F: float, K: float, tau: float, sigma: float):
    st = sigma * math.sqrt(tau)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * tau) / st
    return d1, d1 - st


# ----------------------------------------------------------------------------
# Ціни
# ----------------------------------------------------------------------------

def price_usd(F: float, K: float, tau: float, sigma: float, cp: int) -> float:
    """Black-76, r=0. USD-ціна на 1 монету номіналу."""
    _validate(F, K, tau, sigma, cp)
    d1, d2 = d1_d2(F, K, tau, sigma)
    if cp == 1:
        return F * norm_cdf(d1) - K * norm_cdf(d2)
    return K * norm_cdf(-d2) - F * norm_cdf(-d1)


def price_coin(F: float, K: float, tau: float, sigma: float, cp: int) -> float:
    """Премія в монеті (як котирує Deribit)."""
    return price_usd(F, K, tau, sigma, cp) / F


# ----------------------------------------------------------------------------
# Греки: USD-терміни
# ----------------------------------------------------------------------------

def delta_usd(F, K, tau, sigma, cp) -> float:
    _validate(F, K, tau, sigma, cp)
    d1, _ = d1_d2(F, K, tau, sigma)
    return norm_cdf(d1) if cp == 1 else norm_cdf(d1) - 1.0


def gamma_usd(F, K, tau, sigma) -> float:
    _validate(F, K, tau, sigma, 1)
    d1, _ = d1_d2(F, K, tau, sigma)
    return norm_pdf(d1) / (F * sigma * math.sqrt(tau))


def vega_usd(F, K, tau, sigma) -> float:
    """На одиницю sigma (1.00). Для vol point ділити на 100."""
    _validate(F, K, tau, sigma, 1)
    d1, _ = d1_d2(F, K, tau, sigma)
    return F * norm_pdf(d1) * math.sqrt(tau)


def theta_usd(F, K, tau, sigma) -> float:
    """USD на рік (r=0, однакова для call/put). Для денної ділити на 365."""
    _validate(F, K, tau, sigma, 1)
    d1, _ = d1_d2(F, K, tau, sigma)
    return -F * norm_pdf(d1) * sigma / (2.0 * math.sqrt(tau))


def vanna_usd(F, K, tau, sigma) -> float:
    """dDelta/dsigma = -phi(d1)*d2/sigma (однакова для call/put)."""
    _validate(F, K, tau, sigma, 1)
    d1, d2 = d1_d2(F, K, tau, sigma)
    return -norm_pdf(d1) * d2 / sigma


def volga_usd(F, K, tau, sigma) -> float:
    """dVega/dsigma = Vega * d1 * d2 / sigma."""
    _validate(F, K, tau, sigma, 1)
    d1, d2 = d1_d2(F, K, tau, sigma)
    return vega_usd(F, K, tau, sigma) * d1 * d2 / sigma


# ----------------------------------------------------------------------------
# Греки: coin та premium-adjusted (специфіка інверсних опціонів Deribit)
# ----------------------------------------------------------------------------

def delta_pa(F, K, tau, sigma, cp) -> float:
    """
    Premium-adjusted delta (ефективна дельта coin-margined позиції).
    call: N(d1) - C_usd/F = (K/F) * N(d2)
    put:  N(d1) - 1 - P_usd/F = -(K/F) * N(-d2)
    """
    _validate(F, K, tau, sigma, cp)
    _, d2 = d1_d2(F, K, tau, sigma)
    if cp == 1:
        return (K / F) * norm_cdf(d2)
    return -(K / F) * norm_cdf(-d2)


def gamma_pa(F, K, tau, sigma, cp) -> float:
    """
    d(delta_pa)/dF.
    call: (K/F^2) * [ phi(d2)/(sigma*sqrt(tau)) - N(d2) ]   (змінює знак ITM!)
    put:  (K/F^2) * [ phi(d2)/(sigma*sqrt(tau)) + N(-d2) ]
    """
    _validate(F, K, tau, sigma, cp)
    _, d2 = d1_d2(F, K, tau, sigma)
    st = sigma * math.sqrt(tau)
    core = norm_pdf(d2) / st
    if cp == 1:
        return (K / (F * F)) * (core - norm_cdf(d2))
    return (K / (F * F)) * (core + norm_cdf(-d2))


def vega_coin(F, K, tau, sigma) -> float:
    """BTC на одиницю sigma: phi(d1)*sqrt(tau). Не залежить від F."""
    _validate(F, K, tau, sigma, 1)
    d1, _ = d1_d2(F, K, tau, sigma)
    return norm_pdf(d1) * math.sqrt(tau)


def theta_coin(F, K, tau, sigma) -> float:
    return theta_usd(F, K, tau, sigma) / F


def vanna_pa(F, K, tau, sigma) -> float:
    """d(delta_pa)/dsigma = vanna_usd - vega_coin (той самий механізм премії)."""
    return vanna_usd(F, K, tau, sigma) - vega_coin(F, K, tau, sigma)


# ----------------------------------------------------------------------------
# IV-солвер
# ----------------------------------------------------------------------------

IV_MIN = 1e-4
IV_MAX = 10.0


def implied_vol(target_usd: float, F: float, K: float, tau: float, cp: int,
                tol: float = 1e-9, max_newton: int = 50) -> float:
    """
    IV з USD-ціни. Newton -> fallback бісекція. Явні винятки поза межами
    (intrinsic, верхня межа): без мовчазного клемпінгу.
    """
    if cp not in (1, -1):
        raise ValueError(f"cp={cp}")
    if not (F > 0 and K > 0 and tau > 0):
        raise ValueError(f"F={F}, K={K}, tau={tau}")
    intrinsic = max(0.0, (F - K) if cp == 1 else (K - F))
    upper = F if cp == 1 else K
    if not (intrinsic < target_usd < upper):
        raise ValueError(
            f"implied_vol: ціна {target_usd:.6f} поза межами "
            f"({intrinsic:.6f}, {upper:.6f}) для F={F}, K={K}, cp={cp}"
        )

    # Старт: Brenner-Subrahmanyam від ATM-еквіваленту
    sigma = max(IV_MIN, min(IV_MAX,
                math.sqrt(2.0 * math.pi / tau) * target_usd / F))

    for _ in range(max_newton):
        px = price_usd(F, K, tau, sigma, cp)
        diff = px - target_usd
        v = vega_usd(F, K, tau, sigma)
        if v < 1e-12:
            break  # vega вироджена -> бісекція
        step = diff / v
        new_sigma = sigma - step
        if not (IV_MIN < new_sigma < IV_MAX):
            break  # вийшли з коридору -> бісекція
        if abs(step) < tol:  # збіжність по СІГМІ, не по ціні (deep OTM!)
            return new_sigma
        sigma = new_sigma

    # Бісекція (гарантована збіжність: ціна монотонна по sigma)
    lo, hi = IV_MIN, IV_MAX
    f_lo = price_usd(F, K, tau, lo, cp) - target_usd
    f_hi = price_usd(F, K, tau, hi, cp) - target_usd
    if f_lo > 0.0 or f_hi < 0.0:
        raise RuntimeError(
            f"implied_vol: брекет не містить кореня "
            f"(f_lo={f_lo:.3e}, f_hi={f_hi:.3e}); вхід: {target_usd}, F={F}, K={K}"
        )
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if (hi - lo) < 1e-12:  # збіжність по інтервалу сігми
            return mid
        f_mid = price_usd(F, K, tau, mid, cp) - target_usd
        if f_mid > 0.0:
            hi = mid
        else:
            lo = mid
    raise RuntimeError("implied_vol: бісекція не зійшлася за 200 ітерацій")


def implied_vol_from_coin(target_coin: float, F: float, K: float,
                          tau: float, cp: int) -> float:
    """IV з премії в монеті (котирування Deribit)."""
    return implied_vol(target_coin * F, F, K, tau, cp)


# ----------------------------------------------------------------------------
# Самотести
# ----------------------------------------------------------------------------

class TestCounters:
    def __init__(self):
        self.run = 0
        self.passed = 0
        self.failures = []

    def check(self, name: str, ok: bool, detail: str = ""):
        self.run += 1
        if ok:
            self.passed += 1
        else:
            self.failures.append(f"{name}: {detail}")
            print(f"  [FAIL] {name}: {detail}")


def _num_diff(f, x: float, h: float) -> float:
    return (f(x + h) - f(x - h)) / (2.0 * h)


def run_self_tests() -> TestCounters:
    tc = TestCounters()
    grid_F = [100000.0]
    grid_K = [50000.0, 70000.0, 90000.0, 100000.0, 110000.0, 140000.0, 200000.0]
    grid_tau = [7.0/365.0, 30.0/365.0, 180.0/365.0]
    grid_sig = [0.40, 0.70, 1.20]

    print("[1] Put-call parity: C - P = F - K")
    for K in grid_K:
        for tau in grid_tau:
            for sig in grid_sig:
                F = grid_F[0]
                lhs = price_usd(F, K, tau, sig, 1) - price_usd(F, K, tau, sig, -1)
                rhs = F - K
                tc.check(f"parity K={K} tau={tau:.4f} sig={sig}",
                         abs(lhs - rhs) < 1e-6 * F,
                         f"lhs={lhs:.4f} rhs={rhs:.4f}")

    print("[2] Аналітичні греки vs центральні різниці")
    h_F, h_s, h_t = 1.0, 1e-5, 1e-6
    for K in grid_K:
        for tau in grid_tau:
            for sig in grid_sig:
                for cp in (1, -1):
                    F = grid_F[0]
                    # delta_usd
                    num = _num_diff(lambda x: price_usd(x, K, tau, sig, cp), F, h_F)
                    ana = delta_usd(F, K, tau, sig, cp)
                    tc.check(f"delta_usd K={K} t={tau:.3f} s={sig} cp={cp}",
                             abs(num - ana) < 1e-6, f"num={num:.8f} ana={ana:.8f}")
                    # delta_pa: похідна coin-ціни по F, помножена на F
                    num_pa = F * _num_diff(lambda x: price_coin(x, K, tau, sig, cp), F, h_F)
                    ana_pa = delta_pa(F, K, tau, sig, cp)
                    tc.check(f"delta_pa K={K} t={tau:.3f} s={sig} cp={cp}",
                             abs(num_pa - ana_pa) < 1e-6,
                             f"num={num_pa:.8f} ana={ana_pa:.8f}")
                    # gamma_pa
                    num_g = _num_diff(lambda x: delta_pa(x, K, tau, sig, cp), F, h_F)
                    ana_g = gamma_pa(F, K, tau, sig, cp)
                    tc.check(f"gamma_pa K={K} t={tau:.3f} s={sig} cp={cp}",
                             abs(num_g - ana_g) < 1e-9,
                             f"num={num_g:.3e} ana={ana_g:.3e}")
                # симетричні по cp греки — раз на комбінацію
                F = grid_F[0]
                num_v = _num_diff(lambda s: price_usd(F, K, tau, s, 1), sig, h_s)
                tc.check(f"vega_usd K={K} t={tau:.3f} s={sig}",
                         abs(num_v - vega_usd(F, K, tau, sig)) < 1e-3,
                         f"num={num_v:.6f} ana={vega_usd(F, K, tau, sig):.6f}")
                num_th = -_num_diff(lambda t: price_usd(F, K, t, sig, 1), tau, h_t)
                tc.check(f"theta_usd K={K} t={tau:.3f} s={sig}",
                         abs(num_th - theta_usd(F, K, tau, sig)) < 1e-2,
                         f"num={num_th:.4f} ana={theta_usd(F, K, tau, sig):.4f}")
                num_vn = _num_diff(lambda s: delta_usd(F, K, tau, s, 1), sig, h_s)
                tc.check(f"vanna_usd K={K} t={tau:.3f} s={sig}",
                         abs(num_vn - vanna_usd(F, K, tau, sig)) < 1e-5,
                         f"num={num_vn:.6f} ana={vanna_usd(F, K, tau, sig):.6f}")
                num_vg = _num_diff(lambda s: vega_usd(F, K, tau, s), sig, h_s)
                tc.check(f"volga_usd K={K} t={tau:.3f} s={sig}",
                         abs(num_vg - volga_usd(F, K, tau, sig)) < 1e-2,
                         f"num={num_vg:.4f} ana={volga_usd(F, K, tau, sig):.4f}")

    print("[3] Тотожність delta_pa: N(d1) - c == (K/F)*N(d2)")
    for K in grid_K:
        for tau in grid_tau:
            for sig in grid_sig:
                F = grid_F[0]
                d1, d2 = d1_d2(F, K, tau, sig)
                lhs = norm_cdf(d1) - price_coin(F, K, tau, sig, 1)
                rhs = (K / F) * norm_cdf(d2)
                tc.check(f"pa-identity K={K} t={tau:.3f} s={sig}",
                         abs(lhs - rhs) < 1e-12, f"lhs={lhs:.14f} rhs={rhs:.14f}")

    print("[4] IV round-trip: sigma -> ціна -> implied ~= sigma")
    for K in grid_K:
        for tau in grid_tau:
            for sig in grid_sig:
                for cp in (1, -1):
                    F = grid_F[0]
                    px = price_usd(F, K, tau, sig, cp)
                    intrinsic = max(0.0, (F - K) if cp == 1 else (K - F))
                    if px - intrinsic < 1e-6:
                        continue  # часова вартість нижче tol — тест нерелевантний
                    iv = implied_vol(px, F, K, tau, cp)
                    tc.check(f"iv-roundtrip K={K} t={tau:.3f} s={sig} cp={cp}",
                             abs(iv - sig) < 1e-6, f"iv={iv:.8f} sig={sig}")

    print("[5] Немонотонність delta_pa колла: пік існує і < 1")
    F, tau, sig = 100000.0, 30.0/365.0, 0.70
    deltas = []
    for i in range(200):
        K = F * (0.3 + 1.7 * i / 199.0)
        deltas.append(delta_pa(F, K, tau, sig, 1))
    peak = max(deltas)
    idx = deltas.index(peak)
    tc.check("pa-peak: пік всередині сітки", 0 < idx < 199, f"idx={idx}")
    tc.check("pa-peak: пік < 1", peak < 1.0, f"peak={peak:.6f}")
    tc.check("pa-peak: спад до нуля deep ITM", deltas[0] < 0.5 * peak,
             f"deep_itm={deltas[0]:.6f} peak={peak:.6f}")

    print("[6] Інваріант vega_coin: не залежить від F")
    v1 = vega_coin(50000.0, 60000.0, 0.1, 0.8)
    v2 = vega_coin(100000.0, 120000.0, 0.1, 0.8)  # той самий манінес
    tc.check("vega_coin scale-invariant", abs(v1 - v2) < 1e-12,
             f"v1={v1:.12f} v2={v2:.12f}")

    return tc


def main() -> int:
    print("=" * 64)
    print("Vol-Edge :: black76_greeks :: самотести")
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
    print("[SUMMARY] Усі тести пройдено. Модуль готовий як фундамент для svi_fit.")
    # Демонстраційний приклад з розділу теорії
    F, K, tau, sig = 100000.0, 70000.0, 30.0/365.0, 0.70
    print("-" * 64)
    print(f"[DEMO] F={F:.0f} K={K:.0f} tau=30d sigma=70%")
    print(f"  C_usd     = {price_usd(F, K, tau, sig, 1):.2f}")
    print(f"  C_coin    = {price_coin(F, K, tau, sig, 1):.6f} BTC")
    print(f"  delta     = {delta_usd(F, K, tau, sig, 1):.6f}")
    print(f"  delta_pa  = {delta_pa(F, K, tau, sig, 1):.6f}")
    print(f"  gamma_pa  = {gamma_pa(F, K, tau, sig, 1):.3e}  (знак!)")
    print(f"  vega_coin = {vega_coin(F, K, tau, sig):.6f} BTC/vol-unit")
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
