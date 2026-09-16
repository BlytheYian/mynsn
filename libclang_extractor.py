"""
libclang 提取 C 代碼的邏輯範例

安裝：pip install libclang
"""

from clang.cindex import Index, CursorKind, TypeKind

def extract_from_c_fixture(filepath, func_name):
    """
    從 C 檔案提取函數簽名、參數型別、決策條件

    返回：
        auto_sig: "func_name(param1, param2, ...)"
        auto_types: {"param1": "int", "param2": "bool", ...}
        decisions: [決策條件列表]
    """
    index = Index.create()
    tu = index.parse(filepath)

    auto_sig = None
    auto_types = {}
    decisions = []

    # ════════════════════════════════════════════════════════
    # Step 1: 遍歷 AST，找到目標函數
    # ════════════════════════════════════════════════════════

    def visit_node(cursor, depth=0):
        nonlocal auto_sig, auto_types, decisions

        # 尋找函數定義
        if cursor.kind == CursorKind.FUNCTION_DECL and cursor.spelling == func_name:
            print(f"[找到函數] {cursor.spelling}")

            # ──── 提取函數簽名 ────
            param_names = []
            for child in cursor.get_children():
                if child.kind == CursorKind.PARM_DECL:
                    param_names.append(child.spelling)

            auto_sig = f"{func_name}({', '.join(param_names)})"
            print(f"  簽名: {auto_sig}")

            # ──── 提取參數型別 ────
            for child in cursor.get_children():
                if child.kind == CursorKind.PARM_DECL:
                    param_name = child.spelling
                    param_type = _get_type_string(child.type)
                    auto_types[param_name] = param_type
                    print(f"  參數: {param_name} → {param_type}")

            # ──── 提取決策條件 ────
            decisions = _extract_conditions(cursor)
            print(f"  條件數: {len(decisions)}")

        # 遞迴訪問子節點
        for child in cursor.get_children():
            visit_node(child, depth + 1)

    # 從檔案根開始遍歷
    visit_node(tu.cursor)

    return auto_sig, auto_types, decisions


def _get_type_string(type_obj):
    """
    將 libclang Type 轉換為型別字串

    例：
        - int → "int"
        - bool → "bool"
        - double → "float"
        - struct LoanApplication * → "struct"
    """
    kind = type_obj.kind

    # 基本型別
    if kind == TypeKind.INT:
        return "int"
    elif kind == TypeKind.LONG:
        return "int"
    elif kind == TypeKind.FLOAT:
        return "float"
    elif kind == TypeKind.DOUBLE:
        return "float"
    elif kind == TypeKind.BOOL:
        return "bool"

    # 指標型別（結構體指標）
    elif kind == TypeKind.POINTER:
        pointee = type_obj.get_pointee()
        if pointee.kind == TypeKind.STRUCT:
            return "struct"
        return "pointer"

    # 結構體
    elif kind == TypeKind.STRUCT:
        return "struct"

    else:
        return "unknown"


def _extract_conditions(func_cursor):
    """
    從函數中提取所有決策條件

    找出 if/while 中的布林表達式
    """
    conditions = []

    def visit_for_conditions(cursor):
        # IF 語句
        if cursor.kind == CursorKind.IF_STMT:
            # if 的條件在第一個子節點
            children = list(cursor.get_children())
            if children:
                cond_expr = children[0]
                cond_str = _extract_expression(cond_expr)
                if cond_str:
                    conditions.append({
                        'type': 'if',
                        'expr': cond_str,
                        'line': cursor.location.line
                    })
                    print(f"  [IF] {cond_str} (line {cursor.location.line})")

        # WHILE 語句
        elif cursor.kind == CursorKind.WHILE_STMT:
            children = list(cursor.get_children())
            if children:
                cond_expr = children[0]
                cond_str = _extract_expression(cond_expr)
                if cond_str:
                    conditions.append({
                        'type': 'while',
                        'expr': cond_str,
                        'line': cursor.location.line
                    })
                    print(f"  [WHILE] {cond_str} (line {cursor.location.line})")

        # 遞迴
        for child in cursor.get_children():
            visit_for_conditions(child)

    visit_for_conditions(func_cursor)
    return conditions


def _extract_expression(expr_cursor):
    """
    從 libclang Cursor 提取表達式字串

    例：age >= 65 → "age >= 65"
    """
    # 獲取源碼片段
    try:
        extent = expr_cursor.extent
        start_offset = extent.start.offset
        end_offset = extent.end.offset

        # 從原始檔案讀取
        with open(expr_cursor.translation_unit.spelling, 'r') as f:
            src = f.read()
            expr_str = src[start_offset:end_offset].strip()

            # 簡單清理
            if expr_str.endswith(')'):
                expr_str = expr_str[:-1].strip()

            return expr_str
    except Exception as e:
        print(f"  [警告] 無法提取表達式: {e}")
        return None


# ════════════════════════════════════════════════════════
# 使用範例
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("vaccine_eligibility.c")
    print("=" * 60)

    sig, types, conds = extract_from_c_fixture(
        "tests/fixtures/vaccine_eligibility.c",
        "vaccine_eligible"
    )

    print(f"\n簽名: {sig}")
    print(f"型別: {types}")
    print(f"條件數: {len(conds)}")
    for i, cond in enumerate(conds, 1):
        print(f"  {i}. [{cond['type']}] {cond['expr']}")

    print("\n" + "=" * 60)
    print("loan_approval.c")
    print("=" * 60)

    sig2, types2, conds2 = extract_from_c_fixture(
        "tests/fixtures/loan_approval.c",
        "loan_approved"
    )

    print(f"\n簽名: {sig2}")
    print(f"型別: {types2}")
    print(f"條件數: {len(conds2)}")
    for i, cond in enumerate(conds2, 1):
        print(f"  {i}. [{cond['type']}] {cond['expr']}")
