# IFL-MCDC：迭代反饋迴圈驅動的 MC/DC 測試生成系統

以 SMT 求解器導引 LLM 生成測試案例，自動達成 MC/DC 覆蓋率。

---

## 專案結構

```
ifl_mcdc-master/
│
├── ifl_mcdc/                        # 核心套件
│   ├── config.py                    # 全域設定（LLM provider、SMT timeout、迭代參數）
│   ├── orchestrator.py              # IFL 主控迴圈，協調三層模組
│   ├── exceptions.py                # 自訂例外（Z3TimeoutError、LLMSamplingError 等）
│   │
│   ├── layer1/                      # 解析、探針注入、覆蓋追蹤
│   │   ├── ast_parser.py            # Python AST 解析，提取決策節點與原子條件
│   │   ├── probe_injector.py        # 動態注入探針到目標程式碼
│   │   ├── coverage_engine.py       # MC/DC 覆蓋率計算（_others_ok 配對驗證）
│   │   └── coupling_graph.py        # 條件耦合關係圖（AND/OR 結構分析）
│   │
│   ├── layer2/                      # SMT 約束合成
│   │   ├── smt_synthesizer.py       # Z3 約束合成（True 側 + 補集 False 側）
│   │   ├── gap_analyzer.py          # 未覆蓋缺口分析與難度估算
│   │   ├── bound_extractor.py       # 從 Z3 模型提取變數邊界（BoundSpec）
│   │   └── boolean_derivative.py    # 布林導數計算（MC/DC 獨立影響性）
│   │
│   ├── layer3/                      # LLM 互動
│   │   ├── llm_sampler.py           # LLM 後端（OpenAI / Anthropic / Ollama）、重試、JSON 解析
│   │   ├── prompt_builder.py        # 四段式 Gap-Guided Prompt 建構
│   │   ├── domain_validator.py      # 領域規則驗證（型別、值域）
│   │   └── acceptance_gate.py       # Gate 驗證：判斷測試案例是否提升覆蓋率
│   │
│   ├── models/                      # 資料結構
│   │   ├── decision_node.py         # DecisionNode、ConditionSet、Condition
│   │   ├── coverage_matrix.py       # MCDCMatrix、GapEntry
│   │   ├── smt_models.py            # SMTResult、BoundSpec
│   │   ├── probe_record.py          # ProbeRecord、ProbeLog
│   │   └── validation.py            # DomainRule、ValidationResult
│   │
│   └── data/
│       ├── clinical_profiles.json   # 各 fixture 的臨床流行病學參考資料
│       └── clinical_profile_loader.py
│
├── tests/
│   ├── fixtures/                    # 受測 Python 函式（實驗對象）
│   │   ├── vaccine_eligibility.py   # 流感疫苗資格篩選（k=5）
│   │   ├── loan_approval.py         # 銀行貸款審核（k=6）
│   │   ├── surgery_risk.py          # 手術風險評估（k=9）
│   │   ├── icu_admission.py         # ICU 入住評估（k=10）
│   │   ├── tcas.py                  # 空中防撞系統 TCAS（k=12）
│   │   ├── dose_adjustment.py       # 藥物劑量調整
│   │   └── complex_medical_logic.py # 複雜醫療邏輯
│   │
│   ├── unit/                        # 單元測試（對應每個模組）
│   │   ├── test_layer1_ast_parser.py
│   │   ├── test_layer1_coupling_graph.py
│   │   ├── test_layer1_coverage_engine.py
│   │   ├── test_layer1_probe_injector.py
│   │   ├── test_layer2_boolean_derivative.py
│   │   ├── test_layer2_gap_analyzer.py
│   │   ├── test_layer2_smt_synthesizer.py
│   │   ├── test_layer2_pairwise_feasibility.py
│   │   ├── test_layer3_acceptance_gate.py
│   │   ├── test_layer3_domain_validator.py
│   │   ├── test_layer3_llm_sampler.py
│   │   ├── test_layer3_prompt_builder.py
│   │   ├── test_models.py
│   │   ├── test_orchestrator.py
│   │   └── test_run_experiments.py
│   │
│   ├── integration/                 # 整合測試
│   │   ├── test_full_pipeline.py
│   │   ├── test_layer1_to_layer2.py
│   │   ├── test_layer2_to_layer3.py
│   │   ├── test_complex_fixtures.py
│   │   └── test_vaccine_e2e.py
│   │
│   └── QuixBugs/                    # QuixBugs 基準測試集
│
├── docs/
│   ├── SRS.md / SRS_IFL_MCDC_System.md    # 軟體需求規格
│   ├── SDD.md / SDD_IFL_MCDC_System.md    # 軟體設計文件
│   └── STD.md / STD_IFL_MCDC_System.md    # 軟體測試文件
│
├── run_experiments.py               # 早期實驗腳本
├── run_experiments1.py              # 實驗腳本 v1
├── run_experiments2.py              # 主要對照實驗（A:隨機 / B:純LLM / C:IFL）
├── run_complexity_experiments.py    # 複雜度梯度實驗
├── run_convergence.py               # 收斂性實驗
├── run_crosshair_benchmark.py       # CrossHair 基準比較
├── run_ifl_diversity.py             # 多樣性分析實驗
├── run_pynguin_experiments.py       # Pynguin 基準比較
├── run_validation.py                # 驗證腳本
├── run_validation_complex.py        # 複雜 fixture 驗證
│
├── diversity_reporter.py            # D1/D2/D3 多樣性指標計算
├── statistical_validator.py         # 統計顯著性驗證
├── validate_llm_semantics.py        # LLM 語意品質驗證
├── generate_compliance_report.py    # 合規報告生成
├── validation_fixtures.py           # 驗證用 fixture
│
├── debug_ifl.py                     # 除錯工具
├── diagnose_ifl_gap.py              # Gap 診斷工具
├── _gen_report.py / _gen_report2.py # 報告生成輔助腳本
├── _rerun.py                        # 重跑實驗腳本
│
├── pyproject.toml                   # 套件設定與依賴
├── .env.example                     # 環境變數範本
└── readme.md                        # 本文件
```

