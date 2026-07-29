"""농산물 가격 변동 요인 분석 대시보드.

실행:  streamlit run src/app/dashboard.py

화면 흐름
  1단계  품목 선택
  2단계  전국 지역별 가격 지도 (마우스 오버 → 가격 툴팁 / 클릭 → 지역 선택)
  3단계  지역별 상세 팝업 (가격 시계열 + 주산지 기상 + 관련 뉴스 + 요인 기여도)

설계 원칙 하나만 기억하십시오.
지도의 가격은 '소비지' 값이고, 원인인 기상은 '산지' 값입니다.
대전 배추값이 오른 원인은 대전 날씨가 아니라 해남 날씨입니다.
그래서 팝업의 기상 정보에는 반드시 산지 라벨을 함께 표시합니다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

from src import config
from src.app.map_view import build_map, load_geojson
from src.io_utils import load
from src.transform.merge import map_layer

st.set_page_config(page_title="농산물 가격 변동 요인 분석",
                   page_icon="🥬", layout="wide")


# ── 데이터 로드 ───────────────────────────────────────────────────────
@st.cache_data(show_spinner="데이터 불러오는 중...")
def get_data():
    prices = load(config.STAGING / "prices")
    mart = load(config.MART)
    try:
        news = load(config.STAGING / "news")
    except FileNotFoundError:
        news = pd.DataFrame()
    return prices, mart, news


try:
    prices, mart, news = get_data()
except FileNotFoundError as exc:
    st.error(str(exc))
    st.code("python scripts/build_dataset.py --mock", language="bash")
    st.stop()


# ── 상태 초기화 ───────────────────────────────────────────────────────
st.session_state.setdefault("step", 1)
st.session_state.setdefault("item", None)
st.session_state.setdefault("region", None)
st.session_state.setdefault("handled_pick", None)  # 마지막으로 팝업을 띄운 선택값


def goto(step: int):
    st.session_state.step = step
    st.session_state.handled_pick = None


# ══════════════════════════════════════════════════════════════════════
# 1단계 — 품목 선택
# ══════════════════════════════════════════════════════════════════════
if st.session_state.step == 1:
    st.title("🥬 농산물 가격 변동 요인 분석 시스템")
    st.caption("KAMIS 가격 · 기상 · 뉴스 결합 기반 · 한국농수산식품유통공사")

    items = sorted(prices["item"].unique())
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        item = st.selectbox("분석할 품목", items, index=0)
    with col2:
        st.selectbox("구분", ["소매"], index=0, disabled=True,
                     help="도매 데이터는 아직 수집되지 않습니다 "
                          "(src/collect/kamis.py 가 CLS_RETAIL 만 수집). "
                          "도매 연동 후 활성화하십시오.")
    with col3:
        max_d = pd.to_datetime(prices["date"]).max()
        as_of = st.date_input("기준일자", value=max_d,
                              max_value=max_d,
                              min_value=pd.to_datetime(prices["date"]).min())

    st.divider()
    if st.button("확인 →", type="primary", use_container_width=True):
        st.session_state.item = item
        st.session_state.as_of = pd.Timestamp(as_of)
        goto(2)
        st.rerun()

    with st.expander("데이터 현황"):
        summary = (prices.groupby("item")
                   .agg(조사지역수=("sgg_code", "nunique"),
                        기간시작=("date", "min"), 기간종료=("date", "max"),
                        행수=("price_avg", "size")))
        st.dataframe(summary, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════
# 2단계 — 전국 지역별 가격 지도
# ══════════════════════════════════════════════════════════════════════
elif st.session_state.step == 2:
    item = st.session_state.item
    as_of = st.session_state.get("as_of")

    top = st.columns([1, 6])
    with top[0]:
        if st.button("← 품목 변경"):
            goto(1)
            st.rerun()
    with top[1]:
        st.subheader(f"{item} — 지역별 가격 현황")

    layer = map_layer(prices, item, as_of)
    if layer.empty:
        st.warning("해당 기준일자에 데이터가 없습니다.")
        st.stop()

    unit = layer["unit"].iloc[0]
    survey = pd.Timestamp(layer["survey_date"].iloc[0])

    k = st.columns(4)
    k[0].metric("전국 평균", f"{layer['price_avg'].mean():,.0f}원/{unit}")
    k[1].metric("최고 지역",
                f"{layer.iloc[0]['sgg_name']}",
                f"{layer.iloc[0]['price_avg']:,.0f}원")
    k[2].metric("최저 지역",
                f"{layer.iloc[-1]['sgg_name']}",
                f"{layer.iloc[-1]['price_avg']:,.0f}원")
    gap = layer["price_avg"].max() / layer["price_avg"].min() - 1
    k[3].metric("지역 간 격차", f"{gap * 100:.1f}%",
                help="최고 지역이 최저 지역보다 몇 % 비싼지")
    st.caption(f"최근 조사일 {survey:%Y-%m-%d}")

    metric = st.radio("색상 기준", ["가격 수준", "전주 대비 변동률"],
                      horizontal=True, label_visibility="collapsed")
    color_col = "price_avg" if metric == "가격 수준" else "wow_rate"

    left, right = st.columns([3, 2])

    with left:
        # folium 은 OSM 타일을 배경으로 쓰므로 GeoJSON 이 없어도 지도가 나옵니다.
        # 경계 파일을 넣으면 map_view 가 자동으로 면 색칠까지 추가합니다.
        fmap = build_map(layer, color_col, unit)
        state = st_folium(fmap, height=560, use_container_width=True,
                          returned_objects=["last_object_clicked_popup"],
                          key="folium_map")
        if load_geojson()[0] is None:
            st.caption(
                "마커 크기는 가격 수준, 색은 선택한 기준입니다. "
                f"시도 경계를 면으로 칠하려면 `{config.GEOJSON.name}` 을 "
                "`data/` 에 넣으십시오(mapshaper 로 1% 단순화). 없어도 동작합니다.")
        else:
            st.caption("마우스를 올리면 가격이 표시되고, 마커를 클릭하면 상세가 열립니다.")

        picked = (state or {}).get("last_object_clicked_popup")
        # region 은 팝업이 닫힐 때 None 으로 비워지지만 folium 클릭 상태는 남아
        # 있으므로, region 과 비교하면 팝업을 닫는 순간 다시 열립니다.
        # '직전에 처리한 선택'을 따로 기억해 실제 선택 변경에만 반응합니다.
        if picked and picked != st.session_state.handled_pick:
            st.session_state.handled_pick = picked
            st.session_state.region = picked
            st.rerun()

    with right:
        st.markdown("**지역별 순위**")
        show = layer[["sgg_name", "price_avg", "wow_rate", "vs_national"]].copy()
        show.columns = ["지역", f"평균({unit})", "전주대비(%)", "전국대비(%)"]
        st.dataframe(show.round(1), use_container_width=True, height=430,
                     hide_index=True)
        sel = st.selectbox("지역 직접 선택", layer["sgg_name"].tolist(),
                           index=None, placeholder="지역을 고르세요")
        if sel and sel != st.session_state.handled_pick:
            st.session_state.handled_pick = sel
            st.session_state.region = sel
            st.rerun()

    # ── 3단계 팝업 ────────────────────────────────────────────────────
    @st.dialog("지역별 가격 상세", width="large")
    def detail(region: str):
        item_ = st.session_state.item
        g = (prices[(prices["item"] == item_) & (prices["sgg_name"] == region)]
             .dropna(subset=["price_avg"]).sort_values("date"))
        m = mart[mart["item"] == item_].sort_values("date")

        st.markdown(f"### {region} — {item_}")

        # 가격 시계열 + 평년 밴드
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=g["date"], y=g["price_max"], line=dict(width=0),
            showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(
            x=g["date"], y=g["price_min"], fill="tonexty",
            fillcolor="rgba(200,200,200,0.35)", line=dict(width=0),
            name="최저~최고"))
        fig.add_trace(go.Scatter(
            x=g["date"], y=g["price_avg"], name="평균가",
            line=dict(color="#c0392b", width=2)))
        fig.update_layout(height=300, margin=dict(l=0, r=0, t=20, b=0),
                          hovermode="x unified",
                          legend=dict(orientation="h", y=1.15))
        st.plotly_chart(fig, use_container_width=True)

        # 시점 선택: 클릭 대신 슬라이더.
        # Streamlit 모달은 내부 상호작용마다 재실행되므로, 팝업 안에서
        # 차트 클릭 이벤트를 중첩하면 상태 관리가 매우 까다로워집니다.
        # MVP 는 슬라이더로 처리하고 클릭 드릴다운은 2차로 넘기십시오.
        dates = g["date"].dt.date.tolist()
        if not dates:
            st.warning("데이터가 없습니다.")
            return
        picked = st.select_slider("분석 시점", options=dates,
                                  value=dates[-1])
        picked_ts = pd.Timestamp(picked)

        row = g[g["date"] == picked_ts]
        mrow = m[m["date"] == picked_ts]
        c = st.columns(3)
        if not row.empty:
            c[0].metric("평균가", f"{row['price_avg'].iloc[0]:,.0f}원")
        prev = g[g["date"] <= picked_ts - pd.Timedelta(days=7)]
        if not prev.empty and not row.empty:
            chg = row["price_avg"].iloc[0] / prev["price_avg"].iloc[-1] - 1
            c[1].metric("전주 대비", f"{chg * 100:+.1f}%")
        c[2].metric("기준일", f"{picked:%Y-%m-%d}")

        st.divider()

        # 주산지 기상 — 클릭 지역이 아님을 명시
        origin = (mrow["origin_label"].iloc[0]
                  if not mrow.empty and "origin_label" in mrow else "미지정")
        st.markdown(f"#### 주산지 기상  ·  산지: **{origin}**")
        st.caption(
            f"⚠️ 아래 기상은 선택한 소비지({region})가 아니라 "
            f"해당 시기 주산지({origin}) 기준입니다. "
            "가격은 소비지에서 관측되지만 변동 원인은 산지에서 발생합니다.")

        win = m[(m["date"] > picked_ts - pd.Timedelta(days=30))
                & (m["date"] <= picked_ts)]
        if not win.empty:
            w = st.columns(4)
            w[0].metric("30일 누적일조",
                        f"{win['sunshine'].sum():.0f}h")
            w[1].metric("30일 누적강수", f"{win['rain'].sum():.0f}mm")
            w[2].metric("평균기온", f"{win['tavg'].mean():.1f}℃")
            w[3].metric("30℃ 초과일", f"{int((win['tavg'] > 26).sum())}일")

            wfig = go.Figure()
            wfig.add_trace(go.Bar(x=win["date"], y=win["rain"], name="강수(mm)",
                                  marker_color="#5dade2"))
            wfig.add_trace(go.Scatter(x=win["date"], y=win["sunshine"],
                                      name="일조(h)", yaxis="y2",
                                      line=dict(color="#f39c12")))
            wfig.update_layout(
                height=220, margin=dict(l=0, r=0, t=10, b=0),
                yaxis2=dict(overlaying="y", side="right"),
                legend=dict(orientation="h", y=1.2))
            st.plotly_chart(wfig, use_container_width=True)

        # 관련 뉴스
        st.markdown("#### 관련 뉴스")
        if news.empty:
            st.caption("뉴스 데이터가 아직 수집되지 않았습니다.")
        else:
            nd = news.copy()
            nd["date"] = pd.to_datetime(nd["date"])
            sel_news = nd[(nd["item"] == item_)
                          & (nd["date"] > picked_ts - pd.Timedelta(days=14))
                          & (nd["date"] <= picked_ts)]
            if sel_news.empty:
                st.caption("해당 기간 관련 기사가 없습니다.")
            else:
                for _, r in sel_news.head(8).iterrows():
                    arrow = "▲" if r.get("direction", 0) > 0 else "▼"
                    st.markdown(
                        f"- `{r['date']:%m-%d}` **{r.get('category', '')}** "
                        f"{arrow} {r['title']} · {r.get('press', '')} "
                        f"[[원문]({r.get('url', '#')})]")
                st.caption(
                    "저작권 보호를 위해 제목·메타데이터만 표시하며 "
                    "원문은 링크로 연결합니다.")

        st.markdown("#### 요인 기여도")
        st.caption(
            "SHAP 기여도 연결 예정 — src/explain/shap_attr.py 참조. "
            "LLM 요약은 SHAP 상위 요인과 위 근거만 입력해 생성하고, "
            "두 결과가 불일치하면 병기 표시합니다.")

    if st.session_state.region:
        detail(st.session_state.region)
        st.session_state.region = None
