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
from src.transform.merge import item_movers, map_layer

# 지도의 sgg_name(예: "경상남도") → 뉴스 제목에서 실제로 쓰이는 줄임말.
# 그냥 앞 2글자만 자르면 "경상북도"/"경상남도"가 둘 다 "경상"이 되어
# 구분이 안 되고, "전라남도"는 뉴스에서 보통 "전남"으로 쓰여 "전라"로는
# 잘 안 잡힙니다.
_REGION_ALIASES: dict[str, list[str]] = {
    "서울특별시": ["서울"], "부산광역시": ["부산"], "대구광역시": ["대구"],
    "인천광역시": ["인천"], "광주광역시": ["광주"], "대전광역시": ["대전"],
    "울산광역시": ["울산"], "세종특별자치시": ["세종"],
    "경기도": ["경기"], "강원특별자치도": ["강원"],
    "충청북도": ["충북"], "충청남도": ["충남"],
    "전북특별자치도": ["전북", "전라북도"], "전라남도": ["전남"],
    "경상북도": ["경북"], "경상남도": ["경남"],
    "제주특별자치도": ["제주"],
}

st.set_page_config(page_title="농산물 가격 변동 요인 분석",
                   page_icon="🌾", layout="wide")

st.html("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Noto+Sans+KR:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --brand: #1f6f4a;
  --brand-dark: #164f35;
  --bg: #f5f7f6;
  --card: #ffffff;
  --text: #1c2620;
  --muted: #667169;
  --border: #e3e8e4;
}
html, body, [class*="css"] {
  font-family: 'Noto Sans KR', 'Inter', -apple-system, BlinkMacSystemFont,
               'Malgun Gothic', sans-serif;
}
[data-testid="stAppViewContainer"] { background: var(--bg); }
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1100px; }

.app-hero {
  background: linear-gradient(135deg, var(--brand) 0%, var(--brand-dark) 100%);
  border-radius: 16px;
  padding: 2.2rem 2.5rem;
  color: white;
  margin-bottom: 1.5rem;
  box-shadow: 0 8px 24px rgba(22, 79, 53, 0.18);
}
.app-hero .badge {
  display: inline-block;
  background: rgba(255,255,255,0.18);
  padding: 0.25rem 0.75rem;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 500;
  letter-spacing: 0.02em;
  margin-bottom: 0.75rem;
}
.app-hero h1 { font-size: 1.9rem; font-weight: 700; margin: 0 0 0.4rem 0; color: white; }
.app-hero p { font-size: 0.95rem; opacity: 0.92; margin: 0; }

.stat-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1rem 1.1rem;
  height: 100%;
}
.stat-card .label { font-size: 0.78rem; color: var(--muted); margin-bottom: 0.35rem; }
.stat-card .value { font-size: 1.35rem; font-weight: 700; color: var(--text); }

[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: 14px !important;
  box-shadow: 0 2px 10px rgba(0,0,0,0.04);
}
[data-testid="stVerticalBlockBorderWrapper"] > div {
  border-radius: 14px !important;
}

div[data-testid="stButton"] > button[kind="primary"] {
  background: var(--brand);
  border: none;
  border-radius: 8px;
  font-weight: 600;
  transition: background 0.15s ease;
}
div[data-testid="stButton"] > button[kind="primary"]:hover { background: var(--brand-dark); }

div[data-testid="stMetric"] {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 0.9rem 1rem;
}

div[data-testid="stDataFrame"] {
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid var(--border);
}

/* ── 메인 검색 화면 ── */
.brand-mark {
  text-align: center;
  margin: 2.2rem 0 0.4rem 0;
}
.brand-mark .icon { font-size: 2.6rem; line-height: 1; }
.brand-mark h1 {
  font-size: 1.7rem; font-weight: 700; color: var(--text);
  margin: 0.3rem 0 0.15rem 0;
}
.brand-mark p { color: var(--muted); font-size: 0.85rem; margin: 0; }

.section-title {
  font-size: 0.95rem; font-weight: 700; color: var(--text);
  margin: 0 0 0.6rem 2px; display: flex; align-items: center; gap: 0.4rem;
}