---

## 系統架構

```
目標程式碼
    │
    ▼
Layer 1：解析 & 覆蓋追蹤
  ASTParser → DecisionNode / ConditionSet
  ProbeInjector → 動態探針注入
  MCDCCoverageEngine → 覆蓋矩陣 & 缺口識別
    │
    ▼ GapEntry（未覆蓋條件 + 難度）
Layer 2：SMT 約束合成
  GapAnalyzer → 選擇最易缺口
  SMTConstraintSynthesizer（Z3）→ 合法解 + 變數邊界
    │
    ▼ BoundSpec（精確值域）+ SMT 錨點範例
Layer 3：LLM 生成 & 驗證
  PromptConstructor → 四段式 Gap-Guided Prompt
  LLMSampler → 呼叫 LLM、JSON 解析、重試（最多 3 次）
  AcceptanceGate → 執行探針、驗證覆蓋率提升
    │
    ▼
IFLOrchestrator 主迴圈（迭代直至收斂或預算耗盡）
```

---

### 頂層模組

#### `ifl_mcdc/config.py`
全域設定入口，所有可調整參數集中管理（對應 SDD 第 8 章）。

| 類別 / 函式 | 說明 |
|---|---|
| `IFLConfig(BaseSettings)` | pydantic-settings 設定類，讀取環境變數（前綴 `IFL_`）或 `.env` 檔 |
| `.llm_provider / .llm_model / .llm_api_key` | LLM 供應商與認證設定 |
| `.smt_timeout_ms` | Z3 求解器超時限制（毫秒） |
| `.max_iterations / .min_coverage` | IFL 迭代上限與收斂門檻 |
| `.domain_types / .domain_bounds` | 變數型別（int/bool）與合法值域映射 |
| `.llm_backend` *(property)* | 依 `llm_provider` 回傳對應的 `LLMBackend` 實例 |
| `.domain_validator` *(property)* | 依 `domain_types/domain_bounds` 動態建立 `DomainValidator` |
| `.clinical_profile` *(property)* | 從 `clinical_profiles.json` 讀取對應 fixture 的臨床資料 |
| `_is_valid_int(v, lo, hi)` | 容忍 LLM 輸出浮點/字串格式整數的輔助驗證函式 |

#### `ifl_mcdc/orchestrator.py`
IFL 主控迴圈，協調三層模組（對應 SDD 第 6 章）。

