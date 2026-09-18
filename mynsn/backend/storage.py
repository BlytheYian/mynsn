"""環境（Environment）持久化：輕量 sqlite3（標準庫），維持 mynsn 一貫的薄後端風格，
不引入 ORM／新依賴。

三張表：
  environments — 一份原始碼＋測試設定（函式名稱、domain_types/bounds…）
  versions     — 環境的原始碼歷程；同一份原始碼（sha256 相同）不會重複建版本
  runs         — 每次針對某個環境、某個版本送出的測試生成，完成後把 ifl_api 的完整
                 結果整包存進 result_json——ifl_api 自己的 JobStore 是純記憶體＋1 小時
                 TTL（見 ifl_api/jobs.py），run 完成後這裡存的資料才是真正持久的。

所有函式都用「一次呼叫開一條連線、做完就關」的方式，不維護長駐連線／連線池——
這是單人本機工具，不需要處理連線併發，圖的是簡單、不會有奇怪的執行緒共用問題。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parent / "data" / "mynsn.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS environments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'python',
    test_type TEXT NOT NULL DEFAULT 'mcdc',
    func_name TEXT NOT NULL,
    func_signature TEXT NOT NULL,
    domain_context TEXT DEFAULT '',
    domain_types TEXT NOT NULL,
    domain_bounds TEXT NOT NULL,
    current_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY,
    env_id TEXT NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    source_code TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    env_id TEXT NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES versions(id),
    ifl_job_id TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    llm_provider TEXT,
    llm_model TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_versions_env ON versions(env_id);
CREATE INDEX IF NOT EXISTS idx_runs_env ON runs(env_id);
CREATE INDEX IF NOT EXISTS idx_runs_ifl_job ON runs(ifl_job_id);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _session() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        with conn:  # 交易範圍：成功自動 commit、例外自動 rollback
            yield conn
    finally:
        conn.close()


# 給「_SCHEMA 建表時就有」之後才新增的欄位用的手動 migration——CREATE TABLE IF NOT
# EXISTS 不會幫已存在的舊表補欄位，所以新欄位要額外用 ALTER TABLE 補上。用
# try/except 包起來讓它可以重複執行：欄位已存在時 sqlite 會丟錯，直接忽略即可。
_MIGRATIONS = [
    "ALTER TABLE environments ADD COLUMN test_type TEXT NOT NULL DEFAULT 'mcdc'",
]


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # 欄位已存在（舊資料庫升級後常見），忽略即可
    finally:
        conn.close()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    return uuid.uuid4().hex


def _hash_source(source_code: str) -> str:
    return hashlib.sha256(source_code.encode("utf-8")).hexdigest()


# ── environments ─────────────────────────────────────────────────────────────


def create_environment(
    name: str,
    language: str,
    func_name: str,
    func_signature: str,
    domain_context: str,
    domain_types: dict[str, str],
    domain_bounds: dict[str, list[int]],
    source_code: str,
    test_type: str = "mcdc",
) -> str:
    """建立新環境＋初始版本，回傳 env_id。"""
    env_id = _new_id()
    version_id = _new_id()
    now = _now()
    with _session() as conn:
        # 先建 environments（current_version_id 暫時留 NULL）再建 versions，
        # 順序不能反過來——versions.env_id 有外鍵約束，指向的 environments 列
        # 必須先存在，不然會踩 FOREIGN KEY constraint failed。
        conn.execute(
            """INSERT INTO environments
               (id, name, language, test_type, func_name, func_signature, domain_context,
                domain_types, domain_bounds, current_version_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                env_id, name, language, test_type, func_name, func_signature, domain_context,
                json.dumps(domain_types), json.dumps(domain_bounds), None, now, now,
            ),
        )
        conn.execute(
            "INSERT INTO versions (id, env_id, source_code, source_hash, created_at) VALUES (?,?,?,?,?)",
            (version_id, env_id, source_code, _hash_source(source_code), now),
        )
        conn.execute(
            "UPDATE environments SET current_version_id=? WHERE id=?", (version_id, env_id)
        )
    return env_id


