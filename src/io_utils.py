"""저장·로드 헬퍼.

parquet 이 가장 좋지만 pyarrow 가 없는 환경도 있으므로 CSV 로 폴백합니다.
경로 확장자만 바꿔서 저장하고, 로드 시 존재하는 쪽을 자동으로 읽습니다.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def _has_parquet() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        try:
            import fastparquet  # noqa: F401
            return True
        except ImportError:
            return False


HAS_PARQUET = _has_parquet()


def save(df: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if HAS_PARQUET:
        target = path.with_suffix(".parquet")
        df.to_parquet(target, index=False)
    else:
        target = path.with_suffix(".csv")
        df.to_csv(target, index=False)
    return target


def load(path: Path) -> pd.DataFrame:
    path = Path(path)
    pq, csv = path.with_suffix(".parquet"), path.with_suffix(".csv")
    if pq.exists():
        return pd.read_parquet(pq)
    if csv.exists():
        df = pd.read_csv(csv)
        for col in df.columns:
            if col in ("date", "survey_date"):
                df[col] = pd.to_datetime(df[col])
        return df
    raise FileNotFoundError(
        f"{pq} 또는 {csv} 가 없습니다. 먼저 "
        "`python scripts/build_dataset.py --mock` 을 실행하십시오.")