| 類別 / 方法 | 說明 |
|---|---|
| `IFLResult` | 執行結果 dataclass：覆蓋率、測試套件、損失歷程、失敗日誌、迭代詳情 |
| `IFLOrchestrator.__init__` | 初始化所有子模組（ASTParser、Engine、GapAnalyzer、SMT、Prompt、Sampler、Gate） |
| `.run(source_path)` | 主流程：解析 → 探針注入 → 隨機初始測試 → IFL 迭代迴圈 → 回傳 IFLResult |
| `._run_test(module, test_case, log)` | 執行單一測試案例，設定 test_id，呼叫被測函式，記錄探針 |
| `._load_from_string(source, module_name)` | 動態編譯並 `exec` 儀表板化原始碼，載入為 Module |
| `._inject_probes(module, log)` | 將 `_ifl_probe` / `_ifl_record_decision` 注入儀表板化模組命名空間 |
| `._generate_random_test(dn, domain_types, domain_bounds)` | 依型別與值域隨機生成初始測試案例（int/bool 各自取樣） |
| `._fill_missing_params(case, domain_types, domain_bounds)` | 補全 LLM 輸出中遺漏的函數參數，避免 TypeError |

#### `ifl_mcdc/exceptions.py`
自訂例外體系，所有例外繼承自 `IFLBaseError`。

| 例外類別 | 觸發時機 |
|---|---|
| `ASTParseError` | AST 解析失敗（語法錯誤或不支援的語法） |
| `CouplingBuildError` | 耦合圖建構異常 |
| `ProbeInjectionError` | 探針 AST 重寫過程出錯 |
| `Z3TimeoutError` | Z3 求解超過 TIMEOUT_MS 限制 |
| `Z3UNSATError` | Z3 回傳 UNSAT（路徑不可達） |
| `LLMSamplingError` | LLM API 重試全部失敗 |
| `DomainValidationError` | 測試資料違反醫療領域規則 |
| `IterationBudgetExhausted` | 迭代次數達到 max_iterations 上限 |

---

### Layer 1：解析 & 覆蓋追蹤（`ifl_mcdc/layer1/`）

#### `layer1/ast_parser.py`
走訪 Python AST，識別所有決策節點，輸出 `DecisionNode` 清單。

| 類別 / 方法 | 說明 |
|---|---|
| `ASTParser(ast.NodeVisitor)` | 繼承 `ast.NodeVisitor`，逐節點觸發對應 `visit_X` |
| `.parse_file(filepath)` | 讀取原始碼檔案，呼叫 `parse_source` |
| `.parse_source(source)` | 解析字串，回傳 `DecisionNode` 列表；語法錯誤拋 `ASTParseError` |
| `.visit_If / .visit_While / .visit_Assert / .visit_IfExp` | 分別處理四種決策節點類型 |
| `._register_decision(node, ntype, test_expr)` | 登記一個決策節點，指派 `D{n}` ID，呼叫 `_decompose_conditions` |
| `._decompose_conditions(decision_id, node)` | 遞迴分解布林表達式為原子條件（`AtomicCondition`），建構 `ConditionSet` |
| `._get_context(line_no, radius=2)` | 回傳原始碼前後各 radius 行的 source_context 片段 |

#### `layer1/probe_injector.py`
繼承 `ast.NodeTransformer`，對決策節點進行 AST 重寫，注入執行期探針。
核心設計：用 `lambda` 封裝每個原子條件的求值，繞過 Python 短路求值問題。

| 類別 / 函式 | 說明 |
|---|---|
| `_ifl_probe(cond_id, eval_func)` | 全域探針函式（注入被測模組）；呼叫 `eval_func()`，記錄條件真值到 `_GLOBAL_LOG`，攔截錯誤並記錄 `error_msg` |
| `_ifl_record_decision(decision_id, result)` | 回填決策節點最後 k 筆記錄的 `decision` 欄位 |
| `_GLOBAL_LOG` / `_CURRENT_TEST_ID` | 模組級全域狀態：日誌容器與執行緒本地的當前 test_id |
| `ProbeInjector(ast.NodeTransformer)` | 重寫 If/While/Assert 節點，插入探針賦值語句 |
| `.inject(source)` | 主入口：解析 → `visit` → `ast.unparse`，回傳儀表板化原始碼字串 |
| `.visit_If / .visit_While / .visit_Assert` | 重寫對應節點：在前面插入條件賦值、決策賦值、回填呼叫 |
| `._build_assignments(dn)` | 生成 `_D{n}_c{m} = _ifl_probe("D{n}.c{m}", lambda: expr)` 賦值語句列表 |
| `._build_decision_assign(dn)` | 生成 `_D{n}_decision = <重組表達式>` 賦值語句 |
| `._build_record_call(dn)` | 生成 `_ifl_record_decision("D{n}", _D{n}_decision)` 呼叫語句 |

