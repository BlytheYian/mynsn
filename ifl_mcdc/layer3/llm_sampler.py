"""
LLM 採樣器：呼叫 LLM 後端、解析 JSON、執行 DomainValidator 驗證、重試。

依 SDD 5.2 節規格：LLMSampler 負責網路層、JSON 解析與領域驗證，
最多重試 MAX_RETRIES 次，回傳 (parsed_dict, ValidationResult)。

TC-U-47: 第一次成功回傳
TC-U-48: 解析 markdown 包裝的 JSON
TC-U-49: 第一次回傳壞 JSON → 第二次成功
TC-U-50: 全部重試失敗 → LLMSamplingError
TC-U-51: token_log 記錄每次嘗試 / 指數退避
TC-U-73: DomainValidator 驗證失敗 → 重試
"""
from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod

from ifl_mcdc.exceptions import LLMSamplingError
from ifl_mcdc.models.validation import ValidationResult

# DomainValidator 在此層引入，實現 SDD 5.2 節規格（sample 內部驗證）
from ifl_mcdc.layer3.domain_validator import DomainValidator


# ─────────────────────────────────────────────
# 共用 System Prompt（所有後端一致）
# ─────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "你是一個嚴密的軟體測試資料生成系統。"
    "你必須嚴格遵守給定的 JSON 格式與邊界條件 (interval)。"
    "💡 重要提示：在生成多個數值時，必須考慮變數之間的「商業邏輯與比例關係」"
    "（例如：貸款金額通常不會超過年薪的數倍），切勿生成互相矛盾的數值組合。"
    "只輸出合法的 JSON，不要包含任何解釋文字。"
)


# ─────────────────────────────────────────────
# LLM 後端抽象介面
# ─────────────────────────────────────────────


class LLMBackend(ABC):
    """LLM 後端統一介面。

    last_usage: 最近一次 complete() 呼叫的真實 token 用量
    {"prompt_tokens": int, "completion_tokens": int}，供呼叫端做精確計費／統計。
    後端若無法取得真實用量會保持為 None，呼叫端需自行退回估算。
    """

    last_usage: dict[str, int] | None = None

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        """呼叫 LLM 並回傳完成文字。"""
        ...


class OpenAIBackend(LLMBackend):
    """OpenAI ChatCompletion 後端。"""

    def __init__(
        self,
        model: str = "gpt-4.1-mini",
        api_key: str = "",
        temperature: float = 0.3,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.temperature = temperature

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        """呼叫 OpenAI API。需安裝 openai 套件並設定 OPENAI_API_KEY。"""
        import openai
        client = openai.OpenAI(api_key=self.api_key or None, timeout=60.0)
        resp = client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        if resp.usage is not None:
            self.last_usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
            }
        return resp.choices[0].message.content or ""


class GroqBackend(LLMBackend):
    """Groq 後端。Groq 的 REST API 跟 OpenAI 相容（同一套 chat/completions 格式），
    所以直接沿用 openai 套件、只是把 base_url 換成 Groq 的端點，不用另外裝 SDK。"""

    _BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(
        self,
        model: str = "llama-3.3-70b-versatile",
        api_key: str = "",
        temperature: float = 0.3,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.temperature = temperature

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        """呼叫 Groq API。需安裝 openai 套件並提供 Groq 的金鑰。"""
        import openai
        client = openai.OpenAI(api_key=self.api_key or None, base_url=self._BASE_URL, timeout=60.0)
        resp = client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        if resp.usage is not None:
            self.last_usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
            }
        return resp.choices[0].message.content or ""


class AnthropicBackend(LLMBackend):
    """Anthropic Messages 後端。"""

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str = "") -> None:
        self.model = model
        self.api_key = api_key

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        """呼叫 Anthropic API。需安裝 anthropic 套件並設定 ANTHROPIC_API_KEY。"""
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key or None)
        msg = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        if msg.usage is not None:
            self.last_usage = {
                "prompt_tokens": msg.usage.input_tokens,
                "completion_tokens": msg.usage.output_tokens,
            }
        block = msg.content[0]
        return block.text if hasattr(block, "text") else ""


