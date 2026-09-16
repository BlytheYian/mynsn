"""
experiment_ui.py

IFL MC/DC 實驗平台 — tkinter GUI
使用方式：python experiment_ui.py

實驗由 run_ifl_only.py 透過 subprocess 執行（sys.executable）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

try:
    from nl_to_code import NLToCodeGenerator
    from ifl_mcdc.layer3.llm_sampler import OpenAIBackend, AnthropicBackend, OllamaBackend
    _NL_AVAILABLE = True
except ImportError:
    _NL_AVAILABLE = False

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.ticker as mticker

matplotlib.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": [
        "Microsoft JhengHei", "Microsoft YaHei", "SimHei",
        "Arial Unicode MS", "DejaVu Sans",
    ],
    "axes.unicode_minus": False,
})

_HERE   = Path(__file__).parent.resolve()
_RUNNER = _HERE / "run_ifl_only.py"

PREDEFINED_PY = [
    {"name": "vaccine_eligibility", "k": 5},
    {"name": "loan_approval",        "k": 6},
    {"name": "surgery_risk",         "k": 9},
    {"name": "icu_admission",        "k": 10},
    {"name": "tcas_sir",             "k": 12},
    {"name": "gpca_alarm",           "k": 10},
]

PREDEFINED_C = [
    {"name": "vaccine_eligibility", "k": 5,  "lang": "c"},
    {"name": "loan_approval",        "k": 6,  "lang": "c"},
    {"name": "surgery_risk",         "k": 9,  "lang": "c"},
    {"name": "icu_admission",        "k": 10, "lang": "c"},
    {"name": "gpca_alarm",           "k": 10, "lang": "c"},
    {"name": "tcas_sir",             "k": 7,  "lang": "c"},
]


# ──────────────────────────────────────────────────────────────
# 自然語言生成 Fixture 對話框
# ──────────────────────────────────────────────────────────────

class NLToCodeDialog(tk.Toplevel):
    """輸入自然語言描述 → LLM 生成被測函式 → 加入 fixture 清單。"""

    def __init__(self, parent, llm_vars: dict[str, tk.StringVar]):
        super().__init__(parent)
        self.title("自然語言生成被測函式")
        self.resizable(True, True)
        self.grab_set()
        self.result: dict | None = None
        self._llm_vars = llm_vars
        self._nl_result = None   # NLToCodeResult
        self._generating = False
        self._build()
        self.geometry("640x560")
        self.wait_window()

    def _build(self):
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=2)
        f.rowconfigure(4, weight=1)

        # ── 語言選擇 ──
        lang_row = ttk.Frame(f)
        lang_row.grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(lang_row, text="生成語言：").pack(side="left")
        self._lang_var = tk.StringVar(value="python")
        ttk.Radiobutton(lang_row, text="Python", variable=self._lang_var,
                        value="python").pack(side="left", padx=(4, 12))
        ttk.Radiobutton(lang_row, text="C",      variable=self._lang_var,
                        value="c").pack(side="left")

        # ── NL 輸入 ──
        ttk.Label(f, text="業務規則描述（自然語言）：").grid(
            row=1, column=0, sticky="w", pady=(0, 2))
        self._nl_text = scrolledtext.ScrolledText(f, height=7, wrap="word",
                                                   font=("Consolas", 9))
        self._nl_text.grid(row=2, column=0, sticky="nsew")
        self._nl_text.insert("1.0",
            "範例：申請人年齡須大於等於 18 歲且為高風險族群，或年齡大於等於 65 歲；\n"
            "距上次接種須超過 180 天；且無蛋過敏史，才可接種疫苗。")
        self._nl_text.tag_add("placeholder", "1.0", "end")
        self._nl_text.tag_configure("placeholder", foreground="gray")
        self._nl_text.bind("<FocusIn>", self._clear_placeholder)

        # ── 生成按鈕 + 狀態 ──
        gen_row = ttk.Frame(f)
        gen_row.grid(row=3, column=0, sticky="ew", pady=(6, 4))
        self._gen_btn = ttk.Button(gen_row, text="⚡ 生成程式碼", command=self._start_generate)
        self._gen_btn.pack(side="left")
        self._status_var = tk.StringVar(value="就緒")
        ttk.Label(gen_row, textvariable=self._status_var,
                  foreground="gray").pack(side="left", padx=(10, 0))

        # ── 程式碼預覽 ──
        ttk.Label(f, text="生成的函式程式碼：").grid(
            row=4, column=0, sticky="w", pady=(4, 2))
        self._code_text = scrolledtext.ScrolledText(f, height=10, wrap="none",
                                                     font=("Consolas", 9),
                                                     state="disabled",
                                                     background="#1e1e1e",
                                                     foreground="#d4d4d4")
        self._code_text.grid(row=5, column=0, sticky="nsew")

        # ── metadata ──
        self._meta_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self._meta_var,
                  foreground="#555", wraplength=600,
                  justify="left").grid(row=6, column=0, sticky="w", pady=(4, 0))

        # ── 底部按鈕 ──
        btn_row = ttk.Frame(f)
        btn_row.grid(row=7, column=0, sticky="e", pady=(8, 0))
        ttk.Button(btn_row, text="取消", command=self.destroy).pack(side="right", padx=(6, 0))
        self._add_btn = ttk.Button(btn_row, text="加入清單", command=self._confirm,
                                    state="disabled")
        self._add_btn.pack(side="right")

    def _clear_placeholder(self, _):
        if "placeholder" in self._nl_text.tag_names("1.0"):
            self._nl_text.delete("1.0", "end")
            self._nl_text.tag_delete("placeholder")

    def _make_backend(self):
        provider = self._llm_vars["provider"].get()
        model    = self._llm_vars["model"].get().strip()
        api_key  = self._llm_vars["apikey"].get().strip()
        if provider == "anthropic":
            return AnthropicBackend(model=model or "claude-sonnet-4-6", api_key=api_key)
        if provider == "ollama":
            return OllamaBackend(model=model or "llama3")
        return OpenAIBackend(model=model or "gpt-4o-mini", api_key=api_key)

    def _start_generate(self):
        nl = self._nl_text.get("1.0", "end").strip()
        if not nl:
            messagebox.showwarning("未輸入規則", "請輸入業務規則描述。", parent=self)
            return
        if self._generating:
            return

        provider = self._llm_vars["provider"].get()
        api_key  = self._llm_vars["apikey"].get().strip()
        if provider != "ollama" and not api_key:
            messagebox.showwarning(
                "API Key 未設定",
                "請先在主視窗「LLM 設定」中填入 API Key，再生成程式碼。",
                parent=self,
            )
            return

        self._generating = True
        self._gen_btn.configure(state="disabled")
        self._add_btn.configure(state="disabled")
        self._status_var.set("生成中…")
        lang = self._lang_var.get()
        threading.Thread(target=self._generate_thread, args=(nl, lang), daemon=True).start()

    def _generate_thread(self, nl: str, language: str):
        try:
            backend   = self._make_backend()
            generator = NLToCodeGenerator(backend)
            result    = generator.generate_and_save(
                nl, output_dir=_HERE / "tests" / "fixtures", language=language)
            self.after(0, self._on_success, result)
        except Exception as exc:
            self.after(0, self._on_error, str(exc))

    def _on_success(self, result):
        self._nl_result  = result
        self._generating = False
        if not self.winfo_exists():
            return
        self._gen_btn.configure(state="normal")
        self._add_btn.configure(state="normal")
        ext = ".c" if result.language == "c" else ".py"
        self._status_var.set(f"完成：{result.func_name}{ext}")

        self._code_text.configure(state="normal")
        self._code_text.delete("1.0", "end")
        self._code_text.insert("1.0", result.code)
        self._code_text.configure(state="disabled")

        self._meta_var.set(
            f"domain_types: {result.domain_types}\n"
            f"domain_bounds: {result.domain_bounds}"
        )

    def _on_error(self, msg: str):
        self._generating = False
        if not self.winfo_exists():
            return
        self._gen_btn.configure(state="normal")
        self._status_var.set("失敗")
        messagebox.showerror("生成失敗", msg, parent=self)

    def _confirm(self):
        if not self._nl_result:
            return
        r = self._nl_result
        lang_tag = "C" if r.language == "c" else "Py"
        self.result = {
            "type":     "custom",
            "name":     f"[NL/{lang_tag}] {r.func_name}",
            "file":     str(r.output_path),
            "func":     r.func_name,
            "context":  r.domain_context,
            "maxiter":  40,
            "bounds":   {k: v for k, v in r.domain_bounds.items()},
            "domain_types": r.domain_types,
            "lang":     r.language,
        }
        self.destroy()


# ──────────────────────────────────────────────────────────────
# 自訂 Fixture 對話框
# ──────────────────────────────────────────────────────────────

class AddFixtureDialog(tk.Toplevel):
    """讓使用者輸入自訂 Python 檔案 + 函式資訊。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("新增自訂 Fixture")
        self.resizable(False, False)
        self.grab_set()
        self.result: dict | None = None
        self._build()
        self.wait_window()

    def _build(self):
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        rows = [
            ("檔案路徑", "file",    ""),
            ("函式名稱", "func",    ""),
            ("情境說明", "context", "自訂函式測試"),
            ("最大迭代", "maxiter", "40"),
            ("數值邊界", "bounds",  '{}'),
        ]
        self._vars: dict[str, tk.StringVar] = {}
        for r, (label, key, default) in enumerate(rows):
            ttk.Label(f, text=label + "：").grid(row=r, column=0, sticky="e", padx=(0, 6), pady=3)
            var = tk.StringVar(value=default)
            self._vars[key] = var
            if key == "file":
                frame = ttk.Frame(f)
                frame.grid(row=r, column=1, sticky="ew")
                frame.columnconfigure(0, weight=1)
                ttk.Entry(frame, textvariable=var).grid(row=0, column=0, sticky="ew")
                ttk.Button(frame, text="瀏覽", width=5,
                           command=self._browse).grid(row=0, column=1, padx=(4, 0))
            else:
                ttk.Entry(f, textvariable=var, width=40).grid(row=r, column=1, sticky="ew", pady=3)

        ttk.Label(f, text="數值邊界格式：{\"age\":[0,120], \"score\":[0,100]}",
                  foreground="gray").grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 6))

        btn = ttk.Frame(f)
        btn.grid(row=6, column=0, columnspan=2, sticky="e")
        ttk.Button(btn, text="確定", command=self._ok).pack(side="right", padx=(6, 0))
        ttk.Button(btn, text="取消", command=self.destroy).pack(side="right")

    def _browse(self):
        path = filedialog.askopenfilename(
            title="選擇原始碼",
            filetypes=[("Python / C files", "*.py *.c"), ("Python files", "*.py"), ("C files", "*.c")],
        )
        if path:
            self._vars["file"].set(path)
            # 嘗試自動偵測函式名稱（Python 用 AST，C 用正規表達式）
            try:
                if path.endswith(".c"):
                    import re
                    src = Path(path).read_text(encoding="utf-8")
                    m = re.search(r'\b(\w+)\s*\(', src)
                    if m and not m.group(1) in ("if", "while", "for", "return"):
                        # 找第一個函數定義
                        fm = re.search(r'^\w[\w\s\*]+\s+(\w+)\s*\(', src, re.MULTILINE)
                        if fm:
                            self._vars["func"].set(fm.group(1))
                else:
                    import ast
                    src = Path(path).read_text(encoding="utf-8")
                    tree = ast.parse(src)
                    funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                             and not n.name.startswith("_")]
                    if funcs:
                        self._vars["func"].set(funcs[0])
            except Exception:
                pass

    def _ok(self):
        file = self._vars["file"].get().strip()
        func = self._vars["func"].get().strip()
        if not file or not func:
            messagebox.showwarning("輸入不完整", "請填寫檔案路徑與函式名稱。", parent=self)
            return
        if not Path(file).exists():
            messagebox.showwarning("檔案不存在", f"找不到：{file}", parent=self)
            return
        try:
            bounds = json.loads(self._vars["bounds"].get().strip() or "{}")
        except json.JSONDecodeError:
            messagebox.showwarning("格式錯誤", "數值邊界必須是合法的 JSON 格式。", parent=self)
            return
        self.result = {
            "type":    "custom",
            "name":    f"{Path(file).stem}/{func}",
            "file":    file,
            "func":    func,
            "context": self._vars["context"].get().strip(),
            "maxiter": int(self._vars["maxiter"].get() or 40),
            "bounds":  bounds,
        }
        self.destroy()


