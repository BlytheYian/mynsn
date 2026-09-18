"""
MC/DC 覆蓋率引擎：從 ProbeLog 建立並增量更新 MCDCMatrix。
"""
from __future__ import annotations

from ifl_mcdc.models.coverage_matrix import MCDCMatrix
from ifl_mcdc.models.decision_node import ConditionSet
from ifl_mcdc.models.probe_record import ProbeLog, ProbeRecord


class MCDCCoverageEngine:

    def build_matrix(
        self,
        cond_set: ConditionSet,
        log: ProbeLog,
    ) -> MCDCMatrix:
        matrix = MCDCMatrix(condition_set=cond_set)
        test_ids = list(dict.fromkeys(r.test_id for r in log.records))
        for test_id in test_ids:
            self._update_one(matrix, log, test_id)
        return matrix

    def update(
        self,
        matrix: MCDCMatrix,
        log: ProbeLog,
        new_test_id: str,
    ) -> bool:
        loss_before = matrix.compute_loss()
        self._update_one(matrix, log, new_test_id)
        return matrix.compute_loss() < loss_before

    def _update_one(
        self,
        matrix: MCDCMatrix,
        log: ProbeLog,
        test_id: str,
    ) -> None:
        new_records = log.get_by_test(test_id)
        existing_tests = list(
            dict.fromkeys(
                r.test_id for r in log.records if r.test_id != test_id
            )
        )
        for existing_id in existing_tests:
            existing_records = log.get_by_test(existing_id)
            self._check_pair(matrix, new_records, existing_records)

    def _check_pair(
        self,
        matrix: MCDCMatrix,
        recs_a: list[ProbeRecord],
        recs_b: list[ProbeRecord],
    ) -> None:
        map_a: dict[str, ProbeRecord] = {r.cond_id: r for r in recs_a}
        map_b: dict[str, ProbeRecord] = {r.cond_id: r for r in recs_b}

        if not map_a or not map_b:
            return

        # 以矩陣所屬決策節點的第一個條件取得正確的 decision 值。
        # 若用 next(iter(map_a.values())) 會拿到 log 中第一筆紀錄（D1.c1），
        # 導致分析 D2/D3 矩陣時比對錯誤的 decision。
        first_cond_id = matrix.condition_set.conditions[0].cond_id
        rec_a_dec = map_a.get(first_cond_id)
        rec_b_dec = map_b.get(first_cond_id)
        if rec_a_dec is None or rec_b_dec is None:
            return  # 其中一個測試未到達此決策節點
        dec_a = rec_a_dec.decision
        dec_b = rec_b_dec.decision

        if dec_a == dec_b:
            return

        for cond in matrix.condition_set.conditions:
            rec_a = map_a.get(cond.cond_id)
            rec_b = map_b.get(cond.cond_id)

            if rec_a is None or rec_b is None:
                continue

            if rec_a.value == rec_b.value:
                continue

            if self._others_ok(matrix, cond.cond_id, map_a, map_b):
                flip = "F2T" if (not rec_a.value and rec_b.value) else "T2F"
                matrix.mark_covered(cond.cond_id, flip)
                other_flip = "T2F" if flip == "F2T" else "F2T"
                matrix.mark_covered(cond.cond_id, other_flip)

                # 記錄這組配對裡，真正代表「條件值=True」跟「條件值=False」
                # 的各自是哪一筆測試——F2T 的佐證案例該秀 True 那筆、T2F 該秀
                # False 那筆，這樣才是真的參與驗證此配對的案例，不是事後另外
                # 找的、跟這組配對無關的同值案例。
                tid_a = recs_a[0].test_id if recs_a else None
                tid_b = recs_b[0].test_id if recs_b else None
                tid_true = tid_a if rec_a.value else tid_b
                tid_false = tid_b if rec_a.value else tid_a
                matrix.record_evidence(cond.cond_id, "F2T", tid_true)
                matrix.record_evidence(cond.cond_id, "T2F", tid_false)

    def _others_ok(
        self,
        matrix: MCDCMatrix,
        target_id: str,
        map_a: dict[str, ProbeRecord],
        map_b: dict[str, ProbeRecord],
    ) -> bool:
        """MC/DC 唯一因 variant：所有非目標條件的探針值在兩個測試中必須相同。

        原始探針值相同即保證有效值（含 negated 轉換）相同，
        確保決策翻轉唯一由目標條件引起。
        """
        for cond in matrix.condition_set.conditions:
            if cond.cond_id == target_id:
                continue

            rec_a = map_a.get(cond.cond_id)
            rec_b = map_b.get(cond.cond_id)

            if rec_a is None or rec_b is None:
                continue

            if rec_a.value != rec_b.value:
                return False

        return True