.mover-row {
  position: relative;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.55rem 0.2rem;
  cursor: default;
}
.mover-row .name { font-weight: 600; color: var(--text); font-size: 0.92rem; }
.mover-row .price { color: var(--muted); font-size: 0.8rem; margin-left: 0.5rem; }
.mover-row .change { font-weight: 700; font-size: 0.92rem; }
.mover-row .change.up { color: #c0392b; }
.mover-row .change.down { color: #2980b9; }

.mover-hover-panel {
  display: none;
  position: absolute; left: 0; right: 0; top: 100%;
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; box-shadow: 0 8px 20px rgba(0,0,0,0.08);
  padding: 0.5rem 0.7rem; z-index: 30; margin-top: 4px;
}
.mover-row:hover .mover-hover-panel { display: block; }
.mover-hover-panel a {
  display: block; font-size: 0.8rem; color: var(--text);
  text-decoration: none; padding: 0.3rem 0;
  border-bottom: 1px solid var(--border);
}
.mover-hover-panel a:last-child { border-bottom: none; }
.mover-hover-panel a:hover { color: var(--brand); }
.mover-hover-panel .empty { font-size: 0.8rem; color: var(--muted); padding: 0.2rem 0; }

/* 뉴스 한 줄 — 날짜·배지·제목을 한 줄에 넣고 넘치면 말줄임 */
.news-row {
  display: flex; align-items: center; gap: 0.45rem;
  padding: 0.5rem 0.1rem; border-bottom: 1px solid var(--border);
  white-space: nowrap; overflow: hidden;
}
.news-row .date { color: var(--muted); font-size: 0.72rem; flex-shrink: 0; }
.news-row .badge {
  display: inline-block; background: var(--brand); color: white;
  border-radius: 999px; padding: 0.03rem 0.5rem; font-size: 0.65rem;
  flex-shrink: 0;
}
.news-row .title-text {
  color: var(--text); font-size: 0.85rem; font-weight: 500;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* 한 줄 전체를 클릭 가능하게 — 투명 버튼을 같은 자리에 겹친다 */
div[class*="st-key-newsrow_"] { position: relative; }
div[class*="st-key-newsrow_"] div[class*="st-key-news_"] {
  position: absolute !important; inset: 0; margin: 0 !important;
}
div[class*="st-key-newsrow_"] div[class*="st-key-news_"] div[data-testid="stButton"],
div[class*="st-key-newsrow_"] div[class*="st-key-news_"] div[data-testid="stButton"] button {
  width: 100%; height: 100%; margin: 0 !important;
}
div[class*="st-key-newsrow_"] div[class*="st-key-news_"] button {
  opacity: 0; cursor: pointer; padding: 0 !important; border: none !important;
  background: transparent !important;
}

div[data-testid="stTextInput"] input {
  border-radius: 999px !important;
  padding: 0.75rem 1.2rem !important;
  border: 1px solid var(--border) !important;
  font-size: 1rem !important;
}

/* 메인 검색창 — 부류/품목 콤보박스보다 크고 둥글게, 구글 검색창처럼 */
div[class*="st-key-main_search"] div[data-baseweb="select"] > div {
  border-radius: 999px !important;
  min-height: 3.2rem !important;
  border: 1px solid var(--border) !important;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  font-size: 1rem !important;
}
div[class*="st-key-main_search"] div[data-baseweb="select"] > div:hover {
  border-color: var(--brand) !important;
}
</style>
""")


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
    d_min = pd.to_datetime(prices["date"]).min()
    d_max = pd.to_datetime(prices["date"]).max()
    items = sorted(prices["item"].unique())

    # 위젯이 이미 만들어진 뒤에는 그 위젯의 session_state 를 직접 바꿀 수
    # 없습니다(Streamlit 제약). 그래서 '검색해서 떠날 때' 바로 지우는 대신,
    # '다음에 이 화면으로 돌아왔을 때, 위젯을 만들기 전에' 지웁니다.
    # 이걸 안 하면 이전 선택값이 위젯에 그대로 남아 있어 화면에 돌아오자마자
    # 다시 그 품목으로 튕겨나갑니다(지도 팝업이 안 닫히던 것과 같은 함정).
    for k in st.session_state.pop("_clear_on_return", []):
        st.session_state.pop(k, None)

    st.html("""
    <div class="brand-mark">
      <div class="icon">🌾</div>
      <h1>농산물 가격 변동 요인 분석</h1>
      <p>KAMIS · 기상청 · 네이버뉴스 결합 · 한국농수산식품유통공사</p>
    </div>
    """)

    def _go_to_item(name: str, *clear_keys: str):
        st.session_state["_clear_on_return"] = list(clear_keys)
        st.session_state.item = name
        st.session_state.as_of = d_max
        goto(2)
        st.rerun()

    categories = sorted({config.ITEMS.get(i, {}).get("category", "기타")
                         for i in items})

    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        cc1, cc2 = st.columns(2)
        with cc1:
            cat_sel = st.selectbox("부류", ["전체"] + categories, index=0,
                                   key="cat_select")
        with cc2:
            item_options = items if cat_sel == "전체" else [
                i for i in items
                if config.ITEMS.get(i, {}).get("category") == cat_sel]
            item_sel = st.selectbox(
                "품목", item_options, index=None, placeholder="품목 선택",
                key="item_select")
        if item_sel:
            _go_to_item(item_sel, "item_select")

        st.write("")
        query_sel = st.selectbox(
            "품목 검색", items, index=None,
            placeholder="🔍 품목명을 입력하세요 (예: 배추)",
            label_visibility="collapsed", key="main_search")
        if query_sel:
            _go_to_item(query_sel, "main_search")

    st.write("")
    st.write("")

    movers = item_movers(prices)
    top_up = movers.head(5)
    top_down = movers.tail(5).sort_values("change_pct")

    def _news_preview_html(item_name: str, n: int = 3) -> str:
        if news.empty:
            return '<div class="empty">뉴스 없음</div>'
        # '기타' = 기상피해·작황부진·출하감소·정책개입·공급증가·수요증가·
        # 가격변동 등 가격 관련 키워드에 하나도 안 걸린 기사입니다. 즉
        # 가격에 영향을 준 근거가 없는 기사라 여기서는 뺍니다.
        nd = news[(news["item"] == item_name) & (news["category"] != "기타")]
        nd = nd.sort_values(["date", "intensity"], ascending=[False, False]).head(n)
        if nd.empty:
            return '<div class="empty">가격 관련 뉴스 없음</div>'
        return "".join(
            f'<a href="{r.get("url", "#")}" target="_blank">'
            f'{r["title"][:40]}</a>'
            for _, r in nd.iterrows())

    def _mover_block(df: pd.DataFrame, title: str):
        st.markdown(f'<div class="section-title">{title}</div>',
                    unsafe_allow_html=True)
        with st.container(border=True):
            for _, r in df.iterrows():
                cls = "up" if r["change_pct"] >= 0 else "down"
                arrow = "▲" if r["change_pct"] >= 0 else "▼"
                unit = config.ITEMS.get(r["item"], {}).get("unit", "")
                row_col, btn_col = st.columns([5, 1])
                with row_col:
                    st.html(f"""
                    <div class="mover-row">
                      <div><span class="name">{r['item']}</span>
                        <span class="price">{r['price']:,.0f}원/{unit}</span></div>
                      <span class="change {cls}">{arrow} {abs(r['change_pct']):.1f}%</span>
                      <div class="mover-hover-panel">
                        {_news_preview_html(r['item'])}
                      </div>
                    </div>
                    """)
                with btn_col:
                    if st.button("보기", key=f"go_{title}_{r['item']}",
                                use_container_width=True):
                        _go_to_item(r["item"])

    left, right = st.columns([1, 1])

    with left:
        if not top_up.empty:
            _mover_block(top_up, "📈 상승 TOP 5")
        if not top_down.empty:
            _mover_block(top_down, "📉 하락 TOP 5")

    with right:
        st.markdown('<div class="section-title">📰 주요 뉴스</div>',
                    unsafe_allow_html=True)
        with st.container(border=True):
            substantive = news[news["category"] != "기타"] if not news.empty else news
            if substantive.empty:
                st.caption("뉴스 데이터가 아직 수집되지 않았습니다.")
            else:
                top_news = substantive.sort_values(
                    ["date", "intensity"], ascending=[False, False]).head(10)

                @st.dialog("뉴스 상세", width="large")
                def _news_dialog(row: dict):
                    st.markdown(f"**{row['title']}**")
                    st.caption(
                        f"{row.get('item', '')} · {row.get('category', '')} · "
                        f"{pd.Timestamp(row['date']):%Y-%m-%d} · "
                        f"{row.get('press', '')}")
                    st.link_button("원문 기사 보기 →", row.get("url", "#"),
                                  use_container_width=True)
                    st.caption("저작권 보호를 위해 원문은 언론사 페이지로 연결합니다.")

                for i, (_, r) in enumerate(top_news.iterrows()):
                    with st.container(key=f"newsrow_{i}"):
                        st.html(f"""
                        <div class="news-row">
                          <span class="date">{pd.Timestamp(r['date']):%m-%d}</span>
                          <span class="badge">{r.get('category','')}</span>
                          <span class="title-text">{r['title']}</span>
                        </div>
                        """)
                        if st.button(r["title"], key=f"news_{i}"):
                            _news_dialog(r.to_dict())

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
                f"{layer.iloc[0]['price_avg']:,.0f}원/{unit}")
    k[2].metric("최저 지역",
                f"{layer.iloc[-1]['sgg_name']}",
                f"{layer.iloc[-1]['price_avg']:,.0f}원/{unit}")
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
        show[f"평균({unit})"] = show[f"평균({unit})"].map(lambda v: f"{v:,.0f}")
        show["전주대비(%)"] = show["전주대비(%)"].round(1)
        show["전국대비(%)"] = show["전국대비(%)"].round(1)
        st.dataframe(show, use_container_width=True, height=430,
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
        unit = config.ITEMS.get(item_, {}).get("unit", "")
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
            mode="lines+markers",
            line=dict(color="#c0392b", width=2),
            marker=dict(size=5)))
        fig.update_layout(height=300, margin=dict(l=0, r=0, t=20, b=0),
                          hovermode="x unified", clickmode="event+select",
                          legend=dict(orientation="h", y=1.15))
        event = st.plotly_chart(fig, use_container_width=True,
                                on_select="rerun", selection_mode="points",
                                key="price_chart")

        dates = g["date"].dt.date.tolist()
        if not dates:
            st.warning("데이터가 없습니다.")
            return

        # 그래프의 점을 클릭하면 그 날짜를 슬라이더 값으로 미리 넣어둡니다.
        # 위젯이 만들어지기 전에 session_state 를 채워야 하므로 반드시
        # select_slider 호출보다 앞에 있어야 합니다(만든 뒤엔 못 바꿉니다).
        points = (event.get("selection", {}).get("points", [])
                 if event else [])
        if points:
            try:
                clicked_date = pd.Timestamp(points[0]["x"]).date()
                if clicked_date in dates:
                    st.session_state["date_slider"] = clicked_date
            except (KeyError, ValueError, TypeError):
                pass

        picked = st.select_slider(
            "분석 시점 — 위 그래프의 점을 클릭해도 이동합니다",
            options=dates, value=dates[-1], key="date_slider")
        picked_ts = pd.Timestamp(picked)

        row = g[g["date"] == picked_ts]
        mrow = m[m["date"] == picked_ts]
        c = st.columns(3)
        if not row.empty:
            c[0].metric("평균가", f"{row['price_avg'].iloc[0]:,.0f}원/{unit}")
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
            # '기타'(가격 관련 키워드에 안 걸린 기사)는 가격 영향 근거로
            # 보기 어려워 제외합니다. 지역 필터는 제목에 그 지역 이름이
            # 실제로 언급된 기사만 남깁니다 — 뉴스에는 지역 태그가 없어서
            # 이렇게 걸러야 '해남 폭우' 기사가 서울 팝업에도 뜨는 걸 막습니다.
            base = nd[(nd["item"] == item_) & (nd["category"] != "기타")
                     & (nd["date"] > picked_ts - pd.Timedelta(days=14))
                     & (nd["date"] <= picked_ts)]
            aliases = _REGION_ALIASES.get(region, [region])
            region_hit = base["title"].apply(
                lambda t: any(a in t for a in aliases))
            sel_news = base[region_hit]
            region_specific = True
            if sel_news.empty:
                # 그 지역을 직접 언급한 기사가 없으면, 아예 안 보여주는
                # 대신 품목 전체 뉴스로 대체합니다 — 없는 것보다는
                # 전국 단위 근거라도 보여주는 게 낫습니다.
                sel_news = base
                region_specific = False
            if sel_news.empty:
                st.caption("해당 기간 관련 기사가 없습니다.")
            else:
                if not region_specific:
                    st.caption(
                        f"'{region}'을 직접 언급한 기사는 없어 "
                        f"{item_} 전체 관련 기사를 보여줍니다.")
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
