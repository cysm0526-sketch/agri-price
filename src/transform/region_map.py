"""품목별·월별 주산지 매핑.

이 프로젝트에서 가장 중요한 도메인 테이블입니다.

전국 평균 기상으로는 신호가 나오지 않습니다. 배추는 여름에 강원 고랭지,
겨울에 전남 해남이 주산지이므로 '월별로 다른 관측소'를 봐야 합니다.
이 테이블을 제대로 만드는 것이 예측 정확도에 가장 크게 기여합니다.

[확인 필요] 아래 가중치는 통계청 재배면적·주산지 현황을 근거로 재검증하십시오.

지점번호 검증 완료 (2026-07-29)
  「기상청 지상(종관,ASOS) 일자료 조회서비스 활용가이드」 첨부 지점코드표와
  전 항목 대조했습니다. 진도만 틀려서 175 → 268 로 정정했습니다
  (175 는 ASOS 일자료 지점 목록에 존재하지 않는 번호입니다).

지점명 불일치 정정 (2026-08-23, 실데이터 첫 수집에서 발견)
  ASOS 응답의 stnNm 이 '진도'가 아니라 '진도군'으로 옵니다. STATIONS 키가
  '진도'로 되어 있어 weighted_weather() 의 inner join 이 조용히 실패,
  진도 관측값이 배추(겨울 20%)·대파(연중 40%) 가중평균에서 통째로 빠지고
  있었습니다(에러 없이 나머지 관측소로만 재정규화되어 발견이 어려웠습니다).
  키를 '진도군'으로 맞춰 정정했습니다.
"""
from __future__ import annotations

import pandas as pd

# 기상청 ASOS 종관관측소 지점번호. 활용가이드 첨부 지점코드표로 검증됨.
STATIONS: dict[str, int] = {
    "대관령": 100,
    "강릉": 105,
    "태백": 216,
    "서울": 108,
    "수원": 119,
    "동두천": 98,
    "대전": 133,
    "전주": 146,
    "광주": 156,
    "목포": 165,
    "해남": 261,
    "여수": 168,
    "진도군": 268,   # ASOS 응답 stnNm 이 '진도군'. 175 는 없는 번호였음
    "대구": 143,
    "안동": 136,
    "창원": 155,
    "부산": 159,
    "제주": 184,
}

# 단기예보 통보문(VilageFcstMsgService) 육상예보구역코드.
# 「단기예보 통보문 조회서비스 활용가이드」 첨부 지점목록('육상' 구분)에서 추출.
# ASOS 관측소와 1:1 로 짝지어 두면 '과거 관측 → 미래 예보' 를 같은 키로 이을 수 있습니다.
FORECAST_ZONES: dict[str, str] = {
    "대관령": "11D20201",
    "강릉": "11D20501",
    "태백": "11D20301",
    "서울": "11B10101",
    "수원": "11B20601",
    "동두천": "11B20401",
    "대전": "11C20401",
    "전주": "11F10201",
    "광주": "11F20501",   # 전남 광주. 경기 광주(11B20702)와 혼동 주의
    "목포": "21F20801",
    "해남": "11F20302",
    "여수": "11F20401",
    "진도군": "21F20201",
    "대구": "11H10701",
    "안동": "11H10501",
    "창원": "11H20301",
    "부산": "11H20201",
    "제주": "11G00201",
}

