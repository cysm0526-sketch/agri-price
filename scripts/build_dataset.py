"""파이프라인 실행 스크립트.

    python scripts/build_dataset.py --mock        # 합성 데이터로 전체 실행
    python scripts/build_dataset.py               # 실제 API 수집 (키 필요)

--mock 은 API 키 없이 파이프라인·대시보드가 도는지 확인하는 용도입니다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows 콘솔 기본 인코딩(cp949)은 이모지 등 일부 유니코드 문자를 인코딩하지
# 못해 print() 가 UnicodeEncodeError 로 죽습니다. 실행 초입에 UTF-8로 강제합니다.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src import config
from src.io_utils import save
from src.models.evaluate import ablation, summarize, walk_forward
from src.transform.clean import clean_prices, clean_weather, quality_report
from src.transform.merge import build_mart


def load_mock():
    from src.data.mock import generate_all
    print("합성 데이터 생성 중...")
    return generate_all()


def load_real():
    from src.collect.kamis import fetch_regional_prices
    from src.collect.weather import fetch_asos
    if not config.KAMIS_KEY:
        raise SystemExit(
            "KAMIS_KEY 가 설정되지 않았습니다. .env 를 확인하거나 --mock 을 쓰십시오.")
    if not config.KAMIS_ID:
        print("⚠️ KAMIS_ID 가 비어 있습니다. KAMIS 는 인증키와 함께 요청자 id 를 "
              "요구하므로 빈 응답이 올 수 있습니다.")
    print("실제 API 수집 중... (시간이 걸립니다)")
    prices = fetch_regional_prices(start="2023-01-01", end=None)
    weather = fetch_asos(start="2023-01-01", end=None)

    # 뉴스는 실패해도 파이프라인을 세우지 않습니다 (후행 지표이므로 없어도 무방)
    news = pd.DataFrame()
    if config.NAVER_ID:
        try:
            from src.collect.news import fetch_news
            print("\n뉴스 수집 중...")
            news = fetch_news()
        except Exception as exc:
            print(f"뉴스 수집 건너뜀: {exc}")
    else:
        print("NAVER_CLIENT_ID 미설정 — 뉴스 수집 생략")
    return {"prices": prices, "weather": weather, "news": news}


def load_forecast():
    """주산지 예보 스냅샷. 실패해도 파이프라인을 세우지 않습니다."""
    if not config.DATA_GO_KR_KEY:
        return pd.DataFrame()
    try:
        from src.collect.kma_forecast import fetch_forecast
        print("\n주산지 예보 수집 중...")
        return fetch_forecast()
    except Exception as exc:
        print(f"예보 수집 건너뜀: {exc}")
        return pd.DataFrame()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="합성 데이터 사용")
    ap.add_argument("--item", default="배추", help="평가할 품목")
    ap.add_argument("--skip-eval", action="store_true")
    ap.add_argument("--push-db", action="store_true",
                    help="Supabase 에 적재 (SUPABASE_URL/KEY 필요)")
    args = ap.parse_args()

    raw = load_mock() if args.mock else load_real()

    print("정제 중...")
    prices = clean_prices(raw["prices"])
    weather = clean_weather(raw["weather"])

    print("\n── 데이터 품질 리포트 ──")
    print(quality_report(prices, weather).to_string(index=False))

    print("\n통합 마트 생성 중...")
    mart = build_mart(prices, weather, raw["news"])

    save(prices, config.STAGING / "prices")
    save(weather, config.STAGING / "weather")
    if not raw["news"].empty:
        save(raw["news"], config.STAGING / "news")
    target = save(mart, config.MART)
    print(f"저장 완료: {target}  ({len(mart):,}행, {mart.shape[1]}컬럼)")

    if args.push_db:
        from src.db import is_enabled, push_all
        from src.db.store import push_map_layers
        if not is_enabled():
            print("\n⚠️ SUPABASE_URL/KEY 가 없어 적재를 건너뜁니다. .env 를 확인하십시오.")
        else:
            print("\nSupabase 적재 중...")
            counts = push_all(prices, weather, mart, raw.get("news"),
                              load_forecast() if not args.mock else None)
            counts["map_layer"] = push_map_layers(prices)
            print("적재 완료: " + ", ".join(f"{k} {v:,}행"
                                        for k, v in counts.items()))

    if args.skip_eval:
        return

    print(f"\n── 피처 블록별 성능 (품목: {args.item}) ──")
    abl = ablation(mart, args.item, horizon=config.HORIZON,
                   min_train=config.MIN_TRAIN_DAYS, step=config.STEP_DAYS)
    print(abl.to_string(index=False) if not abl.empty else "데이터 부족")

    print(f"\n── 전체 피처 모델 비교 (품목: {args.item}) ──")
    from src.features.build import build
    X, y = build(mart, args.item, horizon=config.HORIZON)
    folds = walk_forward(X, y, config.HORIZON,
                         config.MIN_TRAIN_DAYS, config.STEP_DAYS)
    print(summarize(folds).round(2).to_string(index=False))


if __name__ == "__main__":
    main()
