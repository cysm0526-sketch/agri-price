-- Supabase 스키마.
--
-- 적용: Supabase 대시보드 > SQL Editor 에 붙여넣고 실행하십시오.
--
-- 설계 의도
--   1) 대시보드가 읽는 것은 '집계된 결과'입니다. 원본 전량을 매번 읽지 않도록
--      마트(mart)와 지도 레이어(map_layer)를 따로 둡니다. 로드 시간 단축의
--      핵심은 DB 도입 자체가 아니라 '읽을 행 수를 줄이는 것' 입니다.
--   2) 모든 테이블에 자연키 UNIQUE 를 걸어 upsert 가 멱등이 되게 합니다.
--      재수집해도 중복이 쌓이지 않습니다.
--   3) 조회 패턴(품목+일자 범위)에 맞춘 복합 인덱스를 겁니다.

-- ── 가격 (지역별 원본) ────────────────────────────────────────────────
create table if not exists prices (
    id           bigserial primary key,
    date         date        not null,
    item         text        not null,
    sgg_code     text        not null,
    sgg_name     text,
    cls          text,
    unit         text,
    price_avg    numeric,
    price_min    numeric,
    price_max    numeric,
    is_imputed   boolean     default false,
    outlier_flag boolean     default false,
    created_at   timestamptz default now(),
    unique (date, item, sgg_code, cls)
);
create index if not exists idx_prices_item_date on prices (item, date desc);
create index if not exists idx_prices_date on prices (date desc);

-- ── 기상 관측 (ASOS 일자료) ───────────────────────────────────────────
create table if not exists weather (
    id         bigserial primary key,
    date       date not null,
    station    text not null,
    tavg       numeric,
    tmin       numeric,
    tmax       numeric,
    rain       numeric,
    sunshine   numeric,
    humidity   numeric,
    wind       numeric,
    created_at timestamptz default now(),
    unique (date, station)
);
create index if not exists idx_weather_station_date on weather (station, date desc);

-- ── 기상 예보 (단기예보 통보문) ───────────────────────────────────────
-- 과거 예보는 API 로 다시 못 받습니다. 매일 적재해 누적하는 것이 목적입니다.
-- announce_time 을 키에 포함해 '언제 시점의 예보였는지'를 보존합니다.
-- 이게 있어야 나중에 "예보가 실제로 맞았는가" 를 검증할 수 있습니다.
create table if not exists weather_forecast (
    id            bigserial primary key,
    announce_time text  not null,
    date          date  not null,
    station       text  not null,
    tmin          numeric,
    tmax          numeric,
    tavg          numeric,
    rain_prob     numeric,
    wf            text,
    created_at    timestamptz default now(),
    unique (announce_time, date, station)
);
create index if not exists idx_fcst_station_date
    on weather_forecast (station, date desc);

-- ── 뉴스 ──────────────────────────────────────────────────────────────
-- url 을 UNIQUE 로 걸어 통신사 전재 중복을 1차 차단합니다.
-- 제목 기반 유사중복 제거는 적재 전 파이썬 단계에서 처리하십시오.
create table if not exists news (
    id         bigserial primary key,
    date       date not null,
    item       text not null,
    category   text,
    direction  smallint,
    intensity  smallint,
    title      text,
    press      text,
    url        text unique,
    created_at timestamptz default now()
);
create index if not exists idx_news_item_date on news (item, date desc);

-- ── 통합 마트 (모델링·화면 입력) ──────────────────────────────────────
-- 컬럼이 자주 바뀌므로 가변 지표는 jsonb 로 받습니다.
-- 고정 컬럼만 정규화하고 나머지는 metrics 에 넣어 스키마 변경 비용을 없앱니다.
create table if not exists mart (
    id           bigserial primary key,
    date         date not null,
    item         text not null,
    price_avg    numeric,
    price_min    numeric,
    price_max    numeric,
    n_region     integer,
    origin_label text,
    tavg         numeric,
    tmin         numeric,
    tmax         numeric,
    rain         numeric,
    sunshine     numeric,
    metrics      jsonb,
    created_at   timestamptz default now(),
    unique (date, item)
);
create index if not exists idx_mart_item_date on mart (item, date desc);

-- ── 지도 레이어 (사전 집계) ───────────────────────────────────────────
-- 대시보드 2단계가 이 테이블만 읽으면 되도록 미리 계산해 둡니다.
create table if not exists map_layer (
    id          bigserial primary key,
    as_of       date not null,
    item        text not null,
    sgg_code    text not null,
    sgg_name    text,
    unit        text,
    survey_date date,
    price_avg   numeric,
    price_min   numeric,
    price_max   numeric,
    price_prev  numeric,
    wow_rate    numeric,
    vs_national numeric,
    created_at  timestamptz default now(),
    unique (as_of, item, sgg_code)
);
create index if not exists idx_map_asof_item on map_layer (as_of desc, item);

-- ── RLS ───────────────────────────────────────────────────────────────
-- anon 키로는 읽기만 허용하고, 적재는 service_role 로만 하십시오.
-- service_role 키는 RLS 를 우회하므로 로컬 스크립트에서만 쓰고
-- 대시보드/클라이언트에는 절대 넣지 마십시오.
alter table prices           enable row level security;
alter table weather          enable row level security;
alter table weather_forecast enable row level security;
alter table news             enable row level security;
alter table mart             enable row level security;
alter table map_layer        enable row level security;

do $$
declare t text;
begin
  foreach t in array array['prices','weather','weather_forecast','news','mart','map_layer']
  loop
    execute format(
      'drop policy if exists "%s_read" on %I; '
      'create policy "%s_read" on %I for select using (true);',
      t, t, t, t);
  end loop;
end $$;
