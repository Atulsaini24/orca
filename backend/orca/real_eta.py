"""Trip-duration model trained and validated on real NYC taxi trips.

    python -m orca.train eta-real

The simulator's pickup-ETA model learns from simulated pickup legs. This model checks the
same approach against reality: it predicts the duration of real Manhattan taxi trips from
what a dispatcher knows before the trip starts (pickup and dropoff zones, straight-line
distance, time of day, day of week), trained on the earlier days of January 2015 and tested
on the later days it never saw.

Baselines it must beat:
  * distance over the citywide median taxi speed
  * distance over the real median speed for that hour (the traffic profile the simulator uses)
  * the historical median duration for the same pickup zone -> dropoff zone pair in that hour
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .models import ARTIFACTS

REAL = Path(__file__).resolve().parents[1] / "data" / "nyc_tlc_2015-01_manhattan.parquet"
FEATURES = ["pu_zone", "do_zone", "dist_line_m", "dist_zones_m", "same_zone", "hour", "minute_of_day_sin",
            "minute_of_day_cos", "weekday"]


def _report(name, y, pred) -> dict:
    err = np.abs(y - pred)
    return dict(model=name, mae=float(err.mean()), median_abs_err=float(np.median(err)),
                p90_abs_err=float(np.percentile(err, 90)), mape=float(np.mean(err / y)))


def build(df: pd.DataFrame, centroids: pd.DataFrame) -> pd.DataFrame:
    t = df.tpep_pickup_datetime
    mod = t.dt.hour * 60 + t.dt.minute
    c = centroids
    pu = c.loc[df.PULocationID.values].to_numpy()
    do = c.loc[df.DOLocationID.values].to_numpy()
    return pd.DataFrame({
        "pu_zone": df.PULocationID.values, "do_zone": df.DOLocationID.values,
        "dist_line_m": np.hypot(df.dropoff_e.values - df.pickup_e.values, df.dropoff_n.values - df.pickup_n.values),
        "dist_zones_m": np.hypot(do[:, 0] - pu[:, 0], do[:, 1] - pu[:, 1]),
        "same_zone": (df.PULocationID.values == df.DOLocationID.values).astype(int),
        "hour": t.dt.hour.values, "minute_of_day_sin": np.sin(mod / 1440 * 2 * np.pi).values,
        "minute_of_day_cos": np.cos(mod / 1440 * 2 * np.pi).values, "weekday": t.dt.dayofweek.values,
    })


def train(args=None, path: Path = REAL, test_days: int = 4):
    t0 = time.time()
    df = pd.read_parquet(path)
    df["dur"] = (df.tpep_dropoff_datetime - df.tpep_pickup_datetime).dt.total_seconds()
    df = df[df.dur.between(60, 2 * 3600)].reset_index(drop=True)
    days = np.sort(df.tpep_pickup_datetime.dt.date.unique())
    split = days[-test_days]
    is_test = (df.tpep_pickup_datetime.dt.date >= split).values
    tr, te = df[~is_test], df[is_test]
    print(f"{len(tr):,} real trips for training ({days[0]} to {days[-test_days - 1]}), "
          f"{len(te):,} for testing ({split} to {days[-1]})")
    # zone centroids from the training trips only
    pts = pd.concat([tr[["PULocationID", "pickup_e", "pickup_n"]].set_axis(["z", "e", "n"], axis=1),
                     tr[["DOLocationID", "dropoff_e", "dropoff_n"]].set_axis(["z", "e", "n"], axis=1)])
    centroids = pts.groupby("z")[["e", "n"]].mean()
    Xtr, Xte = build(tr, centroids), build(te, centroids)
    ytr, yte = tr.dur.to_numpy(), te.dur.to_numpy()

    # baselines, all fitted on training days only
    speed_all = float(np.median(Xtr.dist_zones_m.clip(lower=200) / ytr))
    b1 = Xte.dist_zones_m.clip(lower=200) / speed_all
    hour_speed = (Xtr.dist_zones_m.clip(lower=200) / ytr).groupby(Xtr.hour).median()
    b2 = Xte.dist_zones_m.clip(lower=200) / Xte.hour.map(hour_speed).to_numpy()
    key_tr = Xtr.pu_zone.astype(str) + ">" + Xtr.do_zone.astype(str) + "@" + Xtr.hour.astype(str)
    key_te = Xte.pu_zone.astype(str) + ">" + Xte.do_zone.astype(str) + "@" + Xte.hour.astype(str)
    pair_hour = pd.Series(ytr).groupby(key_tr.values).median()
    pair = pd.Series(ytr).groupby((Xtr.pu_zone.astype(str) + ">" + Xtr.do_zone.astype(str)).values).median()
    b3 = key_te.map(pair_hour)
    b3 = b3.fillna((Xte.pu_zone.astype(str) + ">" + Xte.do_zone.astype(str)).map(pair)).fillna(pd.Series(b2)).to_numpy()

    cat = [FEATURES.index("pu_zone"), FEATURES.index("do_zone")]
    model = HistGradientBoostingRegressor(loss="absolute_error", max_iter=600, learning_rate=0.08, max_leaf_nodes=63,
                                          categorical_features=cat, l2_regularization=1.0, early_stopping=True,
                                          validation_fraction=0.1, random_state=0)
    # predict log duration: errors are multiplicative across short and long trips
    model.fit(Xtr[FEATURES], np.log(ytr))
    pred = np.exp(model.predict(Xte[FEATURES]))
    rows = [_report("distance / citywide median speed", yte, b1),
            _report("distance / real median speed for the hour", yte, b2),
            _report("historical median for zone pair and hour", yte, b3),
            _report("gradient boosting (ORCA)", yte, pred)]
    print(f"\nReal trip duration on {len(yte):,} held-out trips (later days than training)")
    print(f"  {'model':<44}{'MAE':>9}{'median':>9}{'p90':>9}{'MAPE':>8}")
    for r in rows:
        print(f"  {r['model']:<44}{r['mae']:8.0f}s{r['median_abs_err']:8.0f}s{r['p90_abs_err']:8.0f}s{r['mape'] * 100:7.1f}%")
    # error by hour for the home page chart
    by_hour = pd.DataFrame({"h": Xte.hour.values, "m": np.abs(pred - yte), "b": np.abs(b3 - yte)}).groupby("h").mean()
    meta = dict(task="eta_real", source="NYC TLC yellow taxi, January 2015, Manhattan weekdays", features=FEATURES,
                target="log trip duration", n_train=len(ytr), n_test=len(yte), train_days=[str(days[0]), str(days[-test_days - 1])],
                test_days=[str(split), str(days[-1])], results=rows,
                mae_by_hour=dict(orca=[round(float(v), 1) for v in by_hour.m], pair_hour=[round(float(v), 1) for v in by_hour.b]),
                note="Positions inside a zone are reconstructed (the TLC publishes zone IDs), so features use zone-level geometry.",
                trained_at=time.strftime("%Y-%m-%d %H:%M"))
    ARTIFACTS.mkdir(exist_ok=True)
    joblib.dump(dict(model=model, meta=meta, centroids=centroids), ARTIFACTS / "eta_real.joblib")
    (ARTIFACTS / "eta_real_metrics.json").write_text(json.dumps(meta, indent=2))
    print(f"\nSaved artifacts/eta_real.joblib ({time.time() - t0:.0f}s)")
    return meta
