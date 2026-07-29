"""프로젝트 전역 설정.

API 키는 절대 이 파일에 쓰지 말고 .env 에 두십시오.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv 미설치 시에도 동작
    pass

# ── 경로 ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"          # API 원본 응답. 절대 수정하지 않음
STAGING = DATA / "staging"  # 정제된 parquet
MART = DATA / "mart"  # 확장자는 io_utils 가 결정 (parquet 우선, 없으면 csv)
GEOJSON = DATA / "korea_sido.geojson"

for _p in (RAW, STAGING):
    _p.mkdir(parents=True, exist_ok=True)

# ── 분석 대상 ─────────────────────────────────────────────────────────
# KAMIS 품목코드/품종코드/등급코드는 반드시 API 명세서로 확인 후 확정하십시오.
# 같은 '배추'도 품종·등급에 따라 가격이 크게 다르므로 조합을 고정해야 합니다.
ITEMS: dict[str, dict] = {
    "배추": {"item_code": "211", "kind_code": "01", "rank_code": "04", "unit": "포기"},
    "무": {"item_code": "231", "kind_code": "01", "rank_code": "04", "unit": "개"},
    "양파": {"item_code": "245", "kind_code": "00", "rank_code": "04", "unit": "kg"},
    "대파": {"item_code": "246", "kind_code": "00", "rank_code": "04", "unit": "kg"},
}

# 도소매 구분: KAMIS productclscode (01=소매, 02=도매) — 명세서 확인 필요
CLS_RETAIL = "01"
CLS_WHOLESALE = "02"

# ── API ───────────────────────────────────────────────────────────────
KAMIS_KEY = os.getenv("KAMIS_KEY", "")
KAMIS_ID = os.getenv("KAMIS_ID", "")
# http 로 호출하면 302 로 https 로 넘겨지므로 처음부터 https 를 씁니다.
# 단, KAMIS 서버는 구형 TLS 설정이라 OpenSSL 3.x 기본값으로는 핸드셰이크가
# 실패합니다. src/collect/kamis.py 의 세션이 암호 보안수준을 낮춰 처리합니다.
KAMIS_BASE = "https://www.kamis.or.kr/service/price/xml.do"

# 공공데이터포털(data.go.kr) — ASOS 일자료와 단기예보 통보문이 같은 키를 씁니다.
# ★ 'Decoding' 키를 .env 에 넣으십시오. requests 가 파라미터를 자동 인코딩하므로
#   Encoding 키를 넣으면 이중 인코딩되어 인증 실패합니다.
DATA_GO_KR_KEY = os.getenv("DATA_GO_KR_KEY", "")

# ★ 반드시 https 를 쓰십시오. http 로 호출하면 502 Bad Gateway 가 옵니다
#   (명세서 예제는 http 로 적혀 있지만 실제로는 https 만 응답합니다).
# 지상(종관, ASOS) 일자료 조회서비스 — 전일(D-1)까지, 전일 자료는 11시 이후 제공
KMA_ASOS = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
# 단기예보 통보문 조회서비스 — 육상예보(예상기온·강수확률). 갱신 05/11/17시
KMA_LAND_FCST = ("https://apis.data.go.kr/1360000/VilageFcstMsgService"
                 "/getLandFcst")

NAVER_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Supabase ──────────────────────────────────────────────────────────
# 비어 있으면 src/db 계층이 자동으로 로컬 parquet 폴백으로 동작합니다.
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def has_supabase() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)

# ── 모델링 ────────────────────────────────────────────────────────────
HORIZON = 7             # 예측 지평 (일)
# lag_365/yoy 피처가 365일 전 값을 참조하므로, 첫 학습 구간은
# 365 + HORIZON 보다 넉넉히 커야 합니다. 그렇지 않으면 첫 폴드에서
# 해당 컬럼이 전부 결측이 되어 HistGBM 비닝이 실패합니다.
MIN_TRAIN_DAYS = 400    # walk-forward 최초 학습 구간
STEP_DAYS = 14          # walk-forward 이동 간격
SPIKE_THRESHOLD = 0.15  # 급등 정의: 전주 대비 +15%
