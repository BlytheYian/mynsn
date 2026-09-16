import pytest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent


@pytest.fixture
def vaccine_source_path() -> Path:
    return FIXTURES_DIR / "vaccine_eligibility.py"


@pytest.fixture
def vaccine_source_code() -> str:
    return (FIXTURES_DIR / "vaccine_eligibility.py").read_text(encoding="utf-8")
