"""Online models used by the ORCA dispatcher.

Each model has a transparent fallback so the engine always runs, and a learned
version that is loaded from ``artifacts/`` when ``python -m orca.train`` has
produced one.
"""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: F401  (load before limiting threads)
from threadpoolctl import threadpool_limits

threadpool_limits(1)   # belt and braces; see orca/__init__.py


from ..features import FeatureStore  # noqa: E402

ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts"


def artifact(stem: str, ext: str = "joblib") -> Path:
    """Path of a model artifact for the active city: grid keeps plain names, others get a suffix."""
    from .. import city as G
    c = G.current().name
    return ARTIFACTS / (f"{stem}.{ext}" if c == "grid" else f"{stem}_{c}.{ext}")


def _load(name):
    p = artifact(name)
    if p.exists():
        try:
            return joblib.load(p)
        except Exception:  # corrupt or incompatible artifact: fall back
            return None
    return None


class EtaModel:
    """Pickup ETA in seconds.

    * ``learned``: gradient-boosted trees trained on logged pickup legs.
    * fallback: Manhattan distance over observed zone speeds (no hidden truth).
    """

    def __init__(self, fs: FeatureStore, use_learned: bool = True):
        self.fs = fs
        bundle = _load("eta") if use_learned else None
        self.model = bundle["model"] if bundle else None
        self.residual = bool(bundle) and bundle.get("meta", {}).get("target") == "log_ratio_to_physics"
        self.kind = "learned (gradient boosting)" if self.model is not None else "observed-speed heuristic"

    def predict_many(self, pairs, clock) -> np.ndarray:
        """pairs: list of (ax, az, bx, bz)."""
        if not pairs:
            return np.zeros(0)
        fs = self.fs
        if self.model is not None:
            X = np.array([fs.eta_features(*p, clock) for p in pairs])
            if self.residual:      # model predicts a log correction to distance / observed zone speed
                base = X[:, 0] / np.maximum(0.05, X[:, 3:6].mean(axis=1)) + 4
                return np.maximum(4.0, base * np.exp(self.model.predict(X)))
            return np.maximum(4.0, self.model.predict(X))
        out = np.empty(len(pairs))
        for i, (ax, az, bx, bz) in enumerate(pairs):
            v = (fs.zone_speed(ax, az) + fs.zone_speed(bx, az) + fs.zone_speed(bx, bz)) / 3
            out[i] = (abs(bx - ax) + abs(bz - az)) / max(0.05, v) + 4
        return out


class AcceptanceModel:
    """Per-driver acceptance probability: Beta-Bernoulli posterior updated online."""

    PRIOR_MEAN, PRIOR_N = 0.82, 8.0

    def p(self, agent, eta: float, p1: bool) -> float:
        base = (agent.accepts + self.PRIOR_N * self.PRIOR_MEAN) / (agent.offers + self.PRIOR_N)
        return min(0.995, max(0.03, base - 0.03 * max(0.0, eta / 60 - 4) + (0.06 if p1 else 0)))


class DemandForecaster:
    """Expected requests per zone over the next 5 minutes."""

    def __init__(self, fs: FeatureStore, use_learned: bool = True, source: str = "sim"):
        self.fs = fs
        bundle = _load(f"demand_{source}") if use_learned else None
        self.model = bundle["model"] if bundle else None
        self.kind = "learned (gradient boosting)" if self.model is not None else "EWMA with trend"
        n = fs.n_zones
        self.rate = [0.0] * n
        self.prev = [0.0] * n
        self.init = False
        self.forecast = [0.0] * n

    def update(self, clock: float):
        """Call once per simulated minute, after FeatureStore.roll_minute()."""
        fs = self.fs
        last = fs.hist[-1] if fs.hist else [0] * len(self.rate)
        for i, c in enumerate(last):
            self.prev[i] = self.rate[i]
            self.rate[i] = 0.65 * self.rate[i] + 0.35 * c if self.init else float(c)
        self.init = True
        if self.model is not None and len(fs.hist) >= 5:
            X = np.array([fs.demand_features(i, clock) for i in range(len(self.rate))])
            self.forecast = list(np.maximum(0.0, self.model.predict(X)))
        else:
            self.forecast = [r * 5 * min(1.5, max(0.7, r / (p + 1e-3))) for r, p in zip(self.rate, self.prev)]
