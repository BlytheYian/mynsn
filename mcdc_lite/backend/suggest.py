"""輕量原始碼分析：偵測函式、推斷參數型別、建議測試值域。

純 stdlib ast 實作，不 import ifl_mcdc——所有真正的測試生成／覆蓋率計算一律
呼叫既有的 ifl_api，這裡只負責幫新手使用者把表單先填好，減少手動輸入。
"""
from __future__ import annotations

import ast


def list_functions(source_code: str) -> list[str]:
    """回傳原始碼中所有頂層函式名稱（依出現順序）。"""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []
    return [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]


def _bool_context_names(func_node: ast.FunctionDef) -> set[str]:
    """收集在布林語境（if/while/assert 的條件、not、and/or 運算元）中「直接」出現、
    沒有被比較運算式包住的變數名——這些多半是布林旗標而非數值。"""
    names: set[str] = set()

    def walk_bool_expr(node: ast.expr) -> None:
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            walk_bool_expr(node.operand)
        elif isinstance(node, ast.BoolOp):
            for v in node.values:
                walk_bool_expr(v)

    for node in ast.walk(func_node):
        if isinstance(node, (ast.If, ast.While, ast.Assert, ast.IfExp)):
            walk_bool_expr(node.test)
    return names


def _condition_var_names(func_node: ast.FunctionDef) -> set[str]:
    """收集函式內所有 if 判斷式用到的變數名稱，不管是不是函式簽名宣告的參數——
    用來抓出簽名沒宣告、但條件式裡直接用到的自由變數（例如函式外部定義的常數，
    透過閉包／全域查找被引用）。這裡刻意跟後端引擎自己解析決策節點的邏輯一致
    （引擎收集的也是條件式裡出現的所有 Name，同樣不分是不是形式參數），這樣
    畫面上列出來的變數才會跟引擎實際判斷「這個決策需要哪些變數」一致。"""
    names: set[str] = set()
    for node in ast.walk(func_node):
        if isinstance(node, ast.If):
            for n in ast.walk(node.test):
                if isinstance(n, ast.Name):
                    names.add(n.id)
    return names


def infer_param_types(source_code: str, func_name: str) -> dict[str, str]:
    """優先用型別註解；沒有註解時，若變數只在布林語境中被直接使用（未經比較），
    推斷為 bool，否則預設 int。除了函式簽名宣告的參數，也會一併列出條件式裡
    用到、但簽名沒宣告的自由變數（順序排在宣告參數後面）。"""
    result: dict[str, str] = {}
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return result
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            bool_names = _bool_context_names(node)
            declared = {arg.arg for arg in node.args.args}
            for arg in node.args.args:
                ann = arg.annotation
                if isinstance(ann, ast.Name) and ann.id.lower() in ("int", "bool", "float"):
                    result[arg.arg] = ann.id.lower()
                elif arg.arg in bool_names:
                    result[arg.arg] = "bool"
                else:
                    result[arg.arg] = "int"
            free_names = sorted(_condition_var_names(node) - declared)
            for name in free_names:
                result[name] = "bool" if name in bool_names else "int"
            break
    return result


def list_free_vars(source_code: str, func_name: str) -> list[str]:
    """列出條件式裡用到、但函式簽名沒有宣告的自由變數名稱（跟 infer_param_types
    找到的是同一組規則，這裡只回傳「不是正式參數」的那部分，給前端顯示風險提示
    用——引擎實際呼叫函式時只會傳入正式參數，這些自由變數就算在畫面上設定了
    型別／範圍，函式本身如果不接受同名參數，執行時仍可能出錯。"""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            declared = {arg.arg for arg in node.args.args}
            return sorted(_condition_var_names(node) - declared)
    return []


def build_signature(func_name: str, param_types: dict[str, str]) -> str:
    params = ", ".join(f"{name}: {typ}" for name, typ in param_types.items())
    return f"{func_name}({params})"


def extract_function_source(source_code: str, func_name: str) -> str | None:
    """取出單一函式定義的原始碼片段（不含裝飾器）。

    MC/DC 是針對單一函式做覆蓋率分析——如果把整個多函式檔案原封不動丟給引擎，
    引擎解析出的決策節點會涵蓋檔案裡「所有」函式，但測試執行時只會呼叫目標函式，
    導致其他函式的條件永遠測不到、覆蓋率被拉低甚至跑到迭代上限都無法收斂。
    所以送進引擎前一定要先切出目標函式本身。
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(source_code, node)
    return None


def suggest_bounds(source_code: str, domain_types: dict[str, str]) -> dict[str, list[int]]:
    """從比較運算式的門檻值推斷合理值域；找不到門檻時用變數名稱關鍵字猜測。"""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {}

    thresholds: dict[str, list[float]] = {}

    class _Visitor(ast.NodeVisitor):
        def visit_Compare(self, node: ast.Compare) -> None:
            if isinstance(node.left, ast.Name):
                name = node.left.id
                for val in node.comparators:
                    if isinstance(val, ast.Constant) and isinstance(val.value, (int, float)):
                        thresholds.setdefault(name, []).append(float(val.value))
            for val in node.comparators:
                if (
                    isinstance(val, ast.Name)
                    and isinstance(node.left, ast.Constant)
                    and isinstance(node.left.value, (int, float))
                ):
                    thresholds.setdefault(val.id, []).append(float(node.left.value))
            self.generic_visit(node)

    _Visitor().visit(tree)

    bounds: dict[str, list[int]] = {}
    for name, typ in domain_types.items():
        if typ == "bool":
            continue
        vals = thresholds.get(name, [])
        if not vals:
            nl = name.lower()
            if any(k in nl for k in ("age", "yr")):
                bounds[name] = [0, 130]
            elif any(k in nl for k in ("day", "time")):
                bounds[name] = [0, 3650]
            elif any(k in nl for k in ("alt", "sep", "altitude")):
                bounds[name] = [0, 10000]
            elif any(k in nl for k in ("rate", "speed")):
                bounds[name] = [-1000, 1000]
            else:
                bounds[name] = [0, 100]
        else:
            lo_t, hi_t = min(vals), max(vals)
            lo = max(0, int(lo_t * 0.5)) if lo_t > 0 else int(lo_t * 1.5)
            hi = int(hi_t * 1.5)
            bounds[name] = [lo, hi]
    return bounds
