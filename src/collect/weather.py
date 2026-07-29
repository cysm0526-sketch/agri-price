"""기상청 ASOS 일자료 수집 — 공공데이터포털(data.go.kr) 버전.

주산지 관측소만 수집합니다. 전국 모든 관측소를 받으면 데이터는 커지고
신호는 흐려집니다.

왜 apihub 가 아니라 data.go.kr 인가
  이전 구현은 기상청 API 허브(apihub.kma.go.kr)의 CSV 응답을 컬럼 '위치'로
  파싱했습니다. 컬럼 순서가 바뀌면 조용히 엉뚱한 값을 읽는 구조였습니다.
  data.go.kr 의 AsosDalyInfoService 는 JSON 으로 필드명을 주므로 위치 의존이
  사라지고, 단기예보 통보문과 인증키도 하나로 통일됩니다.

명세 (지상(종관,ASOS) 일자료 조회서비스 활용가이드)
  URL     http://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList
  필수    serviceKey, dataCd=ASOS, dateCd=DAY, startDt, endDt, stnIds
  제공    전일(D-1)까지. 단, 전일 자료는 조회시간 11시 이후에만 조회 가능
  필드    tm(일시) stnId stnNm avgTa minTa maxTa sumRn sumSsHr avgRhm avgWs

응답 저장 위치: data/raw/asos_*.json
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from src import config
from src.transform.region_map import STATIONS

# 명세서 응답 필드 → 프로젝트 표준 스키마.
# 위치가 아니라 이름으로 매핑하므로 컬럼 순서 변경에 영향을 받지 않습니다.
FIELD_MAP = {
    "tm": "date",
    "avgTa": "tavg",
    "minTa": "tmin",
    "maxTa": "tmax",
    "sumRn": "rain",
    "sumSsHr": "sunshine",
    "avgRhm": "humidity",
    "avgWs": "wind",
}

# API 가 실제로 제공하는 최신 일자를 계산.
# 전일 자료는 11시 이후 공개되므로 그 전에는 D-2 까지만 요청합니다.
def latest_available() -> date:
    now = datetime.now()
    return now.date() - timedelta(days=1 if now.hour >= 11 else 2)


def _call(stn_id: int, start: date, end: date, force: bool = False) -> dict:
    """ASOS 일자료 호출 + 원본 캐싱. 이미 받은 구간은 재호출하지 않습니다."""
    cache = Path(config.RAW) / f"asos_{stn_id}_{start:%Y%m%d}_{end:%Y%m%d}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))

    if not config.DATA_GO_KR_KEY:
        raise RuntimeError(
            "DATA_GO_KR_KEY 가 없습니다. .env 에 공공데이터포털 'Decoding' 키를 넣으십시오.")

    params = {
        "serviceKey": config.DATA_GO_KR_KEY,   # requests 가 인코딩 → Decoding 키를 쓸 것
        "dataType": "JSON",
        "dataCd": "ASOS",
        "dateCd": "DAY",
        "startDt": f"{start:%Y%m%d}",
        "endDt": f"{end:%Y%m%d}",
        "stnIds": str(stn_id),
        "numOfRows": "999",
        "pageNo": "1",
    }
    for attempt in range(4):
        try:
            r = requests.get(config.KMA_ASOS, params=params, timeout=30)
            r.raise_for_status()
            data = _check(r)
            cache.write_text(json.dumps(data, ensure_ascii=False),
                             encoding="utf-8")
            return data
        except Exception as exc:
            wait = 2 ** attempt
            print(f"  재시도 {attempt + 1}/4 ({exc}) — {wait}초")
            time.sleep(wait)
    raise RuntimeError(f"ASOS 호출 실패: stn={stn_id} {start}~{end}")


def _check(resp: requests.Response) -> dict:
    """공공데이터포털은 오류도 HTTP 200 으로 돌려주므로 본문을 직접 검사합니다.

    인증 실패 시엔 JSON 이 아니라 XML 이나 HTML 이 오기도 합니다. 그대로
    넘기면 뒤에서 '데이터 없음'으로 조용히 묻히므로 여기서 잡습니다.
    """
    head = resp.text.lstrip()[:400]
    if not head.startswith("{"):
        raise RuntimeError(
            "JSON 이 아닌 응답입니다. 인증키 또는 서비스 신청 상태를 확인하십시오.\n"
            f"    응답 앞부분: {head[:200]}")
    data = resp.json()
    header = data.get("response", {}).get("header", {})
    code = str(header.get("resultCode", "")).zfill(2)
    if code not in ("00", "0"):
        msg = header.get("resultMsg", "")
        hint = ""
        if "SERVICE_KEY" in str(msg).upper():
            hint = ("\n    → .env 의 DATA_GO_KR_KEY 가 'Encoding' 키일 가능성이 큽니다. "
                    "'Decoding' 키로 바꾸십시오.")
        elif "SERVICE" in str(msg).upper() and "ACCESS" in str(msg).upper():
            hint = "\n    → 공공데이터포털에서 해당 서비스 활용신청이 승인되었는지 확인하십시오."
        raise RuntimeError(f"API 오류 [{code}] {msg}{hint}")
    return data


def _records(data: dict) -> list[dict]:
    items = (data.get("response", {}).get("body", {}).get("items", {}))
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):      # 1건이면 리스트가 아닌 dict 로 옵니다
        items = [items]
    return [r for r in (items or []) if isinstance(r, dict)]


def inspect(station: str = "대관령", days: int = 5) -> dict:
    """★ 먼저 실행하여 실제 응답 필드를 확인하십시오.

        python -c "from src.collect.weather import inspect; inspect('대관령')"
    """
    end = latest_available()
    data = _call(STATIONS[station], end - timedelta(days=days), end, force=True)
    recs = _records(data)
    print(f"── {station}({STATIONS[station]}) {end - timedelta(days=days)} ~ {end} ──")
    print(f"레코드 {len(recs)}건")
    if recs:
        print("\n── 응답 키 목록 ──")
        print(list(recs[0].keys()))
        print("\n── 첫 레코드 ──")
        print(json.dumps(recs[0], ensure_ascii=False, indent=2))
        missing = [k for k in FIELD_MAP if k not in recs[0]]
        if missing:
            print(f"\n⚠️ FIELD_MAP 에 있으나 응답에 없는 키: {missing}")
        else:
            print("\n✅ FIELD_MAP 의 모든 키가 응답에 존재합니다.")
    else:
        print("레코드가 비어 있습니다. 지점번호·기간·서비스 신청 상태를 확인하십시오.")
    return data


def _parse(data: dict, fallback_station: str) -> pd.DataFrame:
    recs = _records(data)
    if not recs:
        return pd.DataFrame()

    df = pd.DataFrame(recs)
    df = df.rename(columns={k: v for k, v in FIELD_MAP.items()
                            if k in df.columns})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    # 결측은 빈 문자열로 옵니다. 강수는 '무강수'가 결측으로 표기되므로
    # clean_weather 에서 0 으로 채웁니다. 여기서는 숫자 변환만 합니다.
    for col in ("tavg", "tmin", "tmax", "rain", "sunshine", "humidity", "wind"):
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.strip().replace({"": None, "-": None}),
                errors="coerce")

    df["station"] = df["stnNm"] if "stnNm" in df.columns else fallback_station
    cols = ["date", "station", "tavg", "tmin", "tmax", "rain", "sunshine",
            "humidity", "wind"]
    return df[[c for c in cols if c in df.columns]]


def fetch_asos(start: str = "2023-01-01", end: str | None = None) -> pd.DataFrame:
    """주산지 관측소 전체의 일별 기상을 수집.

    조회 기간에 제한이 있으므로 1년 단위로 쪼개고 원본을 캐싱합니다.
    """
    end_d = date.fromisoformat(end) if end else latest_available()
    frames = []

    for name, stn_id in STATIONS.items():
        cursor = date.fromisoformat(start)
        while cursor <= end_d:
            chunk_end = min(cursor + timedelta(days=364), end_d)
            print(f"  {name}({stn_id}) {cursor:%Y-%m} ~ {chunk_end:%Y-%m}")
            try:
                part = _parse(_call(stn_id, cursor, chunk_end), name)
                if not part.empty:
                    frames.append(part)
                else:
                    print(f"    비어 있음: {name} {cursor:%Y-%m}")
            except Exception as exc:
                print(f"    건너뜀: {exc}")
            time.sleep(0.3)   # 공공 API 예절 (명세상 30 tps)
            cursor = chunk_end + timedelta(days=1)

    if not frames:
        raise RuntimeError(
            "기상 수집 결과가 비어 있습니다. inspect() 로 응답을 먼저 확인하십시오.")
    out = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["date", "station"])

    # 지점명이 STATIONS 키와 다르면 REGION_WEIGHTS 조인이 전부 실패합니다.
    # 조용히 빈 결과가 되는 것을 막기 위해 여기서 경고합니다.
    unknown = sorted(set(out["station"]) - set(STATIONS))
    if unknown:
        print(f"\n⚠️ STATIONS 에 없는 지점명이 응답에 있습니다: {unknown}\n"
              "   region_map.STATIONS 의 키를 응답의 stnNm 과 일치시키십시오.")
    return out