#### `layer1/coverage_engine.py`
從 `ProbeLog` 建立並增量更新 `MCDCMatrix`，實作 MC/DC 唯一因變體（unique cause）驗證。

| 類別 / 方法 | 說明 |
|---|---|
| `MCDCCoverageEngine` | 無狀態覆蓋率計算引擎 |
| `.build_matrix(cond_set, log)` | 依 log 中所有已有 test_id 建立初始 MCDCMatrix |
| `.update(matrix, log, new_test_id)` | 增量更新：呼叫 `_update_one`，回傳損失是否降低（True/False） |
| `._update_one(matrix, log, test_id)` | 將 new_test_id 與所有既有測試配對，呼叫 `_check_pair` |
| `._check_pair(matrix, recs_a, recs_b)` | 比對兩測試是否形成有效 MC/DC 配對：決策相異 + 目標條件相異 + 其他條件相同（`_others_ok`） |
| `._others_ok(matrix, target_id, map_a, map_b)` | MC/DC 唯一因驗證：所有非目標條件的探針值在兩測試中必須相同 |

#### `layer1/coupling_graph.py`
從 BoolOp AST 結構建立 k×k 耦合鄰接矩陣（AND/OR 結構分析）。

| 類別 / 方法 | 說明 |
|---|---|
| `CouplingGraphBuilder` | 耦合圖建構器 |
| `.build(decision_id, root_expr, conditions)` | 回傳 k×k 矩陣（元素為 "AND"、"OR" 或 None）；用 `_traverse` 遞迴走訪 BoolOp，對跨子節點的葉節點配對設值 |
| `_set(a, b, op)` | 設定矩陣元素，OR 優先（一旦有 OR 關係不覆蓋） |
| `_all_leaves(expr)` | 收集子樹所有原子葉子索引 |
| `_traverse(expr)` | 遞迴走訪 BoolOp，對不同子節點的葉節點對填入 AND/OR |

---

### Layer 2：SMT 約束合成（`ifl_mcdc/layer2/`）

#### `layer2/smt_synthesizer.py`
將 `GapEntry` 轉化為 Z3 可求解的 SMT 公式，合成 True 側與 False 側（補集）的合法輸入向量。

| 類別 / 方法 | 說明 |
|---|---|
| `ASTToZ3Converter` | 將 Python AST 節點遞迴轉換為 Z3 表達式；支援 BoolOp/UnaryOp/Compare/Name/Constant |
| `.convert(decision_node)` | 轉換整個 DecisionNode 的布林表達式 |
| `.convert_cond(cond)` | 轉換單一原子條件（含 negated 旗標） |
| `._visit(node)` | 遞迴走訪 AST，對應 And→`z3.And`、Or→`z3.Or`、Not→`z3.Not`、Compare→比較算子 |
| `SMTConstraintSynthesizer` | 主合成器；無狀態，持有 `domain_bounds` |
| `.synthesize(decision_node, gap, domain_types, preceding_nodes, excluded_solutions)` | 建構 Φ_gap（含路徑約束與多樣性排除），Z3 求解，SAT → 回傳 `SMTResult`（含 `BoundSpec`），UNSAT → 回傳 core，超時 → 拋 `Z3TimeoutError` |
| `.synthesize_complement(decision_node, gap, domain_types, true_side_concrete, preceding_nodes)` | 固定非目標條件變數值，Z3 僅為目標條件找 False 側解，確保 `_others_ok` 成立；UNSAT 回 None |
| `.synthesize_with_preferences(...)` ⚠️ | 以 LLM 語意偏好（bool 值、int 偏好 low/medium/high）為軟約束，三層 fallback 確保找到解 —— 生產路徑未呼叫（偏好式流程尚未接入） |
| `._build_phi_gap(dn, gap, z3_vars, f_expr, domain_types)` | 建構 Φ_gap：(A) 決策=True，(B) 目標條件=True，(C) 域約束，(D) 補集可行性配對約束 |
| `._build_complement_phi(...)` | 建構補集公式：決策=False、目標條件=False、域約束 |
| `._create_z3_vars(dn, domain_types)` | 為所有出現在條件中的變數建立 Z3 符號變數（Int/Bool/Real） |
| `._z3_val_to_python(val, var_type)` | 將 Z3 模型值轉換為 Python 原生型別 |

