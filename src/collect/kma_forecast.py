"""기상청 단기예보 통보문 수집 — 주산지 예보(미래 외생변수).

왜 예보가 필요한가
  ASOS 관측은 '이미 일어난' 기상입니다. 7일 후 가격을 예측하려면 예측 시점
  이후의 기상을 알아야 하고, 그건 예보밖에 없습니다. 관측만 쓰면 모델은
  "지난 30일이 더웠다" 까지만 알고 "앞으로 3일 더 덥다" 는 모르는 상태가 됩니다.

명세 (단기예보 통보문 조회서비스 활용가이드)
  URL     http://apis.data.go.kr/1360000/VilageFcstMsgService/getLandFcst
  필수    serviceKey, regId(예보구역코드)
  갱신    05 / 11 / 17시 (일 3회). 최근 24시간 내 최신 발표만 조회됩니다
  필드    announceTime(발표시각) numEf(예보순번) ta(예상기온)
          rnSt(강수확률) wf(날씨) wfCd wd1/wd2(풍향) wsIt(풍속강도)

⚠️ 과거 예보는 조회할 수 없습니다 (최근 발표만 제공).
   따라서 예보를 모델 피처로 쓰려면 매일 호출해 누적 적재해야 합니다.
   이 모듈은 '스냅샷 1회분'을 반환하고, 누적은 DB 계층(src/db)이 담당합니다.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from src import config
from src.transform.region_map import FORECAST_ZONES

# numEf → (며칠 뒤, 최저/최고). 응답을 실측해서 도출한 규칙입니다.
#   numEf=0 은 발표 당일 낮, 이후 (아침 최저, 낮 최고) 가 하루씩 교대합니다.
#   ta 값이 30/20/30/21/30/21/30 처럼 번갈아 나오는 것이 근거입니다.
# [확인 필요] 05시/17시 발표분도 같은 규칙인지 한 번 대조하십시오.
def _numef_to_slot(num_ef: int) -> tuple[int, str]:
    return (num_ef + 1) // 2, "tmax" if num_ef % 2 == 0 else "tmin"


def _call(reg_id: str, force: bool = False) -> dict:
    """육상예보 호출 + 발표시각 단위 캐싱."""
    stamp = datetime.now().strftime("%Y%m%d_%H")
    cache = Path(config.RAW) / f"fcst_{reg_id}_{stamp}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))

    if not config.DATA_GO_KR_KEY:
        raise RuntimeError(
            "DATA_GO_KR_KEY 가 없습니다. .env 에 공공데이터포털 'Decoding' 키를 넣으십시오.")

    params = {
        "serviceKey": config.DATA_GO_KR_KEY,
        "dataType": "JSON",
        "numOfRows": "50",
        "pageNo": "1",
        "regId": reg_id,
    }
    for attempt in range(4):
        try:
            r = requests.get(config.KMA_LAND_FCST, params=params, timeout=30)
            r.raise_for_status()
            head = r.text.lstrip()[:300]
            if not head.startswith("{"):
                raise RuntimeError(f"JSON 이 아닌 응답: {head[:150]}")
            data = r.json()
            header = data.get("response", {}).get("header", {})
            code = str(header.get("resultCode", "")).zfill(2)
            if code not in ("00", "0"):
                raise RuntimeError(
                    f"API 오류 [{code}] {header.get('resultMsg', '')}")
            cache.write_text(json.dumps(data, ensure_ascii=False),
                             encoding="utf-8")
            return data
        except Exception as exc:
            wait = 2 ** attempt
            print(f"  재시도 {attempt + 1}/4 ({exc}) — {wait}초")
            time.sleep(wait)
    raise RuntimeError(f"단기예보 호출 실패: regId={reg_id}")


def _records(data: dict) -> list[dict]:
    items = data.get("response", {}).get("body", {}).get("items", {})
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return [r for r in (items or []) if isinstance(r, dict)]


def inspect(station: str = "대관령") -> dict:
    """★ 응답과 numEf 해석 규칙을 눈으로 확인하십시오.

        python -c "from src.collect.kma_forecast import inspect; inspect('해남')"
    """
    reg = FORECAST_ZONES[station]
    data = _call(reg, force=True)
    recs = _records(data)
    print(f"── {station} (regId={reg}) 레코드 {len(recs)}건 ──")
    if not recs:
        print("비어 있습니다. 예보구역코드를 확인하십시오.")
        return data
    print("키:", list(recs[0].keys()))
    print(f"\n{'numEf':>5} {'해석':>12} {'ta':>5} {'rnSt':>5}  wf")
    for r in sorted(recs, key=lambda x: int(x["numEf"])):
        off, kind = _numef_to_slot(int(r["numEf"]))
        label = f"D+{off} {'최고' if kind == 'tmax' else '최저'}"
        print(f"{r['numEf']:>5} {label:>12} {str(r.get('ta')):>5} "
              f"{str(r.get('rnSt')):>5}  {r.get('wf')}")
    print("\n→ ta 가 최고/최저로 번갈아 나오는지 확인하십시오.")
    return data


def parse_land_fcst(data: dict, station: str) -> pd.DataFrame:
    """육상예보 응답 → 관측소·일자별 1행 (tmin/tmax/강수확률)."""
    recs = _records(data)
    if not recs:
        return pd.DataFrame()

    announce = str(recs[0].get("announceTime", ""))
    base = (datetime.strptime(announce[:8], "%Y%m%d").date()
            if len(announce) >= 8 else date.today())

    rows: dict[date, dict] = {}
    for r in recs:
        try:
            num_ef = int(r["numEf"])
        except (KeyError, ValueError, TypeError):
            continue
        offset, kind = _numef_to_slot(num_ef)
        d = base + timedelta(days=offset)
        row = rows.setdefault(d, {"date": d, "station": station,
                                  "announce_time": announce})
        row[kind] = pd.to_numeric(r.get("ta"), errors="coerce")
        # 강수확률은 아침/낮 중 큰 값을 그날의 대표값으로 씁니다.
        rn = pd.to_numeric(r.get("rnSt"), errors="coerce")
        if pd.notna(rn):
            row["rain_prob"] = max(row.get("rain_prob", 0) or 0, rn)
        row.setdefault("wf", r.get("wf"))

    df = pd.DataFrame(rows.values())
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    if {"tmin", "tmax"} <= set(df.columns):
        df["tavg"] = df[["tmin", "tmax"]].mean(axis=1)
    cols = ["date", "station", "announce_time", "tmin", "tmax", "tavg",
            "rain_prob", "wf"]
    return df[[c for c in cols if c in df.columns]].sort_values("date")


def fetch_forecast(stations: list[str] | None = None) -> pd.DataFrame:
    """주산지 관측소 전체의 최신 예보 스냅샷을 수집."""
    names = stations or list(FORECAST_ZONES)
    frames = []
    for name in names:
        reg = FORECAST_ZONES.get(name)
        if not reg:
            print(f"  건너뜀: {name} — FORECAST_ZONES 에 예보구역코드가 없습니다")
            continue
        print(f"  {name}({reg})")
        try:
            part = parse_land_fcst(_call(reg), name)
            if not part.empty:
                frames.append(part)
        except Exception as exc:
            print(f"    실패: {exc}")
        time.sleep(0.2)

    if not frames:
        raise RuntimeError("예보 수집 결과가 비어 있습니다. inspect() 로 확인하십시오.")
    return pd.concat(frames, ignore_index=True)


def weighted_forecast(fcst: pd.DataFrame, item: str) -> pd.DataFrame:
    """예보를 품목별 주산지 가중평균으로 축약 (관측과 동일한 방식).

    weighted_weather() 와 같은 가중치 테이블을 쓰므로, 관측 시계열 뒤에
    그대로 이어 붙일 수 있습니다.
    """
    from src.transform.region_map import REGION_WEIGHTS

    if fcst.empty:
        return fcst
    f = fcst.copy()
    f["date"] = pd.to_datetime(f["date"])
    f["month"] = f["date"].dt.month

    rows = [{"month": m, "station": s, "weight": w}
            for m, mapping in REGION_WEIGHTS[item].items()
            for s, w in mapping.items()]
    merged = f.merge(pd.DataFrame(rows), on=["month", "station"], how="inner")
    if merged.empty:
        return pd.DataFrame()

    metrics = [c for c in ("tmin", "tmax", "tavg", "rain_prob")
               if c in merged.columns]

    # 가중치를 지표별로 따로 합산합니다. sum() 이 NaN 을 0 으로 건너뛰므로
    # 결측 관측소의 가중치를 분모에 남기면 값이 0 쪽으로 끌려갑니다.
    # (예보 D+0 은 최저기온이 없어 한여름 tmin 이 0℃ 로 나오던 문제)
    wcols = {}
    for c in metrics:
        wcol = f"_w_{c}"
        merged[wcol] = merged["weight"].where(merged[c].notna())
        merged[c] = merged[c] * merged["weight"]
        wcols[c] = wcol

    g = merged.groupby("date", as_index=False).agg(
        {**{c: "sum" for c in metrics},
         **{w: "sum" for w in wcols.values()}})
    for c in metrics:
        g[c] = g[c] / g[wcols[c]].replace(0, pd.NA)
    return g.drop(columns=list(wcols.values()))
