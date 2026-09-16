"""
臨床流行病學比例載入器。

從 JSON 檔案讀取各 Fixture 的臨床比例資料，
提供快取機制避免重複 I/O，找不到時回傳 None 而非拋出例外。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_PROFILE_PATH = Path(__file__).parent / "clinical_profiles.json"


class ClinicalProfileLoader:
    """載入並快取臨床流行病學比例資料。

    用法：
        loader = ClinicalProfileLoader()
        profile = loader.load("surgery_risk")
        if profile is not None:
            section = loader.build_prompt_section(profile)
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = path or _DEFAULT_PROFILE_PATH
        self._cache: dict[str, Any] | None = None

    def _get_all(self) -> dict[str, Any]:
        if self._cache is None:
            try:
                with self._path.open(encoding="utf-8") as f:
                    self._cache = json.load(f)
            except (OSError, json.JSONDecodeError):
                self._cache = {}
        return self._cache

    def load(self, fixture_name: str) -> dict[str, Any] | None:
        """讀取指定 fixture 的臨床比例資料。

        Args:
            fixture_name: 對應 clinical_profiles.json 的頂層 key。

        Returns:
            dict 或 None（找不到或讀取失敗時）。
        """
        if not fixture_name:
            return None
        all_profiles = self._get_all()
        return all_profiles.get(fixture_name) or None

    def build_prompt_section(self, profile: dict[str, Any]) -> str:
        """將臨床資料轉化為自然語言的 Prompt 段落。

        Args:
            profile: load() 回傳的臨床比例 dict。

        Returns:
            Prompt 段落字串，可直接插入 PromptConstructor.build() 結果。
        """
        from ifl_mcdc.layer3.prompt_builder import _build_clinical_section  # noqa: PLC0415
        return _build_clinical_section(profile)