#### `layer2/gap_analyzer.py`
從 `MCDCMatrix` 提取未覆蓋的翻轉對，按難度排序後輸出 `GapEntry` 列表。

| 類別 / 方法 | 說明 |
|---|---|
| `GapAnalyzer` | 缺口分析器（無狀態） |
| `.analyze(matrix)` | 掃描所有條件的 F2T/T2F 方向，提取未覆蓋者，按 `estimated_difficulty` 升序排列 |
| `._estimate_difficulty(cond_set, cond_id)` | 難度 = 耦合邊數量 / (k-1)；耦合越多，SMT 約束越複雜 |

#### `layer2/bound_extractor.py`
從 Z3 Φ_gap 公式求每個變數的真實合法範圍，輸出 `BoundSpec` 列表。
改進：用 `z3.Optimize()` 做 maximize/minimize，而非只取點值 ±10。

| 類別 / 方法 | 說明 |
|---|---|
| `BoundExtractor` | 邊界萃取器（無狀態） |
| `.extract(z3_model, z3_vars, domain_types, domain_bounds, phi)` | 逐變數：bool → 取 model 值為 valid_set；int → 呼叫 `_solve_int_bounds`；float → 點值 ±10 |
| `._solve_int_bounds(z3_var, phi, db_lo, db_hi)` | 用兩次 `z3.Optimize`（minimize + maximize）求 int 變數在 Φ_gap 下的真實上下界 |

#### `layer2/boolean_derivative.py` ⚠️ 整個模組未接入主流程（僅測試使用）
使用 Z3 精確計算布林導數 ∂f/∂xᵢ，偵測遮罩效應（AND 短路 / OR 遮蔽）。`IFLOrchestrator` 從未呼叫本模組任何類別；遮罩偵測功能設計完成但尚未接入主迴圈。

| 類別 / 方法 | 說明 |
|---|---|
| `BooleanDerivativeEngine` ⚠️ | 布林導數引擎（數學精確，不採樣）—— 生產路徑未使用 |
| `.compute(decision_node, target_cond)` ⚠️ | 計算 ∂f/∂target_cond 是否恆為 0；SAT → `is_masked=False`；UNSAT → `is_masked=True` 並呼叫 `_find_masking_cause` |
| `._find_masking_cause(dn, target, z3_vars, f_t, f_f)` ⚠️ | 逐一固定耦合條件（OR 夥伴固 False，AND 夥伴固 True），找出真正遮罩成因 |
| `._build_z3_expr(dn, z3_vars)` ⚠️ | 透過 `_CondIdConverter` 以 cond_id 為葉節點建構 Z3 公式 |
| `_CondIdConverter` ⚠️ | 內部輔助類：將決策表達式轉為以 cond_id 為葉節點的 Z3 公式（expression → cond_id 映射） |

---

### Layer 3：LLM 生成 & 驗證（`ifl_mcdc/layer3/`）

#### `layer3/llm_sampler.py`
LLM 後端抽象介面與採樣器：呼叫 LLM、解析 JSON、DomainValidator 驗證、指數退避重試。

| 類別 / 方法 | 說明 |
|---|---|
| `LLMBackend(ABC)` | 統一後端抽象介面，定義 `complete(prompt, max_tokens)` |
| `OpenAIBackend` | OpenAI ChatCompletion 後端（gpt-4.1-mini 預設） |
| `OllamaBackend` | 本地 Ollama `/api/chat` 後端（用標準庫 urllib，無額外依賴） |
| `LLMSampler` | 採樣器主類；最多重試 `MAX_RETRIES=3` 次 |
| `.sample(prompt)` | 呼叫後端 → 解析 JSON（`_parse_json`）→ 型別預處理（字串布林/浮點→整數）→ `DomainValidator` 驗證 → 失敗則退避重試 |
| `.sample_qualitative(prompt, domain_types)` ⚠️ | 取「語意偏好」輸出（bool=True/False，int=low/medium/high），供 `synthesize_with_preferences` 使用 —— 生產路徑未呼叫（偏好式流程尚未接入） |
| `._parse_json(raw)` | 穩健 JSON 解析：去 markdown → 修正 Python bool → `raw_decode` → regex fallback |
| `._build_retry_prompt(original, error)` | 將錯誤訊息加入重試提示詞，幫助 LLM 修正 |

