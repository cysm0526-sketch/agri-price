# 농산물 가격 변동 요인 분석 및 예측 시스템

KAMIS 가격 · 기상 · 뉴스 데이터를 결합하여 농산물 가격 변동의 **요인을 설명**하고
향후 추이를 **전망**하는 의사결정 지원 시스템.

한국농수산식품유통공사(aT) AI 프런티어 과정 프로젝트 — 6조 이창윤

---

## 다른 PC에서 이어서 작업하기

압축본(`agri-price-전체.zip`)을 풀면 **코드 + git 이력 + `.env`(키)** 가
그대로 들어 있습니다. `data/` 는 용량(98MB) 때문에 빠져 있고, 재수집으로
복원됩니다.

```bash
# 1) 압축 해제 후 그 폴더에서
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e .

# 2) 키 확인 (.env 가 이미 들어 있음)
python -c "from src import config; print('KAMIS', bool(config.KAMIS_KEY), '| data.go.kr', bool(config.DATA_GO_KR_KEY))"

# 3) 동작 확인 (키 없이도 됨)
python scripts/build_dataset.py --mock
streamlit run src/app/dashboard.py

# 4) 실데이터 수집 (최초 1회는 오래 걸립니다 — ASOS 18개 관측소 × 3년)
python scripts/build_dataset.py
```

**Python 이 없는 PC 라면** 먼저 3.11 이상을 설치하십시오
(Windows: `winget install Python.Python.3.12`).

### 압축본에 API 키가 들어 있습니다

`.env` 를 포함했기 때문에 **압축본을 가진 사람은 모든 키를 볼 수 있습니다.**
메일에 첨부하면 키가 메일 서버에 계속 남습니다. 다음을 유념하십시오.

- 이 zip 을 다른 사람에게 전달하거나 공개 저장소에 올리지 마십시오.
- 과정이 끝나면 각 포털에서 키를 재발급(폐기)하십시오.
- 저장소에 커밋할 때는 `.env` 가 `.gitignore` 로 제외됩니다.
  `git check-ignore -v .env` 로 확인할 수 있습니다.

### git 이력이 함께 들어 있습니다

`.git` 폴더가 포함되어 있으므로 `git log` 로 지금까지의 변경 이력을 볼 수
있고, 이어서 커밋할 수 있습니다. 나중에 원격 저장소를 쓰고 싶으면
`git remote add origin <URL>` 후 push 하면 됩니다.

---

## 5분 안에 실행하기 (API 키 불필요)

합성 데이터로 파이프라인과 대시보드가 즉시 동작합니다.
API 키가 나오기 전에 구조를 확인하고 화면을 다듬는 데 쓰십시오.

```bash
uv sync                                     # 또는: pip install -e .
python scripts/build_dataset.py --mock      # 데이터 생성 → 정제 → 결합 → 모델평가
streamlit run src/app/dashboard.py          # 대시보드
```

`--mock` 으로 나온 정확도 수치는 **아무 의미가 없습니다.** 합성 데이터이므로
파이프라인이 도는지만 확인하십시오.

---

## 실제 API 연동 순서 — 이 순서를 지키십시오

### 1) 키 발급 및 설정

```bash
cp .env.example .env    # 발급받은 키 입력
```

`.gitignore` 에 `.env` 가 들어 있는지 **최초 커밋 전에** 확인하십시오.

### 2) 응답 구조 확인 ← 여기서부터 시작

수집 코드를 고치기 전에 실제 응답을 눈으로 보십시오. KAMIS 는
엔드포인트마다 필드명이 다르고 명세서와 어긋나는 경우도 있습니다.

```bash
python -c "from src.collect.kamis import inspect; inspect('배추')"
python -c "from src.collect.weather import inspect; inspect('대관령')"
python -c "from src.collect.kma_forecast import inspect; inspect('해남')"
```

출력된 키 목록에 맞춰 다음을 수정합니다.

