"""Arps decline curve fitting + EUR estimation.

Used for offset-well analysis in supporting tables only. Not used for
acquisition recommendations — landmen evaluate decline + PV10 with their own
licensed reservoir tools (Aries, PHDwin), not automated guesses.

Hyperbolic decline:
    q(t) = qi / (1 + b * di * t) ** (1/b)        for b > 0
    q(t) = qi * exp(-di * t)                     for b == 0 (exponential)

Where t is months elapsed since peak production.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.optimize import curve_fit


@dataclass
class DeclineFit:
    qi: float
    b: float
    di_monthly: float
    months_fit: int
    r_squared: float

    def predict(self, t_months: int) -> float:
        if self.b == 0:
            return self.qi * math.exp(-self.di_monthly * t_months)
        return self.qi / ((1.0 + self.b * self.di_monthly * t_months) ** (1.0 / self.b))

    def eur(self, abandonment_rate: float, max_months: int = 600) -> float:
        """Estimated ultimate recovery: integrate until rate drops below abandonment."""
        total = 0.0
        for t in range(max_months):
            q = self.predict(t)
            if q < abandonment_rate:
                break
            total += q
        return total


def _hyperbolic(t: np.ndarray, qi: float, b: float, di: float) -> np.ndarray:
    # Clamp b ∈ (0, 2] in fit space for numeric stability
    return qi / np.power(1.0 + b * di * t, 1.0 / np.clip(b, 1e-3, 2.0))


def fit_arps(monthly_rates: Sequence[float]) -> DeclineFit | None:
    """Fit Arps hyperbolic decline to a non-empty sequence of monthly production rates."""
    rates = np.asarray([r for r in monthly_rates if r is not None and r > 0], dtype=float)
    if rates.size < 6:
        return None
    # Use the data from the peak onward
    peak_idx = int(np.argmax(rates))
    rates = rates[peak_idx:]
    if rates.size < 6:
        return None
    t = np.arange(rates.size, dtype=float)
    try:
        popt, _ = curve_fit(
            _hyperbolic, t, rates,
            p0=[float(rates[0]), 0.5, 0.05],
            bounds=([1e-3, 1e-3, 1e-4], [rates[0] * 5.0, 2.0, 1.0]),
            maxfev=4000,
        )
        qi, b, di = popt
        pred = _hyperbolic(t, *popt)
        ss_res = float(np.sum((rates - pred) ** 2))
        ss_tot = float(np.sum((rates - rates.mean()) ** 2))
        r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        return DeclineFit(
            qi=float(qi),
            b=float(b),
            di_monthly=float(di),
            months_fit=int(rates.size),
            r_squared=float(r_squared),
        )
    except Exception:
        return None
