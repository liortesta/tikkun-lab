"""Pharmacology readouts — the numbers that travel outside this simulator.

Everything here is a measure a working pharmacologist already uses, computed
from the model's output the same way it would be computed from bench data. That
is deliberate: a result is only worth anything if it lands in units someone can
compare against their own experiment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit


@dataclass
class HillFit:
    """A four-parameter dose-response curve fitted to (dose, effect) data."""

    bottom: float
    top: float
    ec50: float
    hill_slope: float
    r_squared: float

    def __call__(self, dose):
        dose = np.asarray(dose, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(dose > 0, (self.ec50 / np.maximum(dose, 1e-300)) ** self.hill_slope, np.inf)
        return self.bottom + (self.top - self.bottom) / (1.0 + ratio)

    def effective_dose(self, fraction: float) -> float:
        """Dose producing `fraction` of the fitted span, e.g. 0.5 for EC50."""
        if not 0.0 < fraction < 1.0:
            raise ValueError("fraction must be strictly between 0 and 1")
        return self.ec50 * (fraction / (1.0 - fraction)) ** (1.0 / self.hill_slope)

    def summary(self) -> str:
        return (
            f"EC50 {self.ec50:.4g}  Hill {self.hill_slope:.2f}  "
            f"span {self.bottom:.2f}-{self.top:.2f}  R^2 {self.r_squared:.4f}"
        )


def fit_hill(doses, effects) -> HillFit:
    """Fit a four-parameter logistic in log-dose. Raises if the fit fails.

    This is the same curve GraphPad or a plate-reader package fits, so the EC50
    it reports is comparable with one measured on real cells.
    """
    doses = np.asarray(doses, dtype=float)
    effects = np.asarray(effects, dtype=float)
    keep = doses > 0
    doses, effects = doses[keep], effects[keep]
    if doses.size < 4:
        raise ValueError("need at least 4 positive-dose points to fit")

    log_d = np.log10(doses)

    def model(x, bottom, top, log_ec50, slope):
        return bottom + (top - bottom) / (1.0 + 10.0 ** ((log_ec50 - x) * slope))

    guess = [effects.min(), effects.max(), float(np.median(log_d)), 1.0]
    bounds = (
        [-np.inf, -np.inf, log_d.min() - 3.0, 0.05],
        [np.inf, np.inf, log_d.max() + 3.0, 20.0],
    )
    popt, _ = curve_fit(model, log_d, effects, p0=guess, bounds=bounds, maxfev=40000)

    predicted = model(log_d, *popt)
    residual = float(np.sum((effects - predicted) ** 2))
    total = float(np.sum((effects - effects.mean()) ** 2))
    r2 = 1.0 - residual / total if total > 0 else 1.0

    return HillFit(
        bottom=float(popt[0]), top=float(popt[1]),
        ec50=float(10.0 ** popt[2]), hill_slope=float(popt[3]),
        r_squared=r2,
    )


def therapeutic_index(toxic_dose: float, effective_dose: float) -> float:
    """TD50/ED50. Below ~10 a drug needs therapeutic monitoring."""
    if effective_dose <= 0:
        return math.inf
    return toxic_dose / effective_dose


def fold_change(before: float, after: float) -> float:
    """After/before, guarding the zero and infinity cases the model can produce."""
    if before <= 0:
        return math.inf if after > 0 else 1.0
    if not math.isfinite(after):
        return math.inf
    return after / before


def log_shift(before: float, after: float) -> float:
    """Fold change in log10 units — how thresholds are reported in OIT trials."""
    fc = fold_change(before, after)
    if not math.isfinite(fc) or fc <= 0:
        return math.inf
    return math.log10(fc)


def summarise_shift(before: float, after: float, unit: str = "mg") -> str:
    fc = fold_change(before, after)
    arrow = f"{before:.3g} -> {after:.3g} {unit}"
    if not math.isfinite(fc):
        return f"{arrow}  (no reaction at any tested dose)"
    return f"{arrow}  ({fc:.1f}x, {math.log10(fc):+.2f} log10)"
