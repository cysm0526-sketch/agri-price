"""KAMIS Open API 수집.

⚠️ 중요 — 이 파일은 실제 API 응답으로 반드시 검증해야 합니다.

KAMIS API 는 엔드포인트마다 응답 필드명이 다르고, 명세서와 실제 응답이
어긋나는 경우도 있습니다. 그래서 이 모듈은 두 단계로 나눠 두었습니다.

  1) inspect()  — 응답을 그대로 저장하고 구조를 출력. **여기서부터 시작하십시오.**
  2) parse_*()  — 확인된 구조를 DataFrame 으로 변환

parse 함수의 FIELD_MAP 을 실제 응답에 맞게 고치는 것이 첫 작업입니다.
추측으로 넘어가면 이후 전부 재작업이 됩니다.

응답 저장 위치: data/raw/kamis_*.json
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from src import config


class _KamisTLSAdapter(HTTPAdapter):
    """KAMIS 서버용 TLS 어댑터.

    KAMIS(www.kamis.or.kr)는 구형 TLS 설정이라 OpenSSL 3.x 기본 보안수준으로는
    `SSLV3_ALERT_HANDSHAKE_FAILURE` 가 납니다. 암호 보안수준만 1 로 낮춰
    접속하고, **인증서 검증은 그대로 유지**합니다
    (verify=False 로 끄면 중간자 공격을 못 막습니다).
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context(ciphers="DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.mount("https://", _KamisTLSAdapter())
    return _session

# ── 실측으로 확정한 응답 구조 (2026-07-29) ────────────────────────────
#
# periodRetailProductList 가 이 프로젝트의 주 수집원입니다.
#   - **한 번의 호출로 전 조사지역 + 평균 + 평년을 모두 반환**합니다.
#     `p_countycode` 파라미터는 무시되므로(응답 echo 가 null) 지역별로 나눠
#     호출할 필요가 없습니다. 지역 구분은 응답의 `countyname` 으로 합니다.
#   - `countyname` 에는 실제 지역명 22개와 함께 "평균"(당해연도 전국 평균),
#     "평년"(최근 5년 평균)이 섞여 옵니다.
#     ★ **평년은 관측값이 아니므로 학습·지도에서 반드시 제외**하십시오.
#       섞으면 모델이 과거 5년 평균을 실측처럼 학습합니다.
#   - 날짜는 `regday`('MM/DD') + `yyyy` 를 조합해야 합니다.
#
# dailyPriceByCategoryList 는 보조입니다.
#   하루 스냅샷이지만 day1~day5 에 당일/1일전/1주일전/2주일전/1개월전이 함께
#   와서, 최신 시세를 빠르게 확인할 때 유용합니다(전 기간 수집엔 비효율).
#
# ⚠️ 계절 품목 주의: dailyPriceByCategoryList 로 보면 배추 '봄'(kind 01)은
#    7월에 값이 '-' 이고 '여름(고랭지)'만 값이 있습니다. config.ITEMS 에
#    품종코드를 고정해 두면 제철에 빈 값이 나올 수 있습니다.

FIELD_MAP = {
    "regday": "date_raw",      # 'MM/DD' → yyyy 와 조합해야 함
    "yyyy": "year",
    "countyname": "county",    # 실제 지역명 또는 "평균"/"평년"
    "price": "price_avg",
}

# 집계 행 — 관측값이 아니므로 지역 시계열에서 분리합니다.
AGG_ROWS = ("평균", "평년")

# KAMIS 조사지역명 → 행정구역 시도코드.
# periodRetailProductList 응답의 countyname 실측값을 그대로 매핑했습니다.
# 17개 시도를 모두 덮습니다(같은 시도에 여러 조사지역이 있으면 평균 처리).
COUNTY_TO_SIDO: dict[str, str] = {
    "서울": "11",
    "부산": "26",
    "대구": "27",
    "인천": "28",
    "광주": "29",   # 광주광역시
    "대전": "30",
    "울산": "31",
    "세종": "36",
    "수원": "41", "고양": "41", "성남": "41", "용인": "41",
    "의정부": "41",                                          # 경기
    "춘천": "42", "강릉": "42",                              # 강원
    "청주": "43",                                            # 충북
    "천안": "44",                                            # 충남
    "전주": "45",                                            # 전북
    "순천": "46",                                            # 전남
    "포항": "47", "안동": "47",                              # 경북
    "창원": "48", "김해": "48",                              # 경남
    "제주": "50",
}

# 시도코드 → 표시명 (지도 라벨)
SIDO_NAMES: dict[str, str] = {
    "11": "서울특별시", "26": "부산광역시", "27": "대구광역시",
    "28": "인천광역시", "29": "광주광역시", "30": "대전광역시",
    "31": "울산광역시", "36": "세종특별자치시", "41": "경기도",
    "42": "강원특별자치도", "43": "충청북도", "44": "충청남도",
    "45": "전북특별자치도", "46": "전라남도", "47": "경상북도",
    "48": "경상남도", "50": "제주특별자치도",
}


def _get(params: dict, cache_name: str, force: bool = False) -> dict:
    """API 호출 + 원본 캐싱. 이미 받은 응답은 재호출하지 않습니다."""
    cache = Path(config.RAW) / f"{cache_name}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))

    base = {
        "p_cert_key": config.KAMIS_KEY,
        "p_cert_id": config.KAMIS_ID,
        "p_returntype": "json",
    }
    for attempt in range(4):
        try:
            r = session().get(config.KAMIS_BASE, params={**base, **params},
                              timeout=30)
            r.raise_for_status()
            data = r.json()
            _check(data)
            cache.write_text(json.dumps(data, ensure_ascii=False),
                             encoding="utf-8")
            return data
        except Exception as exc:  # 공공 API 는 간헐적 실패가 잦습니다
            wait = 2 ** attempt
            print(f"  재시도 {attempt + 1}/4 ({exc}) — {wait}초 대기")
            time.sleep(wait)
    raise RuntimeError(f"KAMIS 호출 실패: {params}")


def inspect(item_name: str = "배추", days: int = 8) -> dict:
    """★ 두 액션의 응답을 나란히 확인합니다.

        python -c "from src.collect.kamis import inspect; inspect('배추')"
    """
    meta = config.ITEMS[item_name]
    end = date.today()
    start = end - timedelta(days=days)

    print("=" * 66)
    print(f"periodRetailProductList — 지역별 시계열 ({item_name})")
    print("=" * 66)
    raw = _get({
        "action": "periodRetailProductList",
        "p_startday": start.isoformat(),
        "p_endday": end.isoformat(),
        "p_itemcategorycode": meta.get("category_code", "200"),
        "p_itemcode": meta["item_code"],
        "p_productrankcode": meta["rank_code"],
        "p_convert_kg_yn": "N",
    }, cache_name=f"inspect_{item_name}", force=True)

    recs = _records(raw)
    print(f"레코드 {len(recs)}건, 키: {list(recs[0].keys()) if recs else '없음'}")
    counties = sorted({str(r.get("countyname")).strip() for r in recs})
    print(f"\ncountyname 값 {len(counties)}종: {counties}")
    missing = [c for c in counties
               if c not in COUNTY_TO_SIDO and c not in AGG_ROWS]
    print(f"COUNTY_TO_SIDO 미매핑: {missing or '없음'}")

    df = parse_retail_period(raw, item_name)
    if df.empty:
        print("\n파싱 결과가 비었습니다.")
        return raw
    latest = df["date"].max()
    print(f"\n── 최신일({latest:%Y-%m-%d}) 지역별 ──")
    print(df[df["date"] == latest]
          .sort_values("price_avg", ascending=False)
          [["sgg_code", "sgg_name", "price_avg", "n_survey"]]
          .to_string(index=False))
    return raw


def _check(data) -> None:
    """KAMIS 는 오류도 HTTP 200 으로 돌려주므로 본문을 검사합니다.

    - error_code 는 `data` 안에 중첩되어 있습니다 (최상위가 아님).
    - 잘못된 지역코드를 주면 `data` 가 dict 가 아니라 **빈 list** 로 옵니다.
      이걸 놓치면 '데이터 없음'으로 조용히 묻힙니다.
    """
    if not isinstance(data, dict):
        raise RuntimeError(f"예상과 다른 응답 형식: {type(data).__name__}")
    inner = data.get("data", data)
    if isinstance(inner, list):
        raise RuntimeError(
            "data 가 리스트로 왔습니다 — 보통 잘못된 지역코드/품목코드입니다. "
            "COUNTY_CODES 와 config.ITEMS 를 확인하십시오.")
    err = str(inner.get("error_code", data.get("error_code", "000")))
    if err not in ("000", "0", ""):
        msg = {"001": "인증키/요청자 id 오류", "200": "데이터 없음",
               "900": "파라미터 오류"}.get(err, "")
        raise RuntimeError(
            f"KAMIS 오류 [{err}] {msg} — p_cert_key / p_cert_id 를 확인하십시오.")


def _records(data) -> list[dict]:
    """응답에서 레코드 리스트를 꺼낸다."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    inner = data.get("data", data)
    if isinstance(inner, dict):
        items = inner.get("item", [])
        if isinstance(items, dict):
            items = [items]
        if items:
            return [r for r in items if isinstance(r, dict)]
    # dailySalesList 는 최상위 'price' 에 담깁니다
    for key in ("price", "item"):
        got = data.get(key)
        if isinstance(got, list) and got and isinstance(got[0], dict):
            return got
    return []


def _to_num(series: pd.Series) -> pd.Series:
    """'4,850' / '-' / '' → 숫자 또는 결측."""
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.strip()
        .replace({"-": None, "": None, "None": None, "nan": None}),
        errors="coerce")


def parse_retail_period(data: dict, item_name: str) -> pd.DataFrame:
    """periodRetailProductList → 지역별 일별 시계열.

    한 응답에 전 조사지역 + 평균 + 평년이 섞여 있습니다.
    "평년"(최근 5년 평균)은 관측값이 아니므로 버리고, "평균"은 sgg_code="00"
    (전국)으로 따로 남겨 모델 입력에 씁니다.
    """
    recs = _records(data)
    if not recs:
        return pd.DataFrame()

    df = pd.DataFrame(recs).rename(columns=FIELD_MAP)
    if not {"county", "date_raw", "year"} <= set(df.columns):
        return pd.DataFrame()

    df["county"] = df["county"].astype(str).str.strip()
    df = df[df["county"] != "평년"].copy()          # 5년 평균 제외
    if df.empty:
        return pd.DataFrame()

    df["date"] = pd.to_datetime(
        df["year"].astype(str) + "/" + df["date_raw"].astype(str),
        errors="coerce", format="%Y/%m/%d")
    df["price_avg"] = _to_num(df["price_avg"])
    df = df.dropna(subset=["date", "price_avg"])
    if df.empty:
        return pd.DataFrame()

    is_nat = df["county"] == "평균"
    df["sgg_code"] = df["county"].map(COUNTY_TO_SIDO)
    df.loc[is_nat, "sgg_code"] = "00"

    unknown = sorted(set(df.loc[df["sgg_code"].isna(), "county"]))
    if unknown:
        print(f"    ⚠️ COUNTY_TO_SIDO 에 없는 조사지역: {unknown}")
    df = df.dropna(subset=["sgg_code"])

    df["sgg_name"] = df["sgg_code"].map(SIDO_NAMES)
    df.loc[is_nat, "sgg_name"] = "전국"

    out = df[["date", "sgg_code", "sgg_name", "price_avg"]].copy()
    out["item"] = item_name
    out["cls"] = "소매"
    out["unit"] = config.ITEMS[item_name]["unit"]

    # 같은 시도에 조사지역이 둘 이상이면(경기=수원·고양·성남·용인) 평균
    out = (out.groupby(["date", "item", "sgg_code", "sgg_name", "cls", "unit"],
                       as_index=False)
           .agg(price_avg=("price_avg", "mean"),
                n_survey=("price_avg", "size")))
    out["price_min"] = out["price_avg"]
    out["price_max"] = out["price_avg"]
    return out.sort_values(["date", "sgg_code"]).reset_index(drop=True)


def fetch_regional_prices(start: str = "2023-01-01",
                          end: str | None = None) -> pd.DataFrame:
    """지역별 + 전국 일별 가격 시계열.

    한 번의 호출이 전 조사지역을 돌려주므로 지역 루프가 필요 없습니다.
    조회 기간 제한이 있어 연 단위로 쪼개고 원본을 캐싱합니다.

    반환 스키마는 mock.make_prices() 와 동일하므로 하위 코드 수정이 없습니다.
    sgg_code="00" 행이 전국 평균이고, 그 외가 시도별입니다.
    """
    end_d = date.fromisoformat(end) if end else date.today()
    frames = []

    for item_name, meta in config.ITEMS.items():
        cursor = date.fromisoformat(start)
        while cursor <= end_d:
            chunk_end = min(date(cursor.year, 12, 31), end_d)
            tag = f"kamis_retail_{item_name}_{cursor:%Y}"
            print(f"  {item_name} {cursor:%Y}")
            try:
                data = _get({
                    "action": "periodRetailProductList",
                    "p_startday": cursor.isoformat(),
                    "p_endday": chunk_end.isoformat(),
                    "p_itemcategorycode": meta.get("category_code", "200"),
                    "p_itemcode": meta["item_code"],
                    "p_productrankcode": meta["rank_code"],
                    "p_convert_kg_yn": "N",
                }, cache_name=tag)
                part = parse_retail_period(data, item_name)
                if not part.empty:
                    frames.append(part)
                else:
                    print(f"    비어 있음: {item_name} {cursor:%Y}")
            except Exception as exc:
                print(f"    건너뜀: {exc}")
            time.sleep(0.3)   # 공공 API 예절
            cursor = date(cursor.year + 1, 1, 1)

    if not frames:
        raise RuntimeError(
            "수집 결과가 비어 있습니다. inspect() 로 응답을 확인하고 "
            "config.ITEMS 의 품목/부류/등급 코드를 점검하십시오.")
    out = pd.concat(frames, ignore_index=True)
    n_reg = out.loc[out["sgg_code"] != "00", "sgg_code"].nunique()
    print(f"\n  수집 완료: {len(out):,}행, 시도 {n_reg}개 + 전국")
    return out


def fetch_latest_snapshot(as_of: date | None = None,
                          items: list[str] | None = None) -> pd.DataFrame:
    """당일 시세 빠른 확인용 보조 수집 (dailyPriceByCategoryList).

    전 기간 수집에는 쓰지 마십시오 — 하루씩 호출해야 해서 비효율입니다.
    품종별로 행이 나뉘어 나오므로 제철 품종을 확인할 때 유용합니다.
    """
    day = as_of or date.today()
    targets = set(items or config.ITEMS)
    rows = []
    for county, sido in COUNTY_TO_SIDO.items():
        try:
            data = _get({
                "action": "dailyPriceByCategoryList",
                "p_product_cls_code": config.CLS_RETAIL,
                "p_item_category_code": "200",
                "p_country_code": _county_code(county),
                "p_regday": day.isoformat(),
                "p_convert_kg_yn": "N",
            }, cache_name=f"kamis_snap_{county}_{day:%Y%m%d}")
        except Exception as exc:
            print(f"  건너뜀 {county}: {exc}")
            continue
        for rec in _records(data):
            name = rec.get("item_name")
            if name not in targets:
                continue
            cur = _to_num(pd.Series([rec.get("dpr1")])).iloc[0]
            if pd.isna(cur):
                continue      # 제철이 아닌 품종은 '-' 로 옵니다
            rows.append({
                "date": pd.Timestamp(day), "item": name,
                "sgg_code": sido, "sgg_name": SIDO_NAMES.get(sido, county),
                "county": county, "kind": rec.get("kind_name"),
                "unit": rec.get("unit"), "price_avg": cur,
                "price_prev_week": _to_num(
                    pd.Series([rec.get("dpr3")])).iloc[0],
            })
        time.sleep(0.3)
    return pd.DataFrame(rows)


# dailyPriceByCategoryList 는 지역명이 아니라 코드를 받습니다.
# 실측으로 유효 확인한 코드만 담았습니다.
_COUNTY_CODE_BY_NAME = {
    "서울": "1101", "부산": "2100", "대구": "2200", "인천": "2300",
    "광주": "2401", "대전": "2501", "울산": "2601", "수원": "3111",
    "강릉": "3211", "춘천": "3214", "청주": "3311", "전주": "3511",
    "포항": "3711", "제주": "3911",
}


def _county_code(name: str) -> str:
    code = _COUNTY_CODE_BY_NAME.get(name)
    if not code:
        raise RuntimeError(f"'{name}' 의 dailyPriceByCategoryList 지역코드 미확인")
    return code
