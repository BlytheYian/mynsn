"""API 請求／回應的 Pydantic 資料模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── /v1/parse ────────────────────────────────────────────────────────────────


class ParseRequest(BaseModel):
    source_code: str
    language: Literal["python"] = "python"


class AtomicConditionOut(BaseModel):
    cond_id: str
    expression: str
    var_names: list[str]
    negated: bool


class ConditionSetOut(BaseModel):
    decision_id: str
    conditions: list[AtomicConditionOut]
    coupling_matrix: list[list[str | None]]
    k: int


class DecisionNodeOut(BaseModel):
    node_id: str
    node_type: str
    line_no: int
    expression_str: str
    source_context: str
    condition_set: ConditionSetOut


class ParseResponse(BaseModel):
    decision_nodes: list[DecisionNodeOut]


# ── /v1/jobs ─────────────────────────────────────────────────────────────────


class LLMSettings(BaseModel):
    """不含 api_key：金鑰改由 X-LLM-Api-Key header 傳遞，避免跟原始碼混在同一份
    JSON body／log 裡（見 routes.create_job）。"""

    provider: Literal["openai", "anthropic", "groq", "ollama"] = "openai"
    model: str = "gpt-4.1-mini"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.9
    num_ctx: int = 8192


class JobCreateRequest(BaseModel):
    language: Literal["python"] = "python"
    source_code: str
    func_name: str
    func_signature: str
    domain_context: str = ""
    domain_types: dict[str, str] = Field(default_factory=dict)
    domain_bounds: dict[str, list[int]] = Field(default_factory=dict)
    preceding_direction: Literal["sequential", "none"] = "sequential"
    min_initial_random: int = 6
    max_iterations: int = 50
    min_coverage: float = 1.0
    scenarios: list[str] = Field(default_factory=list)
    llm: LLMSettings = Field(default_factory=LLMSettings)


class JobProgress(BaseModel):
    iteration: int = 0
    max_iterations: int = 0
    test_case_count: int = 0
    latest_case: dict | None = None


JobStatusLiteral = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatusLiteral
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    progress: JobProgress
    error: str | None = None


class JobResultResponse(BaseModel):
    job_id: str
    converged: bool
    final_coverage: float
    test_suite: list[dict]
    iteration_count: int
    total_tokens: int
    infeasible_paths: list[str]
    loss_history: list[int]
    all_generated_cases: list[dict]
    gate_exhausted_paths: list[str]
    failure_log: list[str]
    iteration_details: list[dict]
    gap_coverage_map: dict
    decision_truth_tables: dict
    probe_records: list[dict]