| 파일 | 수정 대상 | 상태 |
| :--- | :--- | :--- |
| `src/collect/kamis.py` | `FIELD_MAP`, `COUNTY_TO_SIDO` | ✅ 실측 확정 (17개 시도) |
| `src/config.py` | `ITEMS` 의 품목·등급 코드 | ✅ 수집 동작 확인 |
| `src/collect/weather.py` | data.go.kr JSON 필드명 매핑 | ✅ 실측 확정 |
| `src/transform/region_map.py` | `STATIONS` 지점번호 | ✅ 명세서 대조 완료 |
| `src/transform/region_map.py` | `FORECAST_ZONES` 예보구역코드 | ✅ 명세서에서 추출 |

### 2-0) KAMIS — `periodRetailProductList` 하나로 끝납니다

**한 번의 호출이 전 조사지역을 다 돌려줍니다.** 지역별로 나눠 호출할 필요가
없습니다 (`p_countycode` 는 무시되며 응답 echo 가 `null` 입니다).
지역 구분은 응답의 `countyname` 으로 하십시오.

`countyname` 실측값 24종 — 조사지역 22개 + `평균` + `평년`:

```
강릉 고양 광주 김해 대구 대전 부산 서울 성남 세종 수원 순천
안동 용인 울산 인천 전주 제주 창원 천안 청주 춘천   ← 17개 시도로 매핑됨
평균  ← 당해연도 전국 평균 (sgg_code="00")
평년  ← 최근 5년 평균 ★ 관측값이 아니므로 제외함
```

**주의 1 — `평년`을 반드시 걸러내십시오.** 섞으면 모델이 과거 5년 평균을
실측처럼 학습합니다. `parse_retail_period()` 가 처리합니다.

**주의 2 — TLS.** KAMIS 서버는 구형 설정이라 OpenSSL 3.x 기본값으로는
`SSLV3_ALERT_HANDSHAKE_FAILURE` 가 납니다. `kamis.py` 의 세션이 암호
보안수준만 낮춰 처리하며 **인증서 검증은 유지**합니다.

**주의 3 — 인증키만으로는 안 됩니다.** `KAMIS_ID`(요청자 id)도 필수입니다.

**주의 4 — 계절 품목의 품종코드.** `dailyPriceByCategoryList` 로 보면 배추
`kind_code=01`(봄)은 7월에 값이 `-` 이고 `여름(고랭지)` 만 값이 있습니다.
품종을 고정하면 제철에 빈 값이 나올 수 있어, 기간 조회는 품종코드를
지정하지 않습니다.

### 2-1) 기상청 API — data.go.kr 로 통일했습니다

ASOS 관측과 단기예보 통보문이 **같은 인증키**를 씁니다.

| 서비스 | 엔드포인트 | 용도 |
| :--- | :--- | :--- |
| `AsosDalyInfoService` | `getWthrDataList` | 과거 일별 관측 (학습용) |
| `VilageFcstMsgService` | `getLandFcst` | 3일 예보 (미래 외생변수) |

**주의 1 — 반드시 'Decoding' 키를 `.env` 에 넣으십시오.** `requests` 가
파라미터를 자동 인코딩하므로 Encoding 키를 넣으면 `%2B` → `%252B` 로
이중 인코딩되어 인증이 실패합니다.

**주의 2 — 서비스별로 활용신청이 따로 필요합니다.** 키가 같아도 신청하지
않은 서비스는 `403 Forbidden` 이 됩니다.

**주의 3 — 과거 예보는 조회할 수 없습니다.** 최근 발표분만 제공되므로,
예보를 모델 피처로 쓰려면 매일 호출해 누적 적재해야 합니다
(`weather_forecast` 테이블이 이 용도입니다).

### 3) 지도 — folium 으로 전환했습니다 (GeoJSON 불필요)

지도는 `folium` + OpenStreetMap 타일로 그립니다. **경계 파일이 없어도
실제 지도가 나옵니다.** 시도 중심좌표를 `src/app/map_view.py` 의
`SIDO_CENTROIDS` 에 내장했고, 가격은 원형 마커의 크기·색으로 표현합니다.

