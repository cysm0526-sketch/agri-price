"""LLM 기반 변동 요인 요약 — 환각 통제가 이 파일의 전부입니다.

심사에서 반드시 나오는 질문: "AI가 지어낸 원인을 공사 자료에 쓰겠다는 겁니까?"

그 질문을 막는 장치를 코드로 강제합니다.

  1. 프롬프트에 제공한 근거 목록 밖의 내용은 서술 금지
  2. 문장마다 근거 ID 를 반드시 부기 (예: [W2], [N5])
  3. 근거가 부족하면 "근거 불충분" 으로 응답
  4. 반환 후 근거 ID 를 실제로 검증하고, 없는 ID 를 인용하면 경고

호출 순서를 지키십시오. 통계(SHAP) → 근거 수집 → 마지막에 LLM.
LLM 을 먼저 부르고 통계로 확인하면 확증 편향이 생깁니다.
"""
from __future__ import annotations

import re

import pandas as pd

from src import config

SYSTEM = """당신은 농산물 수급 분석 보조 도구입니다.

절대 규칙:
- 아래 [근거] 목록에 명시된 수치와 기사만 사용하십시오.
- 근거 목록에 없는 원인을 추론하거나 일반 상식으로 보충하지 마십시오.
- 모든 문장 끝에 사용한 근거 ID를 대괄호로 표기하십시오. 예: [W2][N5]
- 근거가 결론을 뒷받침하기에 부족하면, 추측하지 말고 정확히
  "근거 불충분"이라고만 답하십시오.
- 인과관계를 확정하지 마십시오. "요인으로 보인다", "시기가 겹친다" 같은
  표현을 쓰고, 단정적인 "때문이다"는 쓰지 마십시오.
- 3문장 이내로 작성하십시오."""

TEMPLATE = """[분석 대상]
품목: {item}
기준일: {date}
가격 변동: 전주 대비 {change:+.1f}%

[근거 - 모델 요인 기여도]
{factors}

[근거 - 주산지 기상 (산지: {origin})]
{weather}

[근거 - 관련 보도]
{news}

위 근거만 사용하여 가격 변동의 주요 요인을 요약하십시오."""


def build_prompt(item: str, date, change: float, origin: str,
                 factors: pd.DataFrame, weather: dict,
                 news: pd.DataFrame) -> tuple[str, set[str]]:
    """프롬프트와 유효 근거 ID 집합을 함께 반환."""
    valid: set[str] = set()

    flines = []
    for i, r in enumerate(factors.itertuples(), start=1):
        fid = f"F{i}"
        valid.add(fid)
        flines.append(f"[{fid}] {r.label}: 기여도 {r.contribution:+.1f}")

    wlines = []
    for i, (k, v) in enumerate(weather.items(), start=1):
        wid = f"W{i}"
        valid.add(wid)
        wlines.append(f"[{wid}] {k}: {v}")

    nlines = []
    if news is not None and not news.empty:
        for i, r in enumerate(news.head(8).itertuples(), start=1):
            nid = f"N{i}"
            valid.add(nid)
            nlines.append(
                f"[{nid}] {r.date:%m-%d} {getattr(r, 'category', '')} "
                f"({'상승요인' if getattr(r, 'direction', 0) > 0 else '하락요인'}) "
                f"{r.title}")
    else:
        nlines.append("(해당 기간 관련 보도 없음)")

    prompt = TEMPLATE.format(
        item=item, date=pd.Timestamp(date).strftime("%Y-%m-%d"),
        change=change * 100, origin=origin,
        factors="\n".join(flines) or "(없음)",
        weather="\n".join(wlines) or "(없음)",
        news="\n".join(nlines),
    )
    return prompt, valid


def verify(text: str, valid: set[str]) -> tuple[bool, list[str]]:
    """응답이 인용한 근거 ID 가 실제로 존재하는지 검증."""
    cited = set(re.findall(r"\[([FWN]\d+)\]", text))
    bogus = sorted(cited - valid)
    has_citation = bool(cited) or "근거 불충분" in text
    return (has_citation and not bogus), bogus


def summarize(item: str, date, change: float, origin: str,
              factors: pd.DataFrame, weather: dict,
              news: pd.DataFrame | None = None) -> dict:
    """LLM 요약 생성. 반환값에 검증 결과를 함께 담습니다."""
    prompt, valid = build_prompt(item, date, change, origin,
                                 factors, weather, news)

    if not config.ANTHROPIC_KEY:
        return {"text": "(ANTHROPIC_API_KEY 미설정 — 요약 생략)",
                "prompt": prompt, "verified": None, "bogus": []}

    from anthropic import Anthropic

    client = Anthropic(api_key=config.ANTHROPIC_KEY)
    resp = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=500,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    ok, bogus = verify(text, valid)
    return {"text": text, "prompt": prompt, "verified": ok, "bogus": bogus}


def cross_check(factors: pd.DataFrame, text: str) -> list[str]:
    """SHAP 상위 요인이 LLM 서술에 실제로 등장하는지 대조.

    불일치하면 화면에 병기 경고를 띄우기 위한 근거입니다.
    """
    missing = []
    for r in factors.head(3).itertuples():
        core = re.sub(r"\d+일|\d+", "", r.label).strip()
        if core and core not in text:
            missing.append(r.label)
    return missing
