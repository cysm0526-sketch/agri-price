"""요인 기여도 산출.

예측 트랙이 아니라 '설명 트랙'의 출발점입니다. 이 결과가 LLM 요약의
유일한 근거가 되므로, 여기서 나온 상위 요인 외에는 LLM 이 새로운 원인을
말하지 못하게 막습니다.

shap 이 설치되어 있으면 SHAP 값을, 없으면 순열 중요도로 폴백합니다.
폴백은 전역 중요도라서 '특정 날짜의 기여도'는 나오지 않습니다.
발표용으로는 shap 설치를 권합니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    import shap
    HAS_SHAP = True
except ImportError:
    HAS_SHAP = False

# 피처명 → 사람이 읽는 라벨. 심사위원은 'sun_dev30' 을 이해하지 못합니다.
LABELS = {
    "sunshine_ma30": "30일 평균 일조",
    "sun_dev30": "일조 평년 대비 편차",
    "rain_sum30": "30일 누적 강수",
    "rain_sum60": "60일 누적 강수",
    "rain_sum90": "90일 누적 강수",
    "heat_days_30": "30일 내 고온일수",
    "cold_days_30": "30일 내 저온일수",
    "tavg_ma30": "30일 평균기온",
    "trange": "일교차",
    "dry_streak": "무강수 연속일",
    "n_articles": "기사량",
    "art_z30": "기사량 급증도",
    "press_net": "뉴스 순압력",
    "news_기상피해": "기상피해 보도",
    "news_작황부진": "작황부진 보도",
    "news_출하감소": "출하감소 보도",
    "news_공급증가": "공급증가 보도",
    "news_정책개입": "정책개입 보도",
    "yoy": "전년 동기 대비",
    "gap_ma7": "7일 이동평균 이격도",
    "gap_ma30": "30일 이동평균 이격도",
    "ma_7": "7일 이동평균",
    "ma_30": "30일 이동평균",
    "chg_7": "최근 1주 변동률",
    "doy_sin": "계절성",
    "doy_cos": "계절성",
    "is_kimjang": "김장철",
}


def label(name: str) -> str:
    if name in LABELS:
        return LABELS[name]
    if name.startswith("lag_"):
        return f"{name.split('_')[1]}일 전 가격"
    return name


def fit_and_attribute(X: pd.DataFrame, y: pd.Series, target_date=None,
                      top_n: int = 6) -> pd.DataFrame:
    """모델을 학습하고 요인 기여도를 반환.

    Parameters
    ----------
    target_date : 특정 일자의 기여도를 원할 때 지정 (SHAP 필요)
    """
    from src.models.evaluate import make_models

    feats = [c for c in X.columns if c != "date"]
    Xv = X[feats].to_numpy(dtype=float)
    yv = y.to_numpy(dtype=float)
    mask = ~np.isnan(yv)

    models = make_models()
    name = "LightGBM" if "LightGBM" in models else "HistGBM"
    model = models[name]
    model.fit(Xv[mask], yv[mask])

    if HAS_SHAP and target_date is not None:
        idx = X.index[pd.to_datetime(X["date"]) == pd.Timestamp(target_date)]
        if len(idx):
            explainer = shap.TreeExplainer(model)
            values = explainer.shap_values(Xv[idx[0]:idx[0] + 1])
            contrib = np.asarray(values).ravel()
            out = pd.DataFrame({
                "feature": feats,
                "label": [label(f) for f in feats],
                "contribution": contrib,
            })
            out["abs"] = out["contribution"].abs()
            return (out.sort_values("abs", ascending=False)
                    .head(top_n).drop(columns=["abs"]).reset_index(drop=True))

    # 폴백: 순열 중요도 (전역 중요도, 방향 정보 없음)
    from sklearn.inspection import permutation_importance
    r = permutation_importance(model, Xv[mask], yv[mask], n_repeats=5,
                              random_state=0, scoring="neg_mean_absolute_error")
    out = pd.DataFrame({
        "feature": feats,
        "label": [label(f) for f in feats],
        "contribution": r.importances_mean,
    })
    return (out.sort_values("contribution", ascending=False)
            .head(top_n).reset_index(drop=True))
