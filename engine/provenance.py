"""Provenance tracking for every number the simulator uses.

This project stands or falls on one rule: a number is only usable if you can say
where it came from. Parameters therefore never appear as bare floats. Each one
carries a unit, a provenance class and a citation, and `Registry.audit()` renders
the whole set so any result can be traced back to its inputs.

Provenance classes, strongest to weakest:

    MEASURED    reported directly in the cited experiment
    DERIVED     arithmetic on measured values (unit conversion, ratio)
    CALIBRATED  fitted so the model reproduces a published clinical curve
    ASSUMED     a modelling choice with no measurement behind it
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Provenance(str, Enum):
    MEASURED = "measured"
    DERIVED = "derived"
    CALIBRATED = "calibrated"
    ASSUMED = "assumed"


#: Higher is stronger. Used to score how much of a result rests on real data.
TRUST = {
    Provenance.MEASURED: 3,
    Provenance.DERIVED: 2,
    Provenance.CALIBRATED: 1,
    Provenance.ASSUMED: 0,
}


@dataclass(frozen=True)
class Param:
    """One number, with everything needed to defend it."""

    value: float
    unit: str
    provenance: Provenance
    source: str
    note: str = ""

    def __float__(self) -> float:
        return float(self.value)


class Registry:
    """Attribute access returns the float; `meta()` returns the full Param.

        >>> P = Registry({"kon": Param(1e5, "1/M/s", Provenance.MEASURED, "Kinet 1999")})
        >>> P.kon
        100000.0
        >>> P.meta("kon").source
        'Kinet 1999'
    """

    def __init__(self, params: dict[str, Param], name: str = "params"):
        self._params = dict(params)
        self._name = name

    def __getattr__(self, key: str) -> float:
        try:
            return float(self._params[key].value)
        except KeyError:
            raise AttributeError(f"{self._name} has no parameter {key!r}") from None

    def __contains__(self, key: str) -> bool:
        return key in self._params

    def __iter__(self):
        return iter(self._params)

    def meta(self, key: str) -> Param:
        return self._params[key]

    def items(self):
        return self._params.items()

    def override(self, **changes: float) -> "Registry":
        """A copy with some values replaced. Overrides are marked ASSUMED —
        a hand-set value has no citation behind it any more."""
        out = dict(self._params)
        for key, value in changes.items():
            if key not in out:
                raise KeyError(f"{self._name} has no parameter {key!r}")
            old = out[key]
            out[key] = Param(
                value=value,
                unit=old.unit,
                provenance=Provenance.ASSUMED,
                source="runtime override",
                note=f"was {old.value:g} ({old.provenance.value}, {old.source})",
            )
        return Registry(out, self._name)

    def trust_score(self) -> float:
        """Fraction of maximum provenance strength across the registry, 0..1."""
        if not self._params:
            return 0.0
        got = sum(TRUST[p.provenance] for p in self._params.values())
        return got / (3 * len(self._params))

    def counts(self) -> dict[str, int]:
        out = {p.value: 0 for p in Provenance}
        for param in self._params.values():
            out[param.provenance.value] += 1
        return out

    def audit(self) -> str:
        """A readable provenance table for the whole registry."""
        rows = sorted(
            self._params.items(),
            key=lambda kv: (-TRUST[kv[1].provenance], kv[0]),
        )
        width = max((len(k) for k in self._params), default=4)
        lines = [
            f"{'parameter'.ljust(width)}  {'value':>12}  {'unit':<16} {'provenance':<11} source",
            f"{'-' * width}  {'-' * 12}  {'-' * 16} {'-' * 11} {'-' * 40}",
        ]
        for key, param in rows:
            lines.append(
                f"{key.ljust(width)}  {param.value:>12.4g}  {param.unit:<16} "
                f"{param.provenance.value:<11} {param.source}"
            )
        counts = self.counts()
        lines.append("")
        lines.append(
            "  ".join(f"{k}={v}" for k, v in counts.items())
            + f"   trust={self.trust_score():.0%}"
        )
        return "\n".join(lines)
