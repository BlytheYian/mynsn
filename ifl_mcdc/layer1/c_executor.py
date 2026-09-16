"""
C 語言執行器：編譯插樁後的 .c、生成測試 harness、執行並解析探針 log。

對應 Python 版的 ProbeInjector + exec() 流程，
但 C 需要：插樁 → 生成 main() harness → gcc 編譯 → subprocess 執行 → 解析 stdout。

stdout 格式（由 ifl_probe.h 定義）：
    PROBE|{test_id}|{cond_id}|{0|1}        （每個原子條件）
    PROBE_DEC|{test_id}|{decision_id}|{0|1} （決策結果）

輸出：ProbeLog（與 Python 版相同格式，可直接送 MCDCCoverageEngine）
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clang.cindex import CursorKind, Index

from ifl_mcdc.models.probe_record import ProbeLog, ProbeRecord
from ifl_mcdc.layer1.c_probe_injector import IFL_PROBE_HEADER


# ──────────────────────────────────────────────────────────────────────────────
# 內部資料類別
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CFuncInfo:
    """C 函數的簽名資訊（由 libclang 提取）。"""
    name: str
    return_type: str                     # 例：'bool', 'int'
    params: list[tuple[str, str]] = field(default_factory=list)
    # params: [(param_name, c_type_spelling), ...]
    # c_type_spelling 為 libclang 回傳的類型字串，如 '_Bool', 'int', 'double'


# ──────────────────────────────────────────────────────────────────────────────
# 主類別
# ──────────────────────────────────────────────────────────────────────────────

class CExecutor:
    """編譯並執行插樁後的 C 原始碼，回傳 ProbeLog。

    使用方式：
        executor = CExecutor()
        log = executor.execute(
            original_filepath = Path('tests/fixtures/vaccine_eligibility.c'),
            instrumented_src  = injector.inject(...),
            func_name         = 'check_vaccine_eligibility',
            test_cases        = [
                {'age': 70, 'high_risk': True, 'days_since_last': 200, 'egg_allergy': False},
                {'age': 30, 'high_risk': False, 'days_since_last': 90, 'egg_allergy': True},
            ],
        )
    """

    def execute(
        self,
        original_filepath: str | Path,
        instrumented_src: str,
        func_name: str,
        test_cases: list[dict[str, Any]],
        work_dir: str | Path | None = None,
        gcc_flags: list[str] | None = None,
    ) -> ProbeLog:
        """主入口：端對端執行並回傳 ProbeLog。

        Args:
            original_filepath: 原始未插樁的 .c 檔（用於提取函數簽名）。
            instrumented_src:  由 CProbeInjector.inject() 生成的插樁後原始碼字串。
            func_name:         被測函數名稱。
            test_cases:        測試輸入列表，每筆為 {參數名: 值} 的 dict。
            work_dir:          暫存目錄；None 則自動建立並在完成後刪除。
            gcc_flags:         額外的 gcc 編譯選項（預設 ['-std=c11']）。

        Returns:
            ProbeLog，格式與 Python 版相同，可直接送 MCDCCoverageEngine。
        """
        original_filepath = Path(original_filepath)
        gcc_flags = gcc_flags or ['-std=c11']

        # 提取函數簽名（參數名稱與型別）
        func_info = self._extract_func_info(original_filepath, func_name)

        # 決定工作目錄
        _tmp_dir = None
        if work_dir is None:
            _tmp_dir = tempfile.mkdtemp(prefix='ifl_c_')
            work_dir = Path(_tmp_dir)
        else:
            work_dir = Path(work_dir)
            work_dir.mkdir(parents=True, exist_ok=True)

        try:
            return self._run_pipeline(
                work_dir, instrumented_src, func_info, test_cases, gcc_flags
            )
        finally:
            if _tmp_dir:
                shutil.rmtree(_tmp_dir, ignore_errors=True)

    # ──────────────────────────────────────────────────────────
    # 管線步驟
    # ──────────────────────────────────────────────────────────

    def _run_pipeline(
        self,
        work_dir: Path,
        instrumented_src: str,
        func_info: CFuncInfo,
        test_cases: list[dict[str, Any]],
        gcc_flags: list[str],
    ) -> ProbeLog:
        # Step 1: 寫出所需檔案
        probe_h = work_dir / 'ifl_probe.h'
        probe_h.write_text(IFL_PROBE_HEADER, encoding='utf-8')

        instr_c = work_dir / 'instrumented.c'
        instr_c.write_text(instrumented_src, encoding='utf-8')

        harness_c = work_dir / 'harness.c'
        harness_c.write_text(
            self._generate_harness(func_info, test_cases),
            encoding='utf-8',
        )

        # Step 2: 編譯
        binary = self._compile(work_dir, instr_c, harness_c, gcc_flags)

        # Step 3: 執行，捕捉 stdout
        raw_output = self._run_binary(binary)

        # Step 4: 解析輸出 → ProbeLog
        return self._parse_output(raw_output)

    # ──────────────────────────────────────────────────────────
    # 函數簽名提取（libclang）
    # ──────────────────────────────────────────────────────────

    def _extract_func_info(self, filepath: Path, func_name: str) -> CFuncInfo:
        """使用 libclang 提取函數的回傳型別與參數列表。"""
        index = Index.create()
        tu = index.parse(str(filepath), args=['-std=c11'])

        for cursor in tu.cursor.get_children():
            if (
                cursor.kind == CursorKind.FUNCTION_DECL
                and cursor.spelling == func_name
                and cursor.is_definition()
            ):
                params = [
                    (child.spelling, child.type.spelling)
                    for child in cursor.get_children()
                    if child.kind == CursorKind.PARM_DECL
                ]
                return CFuncInfo(
                    name=func_name,
                    return_type=cursor.result_type.spelling,
                    params=params,
                )

        raise ValueError(f"找不到函數定義：{func_name} 於 {filepath}")

    # ──────────────────────────────────────────────────────────
    # harness 生成
    # ──────────────────────────────────────────────────────────

    def _generate_harness(
        self,
        func_info: CFuncInfo,
        test_cases: list[dict[str, Any]],
    ) -> str:
        """生成包含 main() 的測試 harness .c 原始碼。"""
        lines: list[str] = [
            '#include <stdbool.h>',
            '#include "ifl_probe.h"',
            '',
            '/* 全域 test_id，由 ifl_probe.h extern 宣告 */',
            'int _ifl_test_id = 0;',
            '',
        ]

        # 函數前向宣告
        param_decls = ', '.join(
            f'{_normalize_type(typ)} {name}'
            for name, typ in func_info.params
        )
        ret = _normalize_type(func_info.return_type)
        lines.append(f'{ret} {func_info.name}({param_decls});')
        lines.append('')
        lines.append('int main(void) {')

        # 各測試案例呼叫
        for i, tc in enumerate(test_cases):
            args = ', '.join(
                _to_c_literal(tc[name], typ)
                for name, typ in func_info.params
            )
            lines.append(f'    _ifl_test_id = {i};')
            lines.append(f'    {func_info.name}({args});')
            lines.append('')

        lines.append('    return 0;')
        lines.append('}')
        return '\n'.join(lines) + '\n'

    # ──────────────────────────────────────────────────────────
    # 編譯
    # ──────────────────────────────────────────────────────────

    def _compile(
        self,
        work_dir: Path,
        instr_c: Path,
        harness_c: Path,
        gcc_flags: list[str],
    ) -> Path:
        """用 gcc 編譯兩個 .c 檔，回傳可執行檔路徑。"""
        import sys
        suffix = '.exe' if sys.platform == 'win32' else ''
        binary = work_dir / f'test_bin{suffix}'
        cmd = [
            'gcc',
            *gcc_flags,
            f'-I{work_dir}',          # 讓 #include "ifl_probe.h" 找得到
            str(instr_c),
            str(harness_c),
            '-o', str(binary),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"gcc 編譯失敗：\n{result.stderr}"
            )
        return binary

    # ──────────────────────────────────────────────────────────
    # 執行
    # ──────────────────────────────────────────────────────────

    def _run_binary(self, binary: Path, timeout: int = 30) -> str:
        """執行已編譯的 binary，回傳 stdout 字串。"""
        result = subprocess.run(
            [str(binary)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout

    def compile_runtime(
        self,
        original_filepath: str | Path,
        instrumented_src: str,
        func_name: str,
        work_dir: str | Path | None = None,
        gcc_flags: list[str] | None = None,
    ) -> 'CRuntime':
        """編譯插樁後的 .c，生成接受命令列參數的 harness，回傳 CRuntime。

        CRuntime 的 binary 只編譯一次；後續每筆測試以 run_one() 傳入參數。
        work_dir 由呼叫者管理（不自動清除），適合 orchestrator 持有整個 session。
        """
        original_filepath = Path(original_filepath)
        gcc_flags = gcc_flags or ['-std=c11']

        func_info = self._extract_func_info(original_filepath, func_name)

        if work_dir is None:
            work_dir = Path(tempfile.mkdtemp(prefix='ifl_c_rt_'))
        else:
            work_dir = Path(work_dir)
            work_dir.mkdir(parents=True, exist_ok=True)

        # 寫出檔案
        (work_dir / 'ifl_probe.h').write_text(IFL_PROBE_HEADER, encoding='utf-8')
        instr_c = work_dir / 'instrumented.c'
        instr_c.write_text(instrumented_src, encoding='utf-8')
        harness_c = work_dir / 'harness.c'
        harness_c.write_text(
            self._generate_argv_harness(func_info),
            encoding='utf-8',
        )

        binary = self._compile(work_dir, instr_c, harness_c, gcc_flags)
        return CRuntime(binary, func_info)

    def _generate_argv_harness(self, func_info: CFuncInfo) -> str:
        """生成從 argv 讀取測試輸入的 harness。

        協議：test_bin <test_id> <arg1> <arg2> ...
          bool 傳 0/1，int 傳整數，float/double 傳浮點字串。
        """
        lines = [
            '#include <stdbool.h>',
            '#include <stdlib.h>',
            '#include <string.h>',
            '#include "ifl_probe.h"',
            '',
            'int _ifl_test_id;',
            '',
        ]
        # 前向宣告
        param_decls = ', '.join(
            f'{_normalize_type(typ)} {name}'
            for name, typ in func_info.params
        )
        ret = _normalize_type(func_info.return_type)
        lines.append(f'{ret} {func_info.name}({param_decls});')
        lines.append('')
        lines.append('int main(int argc, char* argv[]) {')
        n_params = len(func_info.params)
        lines.append(f'    if (argc < {n_params + 2}) return 1;')
        lines.append('    _ifl_test_id = atoi(argv[1]);')

        for i, (name, typ) in enumerate(func_info.params):
            norm = _normalize_type(typ)
            idx = i + 2  # argv[2] 開始
            if norm == 'bool':
                lines.append(f'    {norm} {name} = (bool)atoi(argv[{idx}]);')
            elif norm in ('float', 'double'):
                lines.append(f'    {norm} {name} = ({norm})atof(argv[{idx}]);')
            else:
                lines.append(f'    {norm} {name} = ({norm})atoi(argv[{idx}]);')

        args = ', '.join(name for name, _ in func_info.params)
        lines.append(f'    {func_info.name}({args});')
        lines.append('    return 0;')
        lines.append('}')
        return '\n'.join(lines) + '\n'

    # ──────────────────────────────────────────────────────────
    # 解析 stdout → ProbeLog
    # ──────────────────────────────────────────────────────────

    def _parse_output(self, output: str) -> ProbeLog:
        """解析探針 stdout，建立 ProbeLog。"""
        log = ProbeLog()
        for r in _parse_probe_lines(output):
            log.append(r)
        return log


# ──────────────────────────────────────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────────────────────────────────────

def _parse_probe_lines(output: str) -> list[ProbeRecord]:
    """將 PROBE/PROBE_DEC 行解析為 ProbeRecord 列表（共用邏輯）。"""
    cond_rows: dict[str, list[tuple[str, bool]]] = {}
    dec_rows: dict[str, dict[str, bool]] = {}

    for line in output.splitlines():
        if line.startswith('PROBE_DEC|'):
            parts = line.split('|')
            if len(parts) == 4:
                _, tid, did, val = parts
                dec_rows.setdefault(tid, {})[did] = bool(int(val))
        elif line.startswith('PROBE|'):
            parts = line.split('|')
            if len(parts) == 4:
                _, tid, cid, val = parts
                cond_rows.setdefault(tid, []).append((cid, bool(int(val))))

    records: list[ProbeRecord] = []
    for tid, cond_list in cond_rows.items():
        decisions = dec_rows.get(tid, {})
        for cond_id, value in cond_list:
            decision_id = cond_id.split('.')[0]
            records.append(ProbeRecord(
                test_id=tid,
                cond_id=cond_id,
                value=value,
                decision=decisions.get(decision_id, False),
            ))
    return records


# ──────────────────────────────────────────────────────────────────────────────
# CRuntime：編譯一次、逐筆執行（供 CIFLOrchestrator 使用）
# ──────────────────────────────────────────────────────────────────────────────

class CRuntime:
    """持久化執行期：binary 只編譯一次，每次 run_one() 以命令列參數傳入單筆測試。

    命令列協議：
        test_bin <test_id> <arg1> <arg2> ...
        （bool 傳 0/1；int 傳整數；float 傳浮點數字串）
    """

    def __init__(self, binary: Path, func_info: CFuncInfo) -> None:
        self.binary = binary
        self.func_info = func_info
        self._counter: int = 0

    def run_one(
        self,
        test_case: dict[str, Any],
        timeout: int = 10,
    ) -> tuple[str, list[ProbeRecord]]:
        """執行單筆測試案例，回傳 (test_id, 新增的 ProbeRecord 列表)。

        test_id 為整數字串（"0", "1", ...），與 IFLOrchestrator 的 UUID 格式不同，
        但 ProbeRecord.test_id 是 str，不影響 CoverageEngine 的邏輯。
        """
        test_id = str(self._counter)
        self._counter += 1

        # 組合命令列參數：test_id + 各參數值
        argv = [str(self.binary), test_id]
        for name, typ in self.func_info.params:
            value = test_case.get(name)
            argv.append(_value_to_argv(value, typ))

        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        records = _parse_probe_lines(result.stdout)
        return test_id, records


def _normalize_type(c_type: str) -> str:
    """將 libclang 型別字串正規化為 C 宣告可用的型別名稱。

    libclang 對 bool 回傳 '_Bool'；stdbool.h 的 bool 巨集等同 _Bool，
    但在 harness 裡直接用 'bool' 更易讀。
    """
    mapping = {
        '_Bool': 'bool',
        'unsigned int': 'unsigned int',
        'unsigned long': 'unsigned long',
        'long long': 'long long',
    }
    return mapping.get(c_type, c_type)


def _to_c_literal(value: Any, c_type: str) -> str:
    """將 Python 值轉換為對應的 C 字面量字串（供批次 harness 使用）。"""
    normalized = _normalize_type(c_type)
    if normalized == 'bool':
        return 'true' if value else 'false'
    if normalized in ('float', 'double'):
        return repr(float(value))
    return str(int(value))


def _value_to_argv(value: Any, c_type: str) -> str:
    """將 Python 值轉為命令列字串（供 CRuntime 使用）。

    bool → "0"/"1"；int → str(int)；float → str(float)
    """
    normalized = _normalize_type(c_type)
    if normalized == 'bool':
        return '1' if value else '0'
    if normalized in ('float', 'double'):
        return repr(float(value))
    return str(int(value))
