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

# 지도 초기 시점 — 남한 전체가 들어오는 값
KOREA_CENTER = (36.5, 127.8)
KOREA_ZOOM = 7


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


def _color(value: float, lo: float, hi: float, diverging: bool) -> str:
    """값 → 색. 변동률은 발산형(빨강↑/파랑↓), 가격수준은 순차형."""
    if pd.isna(value):
        return "#999999"
    if diverging:
        if value > 0:
            t = min(value / max(hi, 1e-9), 1.0) if hi > 0 else 0.0
            return ["#fddbc7", "#ef8a62", "#b2182b"][min(int(t * 3), 2)]
        t = min(abs(value) / max(abs(lo), 1e-9), 1.0) if lo < 0 else 0.0
        return ["#d1e5f0", "#67a9cf", "#2166ac"][min(int(t * 3), 2)]
    span = hi - lo
    t = 0.0 if span <= 0 else (value - lo) / span
    return ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"][
        min(int(t * 5), 4)]


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

    m = folium.Map(location=KOREA_CENTER, zoom_start=KOREA_ZOOM,
                   tiles="CartoDB positron", control_scale=True)

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

    for _, r in layer.iterrows():
        latlon = SIDO_CENTROIDS.get(str(r["sgg_code"]))
        if latlon is None:
            continue   # 시군구 단계로 내려가면 좌표표를 확장해야 합니다
        v = pd.to_numeric(r[color_col], errors="coerce")
        radius = 8 + 14 * ((float(r["price_avg"]) - p_lo) / p_span
                           if pd.notna(r["price_avg"]) else 0)
        wow = r.get("wow_rate")
        vs_nat = r.get("vs_national")
        tip = (f"<b>{r['sgg_name']}</b><br>"
               f"평균 {r['price_avg']:,.0f}원/{unit}<br>"
               f"전주 대비 {wow:+.1f}%<br>" if pd.notna(wow) else
               f"<b>{r['sgg_name']}</b><br>평균 {r['price_avg']:,.0f}원/{unit}<br>")
        if pd.notna(vs_nat):
            tip += f"전국 대비 {vs_nat:+.1f}%"
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
