"""결합 계층 — 소비지 가격과 산지 기상을 분리해서 붙인다.

설계상 가장 중요한 지점입니다.

가격은 '조사 지역'(소비지) 속성이고, 기상은 '주산지' 속성입니다.
대전 배추값이 오른 원인은 대전 날씨가 아니라 해남 날씨입니다.
따라서 두 지역 개념을 절대 같은 키로 조인하면 안 됩니다.

- 예측/설명 모델링   → 품목 전국 평균가격 + 주산지 기상  (지역 무관)
- 지도 화면          → 지역별 가격                       (기상 무관)
두 산출물을 별도 테이블로 만듭니다.
"""
from __future__ import annotations

import pandas as pd

from src.transform.region_map import weighted_weather


def national_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """품목별 전국 일별 가격 (모델링 입력).

    KAMIS 는 자체 산출한 전국 평균을 sgg_code="00" 행으로 함께 줍니다.
    그 행이 있으면 그것을 쓰고(공사 공표치와 일치), 없으면(mock 데이터 등)
    지역 평균을 직접 계산합니다. 둘을 섞으면 전국 평균이 이중 계산됩니다.
    """
    has_national = (prices["sgg_code"].astype(str) == "00").any()
    src = prices[prices["sgg_code"].astype(str) == "00"] if has_national \
        else prices

    agg_spec = {
        "price_avg": ("price_avg", "mean"),
        "price_min": ("price_min", "mean"),
        "price_max": ("price_max", "mean"),
        "n_region": ("price_avg", "count"),
    }
    if "is_imputed" in src.columns:
        agg_spec["imputed_ratio"] = ("is_imputed", "mean")

    agg = src.groupby(["item", "date"], as_index=False).agg(**agg_spec)
    if has_national:
        # 전국 행은 지역 수를 세는 의미가 없으므로 실제 시도 수로 대체
        counts = (prices[prices["sgg_code"].astype(str) != "00"]
                  .groupby(["item", "date"], as_index=False)
                  .agg(n_region=("price_avg", "count")))
        agg = (agg.drop(columns=["n_region"])
               .merge(counts, on=["item", "date"], how="left"))
    return agg.sort_values(["item", "date"]).reset_index(drop=True)


def news_daily(news: pd.DataFrame) -> pd.DataFrame:
    """뉴스 → 일별 정량 지표.

    단순 긍부정 감성보다 이슈 카테고리별 압력 지표가 해석 가능합니다.
    """
    if news.empty:
        return pd.DataFrame(columns=["item", "date", "n_articles",
                                     "press_up", "press_down", "press_net"])
    n = news.copy()
    n["date"] = pd.to_datetime(n["date"])
    n["signed"] = n["direction"] * n["intensity"]
    g = n.groupby(["item", "date"], as_index=False).agg(
        n_articles=("title", "count"),
        press_up=("signed", lambda s: s[s > 0].sum()),
        press_down=("signed", lambda s: -s[s < 0].sum()),
    )
    g["press_net"] = g["press_up"] - g["press_down"]

    # 카테고리별 기사 수를 와이드로 펼친다
    cat = (n.pivot_table(index=["item", "date"], columns="category",
                         values="title", aggfunc="count")
           .add_prefix("news_").fillna(0).reset_index())
    return g.merge(cat, on=["item", "date"], how="left").fillna(0)


def build_mart(prices: pd.DataFrame, weather: pd.DataFrame,
               news: pd.DataFrame) -> pd.DataFrame:
    """모델링용 통합 마트. 일자 × 품목 1행."""
    nat = national_prices(prices)
    nd = news_daily(news)

    frames = []
    for item, g in nat.groupby("item"):
        wx = weighted_weather(weather, item)
        m = g.merge(wx, on="date", how="left")
        if not nd.empty:
            m = m.merge(nd[nd["item"] == item].drop(columns=["item"]),
                        on="date", how="left")
        news_cols = [c for c in m.columns
                     if c.startswith("news_") or c.startswith("press_")
                     or c == "n_articles"]
        m[news_cols] = m[news_cols].fillna(0)
        frames.append(m)

    mart = pd.concat(frames, ignore_index=True)
    return mart.sort_values(["item", "date"]).reset_index(drop=True)


def item_movers(prices: pd.DataFrame) -> pd.DataFrame:
    """품목별 전국 평균가의 최신 등락률 — 메인 화면 상승/하락 TOP5 용.

    '오늘'이 아니라 '가장 최근 공개된 조사일'과 그 직전 조사일을 비교한다.
    KAMIS·ASOS 모두 D-1~D-2 공개 지연이 있어 리터럴 오늘 데이터는 없다.
    """
    nat = national_prices(prices)
    rows = []
    for item, g in nat.groupby("item"):
        g = g.sort_values("date").dropna(subset=["price_avg"])
        if len(g) < 2:
            continue
        latest, prev = g.iloc[-1], g.iloc[-2]
        if not prev["price_avg"]:
            continue
        rows.append({
            "item": item,
            "price": latest["price_avg"],
            "date": latest["date"],
            "prev_date": prev["date"],
            "change_pct": (latest["price_avg"] / prev["price_avg"] - 1) * 100,
        })
    return pd.DataFrame(rows).sort_values("change_pct", ascending=False)


def map_layer(prices: pd.DataFrame, item: str,
              as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """지도 화면용 지역별 최근 가격 + 전주 대비 변동률."""
    p = prices[prices["item"] == item].copy()
    # sgg_code="00" 은 KAMIS 가 준 전국 평균이므로 지도에서 제외합니다.
    # 남기면 '전국'이 하나의 지역처럼 지도에 찍히고 vs_national 도 왜곡됩니다.
    p = p[p["sgg_code"].astype(str) != "00"]
    if as_of is not None:
        p = p[p["date"] <= as_of]
    p = p.dropna(subset=["price_avg"])
    if p.empty:
        return pd.DataFrame()

    latest_date = p["date"].max()
    prev_date = latest_date - pd.Timedelta(days=7)

    latest = (p[p["date"] == latest_date]
              .loc[:, ["sgg_code", "sgg_name", "price_avg", "price_min",
                       "price_max", "unit", "date"]]
              .rename(columns={"date": "survey_date"}))

    # 전주 값은 정확히 7일 전이 결측일 수 있으므로 직전 관측을 쓴다
    prev = (p[p["date"] <= prev_date]
            .sort_values("date").groupby("sgg_code", as_index=False).last()
            .loc[:, ["sgg_code", "price_avg"]]
            .rename(columns={"price_avg": "price_prev"}))

    out = latest.merge(prev, on="sgg_code", how="left")
    out["wow_rate"] = (out["price_avg"] / out["price_prev"] - 1) * 100
    nat_avg = out["price_avg"].mean()
    out["vs_national"] = (out["price_avg"] / nat_avg - 1) * 100
    return out.sort_values("price_avg", ascending=False).reset_index(drop=True)