#### `layer3/prompt_builder.py`
建構四段式 Gap-Guided Prompt（§1 情境 → §2 目標缺口 → §3 精確約束 → §4 輸出格式）。

| 類別 / 函式 | 說明 |
|---|---|
| `PromptConstructor` | Prompt 建構器，維護呼叫計數以循環子區間（邊界/中間/極端） |
| `.build(decision_node, gap, bound_specs, func_signature, ...)` | 主入口；選填段落：`sec_path`（路徑可達性）、`sec_mask`（OR 遮罩警告）、`sec_diversity`（多樣性要求）、`sec_clinical`（臨床比例）、`sec_error`（前輪錯誤回饋）、`smt_example`（錨點範例）；超過 MAX_TOKENS 時截斷 §1 source_context |
| `.build_qualitative(decision_node, gap, func_signature, ...)` ⚠️ | 偏好式 Prompt：只要求 LLM 給 low/medium/high 方向，不要精確數值 —— 生產路徑未呼叫（偏好式流程尚未接入） |
| `_build_clinical_section(profile)` | 將臨床流行病學比例 dict 轉為自然語言段落（族群背景、盛行率、共病說明） |

#### `layer3/domain_validator.py`
驗證 LLM 輸出的測試案例是否符合領域規則（型別、值域）。

| 類別 / 常數 | 說明 |
|---|---|
| `DEFAULT_MEDICAL_RULES` | 預設醫療規則列表：age 0~130、days_since_last ≥ 0、high_risk/egg_allergy 為 bool |
| `DomainValidator` | 驗證器；接受自訂 `rules` 列表或使用預設醫療規則 |
| `.validate(test_case_json)` | 解析 JSON → 逐規則驗證 → 回傳 `ValidationResult`（passed + violations 列表） |

#### `layer3/acceptance_gate.py`
接受閘：判斷新測試案例是否使 MC/DC 覆蓋損失 L(X) 降低。

| 類別 / 方法 | 說明 |
|---|---|
| `AcceptanceGate` | 接受閘（持有 `MCDCCoverageEngine` 引用） |
| `.evaluate(matrix, log, new_test_id)` | 呼叫 `engine.update()`，回傳 `compute_loss()` 是否降低（True = 接受） |

---

### Models：資料結構（`ifl_mcdc/models/`）

#### `models/decision_node.py`
AST 解析的核心資料模型。

| 資料類別 | 欄位與說明 |
|---|---|
| `AtomicCondition` | `cond_id`（格式 D{n}.c{m}）、`expression`（原始表達式字串）、`var_names`、`negated`、`ast_node`；`.evaluate(bindings)` ⚠️ 用 eval 求值 —— 生產路徑未呼叫（探針以 `lambda` 在執行期求值，此方法僅測試使用） |
| `ConditionSet` | `decision_id`、`conditions`（AtomicCondition 列表）、`coupling_matrix`（k×k AND/OR/None）；`.get_coupled(cond_id)` 回傳耦合條件列表 |
| `DecisionNode` | `node_id`、`node_type`（If/While/Assert/IfExp）、`line_no`、`expression_str`、`condition_set`、`source_context` |

#### `models/coverage_matrix.py`
MC/DC 覆蓋率矩陣與缺口條目。

| 資料類別 | 欄位與說明 |
|---|---|
| `GapEntry` | `condition_id`、`flip_direction`（F2T/T2F）、`missing_pair_type`、`estimated_difficulty`（0.0~1.0） |
| `MCDCMatrix` | `condition_set`、`_covered`（已覆蓋翻轉對集合）、`_infeasible`（不可行翻轉對集合）；`.coverage_ratio`（傳統）、`.compute_loss()`、`.mark_covered()`；以下方法 ⚠️ 僅測試使用：`.effective_coverage_ratio`、`.feasible_count`、`.compute_effective_loss()`、`.mark_infeasible()`（Orchestrator 用自身 `self._infeasible: set[str]`）、`.get_gap_list()`（GapAnalyzer 直接存取 `_covered`） |

