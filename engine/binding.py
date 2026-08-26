"""Receptor crosslinking and antibody competition — the physics under the model.

Nothing here is fitted. These are closed-form equilibrium solutions to binding
problems that were worked out decades ago; the only inputs are affinities and
counts. They are what makes the simulator produce numbers rather than opinions.
"""

from __future__ import annotations

import math


def free_allergen(total_m: float, igg4_m: float, k_igg4: float) -> float:
    """Allergen still able to bridge two IgE after blocking IgG4 covers epitopes.

    This is the mechanism behind oral immunotherapy, and getting its *shape*
    right matters more than getting its affinity right.

    IgG4 does not disarm the mast cell — it occupies allergen epitopes so the
    allergen can no longer bridge two IgE molecules. At the mucosa IgG4 is in
    large excess over allergen, so the right description is epitope occupancy,
    not mass-action scavenging:

        f = K*G / (1 + K*G)          fraction of epitopes covered
        bridgeable = A_total * (1-f)^2

    The exponent is the whole point. Crosslinking needs *two* free epitopes on
    the same molecule, so blocking is quadratic in coverage: covering 80% of
    epitopes removes 96% of bridging capacity, not 80%. Modelling this as simple
    1:1 sequestration understates OIT's effect roughly four-fold and forces the
    calibration into implausibly high IgG4 titres to compensate.
    """
    if total_m <= 0.0:
        return 0.0
    if k_igg4 <= 0.0 or igg4_m <= 0.0:
        return total_m

    occupancy = k_igg4 * igg4_m / (1.0 + k_igg4 * igg4_m)
    return total_m * (1.0 - occupancy) ** 2


def crosslinks(conc_m: float, receptors: float, k_bind: float, k_cross: float) -> float:
    """Crosslinked receptor pairs per cell at equilibrium.

    Bivalent ligand binding monovalent cell-surface receptors — the Dembo &
    Goldstein (1978) equilibrium. Conservation of receptors gives

        R_total = S + 2*K1*C*S + 2*Kx*K1*C*S^2

    where S is free sensitized receptors, the middle term is singly-bound
    receptors (factor 2 for the ligand's two arms) and the last is receptors
    tied up in crosslinks. Solving the quadratic for S gives the crosslink
    count directly.

    Args:
        conc_m:    free allergen, mol/L
        receptors: sensitized (specific-IgE-bearing) FcepsilonRI per cell
        k_bind:    allergen-IgE association constant, 1/M
        k_cross:   surface crosslinking constant, cell/receptor

    The function is non-monotonic in `conc_m` by construction: crosslinking
    rises linearly at low allergen, peaks near C = 1/(2*K1), then falls as 1/C
    because every IgE gets its own allergen and none are bridged. That is the
    real prozone (hook) effect. `validate.py` checks both that the peak exists
    and that it sits above the doses people actually eat — if it did not, the
    model would be claiming that more milk is safer than less.
    """
    if conc_m <= 0.0 or receptors <= 0.0 or k_bind <= 0.0:
        return 0.0

    kc = k_bind * conc_m
    a = 2.0 * k_cross * kc
    b = 1.0 + 2.0 * kc

    if a <= 0.0:
        free = receptors / b
    else:
        free = (-b + math.sqrt(b * b + 4.0 * a * receptors)) / (2.0 * a)

    return k_cross * kc * free * free


def sensitized_receptors(
    specific_ige_ku: float,
    total_ige_ku: float,
    receptors_per_cell: float,
    k_occupancy_ku: float,
) -> float:
    """FcepsilonRI carrying *milk-specific* IgE, per mast cell.

    Two effects multiply, and both are clinically observed:

      * total IgE sets how full the receptors are (saturable in total IgE)
      * the specific/total ratio sets what fraction of that is milk-reactive

    This is why a child with sIgE 50 kU/L against a total of 200 reacts more
    fiercely than one with sIgE 50 against a total of 2000 — the same specific
    titre, diluted across the same receptors.
    """
    if total_ige_ku <= 0.0 or specific_ige_ku <= 0.0:
        return 0.0

    occupancy = total_ige_ku / (total_ige_ku + k_occupancy_ku)
    specific_fraction = min(1.0, specific_ige_ku / total_ige_ku)
    return receptors_per_cell * occupancy * specific_fraction


def hill(x: float, half: float, coef: float) -> float:
    """Hill activation, 0..1. Guarded against x=0 with fractional coefficients."""
    if x <= 0.0:
        return 0.0
    xn = x**coef
    return xn / (xn + half**coef)
