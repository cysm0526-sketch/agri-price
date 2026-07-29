"""Supabase 적재·조회 계층.

목적은 '로드 시간 단축' 입니다. 그래서 두 가지를 지킵니다.

1. **읽을 행 수를 줄인다.**
   DB 를 붙였다고 빨라지지 않습니다. 대시보드가 원본 전량(수만 행)을 받아
   매번 집계하면 오히려 느려집니다. 그래서 사전 집계 테이블(mart, map_layer)을
   만들고 화면은 그것만 읽습니다. 필요한 품목·기간만 서버에서 필터합니다.

2. **없어도 돌아간다.**
   SUPABASE_URL/KEY 가 비어 있으면 모든 함수가 로컬 parquet 폴백으로
   동작합니다. 발표 직전에 네트워크가 죽어도 화면은 뜹니다.

적재는 upsert 입니다. schema.sql 의 UNIQUE 제약과 짝을 이루어 재실행해도
중복이 쌓이지 않습니다.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from src import config
from src.io_utils import load

# 테이블 → upsert 충돌 판정 키. schema.sql 의 UNIQUE 와 일치해야 합니다.
CONFLICT_KEYS = {
    "prices": "date,item,sgg_code,cls",
    "weather": "date,station",
    "weather_forecast": "announce_time,date,station",
    "news": "url",
    "mart": "date,item",
    "map_layer": "as_of,item,sgg_code",
}

# 각 테이블의 고정 컬럼. 목록 밖 컬럼은 mart 의 경우 metrics(jsonb) 로 접습니다.
MART_FIXED = {"date", "item", "price_avg", "price_min", "price_max",
              "n_region", "origin_label", "tavg", "tmin", "tmax",
              "rain", "sunshine"}

_client = None


def is_enabled() -> bool:
    return config.has_supabase()


def client():
    """Supabase 클라이언트 (지연 생성). 미설정이면 None."""
    global _client
    if not is_enabled():
        return None
    if _client is None:
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError(
                "supabase 패키지가 없습니다: pip install supabase") from exc
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


# ── 직렬화 ────────────────────────────────────────────────────────────
def _clean(value: Any) -> Any:
    """DataFrame 값 → JSON 안전한 값.

    NaN/NaT/Timestamp/numpy 스칼라를 그대로 보내면 supabase-py 가 터지거나
    문자열 'nan' 이 적재되어 나중에 조용한 오염이 됩니다.
    """
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if hasattr(value, "item"):          # numpy 스칼라
        try:
            v = value.item()
            return _clean(v) if isinstance(v, float) else v
        except (ValueError, AttributeError):
            pass
    if isinstance(value, pd.Series):    # 방어
        return None
    return value


def _rows(df: pd.DataFrame, table: str) -> list[dict]:
    if df.empty:
        return []
    out = df.copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce")

    records = []
    for rec in out.to_dict(orient="records"):
        row = {k: _clean(v) for k, v in rec.items()}
        if table == "mart":
            extra = {k: v for k, v in row.items() if k not in MART_FIXED}
            row = {k: v for k, v in row.items() if k in MART_FIXED}
            row["metrics"] = {k: v for k, v in extra.items() if v is not None}
        records.append(row)
    return records


# ── 적재 ──────────────────────────────────────────────────────────────
def push_dataframe(df: pd.DataFrame, table: str, chunk: int = 500) -> int:
    """DataFrame 을 upsert. 적재한 행 수를 반환. 미설정이면 0."""
    cli = client()
    if cli is None:
        print(f"  [skip] Supabase 미설정 — {table} 적재 생략")
        return 0
    if table not in CONFLICT_KEYS:
        raise ValueError(f"알 수 없는 테이블: {table}")

    rows = _rows(df, table)
    if not rows:
        return 0

    done = 0
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        cli.table(table).upsert(
            part, on_conflict=CONFLICT_KEYS[table]).execute()
        done += len(part)
        print(f"  {table}: {done:,}/{len(rows):,}")
    return done


def push_all(prices: pd.DataFrame, weather: pd.DataFrame,
             mart: pd.DataFrame, news: pd.DataFrame | None = None,
             forecast: pd.DataFrame | None = None) -> dict[str, int]:
    """파이프라인 산출물 일괄 적재."""
    result = {
        "prices": push_dataframe(prices, "prices"),
        "weather": push_dataframe(weather, "weather"),
        "mart": push_dataframe(mart, "mart"),
    }
    if news is not None and not news.empty:
        result["news"] = push_dataframe(news, "news")
    if forecast is not None and not forecast.empty:
        result["weather_forecast"] = push_dataframe(forecast,
                                                   "weather_forecast")
    return result


def push_map_layers(prices: pd.DataFrame, as_of: pd.Timestamp | None = None
                    ) -> int:
    """품목별 지도 레이어를 미리 계산해 적재 (화면 로드 단축의 핵심)."""
    from src.transform.merge import map_layer

    as_of = pd.Timestamp(as_of or pd.to_datetime(prices["date"]).max())
    frames = []
    for item in sorted(prices["item"].dropna().unique()):
        layer = map_layer(prices, item, as_of)
        if layer.empty:
            continue
        layer = layer.copy()
        layer["item"] = item
        layer["as_of"] = as_of
        frames.append(layer)
    if not frames:
        return 0
    return push_dataframe(pd.concat(frames, ignore_index=True), "map_layer")


# ── 조회 ──────────────────────────────────────────────────────────────
def _select(table: str, filters: dict | None = None,
            order: str | None = None) -> pd.DataFrame:
    cli = client()
    if cli is None:
        return pd.DataFrame()
    q = cli.table(table).select("*")
    for col, val in (filters or {}).items():
        if isinstance(val, tuple) and len(val) == 2:   # (연산자, 값)
            op, v = val
            q = getattr(q, op)(col, v)
        else:
            q = q.eq(col, val)
    if order:
        q = q.order(order)
    data = q.execute().data or []
    df = pd.DataFrame(data)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


def fetch_mart(item: str | None = None, since: str | None = None
               ) -> pd.DataFrame:
    """마트 조회. Supabase 미설정이면 로컬 parquet 폴백."""
    if not is_enabled():
        df = load(config.MART)
        if item:
            df = df[df["item"] == item]
        if since:
            df = df[df["date"] >= pd.Timestamp(since)]
        return df.reset_index(drop=True)

    filters: dict = {}
    if item:
        filters["item"] = item
    if since:
        filters["date"] = ("gte", since)
    df = _select("mart", filters, order="date")
    # metrics(jsonb) 를 다시 컬럼으로 펼쳐 로컬 폴백과 스키마를 맞춥니다
    if not df.empty and "metrics" in df.columns:
        extra = pd.json_normalize(df["metrics"].apply(
            lambda v: v if isinstance(v, dict) else {}))
        df = pd.concat([df.drop(columns=["metrics"]), extra], axis=1)
    return df


def fetch_prices(item: str | None = None, since: str | None = None
                 ) -> pd.DataFrame:
    if not is_enabled():
        df = load(config.STAGING / "prices")
        if item:
            df = df[df["item"] == item]
        if since:
            df = df[df["date"] >= pd.Timestamp(since)]
        return df.reset_index(drop=True)

    filters: dict = {}
    if item:
        filters["item"] = item
    if since:
        filters["date"] = ("gte", since)
    return _select("prices", filters, order="date")


def fetch_map_layer(item: str, as_of: str | None = None) -> pd.DataFrame:
    """사전 집계된 지도 레이어 조회. 없으면 빈 DataFrame → 호출측이 재계산."""
    if not is_enabled():
        return pd.DataFrame()
    filters: dict = {"item": item}
    if as_of:
        filters["as_of"] = as_of
    df = _select("map_layer", filters)
    if df.empty:
        return df
    if not as_of and "as_of" in df.columns:
        latest = df["as_of"].max()
        df = df[df["as_of"] == latest]
    return df.sort_values("price_avg", ascending=False).reset_index(drop=True)