#### `models/smt_models.py`
SMT 求解相關資料模型。

| 資料類別 | 欄位與說明 |
|---|---|
| `MaskingReport` ⚠️ | `condition_id`、`is_masked`、`masking_cause`（遮罩成因 cond_id 列表）、`derivative_value`（0 或 1） —— 僅由未接入主流程的 `BooleanDerivativeEngine` 回傳，生產路徑未使用 |
| `BoundSpec` | `var_name`、`var_type`、`interval`（數值型 min/max tuple）、`valid_set`（布林型允許值 frozenset）、`medical_unit`、`sub_intervals`（三子區間列表） |
| `SMTResult` | `satisfiable`、`model`（Z3 原始物件）、`model_python`（Python-native 具體解）、`bound_specs`（True 側）、`complement_bound_specs`（False 側）、`core`（UNSAT core）、`solve_time` |

#### `models/probe_record.py`
探針記錄與執行緒安全日誌容器。

| 資料類別 | 欄位與說明 |
|---|---|
| `ProbeRecord` | `test_id`、`cond_id`、`value`（探針布林值）、`decision`（整體決策結果）、`timestamp`、`error_msg`（攔截的求值錯誤） |
| `ProbeLog` | `records` 列表；用 `threading.Lock` 保護；`.append(record)`、`.get_by_test(test_id)`；`.clear()` ⚠️ 從未呼叫（Orchestrator 全程使用同一 ProbeLog 實例） |

#### `models/validation.py`
領域驗證結果資料模型。

| 資料類別 | 欄位與說明 |
|---|---|
| `Violation` | `field`、`description`、`actual_value`（違規的實際值字串） |
| `ValidationResult` | `passed`、`violations`；`.to_corrective_prompt()` 將違規轉為 LLM 可用的修正提示文字 |
| `DomainRule` | `field`、`description`、`validator`（`Callable[[Any], bool]`） |

---

### Data：臨床輔助資料（`ifl_mcdc/data/`）

#### `data/clinical_profile_loader.py`
從 `clinical_profiles.json` 讀取各 fixture 的臨床流行病學比例，提供快取機制。

| 類別 / 方法 | 說明 |
|---|---|
| `ClinicalProfileLoader` | 載入器；`_cache` 避免重複 I/O |
| `.load(fixture_name)` | 回傳指定 fixture 的 dict，找不到或讀取失敗時回傳 None（不拋例外） |
| `.build_prompt_section(profile)` ⚠️ | 呼叫 `prompt_builder._build_clinical_section`，將 dict 轉為自然語言 Prompt 段落 —— 從未呼叫（Orchestrator 直接將 dict 傳給 `PromptConstructor.build()`，後者呼叫模組私有函式 `_build_clinical_section`） |

---

## 快速開始

**安裝依賴**

```bash
pip install -e .
```

**設定環境變數**（複製 `.env.example` 並填入）

```bash
IFL_LLM_PROVIDER=openai        # openai  / ollama
IFL_LLM_MODEL=gpt-4o-mini
IFL_LLM_API_KEY=sk-...
```

**執行主要對照實驗**

```bash
python run_experiments2.py

python run_experiments2.py --fixture tcas --runs 5

python run_experiments2.py --fixture vaccine_eligibility,loan_approval --runs 10
```

**執行測試**

```bash
pytest tests/unit/
pytest tests/integration/
```

---

## 實驗設計

`run_experiments2.py` 以三組對照評估 IFL 效益：

| 組別 | 方法 | LLM 呼叫 |
|------|------|----------|
| A 對照組 | 純隨機生成 | 無 |
| B 對照組 | 純 LLM（無 SMT 導引） | `b_iters` 次 |
| C 實驗組 | IFL（SMT + LLM） | 自適應，上限 `max_iter_ifl` |

評估指標：MC/DC 覆蓋率、收斂率、迭代次數、有效生成比、D1/D2/D3 多樣性。

---

## 支援的 LLM 後端

| Provider | 類別 | 備註 |
|----------|------|------|
| `openai` | `OpenAIBackend` | 預設 `gpt-4.1-mini`，支援 temperature |
| `ollama` | `OllamaBackend` | 本地推論 |

uvicorn ifl_api.main:app --port 8100
uvicorn mynsn.backend.main:app --port 8200