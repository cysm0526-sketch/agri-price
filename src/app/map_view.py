"""folium 지도 레이어.

왜 plotly Choropleth 대신 folium 인가
  기존 화면이 비어 보인 진짜 원인은 plotly 가 아니라 GeoJSON 파일이 없어서였습니다.
  folium 은 OpenStreetMap 타일을 배경으로 쓰므로 **경계 파일이 없어도 실제 지도**가
  나옵니다. 시도 중심좌표만 있으면 원형 마커로 가격을 표현할 수 있습니다.

  GeoJSON 을 나중에 넣으면 자동으로 Choropleth(면 색칠)로 승격됩니다.
  즉 지금 당장 화면이 나오고, 자료가 준비되면 더 좋아지는 구조입니다.

지도의 가격은 '소비지' 값입니다. 원인인 기상은 '산지' 값이므로 이 지도에
기상을 겹쳐 그리면 안 됩니다 (팝업에서 산지 라벨과 함께 따로 보여줍니다).
"""
from __future__ import annotations

import json

import folium
import pandas as pd

from src import config

# 시도 중심좌표 (위도, 경도). GeoJSON 없이 지도를 띄우기 위한 최소 자료.
# mock.SIDO 의 행정구역 코드와 키를 맞춰 두었습니다.
SIDO_CENTROIDS: dict[str, tuple[float, float]] = {
    "11": (37.5665, 126.9780),   # 서울특별시
    "26": (35.1796, 129.0756),   # 부산광역시
    "27": (35.8714, 128.6014),   # 대구광역시
    "28": (37.4563, 126.7052),   # 인천광역시
    "29": (35.1595, 126.8526),   # 광주광역시
    "30": (36.3504, 127.3845),   # 대전광역시
    "31": (35.5384, 129.3114),   # 울산광역시
    "36": (36.4800, 127.2890),   # 세종특별자치시
    "41": (37.4138, 127.5183),   # 경기도
    "42": (37.8228, 128.1555),   # 강원특별자치도
    "43": (36.6357, 127.4917),   # 충청북도
    "44": (36.5184, 126.8000),   # 충청남도
    "45": (35.7175, 127.1530),   # 전북특별자치도
    "46": (34.8679, 126.9910),   # 전라남도
    "47": (36.4919, 128.8889),   # 경상북도
    "48": (35.4606, 128.2132),   # 경상남도
    "50": (33.4996, 126.5312),   # 제주특별자치도
}

# 지도 초기 시점 — 남한 17개 시도 GeoJSON 좌표의 실제 최소/최대값에
# 약간의 여백만 더한 값입니다(korea_sido.geojson 에서 직접 계산해 확인함:
# 위도 33.19~38.61, 경도 124.61~130.92). 남한 데이터만으로 범위를 잡으므로
# 일본·북한이 자연히 화면 밖으로 빠집니다. zoom_start 고정값 대신
# fit_bounds 로 이 범위를 항상 맞춥니다.
KOREA_BOUNDS = [[33.0, 124.9], [38.7, 130.3]]


def load_geojson() -> tuple[dict | None, str | None]:
    """시도 경계 GeoJSON. 없으면 (None, None) → 마커 모드로 동작."""
    if not config.GEOJSON.exists():
        return None, None
    geo = json.loads(config.GEOJSON.read_text(encoding="utf-8"))
    props = geo["features"][0]["properties"]
    for cand in ("CTPRVN_CD", "SIG_CD", "code", "adm_cd", "sido_cd"):
        if cand in props:
            return geo, cand
    return geo, list(props.keys())[0]


# 결측(조사지역 없음)은 빨강·파랑 어느 쪽과도 헷갈리지 않게 보라 계열로
# 분리합니다. 이전엔 회색(#999999)이 최저값 쪽의 거의-흰색과 톤이 비슷해
# '값이 낮은 지역'과 '아예 데이터가 없는 지역'이 구분되지 않았습니다.
_NA_COLOR = "#c9b8d6"
# 빨강 한 계열의 명암만으로는(#fee5d9→#a50f15) 값이 조금만 달라도 톤이
# 비슷해 보여 이웃 지역 구분이 어려웠습니다. 노랑→주황→빨강→진자주로
# 색상 자체가 이동하는 다색 그라데이션(ColorBrewer YlOrRd)을 쓰면 밝기뿐
# 아니라 색조 차이로도 구분되어 인접 지역을 훨씬 쉽게 알아볼 수 있습니다.
_SEQ_STOPS = ["#ffeda0", "#feb24c", "#fc4e2a", "#e31a1c", "#800026"]
_DIV_POS_STOPS = ["#fcae91", "#ef8a62", "#b2182b"]
_DIV_NEG_STOPS = ["#9ecae1", "#67a9cf", "#2166ac"]


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_color(t: float, stops: list[str]) -> str:
    """0~1 사이 t를 stops 색상들 사이에서 연속적으로 보간합니다.

    예전엔 구간을 3~5단계로만 나눠서 값이 비슷한 지역끼리 색이 겹쳤습니다
    (예: 강원·경기가 똑같은 주황). 보간하면 지역마다 실제 값에 비례해
    거의 고유한 색조가 나와 이웃 지역과 구분이 쉬워집니다.
    """
    t = max(0.0, min(1.0, t))
    n = len(stops) - 1
    seg = min(int(t * n), n - 1)
    local_t = t * n - seg
    c1, c2 = _hex_to_rgb(stops[seg]), _hex_to_rgb(stops[seg + 1])
    r, g, b = (round(c1[i] + (c2[i] - c1[i]) * local_t) for i in range(3))
    return f"#{r:02x}{g:02x}{b:02x}"


