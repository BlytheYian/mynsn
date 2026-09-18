"""自然語言 → Python 函式，給不熟悉程式的使用者用。

直接呼叫 LLM provider 的 REST API，不透過 ifl_mcdc／ifl_api——那兩邊都沒有「生成任意
程式碼」這個功能，ifl_mcdc 的 LLMSampler 是設計來輸出結構化 JSON 測試案例，不是拿來
寫函式本身的。呼叫方式比照現有 mynsn 的風格（httpx 直接打 API，不裝額外 SDK）。
"""
from __future__ import annotations

import ast
import json
import re

import httpx

_SYSTEM_PROMPT = (
    "你是一個 Python 程式碼生成助手。根據使用者的自然語言描述，寫出「一個」Python 函式，"
    "函式內要包含至少一個判斷式（if），盡量用多個布林條件以 and/or 組合——這段程式碼會拿去"
    "做 MC/DC 覆蓋率測試，條件越明確、參數型別越清楚越好，建議幫參數加上型別註解"
    "（int / bool / float）。只回傳程式碼本身，不要任何說明文字、不要 markdown code fence。"
)

_TIMEOUT = httpx.Timeout(60.0, read=60.0)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:python)?\s*\n(.*)\n```$", text, re.DOTALL)
    return match.group(1).strip() if match else text


def _validate_python(code: str) -> None:
    ast.parse(code)  # 語法有誤就丟 SyntaxError，讓呼叫端轉成友善訊息


async def generate_from_description(
    description: str, provider: str, model: str, api_key: str, base_url: str
) -> str:
    raw = await _call_llm(description, provider, model, api_key, base_url, _SYSTEM_PROMPT)
    code = _strip_code_fence(raw)
    _validate_python(code)
    return code


_SUGGEST_PARAMS_SYSTEM_PROMPT = (
    "你是程式測試助手。使用者會給你一個 Python 函式的原始碼，以及要判斷的參數名稱清單。"
    "針對「每一個」列出的參數，判斷它的型別（int / bool / float）與適合拿來做邊界測試的"
    "整數最小值、最大值，並用一句話描述這個函式在做什麼樣的判斷（給另一個 AI 產生測試案例"
    "時當背景資訊用）。只回傳以下格式的 JSON，不要任何說明文字、不要 markdown code fence："
    '{"domain_context": "一句話描述", "params": {"參數名": {"type": "int", "min": 0, "max": 100}}}'
    "（布林參數的 min/max 固定填 0、1）。"
)


async def suggest_params_from_code(
    source_code: str,
    func_name: str,
    param_names: list[str],
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
) -> dict:
    user_content = (
        f"函式：\n{source_code}\n\n只需要判斷這幾個參數：{', '.join(param_names)}"
    )
    raw = await _call_llm(user_content, provider, model, api_key, base_url, _SUGGEST_PARAMS_SYSTEM_PROMPT)
    return json.loads(_strip_code_fence(raw))


_EXPLAIN_GAP_SYSTEM_PROMPT = (
    "你是程式測試助手。使用者會給你一個 Python 函式，以及 MC/DC 覆蓋率分析中「沒有成功覆蓋」的"
    "一組條件配對——某個條件要從 True 翻到 False（或 False 翻到 True），同時讓其他條件保持不變、"
    "使整體判斷結果跟著翻轉，但引擎沒能生成滿足這個配對的測試案例。請根據函式邏輯，用不超過三句"
    "白話中文解釋這組配對可能為什麼沒被滿足（例如：這個條件跟另一個條件在邏輯上互相牽制、數值範圍"
    "彼此衝突、根本不可能同時成立等），如果看得出有辦法讓它可能被滿足，也順便簡短建議。只回傳純"
    "文字說明，不要 markdown、不要條列、不要重複覆述題目。"
)

_STATUS_LABELS = {
    "infeasible": "已被引擎證明在邏輯上不可能達成（結構性不可行）",
    "gate_exhausted": "引擎多次嘗試生成測試案例，但都沒有通過驗證，最後放棄",
    "uncovered": "在迭代次數用完之前，引擎還沒有嘗試到這組配對",
}


async def explain_gap(
    source_code: str,
    func_name: str,
    condition_expr: str,
    flip_direction: str,
    status: str,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
) -> str:
    direction_label = "True → False" if flip_direction == "T2F" else "False → True"
    status_label = _STATUS_LABELS.get(status, status)
    user_content = (
        f"函式 {func_name}：\n{source_code}\n\n"
        f"沒有覆蓋成功的條件配對：{condition_expr}（{direction_label}）\n狀態：{status_label}"
    )
    return await _call_llm(user_content, provider, model, api_key, base_url, _EXPLAIN_GAP_SYSTEM_PROMPT)


async def _call_llm(
    content: str, provider: str, model: str, api_key: str, base_url: str, system_prompt: str
) -> str:
    if provider == "openai":
        return await _call_openai(content, model, api_key, system_prompt)
    if provider == "anthropic":
        return await _call_anthropic(content, model, api_key, system_prompt)
    if provider == "groq":
        return await _call_groq(content, model, api_key, system_prompt)
    if provider == "ollama":
        return await _call_ollama(content, model, base_url, system_prompt)
    raise ValueError(f"不支援的 provider：{provider}")


async def _call_openai(content: str, model: str, api_key: str, system_prompt: str) -> str:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# Groq 的 REST API 跟 OpenAI 相容（同一套 chat/completions 格式），只是換一個 base URL，
# 所以請求／回應解析直接照抄 _call_openai，不需要另外寫一套解析邏輯。
async def _call_groq(content: str, model: str, api_key: str, system_prompt: str) -> str:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def _call_anthropic(content: str, model: str, api_key: str, system_prompt: str) -> str:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": [{"role": "user", "content": content}],
            },
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]


async def _call_ollama(content: str, model: str, base_url: str, system_prompt: str) -> str:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                "stream": False,
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


# ── 設定頁「測試連線」：只驗證金鑰／位址有沒有效，不產生任何程式碼、不花 token ──
_TEST_TIMEOUT = httpx.Timeout(15.0, read=15.0)


async def test_connection(provider: str, api_key: str, base_url: str) -> tuple[bool, str]:
    try:
        if provider == "openai":
            async with httpx.AsyncClient(timeout=_TEST_TIMEOUT) as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
            return True, "連線成功，金鑰有效"
        if provider == "anthropic":
            async with httpx.AsyncClient(timeout=_TEST_TIMEOUT) as client:
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                )
                resp.raise_for_status()
            return True, "連線成功，金鑰有效"
        if provider == "groq":
            async with httpx.AsyncClient(timeout=_TEST_TIMEOUT) as client:
                resp = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
            return True, "連線成功，金鑰有效"
        if provider == "ollama":
            async with httpx.AsyncClient(timeout=_TEST_TIMEOUT) as client:
                resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
                resp.raise_for_status()
            return True, "連線成功，Ollama 伺服器有回應"
        return False, f"不支援的 provider：{provider}"
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            return False, "連線失敗：金鑰無效或已過期"
        return False, f"連線失敗（HTTP {status}）：{exc.response.text[:200]}"
    except httpx.HTTPError as exc:
        return False, f"連線失敗：{exc}"
