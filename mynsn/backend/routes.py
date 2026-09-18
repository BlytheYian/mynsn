"""mynsn 的簡化 API：把新手需要的操作濃縮成 analyze / generate / jobs 三組端點，
內部都是呼叫既有的 ifl_api，不重做核心邏輯。"""
from __future__ import annotations

import json
import os
import secrets
import time

import httpx
import websockets
from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse

from . import codegen, github_client, ifl_client, storage, suggest
from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ExplainGapRequest,
    ExplainGapResponse,
    ExtractRequest,
    ExtractResponse,
    FunctionInfo,
    GenerateCodeRequest,
    GenerateCodeResponse,
    GenerateRequest,
    LLMSettings,
    SuggestParamsRequest,
    SuggestParamsResponse,
    TestConnectionRequest,
    TestConnectionResponse,
)

router = APIRouter(prefix="/api")

# 需要先在 GitHub 註冊一個 OAuth App，把 Client ID / Secret 填進這兩個環境變數。
# Authorization callback URL 必須跟 GITHUB_OAUTH_REDIRECT_URI 完全一致。
GITHUB_OAUTH_CLIENT_ID = os.environ.get("GITHUB_OAUTH_CLIENT_ID", "")
GITHUB_OAUTH_CLIENT_SECRET = os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", "")
GITHUB_OAUTH_REDIRECT_URI = os.environ.get(
    "GITHUB_OAUTH_REDIRECT_URI", "http://127.0.0.1:8200/api/github/oauth/callback"
)

# job_id → {"func_name": str}：ifl_api 的 job 清單不含函式名稱，這裡補存給「歷史紀錄」頁用。
# 純記憶體、跟 ifl_api 自己的 JobStore 一樣不做持久化。
_JOB_META: dict[str, dict[str, str]] = {}


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    func_names = suggest.list_functions(req.source_code)
    if not func_names:
        raise HTTPException(400, "找不到任何函式，請確認貼上的是完整的 Python 函式定義")

    functions: list[FunctionInfo] = []
    for name in func_names:
        param_types = suggest.infer_param_types(req.source_code, name)
        functions.append(
            FunctionInfo(
                name=name,
                signature=suggest.build_signature(name, param_types),
                param_types=param_types,
                suggested_bounds=suggest.suggest_bounds(req.source_code, param_types),
                free_vars=suggest.list_free_vars(req.source_code, name),
            )
        )

    decision_count = 0
    condition_count = 0
    warning = None
    try:
        parsed = await ifl_client.parse_source(req.source_code)
        nodes = parsed.get("decision_nodes", [])
        decision_count = len(nodes)
        condition_count = sum(n["condition_set"]["k"] for n in nodes)
    except httpx.HTTPError as exc:
        warning = f"無法連線到核心引擎 API（{ifl_client.IFL_API_BASE_URL}），已略過判斷式分析：{exc}"

    return AnalyzeResponse(
        functions=functions,
        decision_count=decision_count,
        condition_count=condition_count,
        warning=warning,
    )


@router.post("/extract-function", response_model=ExtractResponse)
async def extract_function(req: ExtractRequest) -> ExtractResponse:
    extracted = suggest.extract_function_source(req.source_code, req.func_name)
    if extracted is None:
        raise HTTPException(404, f"在程式碼中找不到函式 {req.func_name}")
    return ExtractResponse(source_code=extracted)


