"""
ssvi_surface.py — Arbitrage-free implied volatility surface builder.

Hollisworks / structured-notes valuation stack.

PURPOSE
    Calibrate a Gatheral-Jacquier SSVI surface from listed European index
    options. Feeds the structured-note pricing engine (embedded fee, mark,
    autocall probability).

SCOPE / KNOWN LIMITS  (read before relying on output)
    * Live snapshot only. yfinance has NO option history, so this CANNOT
      reprice a note as of its 2021 issuance date. Historical repricing
      requires a vendor archive (ORATS-class).
    * Listed liquidity dies ~2y. Notes run 2-5y. Beyond the last LEAPS the
      surface is EXTRAPOLATION, flagged as such in the output.
    * US index only. SX5E / Nikkei require Eurex / OSE data.
    * European exercise assumed. Do NOT point this at SPY (American) or any
      single name — put-call parity becomes an inequality and the derived
      forward is biased.

USAGE
    python ssvi_surface.py --self-test          # offline, no network
    python ssvi_surface.py --ticker ^SPX        # live
    python ssvi_surface.py --ticker ^SPX --json out.json

DEPENDENCIES
    numpy, pandas, scipy            (required)
    yfinance                        (live mode only)
    matplotlib                      (--plot only)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import gc
import json
import sys
import warnings
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import brentq, minimize
from scipy.stats import norm

__version__ = "1.0.0"


# ==========================================================================
# EXCEPTIONS
# ==========================================================================
class SurfaceArbitrageError(Exception):
    """Calibrated surface violates a no-arbitrage condition."""


class SurfaceQualityError(Exception):
    """Calibration converged but does not reproduce the market within tolerance."""


class InsufficientDataError(Exception):
    """Not enough liquid quotes to attempt a calibration."""


# ==========================================================================
# CONFIG  — every magic number lives here, nowhere else
# ==========================================================================
@dataclass(frozen=True)
class SurfaceConfig:
    # --- liquidity filters -------------------------------------------------
    min_maturity_years: float = 0.05
    max_maturity_years: float = 2.50
    max_rel_spread: float = 0.12       # (ask-bid)/mid
    min_open_interest: int = 50
    min_quotes_per_slice: int = 8
    min_atm_pairs_for_regression: int = 5
    atm_band: float = 0.08             # +/- fraction of spot for parity regression

    # --- resource guard (Sprint 31) — NOT a math parameter -------------------
    # Upper bound on the number of option chains pulled in one live request.
    # Each chain is a network round-trip plus two pandas DataFrames; an
    # uncapped ^SPX pull is 40+ of them and is the OOM risk on a small
    # container. Changing this changes cost/coverage, never the fit.
    max_expiries: int = 25

    # --- fit quality gates (IV space, decimal vol: 0.015 == 1.5 vol points) -
    tol_iv_pooled: float = 0.015
    tol_iv_peak_slice: float = 0.030

    # --- optimizer ---------------------------------------------------------
    seeds: Tuple[Tuple[float, float, float], ...] = (
        (-0.45, 1.15, 0.35),
        (-0.25, 0.75, 0.55),
        (-0.65, 1.35, 0.40),
    )
    bounds: Tuple[Tuple[float, float], ...] = ((-0.88, 0.88), (0.05, 3.50), (0.05, 0.95))
    barrier_scale: float = 1e-6
    constraint_scale: float = 1e2

    # --- numerical ---------------------------------------------------------
    iv_search_lo: float = 0.001
    iv_search_hi: float = 4.000
    spread_floor: float = 0.05         # price units, for inverse-spread weights

    def __post_init__(self):
        if self.tol_iv_pooled <= 0 or self.tol_iv_peak_slice <= 0:
            raise ValueError("tolerances must be positive")
        if self.tol_iv_peak_slice < self.tol_iv_pooled:
            raise ValueError("peak-slice tolerance must be >= pooled tolerance")


DEFAULT_CONFIG = SurfaceConfig()


# ==========================================================================
# PURE MATH  — no I/O, no globals. Production and tests call these.
# ==========================================================================
def black_from_forward(F, K, T, df, sigma, option_type="call"):
    """Black-76 European price given forward F and discount factor df."""
    if T <= 0 or sigma <= 0:
        intr = max(F - K, 0.0) if option_type == "call" else max(K - F, 0.0)
        return intr * df
    sqT = sigma * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sigma * sigma * T) / sqT
    d2 = d1 - sqT
    if option_type == "call":
        return df * (F * norm.cdf(d1) - K * norm.cdf(d2))
    return df * (K * norm.cdf(-d2) - F * norm.cdf(-d1))


def implied_vol_from_forward(price, F, K, T, df, option_type="call", cfg=DEFAULT_CONFIG):
    """Invert Black-76. Returns np.nan when the quote is not invertible."""
    intrinsic = (max(F - K, 0.0) if option_type == "call" else max(K - F, 0.0)) * df
    if not np.isfinite(price) or price <= intrinsic:
        return np.nan
    try:
        return brentq(
            lambda s: black_from_forward(F, K, T, df, s, option_type) - price,
            cfg.iv_search_lo,
            cfg.iv_search_hi,
        )
    except ValueError:
        return np.nan


def extract_forward_and_df(strikes, call_minus_put):
    """
    Recover (F, DF) from put-call parity by OLS.

        C - P = DF*F - DF*K      =>  intercept = DF*F,  slope = -DF

    Exact for European options. Do NOT use with American quotes.
    Returns plain Python floats.
    """
    strikes = np.asarray(strikes, dtype=float)
    y = np.asarray(call_minus_put, dtype=float)
    if strikes.size < 2:
        raise InsufficientDataError("need >= 2 strikes for parity regression")
    X = np.column_stack((np.ones(strikes.size), strikes))
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    intercept, slope = float(beta[0]), float(beta[1])
    df = -slope
    if not np.isfinite(df) or df <= 0.0:
        raise InsufficientDataError(f"degenerate discount factor from regression: {df}")
    return intercept / df, df


def slice_theta_atm(ks, ws):
    """ATM total variance: w interpolated to k=0. Requires ks to straddle zero."""
    ks = np.asarray(ks, dtype=float)
    ws = np.asarray(ws, dtype=float)
    if not (ks.min() < 0.0 < ks.max()):
        raise InsufficientDataError("log-moneyness does not straddle k=0")
    order = np.argsort(ks)
    return float(np.interp(0.0, ks[order], ws[order]))


def pava(y):
    """Pool-Adjacent-Violators. Returns the least-squares non-decreasing fit."""
    vals, wts = [], []
    for v in y:
        vals.append(float(v))
        wts.append(1.0)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2 = vals.pop(), wts.pop()
            v1, w1 = vals.pop(), wts.pop()
            vals.append((v1 * w1 + v2 * w2) / (w1 + w2))
            wts.append(w1 + w2)
    return [v for v, w in zip(vals, wts) for _ in range(int(w))]


def ssvi_w(k, theta, rho, phi):
    """
    Gatheral-Jacquier SSVI total variance.

        w(k) = theta/2 * (1 + rho*phi*k + sqrt((phi*k + rho)^2 + (1 - rho^2)))
    """
    v = phi * np.asarray(k, dtype=float)
    return 0.5 * theta * (1.0 + rho * v + np.sqrt((v + rho) ** 2 + (1.0 - rho * rho)))


def ssvi_phi(theta, eta, gamma):
    """Power-law phi(theta) = eta / (theta^gamma * (1+theta)^(1-gamma))."""
    return eta / (np.power(theta, gamma) * np.power(1.0 + theta, 1.0 - gamma))


def gj_conditions(theta, phi, rho):
    """
    Gatheral-Jacquier Thm 4.2 butterfly conditions.
    Both must hold. NOTE the theta factors — omitting them is a classic error
    that makes every short-dated slice look arbitrageable.
    """
    c1 = theta * phi * (1.0 + abs(rho))
    c2 = theta * phi * phi * (1.0 + abs(rho))
    return c1, c2


# ==========================================================================
# CALIBRATION
# ==========================================================================
@dataclass
class Slice:
    T: float
    theta_atm: float
    k: np.ndarray
    w: np.ndarray
    iv: np.ndarray
    weight: np.ndarray
    strike: np.ndarray
    forward: float
    discount_factor: float
    extrapolated: bool = False


@dataclass
class SurfaceFit:
    rho: float
    eta: float
    gamma: float
    rmse_iv_pooled: float
    rmse_iv_peak_slice: float
    n_points: int
    n_slices: int
    max_listed_maturity: float
    theta_adjusted: bool
    ticker: str
    as_of: str
    version: str = __version__
    per_slice: List[dict] = field(default_factory=list)


def ssvi_objective(params, slices: Sequence[Slice], cfg=DEFAULT_CONFIG):
    rho, eta, gamma = params
    if abs(rho) >= 0.99 or eta <= 1e-3 or gamma <= 1e-3 or gamma >= 0.99:
        return 1e8
    barrier = -cfg.barrier_scale * (
        np.log(1.0 - rho * rho) + np.log(gamma) + np.log(1.0 - gamma)
    )
    sse = 0.0
    for s in slices:
        phi = ssvi_phi(s.theta_atm, eta, gamma)
        c1, c2 = gj_conditions(s.theta_atm, phi, rho)
        if c1 >= 4.0:
            sse += cfg.constraint_scale * (c1 - 3.99) ** 2
        if c2 > 4.0:
            sse += cfg.constraint_scale * (c2 - 3.99) ** 2
        sse += float(np.sum(s.weight * (s.w - ssvi_w(s.k, s.theta_atm, rho, phi)) ** 2))
    return sse + barrier


def iv_diagnostics(slices: Sequence[Slice], rho, eta, gamma):
    """
    Fit quality in IV space (vol points), computed from the DATA ONLY —
    no barrier or constraint terms. Total-variance space is unusable as a
    diagnostic because w = sigma^2*T spans ~20x across a 0.1-2.0y tenor
    range, so short-dated residuals dilute the metric.
    """
    sq_total, n_total, peak, per_slice = 0.0, 0, 0.0, []
    for s in slices:
        phi = ssvi_phi(s.theta_atm, eta, gamma)
        iv_model = np.sqrt(np.maximum(ssvi_w(s.k, s.theta_atm, rho, phi), 1e-12) / s.T)
        err = iv_model - s.iv
        sq = float(np.sum(err ** 2))
        rmse_s = float(np.sqrt(sq / len(err)))
        sq_total += sq
        n_total += len(err)
        peak = max(peak, rmse_s)
        per_slice.append(
            {
                "T": round(s.T, 4),
                "theta_atm": round(s.theta_atm, 6),
                "n_quotes": int(len(err)),
                "rmse_iv": round(rmse_s, 5),
                "max_abs_iv_err": round(float(np.max(np.abs(err))), 5),
                "forward": round(s.forward, 4),
                "discount_factor": round(s.discount_factor, 6),
            }
        )
    pooled = float(np.sqrt(sq_total / n_total)) if n_total else np.nan
    return pooled, peak, n_total, per_slice


def calibrate(slices: List[Slice], ticker="SYNTHETIC", cfg=DEFAULT_CONFIG) -> SurfaceFit:
    """Enforce monotone theta, fit globally, then gate on IV-space quality."""
    if not slices:
        raise InsufficientDataError("no slices to calibrate")

    slices = sorted(slices, key=lambda s: s.T)

    # --- calendar arbitrage: SSVI's guarantee is CONDITIONAL on theta_T
    #     being non-decreasing. Enforce it with PAVA, don't assume it.
    thetas = [s.theta_atm for s in slices]
    smoothed = pava(thetas)
    adjusted = not np.allclose(thetas, smoothed, rtol=0, atol=1e-12)
    if adjusted:
        warnings.warn("theta_T was not monotone; PAVA adjustment applied.")
    for s, th in zip(slices, smoothed):
        s.theta_atm = th

    # --- multi-start. Track lowest objective regardless of the success flag;
    #     L-BFGS-B reports success=False on perfectly good solutions.
    best_fun, best = np.inf, None
    for seed in cfg.seeds:
        res = minimize(
            ssvi_objective, list(seed), args=(slices, cfg),
            method="L-BFGS-B", bounds=[tuple(b) for b in cfg.bounds],
        )
        if np.isfinite(res.fun) and res.fun < best_fun:
            best_fun, best = float(res.fun), res.x
    if best is None:
        raise SurfaceQualityError("no seed produced a finite objective")

    rho, eta, gamma = (float(x) for x in best)

    # --- post-fit no-arbitrage gate on the FITTED surface
    for s in slices:
        c1, c2 = gj_conditions(s.theta_atm, ssvi_phi(s.theta_atm, eta, gamma), rho)
        if c1 >= 4.0:
            raise SurfaceArbitrageError(f"GJ condition 1 violated at T={s.T:.3f} (c1={c1:.3f})")
        if c2 > 4.0:
            raise SurfaceArbitrageError(f"GJ condition 2 violated at T={s.T:.3f} (c2={c2:.3f})")

    # --- quality gate
    pooled, peak, n_pts, per_slice = iv_diagnostics(slices, rho, eta, gamma)
    if not np.isfinite(pooled) or pooled > cfg.tol_iv_pooled:
        raise SurfaceQualityError(
            f"pooled IV RMSE {pooled:.4f} exceeds tolerance {cfg.tol_iv_pooled:.4f}"
        )
    if peak > cfg.tol_iv_peak_slice:
        raise SurfaceQualityError(
            f"peak slice IV RMSE {peak:.4f} exceeds tolerance {cfg.tol_iv_peak_slice:.4f}"
        )

    return SurfaceFit(
        rho=rho, eta=eta, gamma=gamma,
        rmse_iv_pooled=pooled, rmse_iv_peak_slice=peak,
        n_points=n_pts, n_slices=len(slices),
        max_listed_maturity=max(s.T for s in slices),
        theta_adjusted=adjusted, ticker=ticker,
        as_of=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        per_slice=per_slice,
    )


def surface_iv(fit: SurfaceFit, k, T, theta_curve):
    """
    Query IV at (k, T). theta_curve maps T -> theta.
    Beyond max_listed_maturity this is EXTRAPOLATION — the caller must
    surface that fact to the user, not bury it.
    """
    theta = theta_curve(T)
    phi = ssvi_phi(theta, fit.eta, fit.gamma)
    return np.sqrt(np.maximum(ssvi_w(k, theta, fit.rho, phi), 1e-12) / T)


# ==========================================================================
# LIVE DATA  (yfinance)
# ==========================================================================
def build_slices_from_yahoo(
    ticker="^SPX", cfg=DEFAULT_CONFIG, stats: Optional[dict] = None
) -> Tuple[List[Slice], str]:
    """
    Pull live chains and build calibration slices.

    ``stats`` is an optional out-parameter (Sprint 31). When a dict is passed it
    is populated with expiry-cap telemetry so the caller can report a bound cap
    to the user. Left as None it behaves exactly as before — ``main()`` and the
    self-tests are unaffected.
    """
    import pandas as pd
    import yfinance as yf

    obj = yf.Ticker(ticker)
    expiries = obj.options
    if not expiries and ticker == "^SPX":
        warnings.warn("^SPX returned no chains; trying ^XSP (mini-SPX, European).")
        ticker = "^XSP"
        obj = yf.Ticker(ticker)
        expiries = obj.options
    if not expiries:
        raise InsufficientDataError(
            f"no option expiries for {ticker}. "
            "Do NOT substitute SPY — American exercise breaks put-call parity."
        )

    hist = obj.history(period="1d")
    if hist.empty:
        raise InsufficientDataError(f"no spot price for {ticker}")
    spot = float(hist["Close"].iloc[-1])

    today = _dt.date.today()
    out: List[Slice] = []

    # --- Sprint 31 resource guard (NOT math). Resolve the in-window expiries
    #     up front, then bound how many chains we actually fetch. When the cap
    #     binds we keep an EVENLY SPACED subsample that always retains the
    #     first and last entry: naively truncating the head would drop the
    #     LEAPS, silently shortening max_listed_maturity and moving the
    #     extrapolation boundary the UI reports. Same filter as before, so with
    #     <= max_expiries in-window expiries the behaviour is byte-identical.
    in_window: List[Tuple[str, float]] = []
    for exp in expiries:
        T = (_dt.datetime.strptime(exp, "%Y-%m-%d").date() - today).days / 365.0
        if cfg.min_maturity_years <= T <= cfg.max_maturity_years:
            in_window.append((exp, T))
    in_window.sort(key=lambda pair: pair[1])

    n_available = len(in_window)
    cap_applied = n_available > cfg.max_expiries
    if cap_applied:
        keep = np.unique(
            np.linspace(0, n_available - 1, cfg.max_expiries).round().astype(int)
        )
        in_window = [in_window[i] for i in keep]
    if stats is not None:
        stats["n_expiries_available"] = n_available
        stats["n_expiries_processed"] = len(in_window)
        stats["expiry_cap"] = cfg.max_expiries
        stats["expiry_cap_applied"] = cap_applied

    for exp, T in in_window:
        try:
            chain = obj.option_chain(exp)
        except Exception as exc:
            warnings.warn(f"chain fetch failed for {exp}: {exc}")
            continue

        # Everything from here to the `finally` is unchanged logic; the wrapper
        # exists only so the pandas frames are released on EVERY exit path,
        # including the `continue`s that skip a slice.
        calls = puts = merged = band = None
        try:
            def clean(df):
                df = df.copy()
                df["mid"] = (df["bid"] + df["ask"]) / 2.0
                rel = (df["ask"] - df["bid"]) / df["mid"].clip(lower=0.01)
                return df[(df["bid"] > 0) & (rel <= cfg.max_rel_spread)]

            calls, puts = clean(chain.calls), clean(chain.puts)

            merged = pd.merge(calls, puts, on="strike", suffixes=("_c", "_p"))
            band = merged[
                (merged["strike"] >= spot * (1 - cfg.atm_band))
                & (merged["strike"] <= spot * (1 + cfg.atm_band))
            ]
            if len(band) < cfg.min_atm_pairs_for_regression:
                continue
            try:
                F, df_disc = extract_forward_and_df(
                    band["strike"].values, (band["mid_c"] - band["mid_p"]).values
                )
            except InsufficientDataError:
                continue
            # DF = exp(-r*T); bound r to a generous but real range (-15%/yr to +2%/yr)
            # rather than the old 0 < DF <= 1.05, which permits ~ -97%/yr at short T
            # and would pass a badly wrong parity regression without complaint.
            df_lo, df_hi = np.exp(-0.02 * T), np.exp(0.15 * T)
            if not (df_lo < df_disc < df_hi):
                continue

            recs = []
            for frame, kind in ((calls, "call"), (puts, "put")):
                for _, r in frame.iterrows():
                    K = float(r["strike"])
                    # OTM only: liquid side, tight spreads, no ITM stale marks
                    if (kind == "call" and K < F) or (kind == "put" and K >= F):
                        continue
                    oi = r.get("openInterest", np.nan)
                    # NaN < n is False -> the filter would FAIL OPEN without this
                    if pd.isna(oi) or oi < cfg.min_open_interest:
                        continue
                    iv = implied_vol_from_forward(float(r["mid"]), F, K, T, df_disc, kind, cfg)
                    if np.isnan(iv):
                        continue
                    recs.append((K, iv, 1.0 / max(float(r["ask"] - r["bid"]), cfg.spread_floor)))

            if len(recs) < cfg.min_quotes_per_slice:
                continue

            K_arr = np.array([r[0] for r in recs])
            iv_arr = np.array([r[1] for r in recs])
            wt_arr = np.array([r[2] for r in recs])
            k_arr = np.log(K_arr / F)
            w_arr = iv_arr ** 2 * T
            try:
                theta = slice_theta_atm(k_arr, w_arr)
            except InsufficientDataError:
                warnings.warn(f"T={T:.3f} dropped: strikes do not straddle k=0")
                continue

            out.append(Slice(T=T, theta_atm=theta, k=k_arr, w=w_arr, iv=iv_arr,
                             weight=wt_arr, strike=K_arr, forward=F, discount_factor=df_disc))
        finally:
            # Sprint 31 containment: drop the per-expiry pandas frames as soon
            # as the slice is built. Only the extracted numpy arrays (k, w, iv,
            # weight, strike) are retained — those are kilobytes; the chains are
            # megabytes. Runs on every path, including the `continue`s above.
            chain = calls = puts = merged = band = None
            del chain, calls, puts, merged, band
            gc.collect()

    if not out:
        raise InsufficientDataError("no slice cleared the liquidity filters")
    return out, ticker


# ==========================================================================
# SYNTHETIC DATA  (offline / CI)
# ==========================================================================
def build_synthetic_slices(rho=-0.72, eta=1.15, gamma=0.38, noise_pct=0.01,
                           seed=0, maturities=(0.1, 0.25, 0.5, 1.0, 2.0)) -> List[Slice]:
    """Generate an SSVI surface with proportional IV noise. For tests."""
    rng = np.random.default_rng(seed)
    out = []
    for T in maturities:
        theta = 0.04 * T                      # ~20% flat ATM vol
        phi = ssvi_phi(theta, eta, gamma)
        k = np.linspace(-0.15, 0.10, 21)
        iv = np.sqrt(ssvi_w(k, theta, rho, phi) / T)
        iv = iv * (1.0 + rng.normal(0.0, noise_pct, k.size))
        w = iv ** 2 * T
        F = 5000.0
        out.append(Slice(T=T, theta_atm=slice_theta_atm(k, w), k=k, w=w, iv=iv,
                         weight=np.ones(k.size), strike=F * np.exp(k),
                         forward=F, discount_factor=float(np.exp(-0.045 * T))))
    return out


# ==========================================================================
# TEST SUITE  — pass/fail only, no prompts. Every test calls PRODUCTION code.
# ==========================================================================
def _t1_parity():
    F_true, DF_true = 4050.25, 0.9550
    K = np.array([3800.0, 3900.0, 4000.0, 4100.0, 4200.0])
    F, DF = extract_forward_and_df(K, DF_true * F_true - DF_true * K)
    assert np.isclose(F, F_true, atol=1e-6), f"F {F} != {F_true}"
    assert np.isclose(DF, DF_true, atol=1e-9), f"DF {DF} != {DF_true}"


def _t2_pava():
    for case in ([0.012, 0.009, 0.006],
                 [0.020, 0.011, 0.015, 0.013, 0.045],
                 [0.001, 0.004, 0.011, 0.028, 0.068]):
        out = pava(case)
        assert all(a <= b + 1e-15 for a, b in zip(out, out[1:])), f"not monotone: {out}"
        assert abs(sum(out) - sum(case)) < 1e-12, "PAVA must preserve the mean"


def _t3_roundtrip():
    """
    Assert on SURFACE REPRODUCTION ONLY — never on rho, eta or gamma.

    SSVI's parameters are weakly identified. Measured over 40 noise seeds at
    1% proportional IV noise:
        eta   can move ~7x while the surface stays within a fraction of a
              vol point, because gamma compensates along a ridge
        rho   |error| mean 0.087, sd 0.053, max 0.205
    A tolerance of 0.10 on rho fails 13/40 seeds; 0.15 fails 7/40. Any
    parameter assertion here is a flaky test.

    rmse_iv_pooled over the same 40 seeds: mean 0.373, max 0.597 vol points.
    Threshold set at 1.0 vol point — comfortably above observed, far below
    the 1.5 production gate.
    """
    for seed in range(12):
        fit = calibrate(build_synthetic_slices(rho=-0.72, seed=seed), ticker="TEST")
        assert fit.rmse_iv_pooled < 0.010, (
            f"seed {seed}: surface not reproduced, rmse {fit.rmse_iv_pooled*100:.2f} vol pts"
        )


def _t4_gj_theta_factor():
    """The theta factor must be present; without it every short slice trips."""
    theta, eta, gamma, rho = 0.001, 1.2, 0.35, -0.75
    phi = ssvi_phi(theta, eta, gamma)
    c1, _ = gj_conditions(theta, phi, rho)
    assert phi * (1 + abs(rho)) > 4.0, "test premise: unscaled quantity should exceed 4"
    assert c1 < 4.0, f"correctly scaled c1 should be well under 4, got {c1}"


def _distorted_slices(amp, seed=3):
    """
    Smile-SHAPE distortion. Note a uniform per-slice IV scale does NOT work as
    a test: theta_atm is read from the data per slice, so SSVI absorbs any
    level shift exactly. Only curvature/tilt in k breaks the fit.
    """
    slices = build_synthetic_slices(seed=seed)
    for s in slices:
        s.iv = s.iv * (1.0 + amp * s.k ** 2 + 0.8 * s.k)
        s.w = s.iv ** 2 * s.T
        s.theta_atm = slice_theta_atm(s.k, s.w)
    return slices


def _t5_quality_gate_rejects():
    """The gate must reject a surface SSVI cannot reproduce."""
    # amp 12 measures ~2+ vol pts, above the 1.5 production tolerance
    try:
        calibrate(_distorted_slices(amp=12.0), ticker="TEST")
    except (SurfaceQualityError, SurfaceArbitrageError):
        pass
    else:
        raise AssertionError("gate accepted a surface it should have rejected")

    # gate mechanism fires on a tightened config even for mild distortion
    tight = SurfaceConfig(tol_iv_pooled=0.002, tol_iv_peak_slice=0.004)
    try:
        calibrate(_distorted_slices(amp=2.5), ticker="TEST", cfg=tight)
    except SurfaceQualityError:
        return
    raise AssertionError("tightened gate did not fire")


def _t8_gate_accepts_good():
    """Guard against a gate so tight it rejects everything."""
    fit = calibrate(build_synthetic_slices(seed=11), ticker="TEST")
    assert fit.rmse_iv_pooled < DEFAULT_CONFIG.tol_iv_pooled


def _t6_straddle_guard():
    """np.interp CLAMPS silently outside its range — must raise instead."""
    try:
        slice_theta_atm(np.array([0.01, 0.02, 0.03]), np.array([0.04, 0.05, 0.06]))
    except InsufficientDataError:
        return
    raise AssertionError("slice_theta_atm accepted ks that do not straddle 0")


def _t7_black_parity():
    F, K, T, df, s = 5000.0, 4800.0, 1.0, 0.955, 0.20
    c = black_from_forward(F, K, T, df, s, "call")
    p = black_from_forward(F, K, T, df, s, "put")
    assert np.isclose(c - p, df * (F - K), atol=1e-9), "Black-76 violates parity"
    iv = implied_vol_from_forward(c, F, K, T, df, "call")
    assert np.isclose(iv, s, atol=1e-8), f"IV inversion {iv} != {s}"


def run_tests(verbose=True) -> bool:
    tests = [
        ("T1  put-call parity regression", _t1_parity),
        ("T2  PAVA monotonicity",          _t2_pava),
        ("T3  SSVI round-trip (surface)",  _t3_roundtrip),
        ("T4  GJ theta scaling",           _t4_gj_theta_factor),
        ("T5  quality gate rejects",       _t5_quality_gate_rejects),
        ("T6  k-straddle guard",           _t6_straddle_guard),
        ("T7  Black-76 parity + inversion", _t7_black_parity),
        ("T8  gate accepts a good fit",     _t8_gate_accepts_good),
    ]
    failed = 0
    for name, fn in tests:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fn()
            if verbose:
                print(f"  PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    if verbose:
        print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed == 0


# ==========================================================================
# CLI
# ==========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="Arbitrage-free SSVI surface builder")
    ap.add_argument("--ticker", default="^SPX", help="European index only (^SPX, ^XSP)")
    ap.add_argument("--self-test", action="store_true", help="run tests offline and exit")
    ap.add_argument("--synthetic", action="store_true", help="calibrate synthetic data")
    ap.add_argument("--json", metavar="PATH", help="write the fit to JSON")
    ap.add_argument("--plot", action="store_true", help="render the surface")
    args = ap.parse_args(argv)

    if args.self_test:
        print(f"ssvi_surface {__version__} — self test\n")
        return 0 if run_tests() else 1

    try:
        if args.synthetic:
            slices, ticker = build_synthetic_slices(), "SYNTHETIC"
        else:
            slices, ticker = build_slices_from_yahoo(args.ticker)

        fit = calibrate(slices, ticker=ticker)
    except InsufficientDataError as exc:
        print(f"\nINSUFFICIENT DATA: {exc}")
        return 2
    except SurfaceQualityError as exc:
        print(f"\nQUALITY GATE FAILED: {exc}")
        return 2
    except SurfaceArbitrageError as exc:
        print(f"\nARBITRAGE VIOLATION: {exc}")
        return 2

    print(f"\nSSVI calibration — {fit.ticker} @ {fit.as_of}")
    print(f"  rho   {fit.rho:+.4f}   (skew; well identified)")
    print(f"  eta   {fit.eta: .4f}   (weakly identified — see docstring)")
    print(f"  gamma {fit.gamma: .4f}  (weakly identified)")
    print(f"  pooled IV RMSE     {fit.rmse_iv_pooled*100:.2f} vol pts")
    print(f"  peak slice IV RMSE {fit.rmse_iv_peak_slice*100:.2f} vol pts")
    print(f"  {fit.n_points} quotes across {fit.n_slices} maturities")
    print(f"  longest listed tenor: {fit.max_listed_maturity:.2f}y "
          f"(beyond this is EXTRAPOLATION)")
    if fit.theta_adjusted:
        print("  WARNING: theta_T required PAVA adjustment")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(asdict(fit), fh, indent=2)
        print(f"\nwrote {args.json}")

    if args.plot:
        import matplotlib.pyplot as plt
        ks = np.linspace(-0.15, 0.10, 60)
        fig = plt.figure(figsize=(11, 7))
        ax = fig.add_subplot(111, projection="3d")
        Ts = sorted(s.T for s in slices)
        Z = np.zeros((len(Ts), ks.size))
        th_map = {s.T: s.theta_atm for s in slices}
        for i, T in enumerate(Ts):
            phi = ssvi_phi(th_map[T], fit.eta, fit.gamma)
            Z[i] = np.sqrt(ssvi_w(ks, th_map[T], fit.rho, phi) / T)
        X, Y = np.meshgrid(ks, Ts)
        ax.plot_surface(X, Y, Z, cmap="coolwarm", edgecolor="none", alpha=0.85)
        for s in slices:
            ax.scatter(s.k, np.full(s.k.size, s.T), s.iv, c="black", s=4)
        ax.set_xlabel("log-moneyness  k = ln(K/F)")
        ax.set_ylabel("maturity T (years)")
        ax.set_zlabel("implied volatility")
        ax.set_title(f"SSVI surface — {fit.ticker}")
        ax.view_init(elev=25, azim=-130)
        plt.tight_layout()
        plt.show()

    return 0


if __name__ == "__main__":
    sys.exit(main())
