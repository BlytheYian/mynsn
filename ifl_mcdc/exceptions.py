class IFLBaseError(Exception):
    """IFL 系統所有自訂例外的基底類別"""
    pass


class ASTParseError(IFLBaseError):
    """AST 解析失敗，含語法錯誤或不支援的語法"""
    pass


class CouplingBuildError(IFLBaseError):
    """耦合圖建構異常"""
    pass


class ProbeInjectionError(IFLBaseError):
    """探針注入失敗，AST 重寫過程出錯"""
    pass


class Z3TimeoutError(IFLBaseError):
    """Z3 求解超過 TIMEOUT_MS 限制"""
    pass


class Z3UNSATError(IFLBaseError):
    """Z3 回傳 UNSAT，路徑不可達"""
    pass


class LLMSamplingError(IFLBaseError):
    """LLM API 重試全部失敗"""
    pass


class DomainValidationError(IFLBaseError):
    """測試資料違反醫療領域規則"""
    pass


class IterationBudgetExhausted(IFLBaseError):
    """IFL 迭代次數達到 max_iterations 上限"""
    pass