def _color(value: float, lo: float, hi: float, diverging: bool) -> str:
    """값 → 색. 변동률은 발산형(빨강↑/파랑↓), 가격수준은 순차형."""
    if pd.isna(value):
        return _NA_COLOR
    if diverging:
        if value > 0 and hi > 0:
            return _lerp_color(value / hi, _DIV_POS_STOPS)
        if value < 0 and lo < 0:
            return _lerp_color(abs(value) / abs(lo), _DIV_NEG_STOPS)
        return _DIV_POS_STOPS[0] if value >= 0 else _DIV_NEG_STOPS[0]
    span = hi - lo
    t = 0.0 if span <= 0 else (value - lo) / span
    return _lerp_color(t, _SEQ_STOPS)


def build_map(layer: pd.DataFrame, color_col: str, unit: str) -> folium.Map:
    """지역별 가격 레이어 → folium 지도.

    Parameters
    ----------
    layer : map_layer() 결과 (sgg_code, sgg_name, price_avg, wow_rate ...)
    color_col : 'price_avg' 또는 'wow_rate'
    """
    diverging = color_col == "wow_rate"
    vals = pd.to_numeric(layer[color_col], errors="coerce")
    lo, hi = float(vals.min()), float(vals.max())

    # 확대/축소를 완전히 막습니다(스크롤·더블클릭·터치핀치·+/- 버튼 전부) —
    # 페이지를 스크롤하다 지도 위에서 실수로 확대되는 것도 함께 막힙니다.
    # zoomSnap 을 소수로 낮추면 fit_bounds 가 정수 줌 레벨에 반올림되지
    # 않고 훨씬 더 꽉 맞는 배율을 골라, 남한 밖 여백이 크게 줄어듭니다.
    m = folium.Map(tiles="OpenStreetMap", control_scale=True,
                   zoom_control=False, scrollWheelZoom=False,
                   doubleClickZoom=False, touchZoom=False, boxZoom=False,
                   keyboard=False, zoomSnap=0.1, zoomDelta=0.1)
    m.fit_bounds(KOREA_BOUNDS)

    geo, key = load_geojson()
    if geo is not None:
        # 경계 파일이 있으면 면 색칠 + 마커 라벨 병행
        lookup = dict(zip(layer["sgg_code"].astype(str), vals))
        folium.GeoJson(
            geo,
            name="시도 경계",
            style_function=lambda feat, _k=key, _l=lookup: {
                "fillColor": _color(_l.get(str(feat["properties"].get(_k)),
                                           float("nan")), lo, hi, diverging),
                "color": "white", "weight": 1, "fillOpacity": 0.75,
            },
        ).add_to(m)

    # 원 마커: 크기는 가격 수준, 색은 선택한 기준
    price = pd.to_numeric(layer["price_avg"], errors="coerce")
    p_lo, p_hi = float(price.min()), float(price.max())
    p_span = max(p_hi - p_lo, 1e-9)
    nat_avg = price.mean()  # map_layer() 의 vs_national 계산과 동일한 기준

    for _, r in layer.iterrows():
        latlon = SIDO_CENTROIDS.get(str(r["sgg_code"]))
        if latlon is None:
            continue   # 시군구 단계로 내려가면 좌표표를 확장해야 합니다
        v = pd.to_numeric(r[color_col], errors="coerce")
        radius = 8 + 14 * ((float(r["price_avg"]) - p_lo) / p_span
                           if pd.notna(r["price_avg"]) else 0)
        wow = r.get("wow_rate")
        vs_nat = r.get("vs_national")
        tip = f"<b>{r['sgg_name']}</b><br>평균 {r['price_avg']:,.0f}원/{unit}<br>"
        if pd.notna(wow) and pd.notna(r.get("price_prev")):
            diff_wow = r["price_avg"] - r["price_prev"]
            tip += f"전주 대비 {diff_wow:+,.0f}원/{unit} ({wow:+.1f}%)<br>"
        if pd.notna(vs_nat):
            diff_nat = r["price_avg"] - nat_avg
            tip += f"전국 대비 {diff_nat:+,.0f}원/{unit} ({vs_nat:+.1f}%)"
        folium.CircleMarker(
            location=latlon,
            radius=radius,
            color="white", weight=1.5,
            fill=True, fill_color=_color(v, lo, hi, diverging),
            fill_opacity=0.9,
            tooltip=folium.Tooltip(tip, sticky=True),
            popup=folium.Popup(f"{r['sgg_name']}", max_width=200),
        ).add_to(m)
        folium.Marker(
            location=latlon,
            icon=folium.DivIcon(html=(
                '<div style="font-size:11px;color:#111;font-weight:600;'
                'text-shadow:0 0 3px #fff,0 0 3px #fff;white-space:nowrap;'
                f'transform:translate(-50%,-170%)">{r["sgg_name"][:2]}</div>')),
        ).add_to(m)

    return m
