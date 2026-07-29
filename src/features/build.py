"""피처 엔지니어링 — 블록 단위로 분리한 이유

피처를 한꺼번에 다 넣으면 "뉴스가 실제로 도움이 됐는가?"에 답할 수 없습니다.
가격 → 달력 → 기상 → 뉴스 순으로 블록을 켜가며 성능 개선폭을 기록하면,
그 기록표가 발표자료의 핵심 슬라이드가 됩니다.

절대 규칙: 시점 t의 피처는 t 시점에 알 수 있는 정보만 사용합니다.
모든 lag/rolling은 shift(1) 이후에 계산합니다. 이걸 어기면 데이터 누수입니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BLOCKS = ("price", "calendar", "weather", "news")


def _price_block(g: pd.DataFrame) -> pd.DataFrame:
    p = g["price_avg"]
    prev = p.shift(1)  # t 시점에는 전일 가격까지만 알 수 있다
    f = pd.DataFrame(index=g.index)

    for lag in (1, 2, 3, 7, 14, 30):
        f[f"lag_{lag}"] = p.shift(lag)
    for win in (3, 7, 14, 30, 60):
        f[f"ma_{win}"] = prev.rolling(win, min_periods=2).mean()
    f["std_7"] = prev.rolling(7, min_periods=3).std()
    f["std_30"] = prev.rolling(30, min_periods=5).std()
    f["chg_1"] = prev.pct_change(1, fill_method=None)
    f["chg_7"] = prev.pct_change(7, fill_method=None)
    f["chg_30"] = prev.pct_change(30, fill_method=None)
    f["gap_ma7"] = prev / f["ma_7"] - 1        # 이격도
    f["gap_ma30"] = prev / f["ma_30"] - 1
    f["pos_30"] = ((prev - prev.rolling(30, min_periods=5).min())
                   / (prev.rolling(30, min_periods=5).max()
                      - prev.rolling(30, min_periods=5).min() + 1e-9))
    f["lag_365"] = p.shift(365)               # 전년 동일자
    f["yoy"] = prev / p.shift(365) - 1
    f["spread"] = (g["price_max"] - g["price_min"]).shift(1)
    up = (prev.diff() > 0).astype(int)
    f["run_up"] = up.groupby((up != up.shift()).cumsum()).cumcount() + 1
    f["run_up"] = f["run_up"] * up
    return f


def _calendar_block(g: pd.DataFrame) -> pd.DataFrame:
    d = g["date"]
    f = pd.DataFrame(index=g.index)
    f["dow"] = d.dt.dayofweek
    f["month"] = d.dt.month
    f["doy"] = d.dt.dayofyear
    f["week"] = d.dt.isocalendar().week.astype(int).to_numpy()
    f["quarter"] = d.dt.quarter
    # 계절성을 순환 인코딩 (12월과 1월이 멀어지지 않게)
    f["doy_sin"] = np.sin(2 * np.pi * f["doy"] / 365)
    f["doy_cos"] = np.cos(2 * np.pi * f["doy"] / 365)
    f["is_kimjang"] = d.dt.month.isin([10, 11, 12]).astype(int)
    # [확인 필요] 실제 구현 시 holidays 패키지로 설·추석 정확한 날짜를 쓰십시오
    f["is_month_end"] = (d.dt.day >= 25).astype(int)
    return f


def _weather_block(g: pd.DataFrame) -> pd.DataFrame:
    """주산지 가중 기상. 생육 시차를 누적 변수로 반영.

    ASOS 일자료(일 강수량·일조시간 등)는 하루가 끝나야 확정되는 값이므로,
    price/news 블록과 동일하게 shift(1) 한 값만 피처로 사용합니다.
    당일 값을 그대로 쓰면 아직 끝나지 않은 하루의 기상을 미리 아는 셈이 되어
    price/news 블록에서 강제한 것과 같은 종류의 데이터 누수가 됩니다.
    """
    f = pd.DataFrame(index=g.index)
    shifted = {c: g[c].shift(1) for c in ("tavg", "tmin", "tmax", "rain", "sunshine")
               if c in g}
    for col, s in shifted.items():
        f[col] = s
        for win in (7, 30, 60, 90):
            if col == "rain":
                f[f"{col}_sum{win}"] = s.rolling(win, min_periods=3).sum()
            else:
                f[f"{col}_ma{win}"] = s.rolling(win, min_periods=3).mean()

    if "tavg" in shifted:
        # 임계값 초과일수 — 선형 기온보다 작황을 잘 설명한다
        f["heat_days_30"] = (shifted["tavg"] > 26).rolling(30, min_periods=3).sum()
        f["cold_days_30"] = (shifted["tavg"] < 0).rolling(30, min_periods=3).sum()
        f["trange"] = shifted["tmax"] - shifted["tmin"]
    if "sunshine" in shifted:
        # 평년(3년 이동) 대비 편차 — 절대값보다 편차가 강한 신호
        base = shifted["sunshine"].rolling(365 * 2, min_periods=90).mean()
        f["sun_dev30"] = f.get("sunshine_ma30") - base
    if "rain" in shifted:
        dry = (shifted["rain"] < 0.1).astype(int)
        f["dry_streak"] = dry.groupby((dry != dry.shift()).cumsum()).cumcount() + 1
        f["dry_streak"] = f["dry_streak"] * dry
    return f


def _news_block(g: pd.DataFrame) -> pd.DataFrame:
    """뉴스는 후행 지표다.

    그래서 t 시점 기사만 쓰는 것으로는 부족하고, 반드시 shift 해서
    '과거 기사'만 들어가게 합니다. 예측 기여도가 낮게 나오는 것이
    정상이며, 그 사실 자체가 발표에서 말할 만한 발견입니다.
    """
    f = pd.DataFrame(index=g.index)
    cols = [c for c in g.columns
            if c.startswith("news_") or c.startswith("press_")
            or c == "n_articles"]
    for c in cols:
        s = g[c].shift(1)
        f[c] = s
        f[f"{c}_sum7"] = s.rolling(7, min_periods=1).sum()
    if "n_articles" in g:
        s = g["n_articles"].shift(1)
        mu = s.rolling(30, min_periods=7).mean()
        sd = s.rolling(30, min_periods=7).std()
        f["art_z30"] = (s - mu) / (sd + 1e-9)
        f["art_surge"] = (f["art_z30"] > 2).astype(int)
    return f


_BUILDERS = {
    "price": _price_block,
    "calendar": _calendar_block,
    "weather": _weather_block,
    "news": _news_block,
}


def build(mart: pd.DataFrame, item: str, blocks=BLOCKS,
          horizon: int = 7) -> tuple[pd.DataFrame, pd.Series]:
    """피처 행렬과 타깃을 만든다.

    타깃은 h일 후 가격. 즉 오늘 정보로 h일 뒤를 맞추는 문제로 정의합니다.

    Returns
    -------
    (X, y) : 결측 타깃 행은 제거된 상태
    """
    g = (mart[mart["item"] == item]
         .sort_values("date").reset_index(drop=True))

    parts = [_BUILDERS[b](g) for b in blocks if b in _BUILDERS]
    X = pd.concat(parts, axis=1)
    X.insert(0, "date", g["date"].to_numpy())

    y = g["price_avg"].shift(-horizon)  # h일 후 가격
    y.name = f"target_h{horizon}"

    keep = y.notna() & X.drop(columns=["date"]).notna().sum(axis=1).gt(0)
    return X.loc[keep].reset_index(drop=True), y.loc[keep].reset_index(drop=True)
