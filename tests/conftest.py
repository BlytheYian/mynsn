"""
頂層 conftest.py：讓 tests/fixtures/ 中定義的 fixture 對所有測試可見。
"""
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def vaccine_source_path() -> Path:
    return FIXTURES_DIR / "vaccine_eligibility.py"


@pytest.fixture
def vaccine_source_code() -> str:
    return (FIXTURES_DIR / "vaccine_eligibility.py").read_text(encoding="utf-8")