# 품목 → 월(1~12) → {관측소: 재배면적 가중치}. 가중치 합은 1.0
REGION_WEIGHTS: dict[str, dict[int, dict[str, float]]] = {
    "배추": {
        # 고랭지배추 (여름)
        6: {"대관령": 0.5, "태백": 0.3, "강릉": 0.2},
        7: {"대관령": 0.5, "태백": 0.3, "강릉": 0.2},
        8: {"대관령": 0.5, "태백": 0.3, "강릉": 0.2},
        9: {"대관령": 0.4, "태백": 0.3, "안동": 0.3},
        # 가을배추
        10: {"안동": 0.4, "대전": 0.3, "전주": 0.3},
        11: {"해남": 0.4, "목포": 0.3, "전주": 0.3},
        # 월동배추 (겨울~봄)
        12: {"해남": 0.5, "목포": 0.3, "진도군": 0.2},
        1: {"해남": 0.5, "목포": 0.3, "진도군": 0.2},
        2: {"해남": 0.5, "목포": 0.3, "진도군": 0.2},
        3: {"해남": 0.4, "목포": 0.3, "전주": 0.3},
        4: {"전주": 0.4, "대전": 0.3, "안동": 0.3},
        5: {"안동": 0.4, "대전": 0.3, "대관령": 0.3},
    },
    "무": {
        6: {"대관령": 0.5, "태백": 0.3, "강릉": 0.2},
        7: {"대관령": 0.5, "태백": 0.3, "강릉": 0.2},
        8: {"대관령": 0.5, "태백": 0.3, "강릉": 0.2},
        9: {"대관령": 0.4, "안동": 0.3, "대전": 0.3},
        10: {"안동": 0.4, "대전": 0.3, "전주": 0.3},
        11: {"제주": 0.4, "목포": 0.3, "전주": 0.3},
        12: {"제주": 0.6, "목포": 0.2, "해남": 0.2},
        1: {"제주": 0.6, "목포": 0.2, "해남": 0.2},
        2: {"제주": 0.6, "목포": 0.2, "해남": 0.2},
        3: {"제주": 0.5, "목포": 0.3, "전주": 0.2},
        4: {"전주": 0.4, "대전": 0.3, "안동": 0.3},
        5: {"안동": 0.4, "대전": 0.3, "대관령": 0.3},
    },
    "양파": {  # 수확 5~6월, 저장 출고가 연중 가격 형성
        m: {"목포": 0.4, "창원": 0.3, "전주": 0.3} for m in range(1, 13)
    },
    "대파": {
        m: {"진도군": 0.4, "부산": 0.3, "동두천": 0.3} for m in range(1, 13)
    },

    # 2026-08-24 확장분 — [초안, 미검증] 위 4개 품목(특히 배추·무)처럼
    # 월별로 산지가 바뀌는 걸 반영하지 못했고, 관측소 하나로 단순화했으며
    # 통계청 재배면적 자료로 대조하지 않았습니다. STATIONS 에 이미 있는
    # 18개 관측소 중 일반적으로 알려진 주산지에 가장 가까운 것으로
    # 근사했을 뿐이라 배추/무보다 신뢰도가 낮습니다 — 특히 콩·녹두·
    # 토마토·호박·시금치는 전국 각지에 흩어져 있어 단일 관측소 근사의
    # 오차가 클 수 있습니다. 발표 전 실제 재배면적 통계로 재검증하십시오.
    "쌀": {m: {"전주": 1.0} for m in range(1, 13)},
    "찹쌀": {m: {"전주": 1.0} for m in range(1, 13)},
    "콩": {m: {"대전": 1.0} for m in range(1, 13)},
    "팥": {m: {"대관령": 1.0} for m in range(1, 13)},
    "감자": {m: {"대관령": 1.0} for m in range(1, 13)},
    "고구마": {m: {"해남": 1.0} for m in range(1, 13)},
    "녹두": {m: {"전주": 1.0} for m in range(1, 13)},

    "마늘": {m: {"창원": 1.0} for m in range(1, 13)},
    "건고추": {m: {"안동": 1.0} for m in range(1, 13)},
    "오이": {m: {"창원": 1.0} for m in range(1, 13)},
    "토마토": {m: {"대전": 1.0} for m in range(1, 13)},
    "상추": {m: {"광주": 1.0} for m in range(1, 13)},
    "시금치": {m: {"대구": 1.0} for m in range(1, 13)},
    "당근": {m: {"제주": 1.0} for m in range(1, 13)},
    "호박": {m: {"광주": 1.0} for m in range(1, 13)},
    "양배추": {m: {"제주": 1.0} for m in range(1, 13)},
    "브로콜리": {m: {"제주": 1.0} for m in range(1, 13)},

    "사과": {m: {"안동": 1.0} for m in range(1, 13)},
    "배": {m: {"광주": 1.0} for m in range(1, 13)},
    "포도": {m: {"안동": 1.0} for m in range(1, 13)},
    "복숭아": {m: {"대전": 1.0} for m in range(1, 13)},
    # 감귤·참다래·단감은 config.ITEMS 에서 제외됨(periodRetailProductList
    # 미지원, 2026-08-24 실측) — 여기도 맞춰서 뺐습니다.
}


def stations_for(item: str, month: int) -> dict[str, float]:
    """해당 품목·월의 주산지 관측소 가중치를 반환."""
    table = REGION_WEIGHTS.get(item)
    if not table:
        raise KeyError(f"주산지 매핑이 정의되지 않은 품목: {item}")
    return table[month]


def weighted_weather(weather: pd.DataFrame, item: str) -> pd.DataFrame:
    """관측소별 일별 기상 → 품목별 주산지 가중평균 시계열로 변환.

    Parameters
    ----------
    weather : DataFrame
        컬럼 = [date, station, tavg, tmin, tmax, rain, sunshine]
    item : str
        품목명

    Returns
    -------
    DataFrame
        일자별 1행. 컬럼 앞에 접두어 없이 기상 변수만 반환.
    """
    w = weather.copy()
    w["date"] = pd.to_datetime(w["date"])
    w["month"] = w["date"].dt.month

    # (월, 관측소) → 가중치 롱테이블
    rows = []
    for month, mapping in REGION_WEIGHTS[item].items():
        for station, weight in mapping.items():
            rows.append({"month": month, "station": station, "weight": weight})
    wt = pd.DataFrame(rows)

    merged = w.merge(wt, on=["month", "station"], how="inner")
    if merged.empty:
        raise ValueError(
            f"'{item}' 주산지 관측소와 기상 데이터가 하나도 매칭되지 않았습니다. "
            "STATIONS 이름과 weather['station'] 값이 일치하는지 확인하십시오."
        )

    metrics = ["tavg", "tmin", "tmax", "rain", "sunshine"]

    # 가중치는 '지표별로' 따로 합산합니다.
    # groupby.sum() 은 NaN 을 0 으로 건너뛰므로, 결측 관측소의 가중치를
    # 분모에 그대로 두면 값이 0 쪽으로 끌려갑니다(예: 한여름 기온이 0℃).
    # 그래서 해당 지표가 결측인 관측소는 분모에서도 제외합니다.
    wcols = {}
    for col in metrics:
        wcol = f"_w_{col}"
        merged[wcol] = merged["weight"].where(merged[col].notna())
        merged[col] = merged[col] * merged["weight"]
        wcols[col] = wcol

    grouped = merged.groupby("date", as_index=False).agg(
        {**{c: "sum" for c in metrics},
         **{w: "sum" for w in wcols.values()}}
    )
    for col in metrics:
        grouped[col] = grouped[col] / grouped[wcols[col]].replace(0, pd.NA)
    grouped = grouped.drop(columns=list(wcols.values()))

    # 주산지 라벨(화면 표시용) — "산지: 강원 평창·태백"
    grouped["origin_label"] = grouped["date"].dt.month.map(
        lambda m: "·".join(REGION_WEIGHTS[item][m].keys())
    )
    return grouped
