"""모델 학습·검증.

이 파일의 존재 이유는 단 하나입니다: **베이스라인 대비 개선률로 성과를 말하기.**

"MAPE 12%"는 좋은 수치인지 알 수 없습니다. 농산물 가격은 임의보행에
가까워서 '전일 가격 그대로' 예측하는 나이브 모델이 의외로 강합니다.
나이브를 못 이기면 모델은 쓸 가치가 없습니다.

그리고 시계열에 무작위 K-Fold를 쓰면 미래로 과거를 예측하는 누수가
발생합니다. walk-forward(확장 윈도우)만 사용합니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

try:  # LightGBM 이 설치되어 있으면 그것을 주력으로 쓴다
    from lightgbm import LGBMRegressor
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False


# ── 지표 ──────────────────────────────────────────────────────────────
def mape(y, p) -> float:
    y, p = np.asarray(y, float), np.asarray(p, float)
    m = y != 0
    return float(np.mean(np.abs((y[m] - p[m]) / y[m])) * 100)


def rmse(y, p) -> float:
    return float(np.sqrt(np.mean((np.asarray(y, float) - np.asarray(p, float)) ** 2)))


def direction_acc(last, y, p) -> float:
    """등락 방향을 맞춘 비율. 실무에서는 절대 오차보다 중요할 수 있다."""
    last, y, p = (np.asarray(v, float) for v in (last, y, p))
    actual = np.sign(y - last)
    pred = np.sign(p - last)
    m = actual != 0
    return float(np.mean(actual[m] == pred[m]) * 100) if m.any() else float("nan")


def spike_f1(last, y, p, threshold=0.15) -> float:
    """급등(임계 초과 상승) 탐지 F1."""
    last, y, p = (np.asarray(v, float) for v in (last, y, p))
    ya = (y / last - 1) > threshold
    pa = (p / last - 1) > threshold
    tp = np.sum(ya & pa)
    fp = np.sum(~ya & pa)
    fn = np.sum(ya & ~pa)
    if tp == 0:
        return 0.0
    prec, rec = tp / (tp + fp), tp / (tp + fn)
    return float(2 * prec * rec / (prec + rec) * 100)


# ── 모델 ──────────────────────────────────────────────────────────────
def make_models() -> dict:
    models = {
        "Ridge": make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "HistGBM": HistGradientBoostingRegressor(
            max_iter=300, learning_rate=0.05, max_depth=6,
            min_samples_leaf=20, l2_regularization=1.0, random_state=0),
    }
    if HAS_LGBM:
        models["LightGBM"] = LGBMRegressor(
            n_estimators=500, learning_rate=0.03, num_leaves=31,
            min_child_samples=20, subsample=0.8, colsample_bytree=0.8,
            reg_lambda=1.0, random_state=0, verbose=-1)
    return models


def walk_forward(X: pd.DataFrame, y: pd.Series, horizon: int = 7,
                 min_train: int = 365, step: int = 14) -> pd.DataFrame:
    """확장 윈도우 walk-forward 검증.

    학습 구간과 검증 구간 사이에 horizon 일의 간격(gap)을 둡니다.
    타깃이 h일 후 값이므로 gap 없이 붙이면 학습 라벨이 검증 구간을
    들여다보게 됩니다. 흔히 놓치는 누수입니다.
    """
    feats = [c for c in X.columns if c != "date"]
    Xv = X[feats].to_numpy(dtype=float)
    Xv = np.nan_to_num(Xv, nan=np.nan)  # HistGBM/LGBM 은 NaN 처리 가능
    yv = y.to_numpy(dtype=float)
    dates = pd.to_datetime(X["date"]).to_numpy()
    last_known = X["lag_1"].to_numpy(dtype=float)

    models = make_models()
    records = []
    n = len(X)
    start = min_train

    while start + step <= n:
        tr_end = start - horizon           # gap 확보
        te_slice = slice(start, min(start + step, n))
        if tr_end < 60:
            start += step
            continue

        Xtr, ytr = Xv[:tr_end], yv[:tr_end]
        Xte, yte = Xv[te_slice], yv[te_slice]
        base = last_known[te_slice]
        ok = ~np.isnan(base)
        if ok.sum() == 0:
            start += step
            continue

        preds = {
            # 베이스라인 1: 전일 가격 유지 (naive)
            "Naive": base,
            # 베이스라인 2: 전년 동일자 (계절성 naive)
            "SeasonalNaive": np.where(
                np.isnan(Xv[te_slice, feats.index("lag_365")]),
                base, Xv[te_slice, feats.index("lag_365")])
            if "lag_365" in feats else base,
        }
        for name, model in models.items():
            mtr = ~np.isnan(ytr)
            if mtr.sum() < 60:
                continue
            if name == "Ridge":  # Ridge 는 NaN 을 못 받으므로 0 대체
                model.fit(np.nan_to_num(Xtr[mtr], nan=0.0), ytr[mtr])
                preds[name] = model.predict(np.nan_to_num(Xte, nan=0.0))
            else:
                model.fit(Xtr[mtr], ytr[mtr])
                preds[name] = model.predict(Xte)

        for name, p in preds.items():
            records.append({
                "model": name,
                "fold_start": pd.Timestamp(dates[te_slice.start]),
                "n": int(ok.sum()),
                "MAPE": mape(yte[ok], p[ok]),
                "RMSE": rmse(yte[ok], p[ok]),
                "방향정확도": direction_acc(base[ok], yte[ok], p[ok]),
                "급등F1": spike_f1(base[ok], yte[ok], p[ok]),
            })
        start += step

    return pd.DataFrame(records)


def summarize(folds: pd.DataFrame) -> pd.DataFrame:
    """폴드별 결과 → 모델별 요약 + 나이브 대비 개선률."""
    if folds.empty:
        return folds
    s = (folds.groupby("model", as_index=False)
         .agg(MAPE=("MAPE", "mean"), RMSE=("RMSE", "mean"),
              방향정확도=("방향정확도", "mean"), 급등F1=("급등F1", "mean"),
              폴드수=("MAPE", "size")))
    naive = s.loc[s["model"] == "Naive", "MAPE"]
    if not naive.empty and naive.iloc[0] > 0:
        s["나이브대비_오차감소율"] = (1 - s["MAPE"] / naive.iloc[0]) * 100
    return s.sort_values("MAPE").reset_index(drop=True)


def ablation(mart: pd.DataFrame, item: str, horizon: int = 7,
             min_train: int = 365, step: int = 14) -> pd.DataFrame:
    """피처 블록을 누적 추가하며 성능 개선폭을 측정한다.

    이 표가 발표자료의 핵심입니다. "뉴스를 넣어서 오차가 몇 % 줄었다"를
    숫자로 말할 수 있게 됩니다.
    """
    from src.features.build import build

    cumulative, rows = [], []
    for block in ("price", "calendar", "weather", "news"):
        cumulative.append(block)
        X, y = build(mart, item, blocks=tuple(cumulative), horizon=horizon)
        if len(X) < min_train + step:
            continue
        folds = walk_forward(X, y, horizon, min_train, step)
        summary = summarize(folds)
        best = summary[~summary["model"].isin(["Naive", "SeasonalNaive"])]
        if best.empty:
            continue
        top = best.iloc[0]
        rows.append({
            "피처블록": " + ".join(cumulative),
            "피처수": len([c for c in X.columns if c != "date"]),
            "최고모델": top["model"],
            "MAPE": round(top["MAPE"], 2),
            "방향정확도": round(top["방향정확도"], 1),
            "급등F1": round(top["급등F1"], 1),
            "나이브대비_오차감소율": round(top.get("나이브대비_오차감소율", np.nan), 1),
        })
    return pd.DataFrame(rows)
