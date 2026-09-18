"""分支覆蓋 API 請求／回應的 Pydantic 資料模型。

跟 schemas.py（MC/DC）平行、獨立的一組，不共用——分支覆蓋的
request/result 形狀本來就不一樣（沒有 scenarios/min_coverage 等
MC/DC 專屬欄位，result 用 branch_coverage_map 取代
decision_truth_tables/gap_coverage_map）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── /v1/branch/parse ────────────────────────────────────────────────────────


class BranchParseRequest(BaseModel):
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


class BranchParseResponse(BaseModel):
    decision_nodes: list[DecisionNodeOut]


# ── /v1/branch/jobs ──────────────────────────────────────────────────────────


class LLMSettings(BaseModel):
    """不含 api_key：金鑰改由 X-LLM-Api-Key header 傳遞（見 branch_routes.create_branch_job）。"""

    provider: Literal["openai", "anthropic", "groq", "ollama"] = "openai"
    model: str = "gpt-4.1-mini"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.9
    num_ctx: int = 8192


class BranchJobCreateRequest(BaseModel):
    language: Literal["python"] = "python"
    source_code: str
    func_name: str
    func_signature: str
    domain_context: str = ""
    domain_types: dict[str, str] = Field(default_factory=dict)
    domain_bounds: dict[str, list[int]] = Field(default_factory=dict)
    preceding_direction: Literal["sequential"] = "sequential"
    min_initial_random: int = 6
    max_iterations: int = 50
    llm: LLMSettings = Field(default_factory=LLMSettings)


class BranchJobProgress(BaseModel):
    iteration: int = 0
    max_iterations: int = 0
    test_case_count: int = 0
    latest_case: dict | None = None


BranchJobStatusLiteral = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class BranchJobStatusResponse(BaseModel):
    job_id: str
    status: BranchJobStatusLiteral
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    progress: BranchJobProgress
    error: str | None = None


class BranchJobResultResponse(BaseModel):
    job_id: str
    converged: bool
    final_coverage: float
    test_suite: list[dict]
    iteration_count: int
    total_tokens: int
    infeasible_paths: list[str]
    loss_history: list[int]
    all_generated_cases: list[dict]
    failure_log: list[str]
    iteration_details: list[dict]
    branch_coverage_map: dict
    probe_records: list[dict]
