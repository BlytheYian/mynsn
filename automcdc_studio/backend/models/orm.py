from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import String, Boolean, Float, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from backend.database import Base, new_id


class TestEnvironment(Base):
    __tablename__ = "test_environments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)       # "python" | "c"
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    function_name: Mapped[str] = mapped_column(String(255), nullable=False)
    func_signature: Mapped[str] = mapped_column(Text, nullable=False)
    domain_context: Mapped[str] = mapped_column(Text, default="")
    domain_types: Mapped[dict] = mapped_column(JSON, default=dict)
    domain_bounds: Mapped[dict] = mapped_column(JSON, default=dict)
    preceding_direction: Mapped[str] = mapped_column(String(20), default="sequential")
    min_initial_random: Mapped[int] = mapped_column(Integer, default=6)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    test_cases: Mapped[list[TestCase]] = relationship(back_populates="environment", cascade="all, delete-orphan")
    test_runs: Mapped[list[TestRun]] = relationship(back_populates="environment", cascade="all, delete-orphan")
    stub_rules: Mapped[list[StubRule]] = relationship(back_populates="environment", cascade="all, delete-orphan")
    param_constraints: Mapped[list[ParameterConstraint]] = relationship(back_populates="environment", cascade="all, delete-orphan")
    oracle: Mapped[Optional[OracleDefinition]] = relationship(back_populates="environment", uselist=False, cascade="all, delete-orphan")


class TestCase(Base):
    __tablename__ = "test_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    env_id: Mapped[str] = mapped_column(ForeignKey("test_environments.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[str] = mapped_column(String(10), default="manual")   # "auto" | "manual"
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    expected_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_source: Mapped[str] = mapped_column(String(10), default="none") # "oracle" | "manual" | "none"
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    environment: Mapped[TestEnvironment] = relationship(back_populates="test_cases")
    results: Mapped[list[TestResult]] = relationship(back_populates="test_case")
    requirement_links: Mapped[list[RequirementLink]] = relationship(back_populates="test_case", cascade="all, delete-orphan")


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    env_id: Mapped[str] = mapped_column(ForeignKey("test_environments.id"), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(10), default="user")   # "user" | "ci"
    status: Mapped[str] = mapped_column(String(10), default="running")      # "running" | "complete" | "failed"
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    environment: Mapped[TestEnvironment] = relationship(back_populates="test_runs")
    results: Mapped[list[TestResult]] = relationship(back_populates="run", cascade="all, delete-orphan")
    coverage_snapshot: Mapped[Optional[CoverageSnapshot]] = relationship(back_populates="run", uselist=False, cascade="all, delete-orphan")
    version_record: Mapped[Optional[VersionRecord]] = relationship(back_populates="run", uselist=False, cascade="all, delete-orphan")


class TestResult(Base):
    __tablename__ = "test_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id"), nullable=False)
    case_id: Mapped[str] = mapped_column(ForeignKey("test_cases.id"), nullable=False)
    actual_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)         # "pass" | "fail" | "error" | "coverage_only"
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    execution_time_ms: Mapped[float] = mapped_column(Float, default=0.0)
    trace_log: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    run: Mapped[TestRun] = relationship(back_populates="results")
    test_case: Mapped[TestCase] = relationship(back_populates="results")


class CoverageSnapshot(Base):
    __tablename__ = "coverage_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id"), nullable=False, unique=True)
    mcdc_coverage: Mapped[float] = mapped_column(Float, default=0.0)
    effective_coverage: Mapped[float] = mapped_column(Float, default=0.0)
    covered_pairs: Mapped[list] = mapped_column(JSON, default=list)
    uncovered_pairs: Mapped[list] = mapped_column(JSON, default=list)
    infeasible_pairs: Mapped[list] = mapped_column(JSON, default=list)
    decision_details: Mapped[dict] = mapped_column(JSON, default=dict)

    run: Mapped[TestRun] = relationship(back_populates="coverage_snapshot")
    exclusions: Mapped[list[ExclusionRecord]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    justifications: Mapped[list[InfeasibleJustification]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")


class VersionRecord(Base):
    __tablename__ = "version_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id"), nullable=False, unique=True)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    git_hash: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    git_commit_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    run: Mapped[TestRun] = relationship(back_populates="version_record")


class OracleDefinition(Base):
    __tablename__ = "oracle_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    env_id: Mapped[str] = mapped_column(ForeignKey("test_environments.id"), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(20), default="function")       # "function" | "reference_impl"
    code: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    environment: Mapped[TestEnvironment] = relationship(back_populates="oracle")


class StubRule(Base):
    __tablename__ = "stub_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    env_id: Mapped[str] = mapped_column(ForeignKey("test_environments.id"), nullable=False)
    call_site: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy: Mapped[str] = mapped_column(String(20), nullable=False)       # "fixed" | "sequence" | "exception"
    return_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    return_sequence: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    exception_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    exception_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    environment: Mapped[TestEnvironment] = relationship(back_populates="stub_rules")


class ParameterConstraint(Base):
    __tablename__ = "parameter_constraints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    env_id: Mapped[str] = mapped_column(ForeignKey("test_environments.id"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    z3_expression: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(10), default="user")     # "user" | "llm"
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    environment: Mapped[TestEnvironment] = relationship(back_populates="param_constraints")


class ExclusionRecord(Base):
    __tablename__ = "exclusion_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("coverage_snapshots.id"), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(50), nullable=False)
    flip_direction: Mapped[str] = mapped_column(String(5), nullable=False)  # "F2T" | "T2F"
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    excluded_by: Mapped[str] = mapped_column(String(255), default="user")
    excluded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    snapshot: Mapped[CoverageSnapshot] = relationship(back_populates="exclusions")


class InfeasibleJustification(Base):
    __tablename__ = "infeasible_justifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("coverage_snapshots.id"), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(50), nullable=False)
    flip_direction: Mapped[str] = mapped_column(String(5), nullable=False)  # "F2T" | "T2F"
    z3_proof: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    signed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    signed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    snapshot: Mapped[CoverageSnapshot] = relationship(back_populates="justifications")


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    req_id: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    links: Mapped[list[RequirementLink]] = relationship(back_populates="requirement", cascade="all, delete-orphan")


class RequirementLink(Base):
    __tablename__ = "requirement_links"

    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), primary_key=True)
    test_case_id: Mapped[str] = mapped_column(ForeignKey("test_cases.id"), primary_key=True)
    linked_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    linked_by: Mapped[str] = mapped_column(String(255), default="user")

    requirement: Mapped[Requirement] = relationship(back_populates="links")
    test_case: Mapped[TestCase] = relationship(back_populates="requirement_links")


class LLMConfig(Base):
    __tablename__ = "llm_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(20), default="openai")     # "openai" | "anthropic" | "ollama"
    model: Mapped[str] = mapped_column(String(100), default="gpt-4.1-mini")
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    base_url: Mapped[str] = mapped_column(Text, default="http://localhost:11434")
    max_iterations: Mapped[int] = mapped_column(Integer, default=50)
    token_budget: Mapped[int] = mapped_column(Integer, default=100000)
    total_tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )
