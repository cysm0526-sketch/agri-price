"""정제 계층.

원칙 세 가지를 코드로 강제합니다.

1. 경매 휴장일 결측은 0으로 채우지 않는다. 거래가 없었던 것이지 가격이
   0인 것이 아니다. 0으로 채우면 모델이 붕괴한다.
2. 이상치를 자동 제거하지 않는다. 급등락 자체가 이 프로젝트의 분석
   대상이므로, 제거 대신 검토 플래그를 붙인다.
3. 보간한 값은 반드시 플래그를 남긴다. 나중에 "이 수치 실측인가?"라는
   질문에 답할 수 있어야 한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PRICE_COLS = ["price_avg", "price_min", "price_max"]


def clean_prices(df: pd.DataFrame, interpolate_limit: int = 3) -> pd.DataFrame:
    """가격 데이터 정제.

    Returns
    -------
    DataFrame
        원본 컬럼 + is_imputed(보간 여부) + outlier_flag(검토 대상)
    """
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values(["item", "sgg_code", "date"]).reset_index(drop=True)

    # 0 이하 가격은 결측 처리 (실제 API에서 '-' 나 0 이 섞여 들어옵니다)
    for c in PRICE_COLS:
        out[c] = pd.to_numeric(out[c], errors="coerce")
        out.loc[out[c] <= 0, c] = np.nan

    # 달력 기준으로 재색인하여 휴장일을 명시적 결측으로 드러낸다
    keys = ["item", "sgg_code"]
    frames = []
    for key, g in out.groupby(keys, sort=False):
        full = pd.date_range(g["date"].min(), g["date"].max(), freq="D")
        g = g.set_index("date").reindex(full)
        g.index.name = "date"
        for i, col in enumerate(keys):
            g[col] = key[i]
        frames.append(g.reset_index())
    out = pd.concat(frames, ignore_index=True)

    # 정적 컬럼 복원
    for col in ("sgg_name", "cls", "unit"):
        if col in out.columns:
            out[col] = out.groupby(keys)[col].transform(
                lambda s: s.ffill().bfill())

    # 보간: 짧은 공백만 채우고 플래그를 남긴다
    out["is_imputed"] = out["price_avg"].isna()
    for c in PRICE_COLS:
        out[c] = out.groupby(keys)[c].transform(
            lambda s: s.interpolate(limit=interpolate_limit, limit_area="inside"))
    # 여전히 결측인 행은 보간하지 않은 것으로 정정
    out.loc[out["price_avg"].isna(), "is_imputed"] = False

    # 이상치 태깅: 전일 대비 변동률의 MAD 기준 (제거하지 않음)
    out["chg"] = out.groupby(keys)["price_avg"].pct_change(fill_method=None)
    med = out.groupby(keys)["chg"].transform("median")
    mad = out.groupby(keys)["chg"].transform(
        lambda s: (s - s.median()).abs().median())
    robust_z = (out["chg"] - med) / (1.4826 * mad.replace(0, np.nan))
    out["outlier_flag"] = robust_z.abs() > 5

    return out.drop(columns=["chg"])


def clean_weather(df: pd.DataFrame) -> pd.DataFrame:
    """기상 데이터 정제. 결측은 관측소별 선형보간(최대 3일)."""
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values(["station", "date"]).reset_index(drop=True)

    cols = ["tavg", "tmin", "tmax", "rain", "sunshine"]
    for c in cols:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    # 강수는 결측을 0으로 두는 것이 관례 (미관측 = 무강수로 기록)
    out["rain"] = out["rain"].fillna(0.0)
    for c in ["tavg", "tmin", "tmax", "sunshine"]:
        out[c] = out.groupby("station")[c].transform(
            lambda s: s.interpolate(limit=3, limit_area="inside"))
    return out


def quality_report(prices: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """데이터 품질 리포트. 발표자료에 그대로 넣을 수 있습니다."""
    rows = []
    for item, g in prices.groupby("item"):
        rows.append({
            "대상": item,
            "행수": len(g),
            "기간": f"{g['date'].min():%Y-%m-%d} ~ {g['date'].max():%Y-%m-%d}",
            "가격결측률(%)": round(g["price_avg"].isna().mean() * 100, 2),
            "보간비율(%)": round(g["is_imputed"].mean() * 100, 2),
            "이상치태깅": int(g["outlier_flag"].sum()),
        })
    rows.append({
        "대상": "기상(전체 관측소)",
        "행수": len(weather),
        "기간": f"{weather['date'].min():%Y-%m-%d} ~ {weather['date'].max():%Y-%m-%d}",
        "가격결측률(%)": None,
        "보간비율(%)": round(weather["sunshine"].isna().mean() * 100, 2),
        "이상치태깅": None,
    })
    return pd.DataFrame(rows)