@router.post("/generate-code", response_model=GenerateCodeResponse)
async def generate_code(req: GenerateCodeRequest) -> GenerateCodeResponse:
    if not req.description.strip():
        raise HTTPException(400, "請輸入描述")
    if req.llm.provider != "ollama" and not req.llm.api_key:
        raise HTTPException(400, "請輸入 API 金鑰")
    try:
        code = await codegen.generate_from_description(
            req.description, req.llm.provider, req.llm.model, req.llm.api_key, req.llm.base_url
        )
    except SyntaxError as exc:
        raise HTTPException(502, f"AI 產生的程式碼有語法錯誤，請換個描述或重試：{exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"呼叫 AI 失敗：{exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"無法連線到 AI 服務：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return GenerateCodeResponse(source_code=code)


@router.post("/test-llm-connection", response_model=TestConnectionResponse)
async def test_llm_connection(req: TestConnectionRequest) -> TestConnectionResponse:
    if req.provider != "ollama" and not req.api_key:
        return TestConnectionResponse(ok=False, message="請先輸入 API 金鑰")
    ok, message = await codegen.test_connection(req.provider, req.api_key, req.base_url)
    return TestConnectionResponse(ok=ok, message=message)


@router.post("/suggest-params", response_model=SuggestParamsResponse)
async def suggest_params(req: SuggestParamsRequest) -> SuggestParamsResponse:
    # 先用既有的 ast 分析拿到「真正的」參數名稱與型別當底——AI 只負責錦上添花
    # （猜更合理的數值範圍、補一句情境描述），AI 回傳格式不對或漏掉某個參數時
    # 都退回這組既有的規則式結果，不會讓整個功能因為 AI 亂回而掛掉。
    fn_types = suggest.infer_param_types(req.source_code, req.func_name)
    if not fn_types:
        raise HTTPException(404, f"在程式碼中找不到函式 {req.func_name} 或它沒有參數")
    if req.llm.provider != "ollama" and not req.llm.api_key:
        raise HTTPException(400, "請先在「設定」頁面填 API 金鑰")

    fallback_bounds = suggest.suggest_bounds(req.source_code, fn_types)
    try:
        raw = await codegen.suggest_params_from_code(
            req.source_code,
            req.func_name,
            list(fn_types.keys()),
            req.llm.provider,
            req.llm.model,
            req.llm.api_key,
            req.llm.base_url,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"呼叫 AI 失敗：{exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"無法連線到 AI 服務：{exc}") from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(502, f"AI 回傳的格式看不懂，請重試：{exc}") from exc

    ai_params = raw.get("params") if isinstance(raw, dict) else None
    domain_types: dict[str, str] = {}
    domain_bounds: dict[str, list[int]] = {}
    for name, fallback_type in fn_types.items():
        info = ai_params.get(name) if isinstance(ai_params, dict) else None
        info = info if isinstance(info, dict) else {}
        typ = info.get("type")
        domain_types[name] = typ if typ in ("int", "bool", "float") else fallback_type
        lo, hi = info.get("min"), info.get("max")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            domain_bounds[name] = [int(lo), int(hi)]
        else:
            domain_bounds[name] = fallback_bounds.get(name, [0, 100])

    domain_context = raw.get("domain_context", "") if isinstance(raw, dict) else ""
    return SuggestParamsResponse(
        domain_types=domain_types,
        domain_bounds=domain_bounds,
        domain_context=str(domain_context)[:300],
    )


@router.post("/explain-gap", response_model=ExplainGapResponse)
async def explain_gap(req: ExplainGapRequest) -> ExplainGapResponse:
    if req.llm.provider != "ollama" and not req.llm.api_key:
        raise HTTPException(400, "請先在「設定」頁面填 API 金鑰")
    try:
        text = await codegen.explain_gap(
            req.source_code,
            req.func_name,
            req.condition_expr,
            req.flip_direction,
            req.status,
            req.llm.provider,
            req.llm.model,
            req.llm.api_key,
            req.llm.base_url,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"呼叫 AI 失敗：{exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"無法連線到 AI 服務：{exc}") from exc
    return ExplainGapResponse(explanation=text.strip())


def build_job_payload(
    source_code: str,
    func_name: str,
    func_signature: str,
    domain_context: str,
    domain_types: dict[str, str],
    domain_bounds: dict[str, list[int]],
    max_iterations: int,
    llm: LLMSettings,
) -> dict:
    """組出 ifl_api /v1/jobs 要的 payload——`/generate`（不記環境）跟
    `environments_routes.py` 的 `/api/environments/{id}/runs`（記環境＋執行歷程）
    共用同一套組法，不要各自維護一份容易兜不齊。

    MC/DC 是針對單一函式算覆蓋率，一定要先切出目標函式本身再送進引擎——
    如果整份多函式檔案原封不動丟進去，引擎解析出的決策節點會涵蓋「所有」函式，
    但測試執行時只呼叫目標函式，其他函式的條件永遠測不到，覆蓋率會被拉低甚至
    卡到迭代上限也無法收斂。找不到就退回原始碼（例如使用者一開始就只貼了單一函式）。
    """
    extracted = suggest.extract_function_source(source_code, func_name)
    source_for_engine = extracted if extracted is not None else source_code

    return {
        "language": "python",
        "source_code": source_for_engine,
        "func_name": func_name,
        "func_signature": func_signature,
        "domain_context": domain_context,
        "domain_types": domain_types,
        "domain_bounds": domain_bounds,
        "preceding_direction": "sequential",
        "min_initial_random": 6,
        "max_iterations": max_iterations,
        "min_coverage": 1.0,
        "scenarios": [],
        "llm": {
            "provider": llm.provider,
            "model": llm.model,
            "base_url": llm.base_url,
            "temperature": 0.9,
            "num_ctx": 8192,
        },
    }


@router.post("/generate")
async def generate(req: GenerateRequest) -> dict:
    payload = build_job_payload(
        req.source_code, req.func_name, req.func_signature, req.domain_context,
        req.domain_types, req.domain_bounds, req.max_iterations, req.llm,
    )
    try:
        job = await ifl_client.create_job(payload, req.llm.api_key)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"無法連線到核心引擎 API：{exc}") from exc

    _JOB_META[job["job_id"]] = {"func_name": req.func_name}
    return job


@router.get("/jobs")
async def job_list() -> list[dict]:
    try:
        jobs = await ifl_client.list_jobs()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"無法連線到核心引擎 API：{exc}") from exc

    for job in jobs:
        job["func_name"] = _JOB_META.get(job["job_id"], {}).get("func_name", "")
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return jobs


@router.get("/jobs/{job_id}")
async def job_status(job_id: str) -> dict:
    try:
        return await ifl_client.get_job(job_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"無法連線到核心引擎 API：{exc}") from exc


@router.get("/jobs/{job_id}/result")
async def job_result(job_id: str) -> dict:
    try:
        raw = await ifl_client.get_job_result(job_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"無法連線到核心引擎 API：{exc}") from exc

    # 如果這個 job 是某個「環境」底下送出的 run，順便把結果整包存進 mynsn 自己
    # 的 sqlite（ifl_api 的 JobStore 是純記憶體＋1 小時 TTL，過了就沒了）。
    # 已經存過的話 snapshot_run_result_by_ifl_job_id 內部會直接跳過，這裡不用先判斷。
    storage.snapshot_run_result_by_ifl_job_id(job_id, "succeeded", raw)

    return build_result_summary(raw)


def build_result_summary(raw: dict) -> dict:
    """把 ifl_api 的原始 job 結果包成前端要的 {summary, raw} 形狀——
    `/api/jobs/{id}/result`（即時代理）跟 `/api/runs/{id}`（讀存檔快照）都要用同一套算法。
    """
    total = len(raw.get("test_suite", []))
    coverage_pct = round(raw.get("final_coverage", 0.0) * 100, 1)
    converged = bool(raw.get("converged", False))
    headline = (
        f"已產生 {total} 組測試資料，達成 {coverage_pct}% MC/DC 覆蓋率"
        if converged
        else f"已產生 {total} 組測試資料，覆蓋率 {coverage_pct}%（在迭代上限內尚未完全達標）"
    )
    summary = {
        "converged": converged,
        "coverage_pct": coverage_pct,
        "test_case_count": total,
        "iteration_count": raw.get("iteration_count", 0),
        "headline": headline,
    }
    return {"summary": summary, "raw": raw}


@router.delete("/jobs/{job_id}")
async def job_cancel(job_id: str) -> dict:
    try:
        return await ifl_client.cancel_job(job_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"無法連線到核心引擎 API：{exc}") from exc


# ── GitHub 匯入 ──────────────────────────────────────────────────────────────
# access token 由前端跑完 Device Flow 後取得，透過 X-GitHub-Token header 傳遞、
# 後端完全不落地儲存（跟 LLM API 金鑰同一套模式）。


def _require_token(x_github_token: str) -> str:
    if not x_github_token:
        raise HTTPException(401, "請先登入 GitHub")
    return x_github_token


def _map_github_error(exc: Exception) -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return HTTPException(401, "GitHub token 無效或已過期")
        if status == 403:
            return HTTPException(403, "GitHub 拒絕存取（可能是 token 權限不足或超過速率限制）")
        if status == 404:
            return HTTPException(404, "找不到指定的 repo／分支／檔案")
        return HTTPException(status, f"GitHub API 錯誤：{exc.response.text}")
    return HTTPException(502, f"無法連線到 GitHub：{exc}")


# state 只用來防 CSRF，記憶體存放、有 TTL；伺服器重啟或多個 worker 就會失效，
# 對這種單人本機工具來說是可接受的取捨。
_OAUTH_STATES: dict[str, float] = {}
_STATE_TTL_SECONDS = 600.0


def _new_oauth_state() -> str:
    now = time.time()
    for s, created in list(_OAUTH_STATES.items()):
        if now - created > _STATE_TTL_SECONDS:
            del _OAUTH_STATES[s]
    state = secrets.token_urlsafe(24)
    _OAUTH_STATES[state] = now
    return state


def _consume_oauth_state(state: str) -> bool:
    return _OAUTH_STATES.pop(state, None) is not None


def _oauth_popup_result(payload: dict) -> HTMLResponse:
    """回一個小頁面：把結果用 postMessage 傳回開啟它的視窗（主頁面），然後自動關閉自己。
    這個頁面只在彈出視窗裡短暫出現，使用者幾乎看不到。"""
    return HTMLResponse(f"""<!DOCTYPE html>
<html><body style="font-family:system-ui;color:#64748b;padding:24px;">
登入處理完成，這個視窗會自動關閉…
<script>
  if (window.opener) {{
    window.opener.postMessage({json.dumps(payload)}, window.location.origin);
  }}
  window.close();
</script>
</body></html>""")


@router.get("/github/oauth/login")
async def github_oauth_login():
    if not GITHUB_OAUTH_CLIENT_ID or not GITHUB_OAUTH_CLIENT_SECRET:
        return _oauth_popup_result({
            "type": "github-oauth-error",
            "error": "伺服器尚未設定 GITHUB_OAUTH_CLIENT_ID / GITHUB_OAUTH_CLIENT_SECRET，"
                     "請先在 GitHub 註冊 OAuth App 並設定環境變數後重啟服務。",
        })
    state = _new_oauth_state()
    url = github_client.build_authorize_url(GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_REDIRECT_URI, state)
    return RedirectResponse(url)


@router.get("/github/oauth/callback")
async def github_oauth_callback(code: str = "", state: str = "", error: str = "") -> HTMLResponse:
    if error:
        return _oauth_popup_result({"type": "github-oauth-error", "error": f"GitHub 拒絕授權：{error}"})
    if not state or not _consume_oauth_state(state):
        return _oauth_popup_result({"type": "github-oauth-error", "error": "驗證失敗，請重新登入"})
    if not code:
        return _oauth_popup_result({"type": "github-oauth-error", "error": "GitHub 沒有回傳授權碼"})

    try:
        token_data = await github_client.exchange_code_for_token(
            GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, code, GITHUB_OAUTH_REDIRECT_URI
        )
        if "access_token" not in token_data:
            raise ValueError(token_data.get("error_description") or token_data.get("error") or "未知錯誤")
        token = token_data["access_token"]
        user = await github_client.get_authenticated_user(token)
    except Exception as exc:  # noqa: BLE001
        return _oauth_popup_result({"type": "github-oauth-error", "error": str(exc)})

    return _oauth_popup_result({"type": "github-oauth-success", "token": token, "user": user})


@router.get("/github/repos")
async def github_repos(x_github_token: str = Header("", alias="X-GitHub-Token")) -> list[dict]:
    token = _require_token(x_github_token)
    try:
        return await github_client.list_repos(token)
    except Exception as exc:  # noqa: BLE001
        raise _map_github_error(exc) from exc


@router.get("/github/repos/{owner}/{repo}/branches")
async def github_branches(
    owner: str, repo: str, x_github_token: str = Header("", alias="X-GitHub-Token")
) -> list[str]:
    token = _require_token(x_github_token)
    try:
        return await github_client.list_branches(token, owner, repo)
    except Exception as exc:  # noqa: BLE001
        raise _map_github_error(exc) from exc


@router.get("/github/repos/{owner}/{repo}/files")
async def github_files(
    owner: str,
    repo: str,
    branch: str,
    x_github_token: str = Header("", alias="X-GitHub-Token"),
) -> list[str]:
    token = _require_token(x_github_token)
    try:
        return await github_client.list_python_files(token, owner, repo, branch)
    except Exception as exc:  # noqa: BLE001
        raise _map_github_error(exc) from exc


@router.get("/github/repos/{owner}/{repo}/content")
async def github_content(
    owner: str,
    repo: str,
    path: str,
    branch: str,
    x_github_token: str = Header("", alias="X-GitHub-Token"),
) -> dict:
    token = _require_token(x_github_token)
    try:
        content = await github_client.get_file_content(token, owner, repo, path, branch)
    except Exception as exc:  # noqa: BLE001
        raise _map_github_error(exc) from exc
    return {"path": path, "content": content}


@router.websocket("/jobs/{job_id}/stream")
async def job_stream(ws: WebSocket, job_id: str) -> None:
    """把前端的 WebSocket 連線接到 ifl_api 既有的 /v1/jobs/{id}/stream，單純轉發事件。"""
    await ws.accept()
    upstream_url = f"{ifl_client.IFL_API_WS_BASE_URL}/v1/jobs/{job_id}/stream"
    try:
        async with websockets.connect(upstream_url) as upstream:
            async for raw in upstream:
                await ws.send_text(raw if isinstance(raw, str) else raw.decode())
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if event.get("type") == "done":
                    # 結果快照的另一個掛勾點：這裡是唯一「不論成功／失敗／取消都一定
                    # 會經過」的地方（前端只有成功時才會另外呼叫 GET .../result），
                    # 所以失敗／取消的 run 狀態要在這裡更新，不然會永遠卡在 running。
                    status = event.get("status") or "failed"
                    if status == "succeeded":
                        try:
                            result = await ifl_client.get_job_result(job_id)
                            storage.snapshot_run_result_by_ifl_job_id(job_id, "succeeded", result)
                        except httpx.HTTPError:
                            pass
                    else:
                        storage.update_run_status_by_ifl_job_id(job_id, status)
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001  # 任何上游連線問題都要讓前端知道，而不是無聲卡住
        try:
            await ws.send_json({"type": "error", "detail": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass
