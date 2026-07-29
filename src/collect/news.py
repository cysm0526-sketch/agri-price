"""네이버 검색 API 뉴스 수집.

⚠️ API 제약을 먼저 이해하십시오
  - 기간 지정 파라미터가 **없습니다.** sort=date 로 최신부터 훑는 것만 가능하고
    start 는 최대 1000 까지입니다. 즉 쿼리 하나로 과거를 끝없이 파고들 수 없습니다.
  - 따라서 역할을 나눕니다: **과거 축적은 빅카인즈, 증분 갱신은 네이버.**
    이 모듈은 증분 담당입니다. 매일 돌려 누적하는 것을 전제로 합니다.

왜 중복 제거가 이 파일의 핵심인가
  통신사 기사는 수십 개 매체가 그대로 전재합니다. 제거하지 않으면 '기사량'
  지표가 사건의 크기가 아니라 전재 매체 수를 세게 되어 완전히 왜곡됩니다.
  `art_z30`(기사량 급증도) 이 통째로 무의미해집니다.

  3단계로 걸러냅니다.
    1) URL 완전 일치
    2) 제목 정규화 일치 (공백·기호·따옴표·대괄호 제거)
    3) 제목 앞부분 지문(fingerprint) 일치 — 매체가 제목 뒤에 덧붙이는
       "…(종합)", "…2보" 같은 꼬리를 흡수합니다
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime

import pandas as pd
import requests

from src import config

NAVER_NEWS = "https://openapi.naver.com/v1/search/news.json"

# 품목별 검색 쿼리. 가격/수급 맥락으로 좁혀야 잡음이 줄어듭니다.
QUERIES: dict[str, list[str]] = {
    "배추": ["배추 가격", "배추 도매가", "배추 출하", "배추 작황", "김장 배추"],
    "무": ["무 가격", "무 도매가", "무 출하", "무 작황"],
    "양파": ["양파 가격", "양파 도매가", "양파 출하", "양파 작황"],
    "대파": ["대파 가격", "대파 도매가", "대파 출하", "대파 작황"],
}

# 규칙 기반 이슈 분류 (LLM 태깅 이전의 베이스라인).
# 순서가 우선순위입니다. 위에서 먼저 걸리면 그 카테고리로 확정합니다.
# [개선 예정] LLM 태깅으로 교체하되, 이 규칙을 정답 비교용 기준선으로 남기십시오.
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("기상피해", ["폭우", "호우", "장마", "태풍", "가뭄", "한파", "폭염", "냉해",
               "우박", "서리", "일조", "침수", "이상기후", "고온"]),
    ("작황부진", ["작황", "생육", "병해충", "무름병", "바이러스", "흉작", "품질 저하"]),
    ("출하감소", ["출하 감소", "출하량 감소", "반입 감소", "물량 감소", "공급 부족",
               "산지 유통", "저장량 감소"]),
    ("정책개입", ["비축", "방출", "수입", "관세", "할당관세", "정부", "농식품부",
               "aT", "농수산식품유통공사", "수급 대책", "할인 지원", "계약재배"]),
    ("공급증가", ["출하 증가", "반입 증가", "물량 증가", "과잉", "공급 과잉",
               "가격 하락", "폭락", "풍작"]),
    ("수요증가", ["수요 증가", "김장", "명절", "성수기", "소비 증가", "급식"]),
]

# 카테고리 → 가격 방향 (+1 상승요인 / -1 하락요인). mock.py 와 동일하게 맞춤.
DIRECTION = {
    "기상피해": 1, "작황부진": 1, "출하감소": 1, "수요증가": 1,
    "공급증가": -1, "정책개입": -1,
}

_STRONG = ["폭등", "급등", "폭락", "급락", "사상 최고", "최고치", "비상", "대책",
           "역대", "최악"]
_MEDIUM = ["상승", "하락", "오름", "내림", "인상", "약세", "강세"]


def _clean_title(raw: str) -> str:
    """네이버 응답 제목에서 <b> 태그와 HTML 엔티티를 제거."""
    t = re.sub(r"</?b>", "", raw or "")
    return html.unescape(t).strip()


def _norm(title: str) -> str:
    """중복 판정용 제목 정규화 — 공백·기호·대괄호 전부 제거."""
    t = re.sub(r"\[[^\]]*\]", "", title)          # [단독], [현장] 등 제거
    t = re.sub(r"\([^)]*\)", "", t)               # (종합), (2보) 등 제거
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t).lower()


def _press(originallink: str, link: str) -> str:
    """도메인에서 매체를 추정. 네이버 응답에 매체명 필드가 없습니다."""
    m = re.search(r"https?://(?:www\.)?([^/]+)", originallink or link or "")
    return m.group(1) if m else ""


def classify(title: str) -> tuple[str, int, int]:
    """제목 → (카테고리, 방향, 강도). 강도는 1~3."""
    for cat, kws in CATEGORY_RULES:
        if any(k in title for k in kws):
            break
    else:
        cat = "기타"

    intensity = 1
    if any(k in title for k in _STRONG):
        intensity = 3
    elif any(k in title for k in _MEDIUM):
        intensity = 2

    direction = DIRECTION.get(cat, 0)
    # 카테고리가 애매해도 제목에 방향어가 있으면 그것을 씁니다
    if direction == 0:
        if any(k in title for k in ["폭등", "급등", "상승", "인상", "오름", "강세"]):
            direction = 1
        elif any(k in title for k in ["폭락", "급락", "하락", "내림", "약세"]):
            direction = -1
    return cat, direction, intensity


def _search(query: str, start: int = 1, display: int = 100) -> dict:
    if not config.NAVER_ID or not config.NAVER_SECRET:
        raise RuntimeError(
            "NAVER_CLIENT_ID/SECRET 이 없습니다. developers.naver.com 에서 "
            "애플리케이션을 등록하고 '검색' API 를 추가하십시오.")
    headers = {
        "X-Naver-Client-Id": config.NAVER_ID,
        "X-Naver-Client-Secret": config.NAVER_SECRET,
    }
    params = {"query": query, "display": display, "start": start,
              "sort": "date"}
    for attempt in range(4):
        try:
            r = requests.get(NAVER_NEWS, headers=headers, params=params,
                             timeout=20)
            if r.status_code == 429:
                raise RuntimeError("일일 호출 한도 초과(25,000건/일)")
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            wait = 2 ** attempt
            print(f"  재시도 {attempt + 1}/4 ({exc}) — {wait}초")
            time.sleep(wait)
    raise RuntimeError(f"네이버 검색 실패: {query}")


def inspect(query: str = "배추 가격") -> dict:
    """★ 응답 구조와 분류 결과를 눈으로 확인하십시오."""
    data = _search(query, display=10)
    print(f"── '{query}' 전체 {data.get('total'):,}건 중 10건 ──")
    print("키:", list(data["items"][0].keys()) if data.get("items") else "없음")
    print()
    for it in data.get("items", []):
        title = _clean_title(it["title"])
        cat, direc, inten = classify(title)
        arrow = "▲" if direc > 0 else ("▼" if direc < 0 else "·")
        print(f"  {arrow} [{cat:5s} 강도{inten}] {title[:55]}")
    return data


def fetch_news(items: list[str] | None = None, pages: int = 3,
               display: int = 100) -> pd.DataFrame:
    """품목별 뉴스 수집 + 중복 제거.

    Parameters
    ----------
    pages : 쿼리당 페이지 수. start 는 1000 을 넘을 수 없습니다
            (pages * display <= 1000).
    """
    targets = items or list(QUERIES)
    rows = []
    for item in targets:
        for query in QUERIES.get(item, [f"{item} 가격"]):
            for page in range(pages):
                start = page * display + 1
                if start > 1000:
                    break
                try:
                    data = _search(query, start=start, display=display)
                except Exception as exc:
                    print(f"    실패 ({query} p{page + 1}): {exc}")
                    break
                got = data.get("items", [])
                if not got:
                    break
                for it in got:
                    title = _clean_title(it.get("title", ""))
                    if not title:
                        continue
                    try:
                        pub = datetime.strptime(
                            it["pubDate"], "%a, %d %b %Y %H:%M:%S %z")
                    except (KeyError, ValueError):
                        continue
                    cat, direc, inten = classify(title)
                    rows.append({
                        "date": pub.date(),
                        "item": item,
                        "category": cat,
                        "direction": direc,
                        "intensity": inten,
                        "title": title,
                        "press": _press(it.get("originallink", ""),
                                        it.get("link", "")),
                        "url": it.get("originallink") or it.get("link", ""),
                        "query": query,
                    })
                time.sleep(0.1)
            print(f"  {item} / {query}: 누적 {len(rows):,}건")

    if not rows:
        raise RuntimeError("뉴스 수집 결과가 비어 있습니다. inspect() 로 확인하십시오.")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return dedupe(df)


def dedupe(df: pd.DataFrame, fingerprint_len: int = 22) -> pd.DataFrame:
    """통신사 전재 중복 제거. 제거 통계를 출력합니다.

    같은 사건을 여러 매체가 전재한 것을 1건으로 봅니다. 품목·일자 단위로
    묶어서 판정하므로, 다른 날 같은 제목(연재 기사)은 살아남습니다.
    """
    before = len(df)
    out = df.copy()

    # 1) URL 완전 일치
    out = out[out["url"].astype(bool)]
    out = out.drop_duplicates(subset=["url"])
    after_url = len(out)

    # 2) 정규화 제목 일치 (품목·일자 내)
    out["_norm"] = out["title"].map(_norm)
    out = out.drop_duplicates(subset=["item", "date", "_norm"])
    after_norm = len(out)

    # 3) 제목 앞부분 지문 일치 — 꼬리만 다른 전재를 흡수
    out["_fp"] = out["_norm"].str.slice(0, fingerprint_len)
    out = out[out["_fp"].str.len() > 0]
    out = out.drop_duplicates(subset=["item", "date", "_fp"])
    after_fp = len(out)

    print(f"\n── 중복 제거 ──\n"
          f"  수집       {before:,}건\n"
          f"  URL 중복 제거 후   {after_url:,}건  (-{before - after_url:,})\n"
          f"  제목 정규화 후     {after_norm:,}건  (-{after_url - after_norm:,})\n"
          f"  제목 지문 후       {after_fp:,}건  (-{after_norm - after_fp:,})\n"
          f"  최종 유지율 {after_fp / max(before, 1) * 100:.1f}%")

    return (out.drop(columns=["_norm", "_fp"])
            .sort_values(["item", "date"])
            .reset_index(drop=True))
