"""합성 데이터 생성기.

API 키가 없어도 파이프라인 전체와 대시보드를 즉시 실행해 볼 수 있게 합니다.
실제 API 연동 후에는 src/collect/* 의 출력이 이 함수들을 대체합니다.
출력 스키마는 실제 수집 모듈과 동일하게 맞춰 두었으므로 교체 시 하위 코드 수정이 없습니다.

주의: 이 데이터로 산출된 정확도 수치는 아무 의미가 없습니다.
      파이프라인이 도는지 확인하는 용도로만 쓰십시오.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import ITEMS
from src.transform.region_map import STATIONS

# 지도 시각화용 시도 (실제 수집 시 시군구코드로 대체)
SIDO = {
    "11": "서울특별시", "26": "부산광역시", "27": "대구광역시",
    "28": "인천광역시", "29": "광주광역시", "30": "대전광역시",
    "31": "울산광역시", "41": "경기도", "42": "강원특별자치도",
    "43": "충청북도", "44": "충청남도", "45": "전라북도",
    "46": "전라남도", "47": "경상북도", "48": "경상남도",
    "50": "제주특별자치도",
}

_BASE_PRICE = {"배추": 4200, "무": 2100, "양파": 2400, "대파": 3300}


def _seasonal(doy: np.ndarray, item: str) -> np.ndarray:
    """품목별 계절 패턴(진폭·위상만 다르게)."""
    phase = {"배추": 0.0, "무": 0.6, "양파": 2.4, "대파": 1.2}[item]
    amp = {"배추": 0.28, "무": 0.22, "양파": 0.14, "대파": 0.20}[item]
    return 1 + amp * np.sin(2 * np.pi * doy / 365 + phase)


def make_weather(start="2023-01-01", end="2026-07-28", seed=42) -> pd.DataFrame:
    """관측소별 일별 기상. 컬럼은 실제 ASOS 수집 결과와 동일."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, end, freq="D")
    frames = []
    for i, station in enumerate(STATIONS):
        doy = dates.dayofyear.to_numpy()
        # 고랭지는 기온이 낮음
        offset = -6.0 if station in ("대관령", "태백") else 0.0
        tavg = 13 + 11 * np.sin(2 * np.pi * (doy - 100) / 365) + offset
        tavg = tavg + rng.normal(0, 2.5, len(dates))
        rain = rng.gamma(0.6, 6.0, len(dates))
        # 장마철 강수 증가
        rain = rain * np.where((doy > 175) & (doy < 225), 3.0, 1.0)
        sun = np.clip(
            8 + 3 * np.sin(2 * np.pi * (doy - 100) / 365) - rain / 12
            + rng.normal(0, 1.5, len(dates)),
            0, 14,
        )
        frames.append(pd.DataFrame({
            "date": dates,
            "station": station,
            "tavg": tavg.round(1),
            "tmin": (tavg - rng.uniform(3, 8, len(dates))).round(1),
            "tmax": (tavg + rng.uniform(3, 9, len(dates))).round(1),
            "rain": rain.round(1),
            "sunshine": sun.round(1),
        }))
        _ = i
    return pd.concat(frames, ignore_index=True)