class OllamaBackend(LLMBackend):
    """Ollama 本地推論後端 (支援 Llama 3 等)。"""

    def __init__(
        self,
        model: str = "llama3",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.3,
        num_ctx: int = 8192,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.num_ctx = num_ctx

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        """呼叫 Ollama /api/chat 介面。使用標準庫 urllib 避免額外依賴。"""
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
            },
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as response:
                if response.status != 200:
                    raise RuntimeError(f"Ollama API status: {response.status}")
                body = json.load(response)
                if "prompt_eval_count" in body or "eval_count" in body:
                    self.last_usage = {
                        "prompt_tokens": body.get("prompt_eval_count", 0),
                        "completion_tokens": body.get("eval_count", 0),
                    }
                return body.get("message", {}).get("content", "")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError(f"Ollama 找不到模型 '{self.model}'。請執行 'ollama pull {self.model}'")
            raise RuntimeError(f"Ollama HTTP 錯誤 ({e.code}): {e.reason}")
        except Exception as exc:
            raise RuntimeError(f"Ollama 連線失敗: {exc}")


class MockLLMBackend(LLMBackend):
    """測試用 Mock 後端，按順序回傳預設回應或拋出例外。"""

    def __init__(self, responses: list[str | BaseException]) -> None:
        self._responses = list(responses)
        self._index = 0

    def complete(self, prompt: str, max_tokens: int = 512) -> str:
        if self._index >= len(self._responses):
            raise LLMSamplingError("MockLLMBackend: 回應列表已耗盡")
        resp = self._responses[self._index]
        self._index += 1
        if isinstance(resp, BaseException):
            raise resp
        return resp


# ─────────────────────────────────────────────
# LLM 採樣器
# ─────────────────────────────────────────────