# ──────────────────────────────────────────────────────────────
# 主視窗
# ──────────────────────────────────────────────────────────────

class ExperimentUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IFL MC/DC 實驗平台")
        self.resizable(True, True)
        self._proc: subprocess.Popen | None = None
        self._queue: list[dict] = []     # 待執行的 fixture
        self._results: dict[str, dict] = {}  # name -> loaded JSON
        self._build_ui()

    # ── 版面 ───────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        # ── 左側：設定 ──────────────────────────────────
        left = ttk.Frame(paned, width=260)
        left.columnconfigure(0, weight=1)
        paned.add(left, weight=0)

        # 語言選擇
        lang_frame = ttk.LabelFrame(left, text="語言", padding=6)
        lang_frame.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._lang_var = tk.StringVar(value="python")
        ttk.Radiobutton(lang_frame, text="Python", variable=self._lang_var,
                        value="python", command=self._on_lang_change).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(lang_frame, text="C",      variable=self._lang_var,
                        value="c",      command=self._on_lang_change).pack(side="left")

        # Fixture 清單
        lbl_fix = ttk.LabelFrame(left, text="測試函式", padding=6)
        lbl_fix.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        lbl_fix.columnconfigure(0, weight=1)
        self._lbl_fix = lbl_fix

        self._fix_frame = ttk.Frame(lbl_fix)
        self._fix_frame.grid(row=0, column=0, sticky="ew")
        self._fix_items: list[dict] = []
        self._fix_check_widgets: list[ttk.Checkbutton] = []

        for fx in PREDEFINED_PY:
            self._add_fixture_row(fx)

        self._add_custom_btn = ttk.Button(lbl_fix, text="＋ 新增自訂函式",
                                          command=self._add_custom)
        self._add_custom_btn.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        nl_btn_state = "normal" if _NL_AVAILABLE else "disabled"
        self._add_nl_btn = ttk.Button(lbl_fix, text="✦ 自然語言生成",
                                       command=self._add_nl_fixture,
                                       state=nl_btn_state)
        self._add_nl_btn.grid(row=2, column=0, sticky="ew", pady=(2, 0))

        # LLM 設定
        lbl_llm = ttk.LabelFrame(left, text="LLM 設定", padding=6)
        lbl_llm.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        lbl_llm.columnconfigure(1, weight=1)

        fields = [
            ("Provider",  "provider", ["openai", "ollama", "anthropic", "gemini"],
             os.environ.get("IFL_LLM_PROVIDER", "openai")),
            ("Model",     "model",    None, os.environ.get("IFL_LLM_MODEL", "gpt-4o-mini")),
            ("API Key",   "apikey",   None, os.environ.get("IFL_LLM_API_KEY", "")),
        ]
        self._llm_vars: dict[str, tk.StringVar] = {}
        for r, (label, key, choices, default) in enumerate(fields):
            ttk.Label(lbl_llm, text=label + "：").grid(row=r, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=default)
            self._llm_vars[key] = var
            if choices:
                ttk.Combobox(lbl_llm, textvariable=var, values=choices,
                             state="readonly", width=16).grid(row=r, column=1, sticky="ew", padx=(2, 0))
            elif key == "apikey":
                ttk.Entry(lbl_llm, textvariable=var, show="*").grid(row=r, column=1, sticky="ew", padx=(2, 0))
            else:
                ttk.Entry(lbl_llm, textvariable=var).grid(row=r, column=1, sticky="ew", padx=(2, 0))

        # Runs + 按鈕
        lbl_run = ttk.LabelFrame(left, text="執行設定", padding=6)
        lbl_run.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        lbl_run.columnconfigure(1, weight=1)

        ttk.Label(lbl_run, text="Runs：").grid(row=0, column=0, sticky="w")
        self._runs_var = tk.IntVar(value=5)
        ttk.Spinbox(lbl_run, from_=1, to=100, textvariable=self._runs_var, width=6).grid(
            row=0, column=1, sticky="w", padx=(2, 0))

        self._no_cache_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(lbl_run, text="忽略快取", variable=self._no_cache_var).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        btn_frame = ttk.Frame(left)
        btn_frame.grid(row=4, column=0, sticky="ew")
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        self._start_btn = ttk.Button(btn_frame, text="▶ 開始", command=self._start)
        self._start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self._stop_btn  = ttk.Button(btn_frame, text="■ 停止", command=self._stop, state="disabled")
        self._stop_btn.grid(row=0, column=1, sticky="ew")

        self._status_var = tk.StringVar(value="就緒")
        ttk.Label(left, textvariable=self._status_var, foreground="gray",
                  wraplength=240).grid(row=5, column=0, sticky="w", pady=(4, 0))

        # ── 右側：結果 ──────────────────────────────────
        right = ttk.Frame(paned)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        paned.add(right, weight=1)

        # 頂部：Fixture 選擇器
        top_bar = ttk.Frame(right)
        top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(top_bar, text="查看結果：").pack(side="left")
        self._result_var = tk.StringVar()
        self._result_cb  = ttk.Combobox(top_bar, textvariable=self._result_var,
                                         state="readonly", width=30)
        self._result_cb.pack(side="left", padx=(2, 0))
        self._result_cb.bind("<<ComboboxSelected>>", lambda _: self._refresh_results())

        ttk.Button(top_bar, text="匯出 JSON", command=self._export).pack(side="right")

        # Notebook (圖表 / 測試案例)
        nb = ttk.Notebook(right)
        nb.grid(row=1, column=0, sticky="nsew")
        self._nb = nb

        # Tab 1：圖表
        chart_tab = ttk.Frame(nb)
        nb.add(chart_tab, text="  圖表  ")
        chart_tab.columnconfigure(0, weight=1)
        chart_tab.rowconfigure(0, weight=1)

        self._fig = Figure(figsize=(7, 6.5))
        self._ax_cov  = self._fig.add_subplot(3, 1, 1)
        self._ax_iter = self._fig.add_subplot(3, 1, 2)
        self._ax_tok  = self._fig.add_subplot(3, 1, 3)
        self._canvas  = FigureCanvasTkAgg(self._fig, master=chart_tab)
        self._canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        self._summary_var = tk.StringVar(value="")
        ttk.Label(chart_tab, textvariable=self._summary_var,
                  foreground="#555").grid(row=1, column=0, sticky="w", padx=4, pady=(2, 0))

        # 不可行路徑顯示（有則列出，無則隱藏）
        self._infeasible_var = tk.StringVar(value="")
        self._infeasible_lbl = ttk.Label(
            chart_tab, textvariable=self._infeasible_var,
            foreground="#e57373", wraplength=700, justify="left",
        )
        self._infeasible_lbl.grid(row=2, column=0, sticky="w", padx=4, pady=(0, 4))

        # Tab 2：測試案例
        case_tab = ttk.Frame(nb)
        nb.add(case_tab, text="  測試案例  ")
        case_tab.columnconfigure(0, weight=1)
        case_tab.rowconfigure(1, weight=1)

        top_case = ttk.Frame(case_tab)
        top_case.grid(row=0, column=0, sticky="ew", pady=(4, 4))
        ttk.Label(top_case, text="第幾次 Run：").pack(side="left")
        self._run_sel_var = tk.StringVar()
        self._run_sel_cb  = ttk.Combobox(top_case, textvariable=self._run_sel_var,
                                          state="readonly", width=8)
        self._run_sel_cb.pack(side="left", padx=(2, 0))
        self._run_sel_cb.bind("<<ComboboxSelected>>", lambda _: self._refresh_cases())

        self._case_status_var = tk.StringVar(value="")
        ttk.Label(top_case, textvariable=self._case_status_var,
                  foreground="gray").pack(side="left", padx=(10, 0))

        self._tree = ttk.Treeview(case_tab, show="headings", selectmode="browse")
        vsb = ttk.Scrollbar(case_tab, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(case_tab, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")

        # Tab 3：缺口報告
        gap_tab = ttk.Frame(nb)
        nb.add(gap_tab, text="  缺口報告  ")
        gap_tab.columnconfigure(0, weight=1)
        gap_tab.rowconfigure(1, weight=2)
        gap_tab.rowconfigure(3, weight=1)

        top_gap = ttk.Frame(gap_tab)
        top_gap.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(4, 4))
        ttk.Label(top_gap, text="第幾次 Run：").pack(side="left")
        self._gap_run_sel_var = tk.StringVar()
        self._gap_run_sel_cb  = ttk.Combobox(top_gap, textvariable=self._gap_run_sel_var,
                                              state="readonly", width=8)
        self._gap_run_sel_cb.pack(side="left", padx=(2, 0))
        self._gap_run_sel_cb.bind("<<ComboboxSelected>>", lambda _: self._refresh_gap_report())
        self._gap_summary_var = tk.StringVar(value="")
        ttk.Label(top_gap, textvariable=self._gap_summary_var,
                  foreground="gray").pack(side="left", padx=(10, 0))

        _gap_cols = ("cond_id", "condition_expr", "flip", "status", "test_summary")
        self._gap_tree = ttk.Treeview(gap_tab, columns=_gap_cols, show="headings",
                                       selectmode="browse")
        _gap_col_cfg = [
            ("cond_id",       "條件 ID",   90,  "center"),
            ("condition_expr","條件表達式", 220, "w"),
            ("flip",          "方向",       60,  "center"),
            ("status",        "狀態",       90,  "center"),
            ("test_summary",  "測試案例摘要",300, "w"),
        ]
        for col, heading, width, anchor in _gap_col_cfg:
            self._gap_tree.heading(col, text=heading)
            self._gap_tree.column(col, width=width, anchor=anchor, stretch=(col == "condition_expr"))
        gap_vsb = ttk.Scrollbar(gap_tab, orient="vertical",   command=self._gap_tree.yview)
        gap_hsb = ttk.Scrollbar(gap_tab, orient="horizontal", command=self._gap_tree.xview)
        self._gap_tree.configure(yscrollcommand=gap_vsb.set, xscrollcommand=gap_hsb.set)
        self._gap_tree.grid(row=1, column=0, sticky="nsew")
        gap_vsb.grid(row=1, column=1, sticky="ns")
        gap_hsb.grid(row=2, column=0, sticky="ew")

        # 狀態色標
        self._gap_tree.tag_configure("covered",      background="#e8f5e9", foreground="#2e7d32")
        self._gap_tree.tag_configure("infeasible",   background="#f3f3f3", foreground="#9e9e9e")
        self._gap_tree.tag_configure("gate_exhausted",background="#fff8e1",foreground="#f57f17")
        self._gap_tree.tag_configure("uncovered",    background="#ffebee", foreground="#c62828")

        # 詳情面板
        detail_frame = ttk.LabelFrame(gap_tab, text="測試案例詳情", padding=4)
        detail_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(4, 0))
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        self._gap_detail = scrolledtext.ScrolledText(detail_frame, height=6, state="disabled",
                                                      wrap="word", font=("Consolas", 8),
                                                      background="#1e1e1e", foreground="#d4d4d4")
        self._gap_detail.grid(row=0, column=0, sticky="nsew")
        self._gap_tree.bind("<<TreeviewSelect>>", self._on_gap_select)

        # Tab 4：原始碼真值表
        src_tab = ttk.Frame(nb)
        nb.add(src_tab, text="  原始碼  ")
        src_tab.columnconfigure(0, weight=1)
        src_tab.rowconfigure(0, weight=1)

        src_paned = ttk.PanedWindow(src_tab, orient="vertical")
        src_paned.grid(row=0, column=0, sticky="nsew")

        # 上半：原始碼檢視器
        src_code_frame = ttk.LabelFrame(src_paned, text="原始碼（點擊決策行查看真值表）", padding=2)
        src_paned.add(src_code_frame, weight=3)
        src_code_frame.columnconfigure(0, weight=1)
        src_code_frame.rowconfigure(0, weight=1)

        self._src_text = tk.Text(src_code_frame, state="disabled", wrap="none",
                                  font=("Consolas", 9), cursor="arrow",
                                  background="#1e1e1e", foreground="#d4d4d4",
                                  selectbackground="#264f78")
        src_vsb = ttk.Scrollbar(src_code_frame, orient="vertical",   command=self._src_text.yview)
        src_hsb = ttk.Scrollbar(src_code_frame, orient="horizontal", command=self._src_text.xview)
        self._src_text.configure(yscrollcommand=src_vsb.set, xscrollcommand=src_hsb.set)
        self._src_text.grid(row=0, column=0, sticky="nsew")
        src_vsb.grid(row=0, column=1, sticky="ns")
        src_hsb.grid(row=1, column=0, sticky="ew")
        self._src_text.tag_configure("cov_full",    background="#1a3a1a", foreground="#89d185")
        self._src_text.tag_configure("cov_partial", background="#3a3000", foreground="#dcdcaa")
        self._src_text.tag_configure("cov_none",    background="#3a1010", foreground="#f48771")
        self._src_text.tag_configure("cov_infeas",  background="#2a2a2a", foreground="#888888")
        self._src_text.tag_configure("cov_selected",background="#264f78", foreground="#ffffff")
        self._src_text.tag_configure("lineno",      foreground="#858585")
        self._src_text.bind("<Button-1>", self._on_src_click)

        # 下半：選取決策節點的 2^k 真值表
        dt_frame = ttk.LabelFrame(src_paned, text="決策真值表（2^k 組合）", padding=2)
        src_paned.add(dt_frame, weight=2)
        dt_frame.columnconfigure(0, weight=1)
        dt_frame.rowconfigure(1, weight=1)

        top_dt = ttk.Frame(dt_frame)
        top_dt.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self._dt_info_var = tk.StringVar(value="← 點擊上方原始碼的決策行")
        ttk.Label(top_dt, textvariable=self._dt_info_var, foreground="#888").pack(side="left")
        ttk.Label(top_dt, text="（覆蓋狀態跨所有 Run 匯總）",
                  foreground="#555").pack(side="right")
        self._tt_run_sel_var = tk.StringVar()   # 保留供 _refresh_results 同步（無實際作用）
        self._tt_run_sel_cb  = ttk.Combobox(top_dt, textvariable=self._tt_run_sel_var,
                                             state="readonly", width=1)

        self._dt_tree = ttk.Treeview(dt_frame, show="headings", selectmode="browse")
        dt_vsb = ttk.Scrollbar(dt_frame, orient="vertical",   command=self._dt_tree.yview)
        dt_hsb = ttk.Scrollbar(dt_frame, orient="horizontal", command=self._dt_tree.xview)
        self._dt_tree.configure(yscrollcommand=dt_vsb.set, xscrollcommand=dt_hsb.set)
        self._dt_tree.grid(row=1, column=0, sticky="nsew")
        dt_vsb.grid(row=1, column=1, sticky="ns")
        dt_hsb.grid(row=2, column=0, sticky="ew")
        self._dt_tree.tag_configure("cov_t",   background="#1a3a1a", foreground="#89d185")
        self._dt_tree.tag_configure("cov_f",   background="#1a1a3a", foreground="#9cdcfe")
        self._dt_tree.tag_configure("uncov",   background="#3a1010", foreground="#f48771")
        self._dt_tree.tag_configure("pair_a",  background="#0d3a5c", foreground="#ffffff")
        self._dt_tree.tag_configure("pair_b",  background="#1a4a7a", foreground="#ffffff")
        self._dt_tree.bind("<<TreeviewSelect>>", self._on_dt_select)

        self._src_decision_lines: dict[int, str] = {}   # line_no → node_id
        self._dt_current_data: dict = {}                 # 目前顯示的決策節點資料

        # 底部：小型日誌
        log_frame = ttk.LabelFrame(self, text="執行日誌", padding=2)
        log_frame.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
        log_frame.columnconfigure(0, weight=1)
        self._log = scrolledtext.ScrolledText(log_frame, height=5, state="disabled",
                                               wrap="word", font=("Consolas", 8),
                                               background="#1e1e1e", foreground="#aaa")
        self._log.grid(row=0, column=0, sticky="ew")
        self._log.tag_configure("error",   foreground="#f48771")
        self._log.tag_configure("success", foreground="#89d185")

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.geometry("1150x740")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── 語言切換 ───────────────────────────────────────────

    def _on_lang_change(self):
        """切換語言時重建 fixture 清單。"""
        # 清除現有 checkbutton
        for w in self._fix_check_widgets:
            w.destroy()
        self._fix_items.clear()
        self._fix_check_widgets.clear()

        lang = self._lang_var.get()
        predefined = PREDEFINED_C if lang == "c" else PREDEFINED_PY
        for fx in predefined:
            self._add_fixture_row(fx)

    # ── Fixture 清單操作 ───────────────────────────────────

    def _add_fixture_row(self, cfg: dict):
        var = tk.BooleanVar(value=True)
        row = len(self._fix_items)
        label = cfg["name"] if cfg.get("type") != "custom" else f"[自訂] {cfg['name']}"
        if "k" in cfg:
            label += f"  (k={cfg['k']})"
        cb = ttk.Checkbutton(self._fix_frame, text=label, variable=var)
        cb.grid(row=row, column=0, sticky="w")
        self._fix_items.append({"name": cfg["name"], "var": var, "cfg": cfg})
        self._fix_check_widgets.append(cb)

    def _add_custom(self):
        dlg = AddFixtureDialog(self)
        if dlg.result:
            self._add_fixture_row(dlg.result)

    def _add_nl_fixture(self):
        dlg = NLToCodeDialog(self, self._llm_vars)
        if dlg.result:
            self._add_fixture_row(dlg.result)

    # ── 執行控制 ───────────────────────────────────────────

    def _start(self):
        selected = [item for item in self._fix_items if item["var"].get()]
        if not selected:
            messagebox.showwarning("未選擇函式", "請至少勾選一個測試函式。")
            return

        self._queue = selected.copy()
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._run_next()

    def _run_next(self):
        if not self._queue:
            self._on_all_done()
            return

        item = self._queue.pop(0)
        cfg  = item["cfg"]
        name = item["name"]
        runs = self._runs_var.get()
        self._status_var.set(f"執行中：{name}  (0/{runs})")

        lang = cfg.get("lang") or self._lang_var.get()
        cmd = [sys.executable, str(_RUNNER), "--runs", str(runs), "--language", lang]
        if cfg.get("type") == "custom":
            cmd += [
                "--file",    cfg["file"],
                "--func",    cfg["func"],
                "--context", cfg["context"],
                "--max-iter", str(cfg["maxiter"]),
                "--bounds",  json.dumps(cfg["bounds"]),
            ]
            if cfg.get("domain_types"):
                cmd += ["--domain-types", json.dumps(cfg["domain_types"])]
        else:
            cmd += ["--fixture", name]

        if self._no_cache_var.get():
            pass  # run_ifl_only 無快取機制，每次都重跑

        env = os.environ.copy()
        env["IFL_LLM_PROVIDER"] = self._llm_vars["provider"].get()
        env["IFL_LLM_MODEL"]    = self._llm_vars["model"].get().strip()
        env["IFL_LLM_API_KEY"]  = self._llm_vars["apikey"].get().strip()
        env["PYTHONUNBUFFERED"]    = "1"
        env["PYTHONIOENCODING"]    = "utf-8"
        env["PYTHONUTF8"]          = "1"

        self._log_append(f"\n>>> {' '.join(cmd)}\n", "success")

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(_HERE),
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._stream, args=(name, runs), daemon=True).start()

    def _stream(self, fixture_name: str, total_runs: int):
        output_path: str | None = None
        for line in self._proc.stdout:
            if line.startswith(">>>PROGRESS:"):
                try:
                    data = json.loads(line[len(">>>PROGRESS:"):])
                    msg = (f"執行中：{fixture_name}  "
                           f"Run {data['run']}/{data['total_runs']}  "
                           f"coverage={data['coverage']:.1%}")
                    self.after(0, self._status_var.set, msg)
                except Exception:
                    pass
            elif line.startswith(">>>DONE:"):
                try:
                    data = json.loads(line[len(">>>DONE:"):])
                    output_path = data.get("output")
                except Exception:
                    pass
            else:
                tag = "error" if "[error]" in line.lower() else ""
                self.after(0, self._log_append, line, tag)

        self._proc.wait()
        rc = self._proc.returncode
        self.after(0, self._fixture_done, fixture_name, output_path, rc)

    def _fixture_done(self, fixture_name: str, output_path: str | None, rc: int):
        if rc != 0:
            self._log_append(f"[結束 code={rc}]\n", "error")

        if output_path and Path(output_path).exists():
            try:
                data = json.loads(Path(output_path).read_text(encoding="utf-8"))
                self._results[fixture_name] = data
                names = list(self._results.keys())
                self._result_cb["values"] = names
                self._result_cb.set(fixture_name)
                self._refresh_results()
            except Exception as exc:
                self._log_append(f"[載入結果失敗] {exc}\n", "error")

        self._run_next()

    def _stop(self):
        self._queue.clear()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._on_all_done()
        self._status_var.set("已停止")

    def _on_all_done(self):
        self._proc = None
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        if self._results:
            self._status_var.set("完成")

    # ── 結果顯示 ───────────────────────────────────────────

    def _refresh_results(self):
        name = self._result_var.get()
        if name not in self._results:
            return
        data = self._results[name]
        runs = data.get("runs", [])
        meta = data.get("meta", {})

        self._ax_cov.cla()
        self._ax_iter.cla()
        self._ax_tok.cla()

        xs          = [int(r["run"])          for r in runs]
        covs        = [float(r["coverage"])   for r in runs]
        iters       = [int(r["iterations"])   for r in runs]
        cov_by_run  = {int(r["run"]): float(r["coverage"]) for r in runs}
        conv_xs     = [int(r["run"]) for r in runs if r.get("converged")]

        covs_pct = [c * 100 for c in covs]
        tokens   = [int(r.get("tokens", 0)) for r in runs]

        def _draw_line(ax, ys, color, ylabel, formatter=None):
            ax.plot(xs, ys, "-", color=color, linewidth=1.6, zorder=1)
            for r, y in zip(runs, ys):
                conv = bool(r.get("converged"))
                ax.plot(int(r["run"]), y,
                        "^" if conv else "o",
                        color="#4caf50" if conv else color,
                        markersize=8 if conv else 6, zorder=3)
            ax.set_ylabel(ylabel)
            ax.grid(True, alpha=0.3)
            ax.set_xticks(xs)
            if formatter:
                ax.yaxis.set_major_formatter(formatter)

        # 覆蓋率
        _draw_line(self._ax_cov, covs_pct, "#4e9ede", "MC/DC 覆蓋率 (%)",
                   mticker.PercentFormatter(xmax=100, decimals=0))
        self._ax_cov.axhline(100, color="#e57373", linestyle="--", linewidth=0.9, alpha=0.8)
        self._ax_cov.set_ylim(0, 112)
        self._ax_cov.set_title(
            f"{name}   k={meta.get('k','?')}   model={meta.get('llm_model','?')}"
        )
        if conv_xs:
            self._ax_cov.plot([], [], "^", color="#4caf50", markersize=8, label="收斂")
            self._ax_cov.legend(fontsize=8)

        # 迭代次數
        _draw_line(self._ax_iter, iters, "#4e9ede", "迭代次數")

        # Token 消耗
        _draw_line(self._ax_tok, tokens, "#ff9800", "Token 消耗",
                   mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
        self._ax_tok.set_xlabel("Run")

        self._fig.tight_layout(pad=0.8)
        self._canvas.draw()

        # 摘要文字
        if runs:
            import statistics as _st
            mean_cov  = _st.mean(covs) * 100
            conv_rate = len(conv_xs) / len(runs)
            mean_iter = _st.mean(iters)
            std_iter  = _st.stdev(iters) if len(iters) > 1 else 0.0
            self._summary_var.set(
                f"平均覆蓋率 {mean_cov:.1f}%　｜　"
                f"收斂 {len(conv_xs)}/{len(runs)} ({conv_rate:.1%})　｜　"
                f"平均迭代 {mean_iter:.1f} ± {std_iter:.1f}"
            )

        # 不可行路徑：收集所有 run 的聯集
        all_infeasible: set[str] = set()
        for r in runs:
            for cid in r.get("infeasible_paths", []):
                all_infeasible.add(cid)

        if all_infeasible:
            sorted_cids = sorted(all_infeasible)
            self._infeasible_var.set(
                f"⚠ 不可行路徑（{len(sorted_cids)} 個）：{', '.join(sorted_cids)}"
            )
            self._infeasible_lbl.grid()   # 顯示
        else:
            self._infeasible_var.set("")
            self._infeasible_lbl.grid_remove()  # 隱藏

        # Run 選擇器：預設選最後一次收斂的 Run
        run_labels    = [f"Run {r['run']}" for r in runs]
        self._run_sel_cb["values"] = run_labels
        self._gap_run_sel_cb["values"] = run_labels
        self._tt_run_sel_cb["values"] = run_labels
        default_label = (f"Run {conv_xs[-1]}" if conv_xs else run_labels[-1]) if run_labels else ""
        self._run_sel_cb.set(default_label)
        self._gap_run_sel_cb.set(default_label)
        self._tt_run_sel_cb.set(default_label)
        self._refresh_cases()
        self._refresh_gap_report()
        self._refresh_source_view()

    def _refresh_cases(self):
        name = self._result_var.get()
        if name not in self._results:
            return
        runs = self._results[name].get("runs", [])
        if not runs:
            return

        # 找到對應 run
        sel = self._run_sel_var.get()
        run_data = runs[-1]
        if sel:
            try:
                run_num  = int(sel.replace("Run ", ""))
                run_data = next((r for r in runs if int(r["run"]) == run_num), runs[-1])
            except ValueError:
                pass

        conv  = bool(run_data.get("converged", False))
        cov   = float(run_data.get("coverage", 0))

        # 只顯示 test_suite（通過 AcceptanceGate、對 MC/DC 覆蓋率有貢獻的案例）
        cases = run_data.get("test_cases", [])

        # 狀態標籤
        if conv:
            self._case_status_var.set(f"MC/DC 100% 收斂 — {len(cases)} 個測試案例")
        else:
            self._case_status_var.set(
                f"未收斂（{cov:.1%}）— {len(cases)} 個案例（供參考）"
            )

        # 重建 treeview
        self._tree.delete(*self._tree.get_children())
        if not cases:
            self._tree["columns"] = ("info",)
            self._tree.heading("info", text="（無測試案例）")
            self._tree.column("info", width=200)
            return

        cols = list(cases[0].keys())
        self._tree["columns"] = cols
        for col in cols:
            w = max(80, len(col) * 10)
            self._tree.heading(col, text=col)
            self._tree.column(col, width=w, anchor="center", stretch=True)

        for case in cases:
            vals = [str(case.get(c, "")) for c in cols]
            self._tree.insert("", "end", values=vals)

    # ── 缺口報告 ────────────────────────────────────────────

    def _refresh_gap_report(self):
        name = self._result_var.get()
        if name not in self._results:
            return
        runs = self._results[name].get("runs", [])
        if not runs:
            return

        sel = self._gap_run_sel_var.get()
        run_data = runs[-1]
        if sel:
            try:
                run_num  = int(sel.replace("Run ", ""))
                run_data = next((r for r in runs if int(r["run"]) == run_num), runs[-1])
            except ValueError:
                pass

        gap_map: dict = run_data.get("gap_coverage_map", {})

        self._gap_tree.delete(*self._gap_tree.get_children())

        if not gap_map:
            self._gap_summary_var.set("（無缺口資料，請重新執行以取得報告）")
            return

        counts = {"covered": 0, "infeasible": 0, "gate_exhausted": 0, "uncovered": 0}
        for key in sorted(gap_map):
            entry   = gap_map[key]
            status  = entry.get("status", "uncovered")
            cond_id = entry.get("condition_id", key)
            flip    = entry.get("flip_direction", "")
            expr    = entry.get("condition_expr", "")
            counts[status] = counts.get(status, 0) + 1

            status_labels = {
                "covered":       "O 已覆蓋",
                "infeasible":    "⛔ 不可行",
                "gate_exhausted":"⚠️ 放棄",
                "uncovered":     "❌ 未覆蓋",
            }
            status_text = status_labels.get(status, status)

            tc = entry.get("test_case")
            if tc:
                summary = "  ".join(f"{k}={v}" for k, v in list(tc.items())[:4])
                if len(tc) > 4:
                    summary += " …"
            elif status == "infeasible":
                summary = f"Z3 形式化證明不可行（{entry.get('proof', '')}）"
            elif status == "gate_exhausted":
                summary = "LLM 多次嘗試後放棄（理論上可達）"
            else:
                summary = "—"

            self._gap_tree.insert("", "end", iid=key,
                                   values=(cond_id, expr, flip, status_text, summary),
                                   tags=(status,))

        total = sum(counts.values())
        self._gap_summary_var.set(
            f"共 {total} 個配對　｜　"
            f"O {counts['covered']} 已覆蓋　"
            f"❌ {counts['uncovered']} 未覆蓋　"
            f"⛔ {counts['infeasible']} 不可行　"
            f"⚠️ {counts.get('gate_exhausted', 0)} 放棄"
        )

    def _on_gap_select(self, _):
        sel = self._gap_tree.selection()
        if not sel:
            return
        key  = sel[0]
        name = self._result_var.get()
        if name not in self._results:
            return
        run_sel = self._gap_run_sel_var.get()
        runs    = self._results[name].get("runs", [])
        run_data = runs[-1]
        if run_sel:
            try:
                run_num  = int(run_sel.replace("Run ", ""))
                run_data = next((r for r in runs if int(r["run"]) == run_num), runs[-1])
            except ValueError:
                pass

        entry = run_data.get("gap_coverage_map", {}).get(key, {})
        lines = [
            f"條件 ID   : {entry.get('condition_id', '')}",
            f"條件表達式 : {entry.get('condition_expr', '')}",
            f"翻轉方向  : {entry.get('flip_direction', '')}",
            f"狀態      : {entry.get('status', '')}",
        ]
        if entry.get("proof"):
            lines.append(f"Z3 證明   : {entry['proof']}")
        if entry.get("implicit"):
            lines.append("（隱性覆蓋：由隨機初始測試或其他缺口的測試附帶覆蓋，無法追溯單一測試案例）")
        tc = entry.get("test_case")
        if tc:
            lines.append("")
            lines.append("測試案例：")
            for k, v in tc.items():
                lines.append(f"  {k} = {v}")

        self._gap_detail.configure(state="normal")
        self._gap_detail.delete("1.0", tk.END)
        self._gap_detail.insert("1.0", "\n".join(lines))
        self._gap_detail.configure(state="disabled")

    # ── 原始碼真值表 ────────────────────────────────────────

    def _get_run_data(self) -> dict | None:
        name = self._result_var.get()
        if name not in self._results:
            return None
        runs = self._results[name].get("runs", [])
        if not runs:
            return None
        sel = self._tt_run_sel_var.get()
        try:
            run_num = int(sel.replace("Run ", ""))
            return next((r for r in runs if int(r["run"]) == run_num), runs[-1])
        except (ValueError, AttributeError):
            return runs[-1]

    def _refresh_source_view(self):
        name = self._result_var.get()
        if name not in self._results:
            return

        dtt: dict = self._results[name].get("decision_truth_tables", {})
        self._src_decision_lines = {}

        # 讀原始碼
        name = self._result_var.get()
        src_path = self._results[name].get("meta", {}).get("file", "")
        try:
            src_lines = Path(src_path).read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            self._src_text.configure(state="normal")
            self._src_text.delete("1.0", tk.END)
            self._src_text.insert("1.0", f"無法讀取原始碼：{src_path}")
            self._src_text.configure(state="disabled")
            return

        # 建立 line_no → (node_id, coverage_tag) 對應
        line_info: dict[int, tuple[str, str]] = {}
        for node_id, node_data in dtt.items():
            ln = node_data.get("line_no", 0)
            rows = node_data.get("rows", [])
            if not rows:
                continue
            covered   = sum(1 for r in rows if r.get("covered"))
            total     = len(rows)
            if covered == total:
                tag = "cov_full"
            elif covered == 0:
                tag = "cov_none"
            else:
                tag = "cov_partial"
            line_info[ln] = (node_id, tag)
            self._src_decision_lines[ln] = node_id

        # 填入 Text widget
        self._src_text.configure(state="normal")
        self._src_text.delete("1.0", tk.END)
        for i, line in enumerate(src_lines, start=1):
            lineno_str = f"{i:4d}  "
            self._src_text.insert(tk.END, lineno_str, "lineno")
            text_line  = line + "\n"
            if i in line_info:
                _, cov_tag = line_info[i]
                self._src_text.insert(tk.END, text_line, (cov_tag,))
            else:
                self._src_text.insert(tk.END, text_line)
        self._src_text.configure(state="disabled")
        self._dt_info_var.set("← 點擊上方標色的決策行查看 2^k 真值表")

    def _on_src_click(self, event):
        index   = self._src_text.index(f"@{event.x},{event.y}")
        line_no = int(index.split(".")[0])
        node_id = self._src_decision_lines.get(line_no)
        if not node_id:
            return
        name = self._result_var.get()
        if name not in self._results:
            return
        dtt = self._results[name].get("decision_truth_tables", {})
        node_data = dtt.get(node_id)
        if not node_data:
            return

        # 高亮選取行
        self._src_text.configure(state="normal")
        self._src_text.tag_remove("cov_selected", "1.0", tk.END)
        self._src_text.tag_add("cov_selected", f"{line_no}.0", f"{line_no}.end")
        self._src_text.configure(state="disabled")

        self._show_decision_tt(node_id, node_data)

    def _show_decision_tt(self, node_id: str, node_data: dict):
        conditions = node_data.get("conditions", [])
        rows       = node_data.get("rows", [])
        expr       = node_data.get("expression", node_id)
        line_no    = node_data.get("line_no", "?")

        self._dt_current_data = node_data
        self._dt_tree.delete(*self._dt_tree.get_children())

        if not rows:
            self._dt_info_var.set(f"第 {line_no} 行：{expr}（無資料）")
            return

        cond_ids   = [c["cond_id"]    for c in conditions]
        cond_exprs = [c["expression"] for c in conditions]
        k          = len(cond_ids)
        covered    = sum(1 for r in rows if r.get("covered"))

        self._dt_info_var.set(
            f"第 {line_no} 行　k={k}　2^k={len(rows)} 組合　"
            f"已覆蓋 {covered}/{len(rows)}　｜　點選列高亮 MC/DC 配對"
        )

        cols = cond_ids + ["decision", "covered"]
        self._dt_tree["columns"] = cols
        for cid, expr_c in zip(cond_ids, cond_exprs):
            short = expr_c if len(expr_c) <= 14 else expr_c[:12] + "…"
            self._dt_tree.heading(cid, text=short)
            self._dt_tree.column(cid, width=max(60, len(short) * 8), anchor="center", stretch=True)
        self._dt_tree.heading("decision", text="決策")
        self._dt_tree.column("decision", width=50, anchor="center", stretch=False)
        self._dt_tree.heading("covered", text="覆蓋")
        self._dt_tree.column("covered", width=50, anchor="center", stretch=False)

        for idx, row in enumerate(rows):
            combo    = row.get("combo", [])
            decision = row.get("decision")
            is_cov   = row.get("covered", False)
            cells    = ["T" if v else "F" for v in combo]
            cells   += ["T" if decision else "F",
                        "O" if is_cov else "❌"]
            if is_cov:
                tag = "cov_t" if decision else "cov_f"
            else:
                tag = "uncov"
            self._dt_tree.insert("", "end", iid=str(idx), values=cells, tags=(tag,))

    def _on_dt_select(self, _):
        sel = self._dt_tree.selection()
        if not sel or not self._dt_current_data:
            return
        idx_a      = int(sel[0])
        rows       = self._dt_current_data.get("rows", [])
        conditions = self._dt_current_data.get("conditions", [])
        if idx_a >= len(rows):
            return

        row_a  = rows[idx_a]
        combo_a = row_a.get("combo", [])
        dec_a   = row_a.get("decision")

        # 找 MC/DC 配對：決策相反且恰好一個條件不同
        pairs: dict[int, list[int]] = {}   # cond_index → [row_idx, ...]
        for idx_b, row_b in enumerate(rows):
            if idx_b == idx_a:
                continue
            combo_b = row_b.get("combo", [])
            dec_b   = row_b.get("decision")
            if dec_a == dec_b:
                continue
            diff = [i for i in range(len(combo_a)) if combo_a[i] != combo_b[i]]
            if len(diff) == 1:
                pairs.setdefault(diff[0], []).append(idx_b)

        # 重設 tag
        for i, row in enumerate(rows):
            is_cov  = row.get("covered", False)
            dec     = row.get("decision")
            if is_cov:
                t = "cov_t" if dec else "cov_f"
            else:
                t = "uncov"
            self._dt_tree.item(str(i), tags=(t,))

        self._dt_tree.item(str(idx_a), tags=("pair_a",))
        for partner_idxs in pairs.values():
            for pi in partner_idxs:
                self._dt_tree.item(str(pi), tags=("pair_b",))

        if pairs:
            pair_conds = "、".join(
                conditions[ci]["expression"] for ci in pairs
            )
            self._dt_info_var.set(f"MC/DC 配對條件：{pair_conds}")
        else:
            self._dt_info_var.set("此組合無 MC/DC 配對")

    # ── 匯出 ───────────────────────────────────────────────

    def _export(self):
        name = self._result_var.get()
        if name not in self._results:
            messagebox.showinfo("尚無結果", "請先執行實驗。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=f"ifl_{name}.json",
        )
        if path:
            Path(path).write_text(
                json.dumps(self._results[name], ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            messagebox.showinfo("完成", f"已匯出至：{path}")

    # ── 日誌 ───────────────────────────────────────────────

    def _log_append(self, text: str, tag: str = ""):
        self._log.configure(state="normal")
        self._log.insert(tk.END, text, tag)
        self._log.see(tk.END)
        self._log.configure(state="disabled")

    def _on_close(self):
        self._stop()
        self.destroy()


if __name__ == "__main__":
    app = ExperimentUI()
    app.mainloop()