def make_prices(weather: pd.DataFrame, start="2023-01-01", end="2026-07-28",
                seed=7) -> pd.DataFrame:
    """지역별·품목별 일별 소매가격.

    기상(일조 부족·고온)이 가격에 시차를 두고 반영되도록 만들어
    피처 엔지니어링과 모델이 실제로 신호를 잡는지 확인할 수 있게 합니다.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, end, freq="D")
    doy = dates.dayofyear.to_numpy()

    # 전국 관측 평균으로 공통 기상 충격 생성
    wx = (weather.groupby("date")[["sunshine", "tavg", "rain"]].mean()
          .reindex(dates).ffill())
    sun30 = wx["sunshine"].rolling(30, min_periods=1).mean()
    sun_shock = -(sun30 - sun30.mean()) / (sun30.std() + 1e-9)  # 일조 부족 → 상승
    heat = wx["tavg"].rolling(30, min_periods=1).apply(
        lambda s: (s > 26).sum(), raw=True)
    heat_shock = (heat - heat.mean()) / (heat.std() + 1e-9)

    rows = []
    for item, meta in ITEMS.items():
        base = _BASE_PRICE[item]
        level = base * _seasonal(doy, item)
        shock = 1 + 0.10 * sun_shock.to_numpy() + 0.06 * heat_shock.to_numpy()
        # 자기상관 있는 잡음 (가격은 임의보행에 가까움)
        noise = np.cumsum(rng.normal(0, 0.012, len(dates)))
        noise = noise - pd.Series(noise).rolling(120, min_periods=1).mean().to_numpy()
        national = level * shock * (1 + noise)

        # 명절 전 수요 급증 (근사)
        for d in ("2023-09-22", "2024-09-10", "2025-10-01", "2026-02-10"):
            idx = (dates >= pd.Timestamp(d) - pd.Timedelta(days=10)) & \
                  (dates <= pd.Timestamp(d))
            national[idx] *= 1.12

        for code, name in SIDO.items():
            # 지역 프리미엄: 산지 인접 지역이 저렴 (전남·제주·강원 할인)
            premium = {"46": 0.88, "50": 0.90, "42": 0.93, "11": 1.10,
                       "28": 1.06, "31": 1.05}.get(code, 1.0)
            regional = national * premium * (1 + rng.normal(0, 0.02, len(dates)))
            rows.append(pd.DataFrame({
                "date": dates,
                "item": item,
                "sgg_code": code,
                "sgg_name": name,
                "cls": "소매",
                "unit": meta["unit"],
                "price_avg": regional.round(0),
                "price_min": (regional * rng.uniform(0.86, 0.94, len(dates))).round(0),
                "price_max": (regional * rng.uniform(1.06, 1.16, len(dates))).round(0),
            }))

    df = pd.concat(rows, ignore_index=True)

    # 실제 데이터처럼 휴장일 결측을 만든다 (일요일 + 임의 공휴일)
    sunday = df["date"].dt.dayofweek == 6
    holidays = pd.to_datetime(
        ["2023-09-28", "2024-09-16", "2025-10-06", "2026-02-17", "2026-01-01"])
    df = df[~sunday & ~df["date"].isin(holidays)].reset_index(drop=True)
    return df


def make_news(prices: pd.DataFrame, seed=11) -> pd.DataFrame:
    """뉴스 메타데이터. 가격 급등 '이후' 기사가 늘어나도록 만든다.

    뉴스는 후행 지표라는 점을 데이터 수준에서 재현합니다.
    """
    rng = np.random.default_rng(seed)
    nat = (prices.groupby(["item", "date"], as_index=False)["price_avg"].mean()
           .sort_values(["item", "date"]))
    nat["wow"] = nat.groupby("item")["price_avg"].pct_change(7)

    cats = ["기상피해", "작황부진", "출하감소", "수요증가", "공급증가", "정책개입"]
    rows = []
    for item, g in nat.groupby("item"):
        for date, wow in zip(g["date"], g["wow"].fillna(0)):
            # 급등 후 기사량 증가 (2~5일 지연)
            lam = 0.7 + max(wow, 0) * 22
            n = rng.poisson(lam)
            for _ in range(int(n)):
                up = wow > 0
                cat = rng.choice(
                    cats,
                    p=[0.25, 0.2, 0.2, 0.1, 0.15, 0.1] if up
                    else [0.08, 0.07, 0.1, 0.1, 0.45, 0.2],
                )
                rows.append({
                    "date": date + pd.Timedelta(days=int(rng.integers(2, 6))),
                    "item": item,
                    "category": cat,
                    "direction": 1 if cat in ("기상피해", "작황부진", "출하감소",
                                              "수요증가") else -1,
                    "intensity": int(rng.integers(1, 4)),
                    "title": f"{item} {cat} 관련 보도",
                    "press": f"{rng.choice(['○○일보', '△△뉴스', '□□경제'])}",
                    "url": "https://example.com/news",
                })
    return pd.DataFrame(rows)


def generate_all() -> dict[str, pd.DataFrame]:
    weather = make_weather()
    prices = make_prices(weather)
    news = make_news(prices)
    return {"weather": weather, "prices": prices, "news": news}
