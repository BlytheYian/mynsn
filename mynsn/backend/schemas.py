"""mcdc_lite 對外的簡化請求／回應模型（給前端用，不是 ifl_api 的原始格式）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    source_code: str


class FunctionInfo(BaseModel):
    name: str
    signature: str
    param_types: dict[str, str]
    suggested_bounds: dict[str, list[int]]
    free_vars: list[str] = []


class AnalyzeResponse(BaseModel):
    functions: list[FunctionInfo]
    decision_count: int
    condition_count: int
    warning: str | None = None


class LLMSettings(BaseModel):
    provider: Literal["openai", "anthropic", "groq", "ollama"] = "openai"
    model: str = "gpt-4.1-mini"
    base_url: str = "http://localhost:11434"
    api_key: str = ""


class GenerateRequest(BaseModel):
    source_code: str
    func_name: str
    func_signature: str
    domain_context: str = ""
    domain_types: dict[str, str] = Field(default_factory=dict)
    domain_bounds: dict[str, list[int]] = Field(default_factory=dict)
    max_iterations: int = 40
    llm: LLMSettings = Field(default_factory=LLMSettings)


class ExtractRequest(BaseModel):
    source_code: str
    func_name: str


class ExtractResponse(BaseModel):
    source_code: str


class GenerateCodeRequest(BaseModel):
    description: str
    llm: LLMSettings = Field(default_factory=LLMSettings)


class GenerateCodeResponse(BaseModel):
    source_code: str


class TestConnectionRequest(BaseModel):
    provider: Literal["openai", "anthropic", "groq", "ollama"]
    api_key: str = ""
    base_url: str = "http://localhost:11434"


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str


class SuggestParamsRequest(BaseModel):
    source_code: str
    func_name: str
    llm: LLMSettings = Field(default_factory=LLMSettings)


class SuggestParamsResponse(BaseModel):
    domain_types: dict[str, str]
    domain_bounds: dict[str, list[int]]
    domain_context: str = ""


class CreateEnvironmentRequest(BaseModel):
    name: str
    source_code: str
    func_name: str
    func_signature: str
    domain_context: str = ""
    domain_types: dict[str, str] = Field(default_factory=dict)
    domain_bounds: dict[str, list[int]] = Field(default_factory=dict)
    language: str = "python"
    # 目前引擎只有 MC/DC 一種測試；先讓環境記住這個欄位，未來加新測試類型時
    # 才不用再補一次 schema migration。
    test_type: str = "mcdc"


class UpdateEnvironmentSourceRequest(BaseModel):
    source_code: str
    func_name: str
    func_signature: str
    domain_context: str = ""
    domain_types: dict[str, str] = Field(default_factory=dict)
    domain_bounds: dict[str, list[int]] = Field(default_factory=dict)


class CreateRunRequest(BaseModel):
    max_iterations: int = 40
    llm: LLMSettings = Field(default_factory=LLMSettings)


class ExplainGapRequest(BaseModel):
    source_code: str
    func_name: str
    condition_expr: str
    flip_direction: Literal["T2F", "F2T"]
    status: str
    llm: LLMSettings = Field(default_factory=LLMSettings)


class ExplainGapResponse(BaseModel):
    explanation: str
