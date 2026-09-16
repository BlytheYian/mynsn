"""
系統全域設定：所有可調整參數集中管理。

參考 SDD 第 8 章。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings

from ifl_mcdc.layer3.domain_validator import DEFAULT_MEDICAL_RULES, DomainValidator
from ifl_mcdc.layer3.llm_sampler import AnthropicBackend, GroqBackend, LLMBackend, OpenAIBackend, OllamaBackend


class IFLConfig(BaseSettings):
    """IFL 系統全域設定，可透過環境變數或 .env 檔覆蓋。"""

    model_config = {"env_prefix": "IFL_", "env_file": ".env", "extra": "ignore"}

    # ── LLM ──
    llm_provider: str = Field(default="openai")
    llm_model: str = Field(default="gpt-4.1-mini")
    llm_api_key: str = Field(default="")
    llm_base_url: str = Field(default="http://localhost:11434")
    llm_temperature: float = Field(default=1.0)
    llm_num_ctx: int = Field(default=8192)

    # ── SMT ──
    smt_timeout_ms: int = Field(default=10_000)

    # ── 路徑約束方向 ──
    # "sequential": 前置節點必須為 False（適用 if-return 結構）
    # "none": 不加路徑約束（適用巢狀 if 結構，如 tcas_sir）
    preceding_direction: str = Field(default="sequential")

    # ── IFL 迭代控制 ──
    max_iterations: int = Field(default=50)
    min_coverage: float = Field(default=1.0)
    llm_retry_delay: float = Field(default=2.0)
    min_initial_random: int = Field(default=6)   # 初始隨機測試最小數量（建議 max(6, 3*k)）
    # ── 目標模組 ──
    func_name: str = Field(default="check_vaccine_eligibility")
    func_signature: str = Field(
        default="check_vaccine_eligibility(age, high_risk, days_since_last, egg_allergy)"
    )
    domain_context: str = Field(default="流感疫苗施打資格篩選系統")

    # ── 領域型別定義（變數名 → Z3 型別）──
    domain_types: dict[str, str] = Field(
        default={
            "age": "int",
            "high_risk": "bool",
            "days_since_last": "int",
            "egg_allergy": "bool",
        }
    )

    # ── 領域數值邊界（變數名 → [min, max]）──
    domain_bounds: dict[str, list[int]] = Field(
        default={
            "age": [0, 130],
            "days_since_last": [0, 3650],
        }
    )

    # ── 臨床比例資料 ──
    fixture_name: str = Field(default="")
    clinical_profile_path: Path | None = Field(default=None)

    # ── 多樣性情境提示（輪換注入 prompt）──
    scenarios: list[str] = Field(default=[])

    # ── 語言模式（"python" | "c"）──
    language: str = Field(default="python")

    @property
    def clinical_profile(self) -> dict[str, Any] | None:
        """從 clinical_profiles.json 讀取對應 fixture 的臨床比例資料。"""
        if not self.fixture_name:
            return None
        from ifl_mcdc.data.clinical_profile_loader import ClinicalProfileLoader  # noqa: PLC0415
        loader = ClinicalProfileLoader(self.clinical_profile_path)
        return loader.load(self.fixture_name)

    @property
    def llm_backend(self) -> LLMBackend:
        """根據 llm_provider 建立對應的 LLM 後端。"""
        if self.llm_provider == "openai":
            return OpenAIBackend(self.llm_model, self.llm_api_key, self.llm_temperature)
        if self.llm_provider == "anthropic":
            return AnthropicBackend(self.llm_model, self.llm_api_key)
        if self.llm_provider == "groq":
            return GroqBackend(self.llm_model, self.llm_api_key, self.llm_temperature)
        if self.llm_provider == "ollama":
            return OllamaBackend(self.llm_model, self.llm_base_url, self.llm_temperature, self.llm_num_ctx)
        raise ValueError(f"不支援的 LLM 供應商：{self.llm_provider!r}")

    @property
    def domain_validator(self) -> DomainValidator:
        """建立帶預設醫療規則的驗證器，或根據 domain_types/domain_bounds 動態建立。"""
        # 🌟 [改進] 優先使用動態建立的規則（如果 domain_types 已設定）
        # 這允許 run_experiments1.py 為不同 fixture 傳入不同的 domain_types/domain_bounds
        if self.domain_types and any(
            ftype in self.domain_types.values() for ftype in ("bool", "int")
        ):
            from ifl_mcdc.models.validation import DomainRule
            rules: list[DomainRule] = []
            for field, ftype in self.domain_types.items():
                if ftype == "bool":
                    fixed_bounds = self.domain_bounds.get(field)
                    fixed_value = (
                        True if fixed_bounds == [1, 1]
                        else False if fixed_bounds == [0, 0]
                        else None
                    )
                    if fixed_value is None:
                        rules.append(DomainRule(
                            field=field,
                            description=f"{field} 必須為布論值",
                            validator=lambda v: isinstance(v, bool),
                        ))
                    else:
                        rules.append(DomainRule(
                            field=field,
                            description=f"{field} 已限定為固定值 {fixed_value}",
                            validator=lambda v, fv=fixed_value: isinstance(v, bool) and v is fv,
                        ))
                elif ftype == "int":
                    if field in self.domain_bounds:
                        lo, hi = self.domain_bounds[field][0], self.domain_bounds[field][1]
                        rules.append(DomainRule(
                            field=field,
                            description=f"{field} 必須為整數（{lo}~{hi}）",
                            validator=lambda v, lo=lo, hi=hi: _is_valid_int(v, lo, hi),
                        ))
                    else:
                        rules.append(DomainRule(
                            field=field,
                            description=f"{field} 必須為非負整數",
                            validator=lambda v: _is_valid_int(v, 0, 9999999),
                        ))
            return DomainValidator(rules) if rules else DomainValidator(DEFAULT_MEDICAL_RULES)
        return DomainValidator(DEFAULT_MEDICAL_RULES)


def _is_valid_int(v: object, lo: int, hi: int) -> bool:
    """容忍 LLM 輸出的浮點數或字串格式的整數。"""
    try:
        val = int(float(v)) if not isinstance(v, bool) else None
        return val is not None and lo <= val <= hi
    except (ValueError, TypeError):
        return False