class LLMSampler:
    """呼叫 LLM 後端，重試解析 JSON 與領域驗證，記錄 token 消耗。

    依 SDD 5.2 節：負責網路層、JSON 解析與 DomainValidator 驗證。
    最多重試 MAX_RETRIES 次（涵蓋 JSON 解析失敗與領域驗證失敗）。
    """

    MAX_RETRIES: int = 3

    def __init__(
        self,
        backend: LLMBackend,
        validator: DomainValidator,
        retry_delay: float = 2.0,
    ) -> None:
        self.backend = backend
        self.validator = validator
        self.retry_delay = retry_delay
        self.token_log: list[dict[str, object]] = []
        self.repair_log: list[dict[str, object]] = []

    def sample(
        self,
        prompt: str,
        fallback_values: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], ValidationResult]:
        """最多重試 MAX_RETRIES 次，回傳第一個通過 DomainValidator 的 (dict, ValidationResult)。

        重試觸發條件：JSON 解析失敗、網路錯誤、領域驗證失敗。

        MAX_RETRIES 次全部失敗後，若提供 fallback_values（通常是 SMT 的 model_python，
        保證滿足 domain_bounds），只針對「最後一次驗證仍違規的欄位」用 fallback_values
        覆蓋，其餘欄位保留 LLM 的選擇——避免整包退化成 Z3 的預設解而犧牲多樣性，
        同時確保單一 gap 不會因為 LLM 持續生不出合法內容而卡死整個 IFL 迭代迴圈。

        Args:
            prompt: Gap-Guided Prompt。
            fallback_values: 保底值（欄位名 → 合法值），僅在全部重試失敗時，
                針對違規欄位使用。None 表示不啟用最後防線（沿用舊行為）。

        Returns:
            (parsed_dict, validation_result)。

        Raises:
            LLMSamplingError: 全部重試失敗，且無 fallback_values 可修補（或修補後仍不合法）。
        """
        current_prompt = prompt
        last_error: str = ""
        last_data: dict[str, object] | None = None
        last_val_result: ValidationResult | None = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            # 第 2 次起：退避等待
            if attempt > 1:
                time.sleep(self.retry_delay * (attempt - 1))
                current_prompt = self._build_retry_prompt(prompt, last_error)

            t_start = time.time()
            self.backend.last_usage = None  # 避免讀到上一次呼叫留下的舊值
            try:
                raw = self.backend.complete(current_prompt)
            except Exception as exc:
                elapsed = time.time() - t_start
                last_error = str(exc)
                self.token_log.append(
                    {
                        "attempt": attempt,
                        "elapsed": elapsed,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    }
                )
                continue

            elapsed = time.time() - t_start
            usage = self.backend.last_usage
            if usage is not None:
                # 後端回報的真實 token 數（OpenAI/Anthropic usage 欄位、Ollama prompt_eval_count/eval_count）
                prompt_tokens = usage["prompt_tokens"]
                completion_tokens = usage["completion_tokens"]
            else:
                # 後端無法提供真實用量時的最後備援：字元數粗略估算，且 prompt+completion 都要算
                prompt_tokens = len(current_prompt) // 4
                completion_tokens = len(raw) // 4
            self.token_log.append(
                {
                    "attempt": attempt,
                    "elapsed": elapsed,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }
            )

            data, parse_error = self._parse_json(raw)
            if data is None:
                last_error = f"JSON 解析失敗：{parse_error}"
                continue

            # ==========================================
            # 🌟 [重要修正] 強制型別預處理 (Type Defense)
            # 解決 Gemma 等模型喜歡把布林/數字加上引號，或輸出長浮點數的問題
            # ==========================================
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, str):
                        v_lower = v.lower().strip()
                        # 1. 處理字串型態的布林值
                        if v_lower == "true":
                            data[k] = True
                            continue
                        elif v_lower == "false":
                            data[k] = False
                            continue
                        
                        # 2. 處理字串型態的數字 (例如 "483.33")
                        try:
                            if "." in v_lower:
                                data[k] = float(v_lower)
                            else:
                                data[k] = int(v_lower)
                        except ValueError:
                            pass
                    
                    # 3. 強制將浮點數四舍五入轉成整數 (解決 483.333 的問題)
                    # 注意：要排除 bool，因為在 Python 中 bool 也是一種 int/float
                    if isinstance(data.get(k), float) and not isinstance(data.get(k), bool):
                        data[k] = round(data[k])  # 用 round 而不是 int，避免精度損失
            # ==========================================

            # 下面這行是原本就有的，把它接在後面
            val_result = self.validator.validate(json.dumps(data))
            if not val_result.passed:
                last_error = f"領域驗證失敗：{val_result.to_corrective_prompt()}"
                last_data, last_val_result = data, val_result
                continue

            return data, val_result

        # 🌟 最後防線：MAX_RETRIES 次全部失敗，且最後一次至少解析出合法 JSON
        # （last_data 不為 None）時，只覆蓋仍違規的欄位，其餘欄位保留 LLM 的選擇。
        # 不會在「連 JSON 都解析不出來」的情況下觸發——那種情況沒有 data 可修補。
        if fallback_values and last_data is not None and last_val_result is not None:
            patched = dict(last_data)
            patched_fields = [
                v.field for v in last_val_result.violations if v.field in fallback_values
            ]
            for field in patched_fields:
                patched[field] = fallback_values[field]
            if patched_fields:
                retry_val = self.validator.validate(json.dumps(patched))
                if retry_val.passed:
                    self.repair_log.append({"patched_fields": patched_fields})
                    return patched, retry_val

        # 🌟 [BUG 修復] 所有重試都失敗時，應拋出異常而不是隱式返回 None
        raise LLMSamplingError(f"LLM 採樣失敗 (已重試 {self.MAX_RETRIES} 次)：最後錯誤 = {last_error}")

    def sample_qualitative(
        self,
        prompt: str,
        domain_types: dict[str, str],
    ) -> tuple[dict[str, object], dict[str, object]]:
        """呼叫 LLM 取得「情境偏好」輸出，回傳 (bool_values, int_preferences)。

        LLM 輸出格式：
        {
          "scenario": "...",
          "preferences": {
            "var_bool": true/false,
            "var_int": "low"/"medium"/"high"
          }
        }

        Args:
            prompt: 由 build_qualitative 建構的偏好式提示詞。
            domain_types: var_name → "int" | "bool" 的映射。

        Returns:
            (bool_values, int_preferences)：
              bool_values   = {var_name: True/False}
              int_preferences = {var_name: "low"/"medium"/"high"}

        Raises:
            LLMSamplingError: 重試後仍無法解析有效偏好。
        """
        VALID_INT_PREFS = {"low", "medium", "high"}
        last_error = ""

        for attempt in range(1, self.MAX_RETRIES + 1):
            if attempt > 1:
                time.sleep(self.retry_delay * (attempt - 1))

            try:
                raw = self.backend.complete(prompt)
            except Exception as exc:
                last_error = str(exc)
                continue

            data, err = self._parse_json(raw)
            if data is None:
                last_error = f"JSON 解析失敗：{err}"
                continue

            prefs_raw = data.get("preferences", data)  # 相容 LLM 直接輸出 flat dict
            if not isinstance(prefs_raw, dict):
                last_error = "preferences 欄位非 dict"
                continue

            bool_values: dict[str, object] = {}
            int_preferences: dict[str, object] = {}
            parse_ok = True

            for var_name, var_type in domain_types.items():
                val = prefs_raw.get(var_name)
                if val is None:
                    continue  # 缺少的變數由 SMT 自由決定

                if var_type == "bool":
                    if isinstance(val, bool):
                        bool_values[var_name] = val
                    elif isinstance(val, str) and val.lower() in ("true", "false"):
                        bool_values[var_name] = val.lower() == "true"
                    else:
                        last_error = f"{var_name} 布林值無效：{val!r}"
                        parse_ok = False
                        break
                else:  # int
                    if isinstance(val, str) and val.lower() in VALID_INT_PREFS:
                        int_preferences[var_name] = val.lower()
                    else:
                        last_error = f"{var_name} 偏好值無效：{val!r}（應為 low/medium/high）"
                        parse_ok = False
                        break

            if parse_ok:
                return bool_values, int_preferences

        raise LLMSamplingError(
            f"qualitative 採樣失敗（已重試 {self.MAX_RETRIES} 次）：{last_error}"
        )

    @staticmethod
    def _parse_json(raw: str) -> tuple[dict[str, object] | None, str | None]:
        """從 LLM 回應中穩健地解析 JSON 物件。

        此函式會處理常見的 LLM 輸出問題：
        1.  移除 Markdown 程式碼區塊 (```json ... ```)。
        2.  將 Python 的布林值 (True/False) 轉換為標準 JSON 的 (true/false)。
        3.  尋找 JSON 的起始 '{'，並使用 `raw_decode` 忽略結尾的雜訊。
        4.  若上述方法失敗，最後嘗試使用正規表示式提取。

        Returns:
            (dict, None) 成功；(None, error_str) 失敗。
        """
        # 步驟 1: 移除 markdown code block
        cleaned = re.sub(r"```json\s*", "", raw)
        cleaned = re.sub(r"```\s*", "", cleaned)
        cleaned = cleaned.strip()

        # 步驟 2: 修正 Python 布林值 (True/False -> true/false)
        fixed_str = cleaned.replace("True", "true").replace("False", "false").replace("None", "null")

        # 步驟 2.5: 處理 Gemma 等模型常見的尾隨逗號問題 (例如 {"a": 1,})
        fixed_str = re.sub(r",\s*(\}|\])", r"\1", fixed_str)

        # 步驟 3: 尋找 JSON 起始點並使用 raw_decode
        start_idx = fixed_str.find("{")
        if start_idx != -1:
            try:
                decoder = json.JSONDecoder()
                data, _ = decoder.raw_decode(fixed_str[start_idx:])
                if isinstance(data, dict):
                    return data, None
            except json.JSONDecodeError:
                # 如果 raw_decode 失敗，繼續嘗試下一個策略
                pass

        # 步驟 4: 最後手段，使用貪婪的正規表示式提取
        match = re.search(r"\{.*\}", fixed_str, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, dict):
                    return data, None
            except json.JSONDecodeError as exc:
                return None, f"Regex 提取後解析失敗: {exc}"

        return None, f"無法從回應中提取有效的 JSON 物件: {raw[:80]!r}"

    @staticmethod
    def _build_retry_prompt(original: str, error: str) -> str:
        """建構 JSON 解析失敗的重試提示詞。
        
        當首次嘗試失敗（JSON 解析、網路或驗證錯誤）時，將錯誤訊息結構化
        地加入提示，幫助 LLM 理解並修正問題。
        
        Args:
            original: 原始提示詞
            error: 此次嘗試的錯誤訊息
            
        Returns:
            包含錯誤反饋的新提示詞
        """
        error_feedback = (
            f"【重試：修正前次錯誤】\n"
            f"你的前一次嘗試遇到了問題：\n"
            f"  - {error}\n\n"
            f"請基於此反饋，生成符合要求的回應。\n"
            f"確保：\n"
            f"  1. 輸出必須是合法的 JSON 物件\n"
            f"  2. 不包含任何文字說明或 markdown 標記\n"
            f"  3. 所有布林值必須寫成 true/false（非 True/False）\n\n"
        )
        return error_feedback + original