def _row_to_environment(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "language": row["language"],
        "test_type": row["test_type"],
        "func_name": row["func_name"],
        "func_signature": row["func_signature"],
        "domain_context": row["domain_context"],
        "domain_types": json.loads(row["domain_types"]),
        "domain_bounds": json.loads(row["domain_bounds"]),
        "current_version_id": row["current_version_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_environment(env_id: str) -> dict[str, Any] | None:
    """回傳環境詳情，含目前版本的原始碼（source_code 欄位）。"""
    with _session() as conn:
        row = conn.execute("SELECT * FROM environments WHERE id=?", (env_id,)).fetchone()
        if row is None:
            return None
        env = _row_to_environment(row)
        ver = conn.execute(
            "SELECT source_code FROM versions WHERE id=?", (row["current_version_id"],)
        ).fetchone()
        env["source_code"] = ver["source_code"] if ver else ""
        return env


def list_environments() -> list[dict[str, Any]]:
    """程式庫列表：每個環境附上最新一次執行的狀態／覆蓋率。"""
    with _session() as conn:
        rows = conn.execute("SELECT * FROM environments ORDER BY updated_at DESC").fetchall()
        envs = []
        for row in rows:
            env = _row_to_environment(row)
            latest = conn.execute(
                "SELECT id, status, result_json, finished_at FROM runs "
                "WHERE env_id=? ORDER BY created_at DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            env["latest_run"] = _run_summary(latest) if latest else None
            envs.append(env)
        return envs


def update_environment_meta(
    env_id: str,
    func_name: str,
    func_signature: str,
    domain_context: str,
    domain_types: dict[str, str],
    domain_bounds: dict[str, list[int]],
) -> None:
    """更新環境的測試設定（不含原始碼——原始碼變更走 get_or_create_version）。"""
    with _session() as conn:
        conn.execute(
            """UPDATE environments
               SET func_name=?, func_signature=?, domain_context=?, domain_types=?,
                   domain_bounds=?, updated_at=?
               WHERE id=?""",
            (
                func_name, func_signature, domain_context,
                json.dumps(domain_types), json.dumps(domain_bounds), _now(), env_id,
            ),
        )


def delete_environment(env_id: str) -> None:
    with _session() as conn:
        conn.execute("DELETE FROM environments WHERE id=?", (env_id,))


# ── versions ─────────────────────────────────────────────────────────────────


def get_or_create_version(env_id: str, source_code: str) -> tuple[str, bool]:
    """比對這次的原始碼跟目前版本的 sha256：相同就沿用既有版本（回傳 is_new=False），
    不同才新增一列版本紀錄並把它設為目前版本（is_new=True）。這是追蹤程式碼變更的機制。
    """
    new_hash = _hash_source(source_code)
    with _session() as conn:
        row = conn.execute(
            """SELECT v.id AS version_id, v.source_hash FROM environments e
               JOIN versions v ON v.id = e.current_version_id
               WHERE e.id = ?""",
            (env_id,),
        ).fetchone()
        if row is not None and row["source_hash"] == new_hash:
            return row["version_id"], False

        version_id = _new_id()
        now = _now()
        conn.execute(
            "INSERT INTO versions (id, env_id, source_code, source_hash, created_at) VALUES (?,?,?,?,?)",
            (version_id, env_id, source_code, new_hash, now),
        )
        conn.execute(
            "UPDATE environments SET current_version_id=?, updated_at=? WHERE id=?",
            (version_id, now, env_id),
        )
        return version_id, True


def list_versions(env_id: str) -> list[dict[str, Any]]:
    with _session() as conn:
        rows = conn.execute(
            "SELECT id, source_hash, created_at FROM versions WHERE env_id=? ORDER BY created_at ASC",
            (env_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_version_source(version_id: str) -> str | None:
    with _session() as conn:
        row = conn.execute("SELECT source_code FROM versions WHERE id=?", (version_id,)).fetchone()
        return row["source_code"] if row else None


def diff_versions(version_id_a: str, version_id_b: str) -> str:
    """兩個版本原始碼的 unified diff 文字（標準庫 difflib，不引入新依賴）。"""
    src_a = get_version_source(version_id_a) or ""
    src_b = get_version_source(version_id_b) or ""
    lines = difflib.unified_diff(
        src_a.splitlines(keepends=True),
        src_b.splitlines(keepends=True),
        fromfile="version_a",
        tofile="version_b",
    )
    return "".join(lines)


# ── runs ─────────────────────────────────────────────────────────────────────


def create_run(
    env_id: str, version_id: str, ifl_job_id: str | None, llm_provider: str, llm_model: str
) -> str:
    run_id = _new_id()
    with _session() as conn:
        conn.execute(
            """INSERT INTO runs (id, env_id, version_id, ifl_job_id, status, llm_provider, llm_model, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (run_id, env_id, version_id, ifl_job_id, "running", llm_provider, llm_model, _now()),
        )
    return run_id


def update_run_status(run_id: str, status: str, result: dict[str, Any] | None = None) -> None:
    with _session() as conn:
        if result is not None:
            conn.execute(
                "UPDATE runs SET status=?, finished_at=?, result_json=? WHERE id=?",
                (status, _now(), json.dumps(result), run_id),
            )
        else:
            conn.execute(
                "UPDATE runs SET status=?, finished_at=? WHERE id=?",
                (status, _now(), run_id),
            )


def snapshot_run_result_by_ifl_job_id(ifl_job_id: str, status: str, result: dict[str, Any]) -> bool:
    """結果快照的掛勾點：ifl_api job 完成、前端照舊呼叫 GET .../result 時，
    後端順便查一次這個 job_id 是不是某個 run 的 ifl_job_id、而且還沒存過結果——
    有的話就整包存下來。回傳是否真的存了（給呼叫端記 log／debug 用，非必要）。
    """
    with _session() as conn:
        row = conn.execute(
            "SELECT id FROM runs WHERE ifl_job_id=? AND result_json IS NULL",
            (ifl_job_id,),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE runs SET status=?, finished_at=?, result_json=? WHERE id=?",
            (status, _now(), json.dumps(result), row["id"]),
        )
        return True


def update_run_status_by_ifl_job_id(ifl_job_id: str, status: str) -> None:
    """給失敗／取消的終結狀態用（成功的走 snapshot_run_result_by_ifl_job_id，
    因為那個情況需要把結果整包存下來，不只是改狀態）。已經有 result_json 的
    run 不會被這裡覆蓋，避免競態把已經存好的成功結果蓋成別的狀態。"""
    with _session() as conn:
        conn.execute(
            "UPDATE runs SET status=?, finished_at=? WHERE ifl_job_id=? AND result_json IS NULL",
            (status, _now(), ifl_job_id),
        )


def _run_summary(row: sqlite3.Row) -> dict[str, Any]:
    coverage_pct = None
    if row["result_json"]:
        try:
            coverage_pct = round(json.loads(row["result_json"]).get("final_coverage", 0.0) * 100, 1)
        except (ValueError, TypeError, AttributeError):
            pass
    return {
        "id": row["id"],
        "status": row["status"],
        "coverage_pct": coverage_pct,
        "finished_at": row["finished_at"],
    }


def list_runs(env_id: str) -> list[dict[str, Any]]:
    """執行歷程列表——JOIN versions 一併帶出這次執行當時測的是哪個版本
    （version_hash），讓執行紀錄的顯示名稱可以標出對應版本，不用另外查一次。"""
    with _session() as conn:
        rows = conn.execute(
            "SELECT r.id, r.status, r.llm_provider, r.llm_model, r.created_at, r.finished_at, "
            "r.result_json, r.version_id, v.source_hash AS version_hash "
            "FROM runs r JOIN versions v ON v.id = r.version_id "
            "WHERE r.env_id=? ORDER BY r.created_at DESC",
            (env_id,),
        ).fetchall()
        out = []
        for row in rows:
            summary = _run_summary(row)
            out.append({
                **summary,
                "llm_provider": row["llm_provider"],
                "llm_model": row["llm_model"],
                "created_at": row["created_at"],
                "version_id": row["version_id"],
                "version_hash": row["version_hash"],
            })
        return out


def get_run(run_id: str) -> dict[str, Any] | None:
    with _session() as conn:
        row = conn.execute(
            "SELECT r.*, v.source_hash AS version_hash FROM runs r "
            "JOIN versions v ON v.id = r.version_id WHERE r.id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "env_id": row["env_id"],
            "version_id": row["version_id"],
            "version_hash": row["version_hash"],
            "ifl_job_id": row["ifl_job_id"],
            "status": row["status"],
            "llm_provider": row["llm_provider"],
            "llm_model": row["llm_model"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }
