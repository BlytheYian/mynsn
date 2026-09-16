"""
提示詞建構器：產生四段式 Gap-Guided Prompt。

TC-U-37: bound_specs 全部出現在 §3
TC-U-38: 超過 MAX_TOKENS 時截斷 §1 的 source_context
TC-U-39: §3 和 §4 完整保留（截斷後）
TC-U-40: F2T/T2F 方向描述正確
"""
from __future__ import annotations

import json
import re
import random as _random
from typing import Any

from ifl_mcdc.models.coverage_matrix import GapEntry
from ifl_mcdc.models.decision_node import DecisionNode
from ifl_mcdc.models.smt_models import BoundSpec

_ZONE_LABELS = ["邊界區（靠近臨界點）", "中間區（可行空間中段）", "極端區（可行空間遠端）"]

_PREFERENCE_LEVELS = {
    "int": ["low（偏低）", "medium（中等）", "high（偏高）"],
    "bool": ["true", "false"],
}


class PromptConstructor:
    """建構四段式 Gap-Guided Prompt。

    §1 醫療情境 → §2 目標缺口 → §3 精確數值約束 → §4 輸出格式
    """

    MAX_TOKENS: int = 2048

    def __init__(self) -> None:
        self._call_count: int = 0  # 用於系統性循環子區間（邊界→中間→極端→…）

    def build(
        self,
        decision_node: DecisionNode,
        gap: GapEntry,
        bound_specs: list[BoundSpec],
        func_signature: str,
        domain_context: str = "",
        clinical_profile: dict[str, Any] | None = None,
        scenario_hint: str = "",
        domain_types: dict[str, str] | None = None,
        domain_bounds: dict[str, list[int]] | None = None,
        error_history: list[str] | None = None,
        smt_example: dict[str, object] | None = None,
        preceding_nodes: list[DecisionNode] | None = None,
        language: str = "python",
        preresolved: dict[str, object] | None = None,
    ) -> str:
        """建構四段式提示詞。

        截斷策略：若超過 MAX_TOKENS（以字元數估算），從 §1 的 source_context
        開始截斷，§3 和 §4 必須完整保留。

        Args:
            decision_node: 決策節點（含 source_context）。
            gap: 目標缺口。
            bound_specs: 變數邊界規格列表。
            func_signature: 函式簽名字串。
            domain_context: 選填的情境說明。
            error_history: 選填的前幾輪錯誤訊息列表，用於糾正。

        Returns:
            四段式提示詞字串。
        """
        # 更新呼叫計數（用於系統性循環子區間）
        self._call_count += 1

        # 找出 gap.condition_id 對應的條件表達式
        _is_c = (language == "c")
        cond_expression = ""
        for cond in decision_node.condition_set.conditions:
            if cond.cond_id == gap.condition_id:
                cond_expression = cond.expression
                break

        # C 模式：將表達式轉回 C 語法（僅供 Prompt 顯示）
        if _is_c:
            cond_expression = _to_c_syntax(cond_expression)

        direction_str = "True" if gap.flip_direction == "F2T" else "False"

        # §diversity（多樣性要求，通用版）
        sec_diversity_lines = [
            "【多樣性要求】",
            f"請生成一個在「{domain_context}」情境上自然多樣的測試案例。",
            "- 數值型參數請在符合約束的範圍內自由選擇，避免每次使用相同數字",
            "- 布林型參數的組合應反映真實的個體差異",
            "- 整體數值組合應代表真實存在的不同個體",
        ]
        if scenario_hint:
            sec_diversity_lines.append(f"- 此次請以「{scenario_hint}」為情境背景")
        sec_diversity = "\n".join(sec_diversity_lines)

        # §3 固定部分（語意引導版）
        # 子區間選擇：用呼叫計數循環（邊界→中間→極端→…），確保系統性覆蓋
        zone_idx = self._call_count % 3

        # 完全釘死的變數（interval 上下限相同、或 valid_set 只有一個值）沒有任何自由度，
        # 值早就確定了（通常是條件式引用的模組層級常數，域範圍鎖死成單一值）。
        # 這種變數不該問 LLM——問了 LLM 也只能「複製」這個值，複製錯還會被
        # DomainValidator 判定違反邊界、白白浪費一次重試；不如乾脆不讓 LLM 知道
        # 這個欄位存在，讓它完全不出現在輸出裡，之後由 orchestrator 用 SMT 算出的
        # model_python 直接補上正確值（見 orchestrator.py 的 _fill_missing_params／
        # smt_result.model_python 回填邏輯）。
        pinned_vars: set[str] = {
            bs.var_name for bs in bound_specs
            if (bs.interval is not None and bs.interval[0] == bs.interval[1])
            or (bs.valid_set is not None and len(bs.valid_set) == 1)
        }
        # 目標條件自己耦合的變數（同一條件用到 2 個以上變數）已由 orchestrator
        # 事先用「窮舉表」或「逐一問＋條件式重算」解好，同樣不再問 LLM 第二次。
        if preresolved:
            pinned_vars |= set(preresolved.keys())

        lines: list[str] = ["【約束條件】", "請根據以下約束，生成一個符合情境的具體案例：", ""]
        for bs in bound_specs:
            if bs.var_name in pinned_vars:
                continue
            if bs.interval is not None:
                if bs.sub_intervals is not None:
                    lo, hi = bs.sub_intervals[zone_idx % len(bs.sub_intervals)]
                    label = _ZONE_LABELS[zone_idx % len(_ZONE_LABELS)]
                    lo_str = str(int(lo)) if lo == int(lo) else str(lo)
                    hi_str = str(int(hi)) if hi == int(hi) else str(hi)
                    unit = f"（單位：{bs.medical_unit}）" if bs.medical_unit else ""
                    lines.append(
                        f"- {bs.var_name}：建議在{label} {lo_str} 到 {hi_str} 之間{unit}"
                    )
                else:
                    lo, hi = bs.interval
                    lo_str = str(int(lo)) if lo == int(lo) else str(lo)
                    hi_str = str(int(hi)) if hi == int(hi) else str(hi)
                    unit = f"（單位：{bs.medical_unit}）" if bs.medical_unit else ""
                    lines.append(f"- {bs.var_name}：必須在 {lo_str} 到 {hi_str} 之間{unit}")
            elif bs.valid_set is not None:
                vals = sorted(bs.valid_set, key=str)
                if len(vals) == 1:
                    lines.append(f"- {bs.var_name}：必須為 {vals[0]}")
                else:
                    lines.append(f"- {bs.var_name}：必須為 {vals} 之一")
        lines += [
            "",
            "🚨【極度重要：多變數邏輯與防呆警告】🚨",
            "1. 請務必檢視上方的「原始碼」！仔細觀察程式碼前方的 `if` 條件（例如變數之間的比例限制、大小關係）。",
            "2. 你生成的數值組合【必須】避開這些提早結束 (early return) 的防呆條件，否則系統會直接崩潰退件！",
            "3. 絕對不可生成互相矛盾的數值（例如：貸款金額大於年收入的 5 倍等不合理比例），請發揮商業與生活常識！",
            "",
            "【情境語意要求】",
            f"請想像一個真實的「{domain_context}」場景，確保：",
            "1. 所有數值符合真實可能的範圍，避免極端或不合理的組合",
            "2. 各參數之間有合理的邏輯關聯性",
            "3. 整體案例代表一個真實存在的個體",
        ]

        # §example：SMT 參考範例段落（若有提供）
        # 提供一個已驗證合法的輸入作為「錨點」，讓 LLM 做變體生成而非從零開始。
        # 這樣 LLM 不需要同時在腦袋裡滿足所有多變數約束，只需對合法點做有意義的變化。
        if smt_example:
            example_vals = ", ".join(f"{k}={v}" for k, v in smt_example.items())
            lines += [
                "",
                "【參考範例】",
                "以下是一個已確認符合所有約束的合法輸入（由系統自動驗證）：",
                f"{{{example_vals}}}",
                "請以此為基礎，生成一個在情境上有所不同的變體版本。",
                "可以調整部分數值以反映不同的真實場景，但必須仍然滿足上方所有約束條件。",
            ]

        sec3 = "\n".join(lines)

        # §error_feedback（選填）- 包含前幾輪的錯誤訊息供 LLM 糾正
        sec_error: str | None = None
        if error_history and len(error_history) > 0:
            error_lines = ["【前輪反饋與糾正】", "上一輪嘗試中遇到的問題，請避免重複："]
            for i, err_msg in enumerate(error_history, 1):
                error_lines.append(f"{i}. {err_msg}")
            error_lines.append("\n請基於上述反饋，生成不同的測試案例。")
            sec_error = "\n".join(error_lines)

        # §4 固定部分（含型別與值域提醒）——釘死變數一樣不列進 example_json／型別提示，
        # 理由同上：不問，讓它自然不出現在 LLM 輸出裡，交給 orchestrator 自動補值。
        example_json = json.dumps(
            {bs.var_name: "..." for bs in bound_specs if bs.var_name not in pinned_vars},
            ensure_ascii=False,
        )
        type_hint_lines: list[str] = []
        if domain_types:
            int_fields = [k for k, v in domain_types.items() if v == "int" and k not in pinned_vars]
            bool_fields = [k for k, v in domain_types.items() if v == "bool" and k not in pinned_vars]
            if int_fields:
                int_descs = []
                for k in int_fields:
                    if domain_bounds and k in domain_bounds:
                        lo, hi = domain_bounds[k][0], domain_bounds[k][1]
                        int_descs.append(f"{k}（{lo}~{hi}）")
                    else:
                        int_descs.append(k)
                type_hint_lines.append("整數欄位：" + "、".join(int_descs))
            if bool_fields:
                type_hint_lines.append(
                    "布林欄位（必須用 true/false，不得使用 1/0 或字串）："
                    + "、".join(bool_fields)
                )
        type_hint_str = ("\n" + "\n".join(type_hint_lines)) if type_hint_lines else ""
        sec4 = (
            "【輸出格式】\n"
            "請僅輸出一個合法的 JSON 物件，鍵名與函式參數完全一致。\n"
            "禁止 markdown、禁止說明文字、禁止 ```json 標記。\n"
            f"{example_json}"
            f"{type_hint_str}"
        )

        # §2 固定部分
        sec2 = (
            f"【目標缺口】\n"
            f"條件 {gap.condition_id}（{cond_expression}）需要值為\n"
            f"{direction_str}，且整體函式輸出因此改變。"
        )

        # §preresolved：目標條件自己耦合的變數已預先決定（不需要再輸出）
        sec_preresolved: str | None = None
        if preresolved:
            resolved_vals = ", ".join(f"{k}={v}" for k, v in preresolved.items())
            sec_preresolved = (
                "【已預先決定的欄位（因跟目標條件的其他變數有耦合關係，"
                "已由系統依約束求解決定，請勿在輸出中重複提供）】\n"
                f"{{{resolved_vals}}}"
            )

        # §path：路徑可達性提示（當目標節點有前置決策節點時）
        sec_path: str | None = None
        if preceding_nodes:
            path_lines = [
                "【⚠️ 路徑可達性】",
                "目標決策節點只有在以下所有條件都「執行後為 False」時才會被執行到，",
                "請確保你生成的數值讓以下表達式的結果為 False：",
            ]
            for pn in preceding_nodes:
                hint_vals = ""
                if smt_example:
                    used_vars = {v for cond in pn.condition_set.conditions for v in cond.var_names}
                    relevant = {k: v for k, v in smt_example.items() if k in used_vars}
                    if relevant:
                        hint_vals = "（建議：" + ", ".join(f"{k}={v}" for k, v in relevant.items()) + "）"
                path_lines.append(f"- {pn.expression_str}{hint_vals}")
            sec_path = "\n".join(path_lines)

        # §mask：OR 遮罩提示（目標條件在 OR 結構中，需要讓其他 OR 條件為 False）
        sec_mask: str | None = None
        try:
            coupled = decision_node.condition_set.get_coupled(gap.condition_id)
            or_coupled = [(c, t) for c, t in coupled if t == "OR"]
            if or_coupled:
                mask_lines = [
                    "【⚠️ MC/DC 獨立性】",
                    f"條件 {gap.condition_id}（{cond_expression}）位於 OR 結構中。",
                    "為了讓該條件能「獨立」影響決策，以下同組 OR 條件必須為 False：",
                ]
                for oc, _ in or_coupled:
                    hint_vals = ""
                    if smt_example:
                        relevant = {k: v for k, v in smt_example.items() if k in oc.var_names}
                        if relevant:
                            hint_vals = " → 建議值：" + ", ".join(f"{k}={v}" for k, v in relevant.items())
                    oc_expr = _to_c_syntax(oc.expression) if _is_c else oc.expression
                    mask_lines.append(f"- {oc_expr}（{oc.cond_id}）{hint_vals}")
                mask_lines.append("若以上條件為 True，系統將無法驗證目標條件的獨立影響性。")
                sec_mask = "\n".join(mask_lines)
        except Exception:
            pass

        # §1 可截斷部分
        source_context = decision_node.source_context

        # §clinical（臨床比例參考段落，選填）
        sec_clinical: str | None = None
        if clinical_profile is not None:
            sec_clinical = _build_clinical_section(clinical_profile)

        code_label = "C 原始碼" if _is_c else "原始碼"

        def _build_full(ctx: str) -> str:
            sec1 = (
                f"【情境】\n"
                f"函式：{func_signature}\n"
                f"情境：{domain_context}\n"
                f"{code_label}：\n"
                f"{ctx}"
            )
            parts = [sec1, sec2]
            if sec_preresolved:
                parts.append(sec_preresolved)
            if sec_path:
                parts.append(sec_path)
            if sec_mask:
                parts.append(sec_mask)
            parts.append(sec_diversity)
            if sec_clinical:
                parts.append(sec_clinical)
            parts.append(sec3)
            if sec_error:
                parts.append(sec_error)
            parts.append(sec4)
            return "\n\n".join(parts)

        full = _build_full(source_context)

        # 截斷：若超過 MAX_TOKENS，逐步縮短 source_context
        if len(full) > self.MAX_TOKENS:
            # 計算固定部分長度（不含 §1 source_context）
            sec1_header = (
                f"【情境】\n"
                f"函式：{func_signature}\n"
                f"情境：{domain_context}\n"
                f"{code_label}：\n"
            )
            clinical_len = (len("\n\n") + len(sec_clinical)) if sec_clinical else 0
            fixed_len = len(sec1_header) + len("\n\n") * 4 + len(sec2) + len(sec_diversity) + clinical_len + len(sec3) + len(sec4)
            budget = self.MAX_TOKENS - fixed_len

            if budget > 0:
                source_context = source_context[:budget]
            else:
                source_context = ""

            full = _build_full(source_context)

        return full

    def build_dependency_table(
        self,
        enum_var: str,
        range_var: str,
        zones: list[tuple[float, float, tuple[float, float]]],
        domain_types: dict[str, str],
        func_signature: str,
        domain_context: str = "",
    ) -> str:
        """建構「變數依賴關係」提示詞：兩個變數同屬一個條件、彼此耦合時，

        不各自給獨立範圍（會各自合法但搭配起來不合法），改用「若 enum_var 在
        某個小範圍，則 range_var 需要在對應範圍」的條件式表格，一次問完兩個
        變數。只適用於 enum_var 域寬度小、可窮舉每個值對應的 range_var 合法
        區間的情況（例如 0~3 這種離散值）。

        Args:
            enum_var: 域寬度小、被窮舉的變數名稱。
            range_var: 依 enum_var 而定合法區間的另一個變數名稱。
            zones: [(enum_lo, enum_hi, (range_lo, range_hi)), ...]——
                每個 enum_var 子區間對應的 range_var 合法區間，已用 Z3 算好。
            domain_types: var_name → "int" | "bool"。
            func_signature: 函式簽名字串。
            domain_context: 情境說明。

        Returns:
            提示詞字串。
        """
        lines = [
            "【情境】",
            f"函式：{func_signature}",
            f"情境：{domain_context}",
            "",
            f"【變數依賴關係——{enum_var} 和 {range_var} 要一起決定，不能各自獨立選值】",
            f"{range_var} 的合法範圍依你選擇的 {enum_var} 而定：",
        ]
        for lo_e, hi_e, (lo_r, hi_r) in zones:
            lo_e_s = str(int(lo_e)) if lo_e == int(lo_e) else str(lo_e)
            hi_e_s = str(int(hi_e)) if hi_e == int(hi_e) else str(hi_e)
            lo_r_s = str(int(lo_r)) if lo_r == int(lo_r) else str(lo_r)
            hi_r_s = str(int(hi_r)) if hi_r == int(hi_r) else str(hi_r)
            lines.append(
                f"- 若 {enum_var} 在 {lo_e_s}~{hi_e_s} 範圍，"
                f"{range_var} 需要在 {lo_r_s}~{hi_r_s} 範圍"
            )
        lines += [
            "請先決定一個合法的 " + enum_var + "，再依上表選一個對應範圍內的 " + range_var + "，"
            "不要先選好 " + range_var + " 再湊 " + enum_var + "。",
            "",
            "【輸出格式】",
            "請僅輸出一個合法的 JSON 物件，只包含這兩個欄位，鍵名與函式參數完全一致。",
            "禁止 markdown、禁止說明文字、禁止 ```json 標記。",
            json.dumps({enum_var: "...", range_var: "..."}, ensure_ascii=False),
        ]
        return "\n".join(lines)

    def build_mask_patch(
        self,
        fixed_case: dict[str, object],
        masked_fields: list[str],
        domain_types: dict[str, str],
        required_bools: dict[str, bool],
        bound_specs_by_var: dict[str, BoundSpec],
        func_signature: str,
        domain_context: str = "",
    ) -> str:
        """建構「決策可達性修補」提示詞：只要求 LLM 針對被遮蔽（masked）的欄位重新給值。

        與 build() 不同，這裡不重述整個決策節點與多樣性規則——其餘欄位已經生成好，
        只需要修正讓目標條件的獨立效果被遮蔽的那幾個欄位。布林欄位給固定的
        true/false 要求（沒有「範圍」可言），整數欄位給 SMT 算出的合法區間，
        讓 LLM 在區間內自行挑一個語意合理的值，而不是直接照搬 Z3 的單一解。

        Args:
            fixed_case: 目前已生成、其餘欄位維持不變的測試案例。
            masked_fields: 需要重新給值的欄位名稱列表。
            domain_types: var_name → "int" | "bool"。
            required_bools: 被遮蔽的布林欄位 → 必須的值。
            bound_specs_by_var: var_name → BoundSpec（取 interval 當範圍）。
            func_signature: 函式簽名字串。
            domain_context: 情境說明。

        Returns:
            提示詞字串。
        """
        fixed_str = ", ".join(
            f"{k}={v}" for k, v in fixed_case.items() if k not in masked_fields
        )
        lines = [
            "【情境】",
            f"函式：{func_signature}",
            f"情境：{domain_context}",
            "",
            "【已生成的測試案例（其餘欄位維持不變，不需要重新提供）】",
            f"{{{fixed_str}}}",
            "",
            "【問題】以上案例中，以下欄位目前的值讓目標條件的獨立效果被遮蔽（masked），"
            "決策結果因此不符合預期。請只針對以下欄位重新給值，使決策能正確反映目標條件：",
        ]
        for var in masked_fields:
            vtype = domain_types.get(var, "int")
            if vtype == "bool":
                required = required_bools.get(var, True)
                lines.append(f"- {var}：必須為 {str(required).lower()}")
            else:
                bs = bound_specs_by_var.get(var)
                if bs is not None and bs.interval is not None:
                    lo, hi = bs.interval
                    lo_str = str(int(lo)) if lo == int(lo) else str(lo)
                    hi_str = str(int(hi)) if hi == int(hi) else str(hi)
                    lines.append(f"- {var}：必須在 {lo_str} 到 {hi_str} 之間")
                else:
                    lines.append(f"- {var}：整數")
        lines += [
            "",
            "【情境語意要求】",
            f"在滿足以上限制的前提下，請讓新值與「已生成的測試案例」裡其餘欄位維持真實個體的一致性，"
            f"符合「{domain_context}」情境下合理的數值與邏輯關聯（例如年齡與病史的搭配要自然），"
            "避免生成矛盾或不合常理的組合。",
            "",
            "【輸出格式】",
            "請僅輸出一個合法的 JSON 物件，只包含上述需要修改的欄位，鍵名與函式參數完全一致。",
            "禁止 markdown、禁止說明文字、禁止 ```json 標記。",
            json.dumps({v: "..." for v in masked_fields}, ensure_ascii=False),
        ]
        return "\n".join(lines)

    def build_qualitative(
        self,
        decision_node: DecisionNode,
        gap: GapEntry,
        func_signature: str,
        domain_context: str,
        domain_types: dict[str, str],
        scenario_hint: str = "",
    ) -> str:
        """建構「情境偏好式」提示詞。

        不要求 LLM 生成精確數值，而是要求：
        - 整數變數：給出 low / medium / high 的偏好方向
        - 布林變數：給出 true / false

        LLM 只需要進行語意推理（「緩慢爬升的飛機」→ OwnRate=low），
        不需要知道或遵守任何數值約束。數值映射由 SMT 負責。

        Args:
            decision_node: 目標決策節點（含 source_context）。
            gap: 目標缺口。
            func_signature: 函式簽名。
            domain_context: 情境說明。
            domain_types: var_name → "int" | "bool" 的映射。
            scenario_hint: 可選的多樣性場景提示。

        Returns:
            偏好式提示詞字串。
        """
        cond_expression = ""
        for cond in decision_node.condition_set.conditions:
            if cond.cond_id == gap.condition_id:
                cond_expression = cond.expression
                break

        direction_str = "True" if gap.flip_direction == "F2T" else "False"

        # 建構變數清單（只給型別，不給數值範圍）
        var_lines: list[str] = []
        example_prefs: dict[str, str] = {}
        for var_name, var_type in domain_types.items():
            if var_type == "bool":
                var_lines.append(f"- {var_name}：布林值，填 true 或 false")
                example_prefs[var_name] = "true"
            else:
                var_lines.append(f"- {var_name}：整數，填 \"low\"、\"medium\" 或 \"high\"")
                example_prefs[var_name] = "medium"

        example_json = json.dumps(
            {"scenario": "一句話描述此測試情境", "preferences": example_prefs},
            ensure_ascii=False,
            indent=2,
        )

        hint_line = f"\n此次請以「{scenario_hint}」為情境背景。" if scenario_hint else ""

        prompt = (
            f"【情境】\n"
            f"函式：{func_signature}\n"
            f"情境：{domain_context}\n"
            f"原始碼：\n{decision_node.source_context}\n\n"
            f"【目標缺口】\n"
            f"條件 {gap.condition_id}（{cond_expression}）在此測試中需要值為 {direction_str}，\n"
            f"且整體函式的輸出決策因此改變（MC/DC 獨立影響性）。\n\n"
            f"【你的任務】{hint_line}\n"
            f"請想像一個真實的「{domain_context}」情境，描述一個具體的測試場景。\n"
            f"對每個參數，根據你對情境的理解給出語意偏好：\n"
            f"  整數參數：填 \"low\"（偏低）、\"medium\"（中等）或 \"high\"（偏高）\n"
            f"  布林參數：填 true 或 false\n\n"
            f"【參數列表】\n"
            + "\n".join(var_lines) + "\n\n"
            f"【輸出格式】\n"
            f"只輸出以下格式的 JSON，禁止任何說明文字：\n"
            f"{example_json}"
        )
        return prompt


def _to_c_syntax(expr: str) -> str:
    """將 Python 正規化表達式轉回 C 語法，僅用於 Prompt 顯示。

    and → &&  |  or → ||  |  not → !
    使用 word boundary 避免誤替換變數名內的子串。
    """
    expr = re.sub(r'\bor\b', '||', expr)
    expr = re.sub(r'\band\b', '&&', expr)
    expr = re.sub(r'\bnot\b\s*', '!', expr)
    return expr


def _build_clinical_section(profile: dict[str, Any]) -> str:
    """將臨床比例資料轉化為自然語言的 Prompt 段落。"""
    lines: list[str] = ["【臨床流行病學參考資料】", "以下為真實臨床盛行率，僅供參考，不是硬性約束；邊界測試案例仍然可以生成。", ""]

    population = profile.get("population", "")
    if population:
        lines.append(f"族群背景：{population}")
        lines.append("")

    variables = profile.get("variables", {})
    if variables:
        lines.append("各變數盛行率：")
        for var_name, info in variables.items():
            if isinstance(info, dict):
                description = info.get("description", "")
                if description:
                    lines.append(f"- {var_name}：{description}")
            elif isinstance(info, str):
                lines.append(f"- {var_name}：{info}")

    comorbidities = profile.get("comorbidities", "")
    if comorbidities:
        lines.append("")
        lines.append(f"共病說明：{comorbidities}")

    lines.append("")
    lines.append("請盡量讓生成的案例反映上述分布，但優先遵守邏輯約束。")
    return "\n".join(lines)