GeoJSON 을 `data/korea_sido.geojson` 에 넣으면 자동으로 면 색칠
(Choropleth)이 추가됩니다. 넣을 때는 **반드시 좌표를 단순화**하십시오
(mapshaper 또는 `topojson` 으로 1% 수준). 원본 경계는 수십 MB 라
그대로 쓰면 지도가 눈에 보이게 버벅입니다.

시군구 단위로 내려가려면 `SIDO_CENTROIDS` 를 시군구 좌표로 확장하고,
공공데이터포털 「지역별 품목별 도·소매 가격정보 조회」 명세서에 첨부된
**시군구코드 표**와 대조하십시오. 코드 체계가 어긋나면 마커가 통째로
사라지므로 초반에 확인하는 것이 좋습니다.

### 4) 주산지 매핑 검증

`src/transform/region_map.py` 의 `REGION_WEIGHTS` 가 이 프로젝트에서
**가장 중요한 도메인 테이블**입니다. 배추는 여름에 강원 고랭지, 겨울에
전남 해남이 주산지이므로 월별로 다른 관측소를 봐야 합니다. 전국 평균
기상으로는 신호가 나오지 않습니다.

현재 값은 초안이므로 통계청 재배면적·주산지 현황으로 재검증하십시오.

### 5) 수집 실행

```bash
python scripts/build_dataset.py --item 배추
```

원본 응답은 `data/raw/` 에 캐싱되어 재실행 시 재호출하지 않습니다.

---

## 구조

```
src/
├── config.py              전역 설정 (키는 .env 에서 읽음)
├── io_utils.py            parquet/csv 저장·로드
├── collect/
│   ├── kamis.py           KAMIS 수집 + inspect()
│   ├── weather.py         ASOS 일자료 수집 (data.go.kr) + inspect()
│   └── kma_forecast.py    단기예보 통보문 수집 (미래 외생변수) + inspect()
├── db/
│   ├── schema.sql         Supabase 테이블 DDL + RLS
│   └── store.py           upsert/조회. 미설정 시 parquet 자동 폴백
├── transform/
│   ├── region_map.py      ★ 품목별·월별 주산지 가중치
│   ├── clean.py           휴장일 결측·이상치 태깅·보간 플래그
│   └── merge.py           소비지 가격 / 산지 기상 분리 결합
├── features/build.py      블록 단위 피처 (price·calendar·weather·news)
├── models/evaluate.py     walk-forward 검증 + 나이브 베이스라인
├── explain/
│   ├── shap_attr.py       요인 기여도
│   └── llm_summary.py     근거 인용 강제 LLM 요약
├── data/mock.py           합성 데이터 생성기
└── app/
    ├── dashboard.py       Streamlit 3단 화면
    └── map_view.py        folium 지도 (시도 중심좌표 내장)
```

---

## Supabase (선택)

목적은 **로드 시간 단축**입니다. 핵심은 DB 도입 자체가 아니라 *읽을 행 수를
줄이는 것*이라, 사전 집계 테이블(`mart`, `map_layer`)을 만들고 화면은
그것만 읽습니다. 원본 전량을 받아 매번 집계하면 오히려 느려집니다.

```bash
# 1) Supabase 대시보드 > SQL Editor 에 src/db/schema.sql 붙여넣고 실행
# 2) .env 에 SUPABASE_URL / SUPABASE_KEY 입력
# 3) 적재
python scripts/build_dataset.py --mock --push-db
```

`SUPABASE_URL` 이 비어 있으면 `src/db` 의 모든 함수가 **로컬 parquet 폴백**으로
동작합니다. 발표 직전 네트워크가 죽어도 화면은 뜹니다.

적재는 전부 `upsert` 이고 `schema.sql` 의 UNIQUE 제약과 짝을 이루므로,
재실행해도 중복이 쌓이지 않습니다.

---

## 설계에서 지킨 원칙 4가지

**1. 소비지 가격과 산지 기상을 분리했습니다.**
대전 배추값이 오른 원인은 대전 날씨가 아니라 해남 날씨입니다. 두 지역
개념을 같은 키로 조인하면 분석이 틀립니다. 화면에도 산지 라벨을 명시합니다.

**2. 뉴스와 기상의 역할을 나눴습니다.**
뉴스는 가격이 오른 *뒤에* 보도되는 후행 지표입니다. 선행 신호는 기상
예보가, 사후 설명은 뉴스가 담당합니다. `_news_block` 은 `shift(1)` 을 강제해
미래 기사가 절대 들어가지 않게 합니다.

**3. 나이브 베이스라인을 항상 함께 측정합니다.**
농산물 가격은 임의보행에 가까워 '전일 가격 유지' 예측이 의외로 강합니다.
`MAPE 12%` 는 좋은 수치인지 알 수 없지만 `나이브 대비 오차 18% 감소` 는
명확한 성과입니다. `summarize()` 가 이 값을 자동 계산합니다.

**4. LLM 이 근거 없는 원인을 말할 수 없게 막았습니다.**
근거 ID 부기 강제, 목록 밖 서술 금지, 근거 부족 시 "근거 불충분" 응답.
`verify()` 가 응답을 사후 검증하고, `cross_check()` 로 SHAP 상위 요인과
LLM 서술이 일치하는지 대조합니다.

---

## 검증 결과 (합성 데이터)

```
── 피처 블록별 성능 (배추, 7일 예측) ──
피처블록                            피처수  최고모델   MAPE  방향정확도  나이브대비
price                                23  Ridge    3.85    68.4      +8.6%
price + calendar                     32  HistGBM  4.05    68.8      +3.8%
price + calendar + weather           62  Ridge    4.21    67.0      -0.1%
price + calendar + weather + news    84  Ridge    4.22    66.6      -0.4%
```

합성 데이터라 기상·뉴스 블록이 기여하지 않는 것이 정상입니다.
**실제 데이터에서 이 표를 다시 만드는 것이 발표자료의 핵심 슬라이드입니다.**

---

## 남은 작업

- [ ] **KAMIS `KAMIS_ID` 입력** — 인증키와 별개로 요청자 id(p_cert_id)가 필요합니다.
      비어 있으면 인증 실패로 빈 응답이 옵니다
- [ ] **data.go.kr 「지상(종관, ASOS) 일자료 조회서비스」 활용신청** —
      현재 `403 Forbidden`. 승인되면 `weather.py` 가 바로 동작합니다
- [ ] **네이버 검색 API 발급** — developers.naver.com 에서 애플리케이션 등록.
      NCP(`X-NCP-APIGW-*`) 자격증명으로는 뉴스 검색이 안 됩니다(401 확인)
- [ ] 예보를 피처로 편입 — `kma_forecast.weighted_forecast()` 를
      `features/build.py` 의 weather 블록 뒤에 이어 붙이기
- [ ] 예보 일일 적재 스케줄러 (과거 예보는 재조회 불가)
- [ ] 뉴스 수집기 `src/collect/news.py` — 빅카인즈(과거) + 네이버(증분)
- [ ] 뉴스 중복 제거 — 통신사 전재로 동일 기사가 수십 건 잡힙니다.
      처리하지 않으면 기사량 지표가 완전히 왜곡됩니다
- [ ] LLM 이슈 카테고리 태깅 파이프라인
- [ ] 팝업 요인 기여도 섹션에 `shap_attr` 연결
- [ ] 예측 정확도 추적 탭
- [ ] 기상 예보 API 연동 (미래 외생변수)

### 2차 과정 이후

시군구 드릴다운, 산지 위험도 레이어, 보고서 자동 생성, 알림 발송,
챗봇, Supabase 이관, 자동 재학습.
