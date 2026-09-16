const API = '/api';

// ── 全域狀態 ──────────────────────────────────────────────────────────────
let envId         = null;
let runId         = null;
let snapId        = null;
let covRunId      = null;   // 覆蓋率頁當前查看的 run_id（null = 最新）
let hintGap       = null;   // { condId, flip }
let genWs         = null;
let parsedData    = {};

const MODEL_DEFS = { openai:'gpt-4.1-mini', anthropic:'claude-sonnet-4-6', ollama:'gemma3:4b' };

// ── 工具函式 ──────────────────────────────────────────────────────────────
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const fmtDate = iso => iso ? new Date(iso).toLocaleString('zh-TW',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
const fmt100  = n => n != null ? Math.round(n * 100) + '%' : '—';

async function apiFetch(url, opts = {}) {
  const res = await fetch(API + url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err);
  }
  return res.status === 204 ? null : res.json();
}

// ── 頁籤切換 ──────────────────────────────────────────────────────────────
function showTab(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.getElementById('tab-'  + name).classList.add('active');
  if (name === 'env')  loadEnvList();
  if (name === 'suit') loadCases();
  if (name === 'run')  loadRunHistory();
  if (name === 'cov')  { covRunId = null; loadCoverage(); }
  if (name === 'req')  loadRequirements();
  if (name === 'cfg')  loadLLMCfg();
  if (name === 'gen')  loadGenDefaults();
}

// ── 環境選擇器 ────────────────────────────────────────────────────────────
function onEnvSelect() {
  envId = document.getElementById('nav-env').value || null;
}

async function refreshNavEnv(selectId) {
  const envs = await apiFetch('/environments');
  const sel  = document.getElementById('nav-env');
  const prev = selectId || sel.value;
  sel.innerHTML = '<option value="">── 選擇測試環境 ──</option>' +
    envs.map(e => `<option value="${e.id}">${esc(e.name)}</option>`).join('');
  if (prev) sel.value = prev;
  envId = sel.value || null;
}

// ── 手風琴 ────────────────────────────────────────────────────────────────
function toggleAcc(id) {
  const body = document.getElementById(id);
  const icon = document.getElementById(id + '-icon');
  const open = body.classList.toggle('open');
  icon.textContent = open ? '▼' : '▶';
}

// ── 自然語言生成程式碼 ────────────────────────────────────────────────────
async function genCodeFromNL() {
  const desc = document.getElementById('nl-desc').value.trim();
  if (!desc) return alert('請輸入函數描述');
  try {
    const data = await apiFetch('/environments/gen-code', {
      method: 'POST',
      body: JSON.stringify({
        description: desc,
        language: document.getElementById('env-lang').value,
      }),
    });
    document.getElementById('env-source').value = data.code;
  } catch(e) { alert('生成失敗：' + e.message); }
}

// ── 解析原始碼 ────────────────────────────────────────────────────────────
async function parseSource() {
  const src  = document.getElementById('env-source').value.trim();
  const lang = document.getElementById('env-lang').value;
  if (!src) return alert('請輸入原始碼');
  try {
    parsedData = await apiFetch('/environments/parse', {
      method: 'POST',
      body: JSON.stringify({ source_code: src, language: lang }),
    });
    const sel = document.getElementById('env-func');
    sel.innerHTML = parsedData.functions.map(f => `<option value="${f}">${f}</option>`).join('');
    document.getElementById('parse-section').style.display = 'block';
    onFuncSelect();
    // 自動建議初始隨機數量 max(6, 3*k)
    const k = parsedData.decision_count || 1;
    document.getElementById('env-min-init').value = Math.max(6, 3 * k);

    // 自動套用 AST 推薦值域
    const sb = parsedData.suggested_bounds || {};
    if (Object.keys(sb).length > 0) {
      Object.entries(sb).forEach(([name, [mn, mx]]) => {
        const minEl = document.querySelector(`.p-min[data-p="${name}"]`);
        const maxEl = document.querySelector(`.p-max[data-p="${name}"]`);
        if (minEl) minEl.value = mn;
        if (maxEl) maxEl.value = mx;
      });
      const hint = document.getElementById('suggest-hint');
      hint.innerHTML = '🔍 已根據程式碼條件自動推薦值域，請確認後可調整';
      hint.style.display = 'block';
    }
  } catch(e) { alert('解析失敗：' + e.message); }
}

function onFuncSelect() {
  const fname = document.getElementById('env-func').value;
  const types = parsedData.param_types?.[fname] || {};
  document.getElementById('env-sig').value = `${fname}(${Object.keys(types).join(', ')})`;
  buildParamTable(types);
}

function buildParamTable(types) {
  document.getElementById('param-table').innerHTML = Object.entries(types).map(([name, type]) => {
    const isBool = type === 'bool';
    const defMin = name.includes('day') ? 0 : 0;
    const defMax = name.includes('day') ? 3650 : name === 'age' ? 130 : 100;
    return `<tr>
      <td><code>${name}</code></td>
      <td><select class="p-type" data-p="${name}" style="border:1px solid #e2e8f0;border-radius:4px;padding:2px 6px;font-size:12px;" onchange="onTypeChange(this)">
        <option value="int"   ${type==='int'   ?'selected':''}>int</option>
        <option value="bool"  ${type==='bool'  ?'selected':''}>bool</option>
        <option value="float" ${type==='float' ?'selected':''}>float</option>
        <option value="str"   ${type==='str'   ?'selected':''}>str</option>
      </select></td>
      <td><input class="p-min" data-p="${name}" type="number" value="${isBool?'':defMin}" ${isBool?'disabled':''} style="border:1px solid #e2e8f0;border-radius:4px;padding:2px 6px;font-size:12px;width:80px;" /></td>
      <td><input class="p-max" data-p="${name}" type="number" value="${isBool?'':defMax}" ${isBool?'disabled':''} style="border:1px solid #e2e8f0;border-radius:4px;padding:2px 6px;font-size:12px;width:80px;" /></td>
      <td></td>
    </tr>`;
  }).join('');
}

function onTypeChange(sel) {
  const isBool = sel.value === 'bool';
  const row = sel.closest('tr');
  row.querySelectorAll('.p-min,.p-max').forEach(inp => {
    inp.disabled = isBool;
    if (isBool) inp.value = '';
  });
}

function collectParams() {
  const types = {}, bounds = {};
  document.querySelectorAll('.p-type').forEach(s => { types[s.dataset.p] = s.value; });
  document.querySelectorAll('.p-min').forEach(inp => {
    const n = inp.dataset.p;
    if (types[n] !== 'bool' && inp.value !== '') {
      if (!bounds[n]) bounds[n] = [0, 100];
      bounds[n][0] = parseFloat(inp.value);
    }
  });
  document.querySelectorAll('.p-max').forEach(inp => {
    const n = inp.dataset.p;
    if (types[n] !== 'bool' && inp.value !== '') {
      if (!bounds[n]) bounds[n] = [0, 100];
      bounds[n][1] = parseFloat(inp.value);
    }
  });
  return { types, bounds };
}

// ── LLM 推薦邊界值 ────────────────────────────────────────────────────────
async function suggestBounds() {
  const hint = document.getElementById('suggest-hint');
  hint.innerHTML = '⏳ 分析中（AST + LLM）...';
  hint.style.display = 'block';

  const { types } = collectParams();
  const desc = prompt('補充說明（選填）：') || '';

  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 95000);

    const res = await fetch(`${API}/environments/suggest-bounds`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_code:  document.getElementById('env-source').value,
        language:     document.getElementById('env-lang').value,
        domain_types: types,
        domain_context: document.getElementById('env-ctx').value,
        description:  desc,
      }),
      signal: controller.signal,
    });
    clearTimeout(timer);

    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const sb = data.suggested_bounds || {};

    if (Object.keys(sb).length === 0) {
      hint.innerHTML = '未能提供建議，請手動填寫。';
    } else {
      hint.innerHTML = '✨ 建議值域：' +
        Object.entries(sb).map(([k, v]) => `<code>${k}</code> [${v[0]}, ${v[1]}]`).join('  ') +
        '<br><small style="color:#94a3b8">已自動套用至下方表格</small>';
      Object.entries(sb).forEach(([name, [mn, mx]]) => {
        const minEl = document.querySelector(`.p-min[data-p="${name}"]`);
        const maxEl = document.querySelector(`.p-max[data-p="${name}"]`);
        if (minEl) minEl.value = mn;
        if (maxEl) maxEl.value = mx;
      });
    }
  } catch(e) {
    hint.innerHTML = e.name === 'AbortError'
      ? '⚠ 超時，已套用 AST 分析結果'
      : '❌ 失敗：' + e.message;
  }
}

// ── CFG 顯示 ──────────────────────────────────────────────────────────────
async function loadCFG() {
  if (!envId) return alert('請先選擇環境');
  try {
    const data = await apiFetch(`/environments/${envId}/cfg`);
    const nodes = data.nodes || [];
    const text = nodes.map(n => {
      const conds = n.conditions.map(c =>
        `  ${c.cond_id}: ${c.expression}${c.negated ? ' [negated]' : ''}`
      ).join('\n');
      return `[${n.node_id}] ${n.node_type} @ line ${n.line_no}\n  ${n.expression}\n條件列表 (k=${n.k}):\n${conds}`;
    }).join('\n\n');
    const view = document.getElementById('cfg-view');
    view.textContent = text || '（未找到決策節點）';
    view.style.display = 'block';
  } catch(e) { alert('CFG 載入失敗：' + e.message); }
}

// ── Stub/Mock ─────────────────────────────────────────────────────────────
async function loadStubs() {
  if (!envId) return;
  const stubs = await apiFetch(`/environments/${envId}/stubs`);
  document.getElementById('stub-list').innerHTML = stubs.map(s => `
    <tr>
      <td><code>${esc(s.call_site)}</code></td>
      <td><span class="badge b-man">${s.strategy}</span></td>
      <td>${esc(s.return_value || s.exception_type || '—')}</td>
      <td><button onclick="deleteStub('${s.id}')" class="btn btn-d btn-sm">刪除</button></td>
    </tr>`).join('');
}

async function addStub() {
  if (!envId) return alert('請先選擇環境');
  const site     = document.getElementById('stub-site').value.trim();
  const strategy = document.getElementById('stub-strategy').value;
  const value    = document.getElementById('stub-value').value.trim();
  if (!site) return alert('請填寫呼叫位置');
  await apiFetch(`/environments/${envId}/stubs`, {
    method: 'POST',
    body: JSON.stringify({
      call_site: site, strategy,
      return_value:     strategy === 'fixed'     ? value : null,
      exception_type:   strategy === 'exception' ? value : null,
    }),
  });
  document.getElementById('stub-site').value  = '';
  document.getElementById('stub-value').value = '';
  loadStubs();
}

async function deleteStub(id) {
  await apiFetch(`/environments/${envId}/stubs/${id}`, { method: 'DELETE' });
  loadStubs();
}

// ── 參數約束 ──────────────────────────────────────────────────────────────
async function loadConstraints() {
  if (!envId) return;
  const cstrs = await apiFetch(`/environments/${envId}/constraints`);
  document.getElementById('cstr-list').innerHTML = cstrs.map(c => `
    <tr>
      <td>${esc(c.description)}</td>
      <td><code>${esc(c.z3_expression)}</code></td>
      <td><span class="badge b-man">${c.created_by}</span></td>
      <td><button onclick="deleteConstraint('${c.id}')" class="btn btn-d btn-sm">刪除</button></td>
    </tr>`).join('');
}

async function addConstraint() {
  if (!envId) return alert('請先選擇環境');
  const desc = document.getElementById('cstr-desc').value.trim();
  const z3   = document.getElementById('cstr-z3').value.trim();
  if (!desc || !z3) return alert('請填寫描述和 Z3 式子');
  await apiFetch(`/environments/${envId}/constraints`, {
    method: 'POST',
    body: JSON.stringify({ description: desc, z3_expression: z3 }),
  });
  document.getElementById('cstr-desc').value = '';
  document.getElementById('cstr-z3').value   = '';
  loadConstraints();
}

async function deleteConstraint(id) {
  await apiFetch(`/environments/${envId}/constraints/${id}`, { method: 'DELETE' });
  loadConstraints();
}

// ── 建立/列表環境 ─────────────────────────────────────────────────────────
let _editingEnvId = null;  // 正在編輯的環境 ID（null = 新增模式）

async function createEnv() {
  const { types, bounds } = collectParams();
  const fname = document.getElementById('env-func').value;
  const body = {
    name:                document.getElementById('env-name').value || fname + '_env',
    language:            document.getElementById('env-lang').value,
    source_code:         document.getElementById('env-source').value,
    function_name:       fname,
    func_signature:      document.getElementById('env-sig').value,
    domain_context:      document.getElementById('env-ctx').value,
    domain_types:        types,
    domain_bounds:       bounds,
    preceding_direction: document.getElementById('env-direction').value,
    min_initial_random:  parseInt(document.getElementById('env-min-init').value) || 6,
  };

  try {
    let data;
    if (_editingEnvId) {
      data = await apiFetch(`/environments/${_editingEnvId}`, {
        method: 'PUT', body: JSON.stringify(body),
      });
    } else {
      data = await apiFetch('/environments', {
        method: 'POST', body: JSON.stringify(body),
      });
    }
    envId = data.id;
    _editingEnvId = null;
    document.getElementById('env-create-btn').textContent = '建立環境';
    await refreshNavEnv(data.id);
    const ok = document.getElementById('env-ok');
    ok.textContent = _editingEnvId ? '✓ 已更新' : '✓ 環境已建立';
    ok.style.display = 'inline';
    setTimeout(() => ok.style.display = 'none', 3000);
    loadEnvList();
  } catch(e) { alert('儲存失敗：' + e.message); }
}

async function loadEnvList() {
  const envs = await apiFetch('/environments');
  await refreshNavEnv();
  document.getElementById('env-list').innerHTML = envs.length ? envs.map(e => `
    <tr class="selrow ${envId === e.id ? 'active' : ''}" onclick="selectEnv('${e.id}')">
      <td><b>${esc(e.name)}</b></td>
      <td style="color:#64748b;">${esc(e.function_name)}</td>
      <td><span class="tag">${e.language}</span></td>
      <td><span class="tag">${Object.keys({}).length}</span></td>
      <td style="color:#94a3b8; font-size:12px;">${fmtDate(e.created_at)}</td>
      <td><button onclick="event.stopPropagation();deleteEnv('${e.id}')" class="btn btn-d btn-sm">刪除</button></td>
    </tr>`).join('') :
    '<tr><td colspan="6" style="text-align:center; color:#94a3b8; padding:20px;">尚無環境，請先建立</td></tr>';
}

async function selectEnv(id) {
  envId = id;
  document.getElementById('nav-env').value = id;
  document.querySelectorAll('#env-list .selrow').forEach(r =>
    r.classList.toggle('active', r.onclick?.toString().includes(`'${id}'`)));
  loadStubs();
  loadConstraints();

  // 載入環境資料填入表單（進入編輯模式）
  try {
    const env = await apiFetch(`/environments/${id}`);
    _editingEnvId = id;
    document.getElementById('env-name').value      = env.name || '';
    document.getElementById('env-lang').value      = env.language || 'python';
    document.getElementById('env-ctx').value       = env.domain_context || '';
    document.getElementById('env-sig').value       = env.func_signature || '';
    document.getElementById('env-direction').value = env.preceding_direction || 'sequential';
    document.getElementById('env-min-init').value  = env.min_initial_random || 6;

    // 讀取原始碼
    const srcRes = await fetch(`${API}/environments/${id}/source`);
    if (srcRes.ok) {
      const src = await srcRes.json();
      document.getElementById('env-source').value = src.source_code || '';
    }

    // 填入參數表格
    if (env.domain_types) {
      buildParamTable(env.domain_types);
      // 套用 domain_bounds
      Object.entries(env.domain_bounds || {}).forEach(([name, [mn, mx]]) => {
        const minEl = document.querySelector(`.p-min[data-p="${name}"]`);
        const maxEl = document.querySelector(`.p-max[data-p="${name}"]`);
        if (minEl) minEl.value = mn;
        if (maxEl) maxEl.value = mx;
      });
      // 填入函數選單
      const sel = document.getElementById('env-func');
      sel.innerHTML = `<option value="${env.function_name}">${env.function_name}</option>`;
    }

    document.getElementById('parse-section').style.display = 'block';
    document.getElementById('env-create-btn').textContent = '更新環境';
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch(e) {
    _editingEnvId = null;
  }
}

async function deleteEnv(id) {
  if (!confirm('確定刪除此環境？所有相關資料也將一併刪除。')) return;
  await apiFetch(`/environments/${id}`, { method: 'DELETE' });
  if (envId === id) { envId = null; document.getElementById('nav-env').value = ''; }
  loadEnvList();
}

// ── 自動生成 ──────────────────────────────────────────────────────────────
function startGenerate(target) {
  if (!envId) return alert('請先選擇環境');
  if (genWs) { genWs.close(); genWs = null; }

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  genWs = new WebSocket(`${proto}://${location.host}/api/environments/${envId}/generate`);
  const maxIter = parseInt(document.getElementById('gen-iter').value) || 1;
  const genBar = document.getElementById('gen-bar');

  document.getElementById('gen-prog').style.display  = 'block';
  document.getElementById('gen-stop').style.display  = 'inline-block';
  genBar.style.background = '';
  genBar.classList.add('indeterminate');
  document.getElementById('gen-pct').textContent     = '—';
  document.getElementById('gen-status').textContent  = '連線中...';
  document.getElementById('gen-latest').textContent  = '';

  genWs.onopen = () => {
    genWs.send(JSON.stringify({
      max_iterations: maxIter,
      target_decision: target || null,
    }));
    document.getElementById('gen-status').textContent = '生成中...';
  };

  genWs.onmessage = ({ data }) => {
    const m = JSON.parse(data);
    if (m.type === 'progress') {
      // IFL-MCDC 的真實覆蓋率要等整次執行（含多輪迭代）結束才知道，
      // 過程中只顯示目前迭代次數與最新案例，不假裝有百分比進度。
      document.getElementById('gen-status').textContent = `迭代 ${m.iter}`;
      if (m.latest_case) {
        const kv = Object.entries(m.latest_case).filter(([k]) => !k.startsWith('__'));
        document.getElementById('gen-latest').textContent = kv.map(([k,v]) => `${k}=${v}`).join('  ');
      }
    } else if (m.type === 'complete') {
      const pct = Math.round((m.final_coverage || 0) * 100);
      genBar.classList.remove('indeterminate');
      genBar.style.width = pct + '%';
      document.getElementById('gen-pct').textContent = pct + '%';
      document.getElementById('gen-stop').style.display = 'none';
      genWs = null;

      if (m.llm_failed) {
        genBar.style.background = '#f59e0b';
        document.getElementById('gen-status').innerHTML =
          `⚠️ LLM 未能生成測試案例（覆蓋率 ${pct}%，${m.case_count} 個隨機初始案例）<br>` +
          `<span style="font-size:11px; color:#92400e;">錯誤：${m.llm_error || '未知'}</span><br>` +
          `<span style="font-size:11px; color:#92400e;">請確認 LLM 設定頁的模型名稱與 API Key 是否正確</span>`;
      } else {
        document.getElementById('gen-status').textContent =
          `O 完成！覆蓋率 ${pct}%，新增 ${m.case_count} 個案例`;
        // 生成完成後自動更新覆蓋率頁資料
        setTimeout(() => loadCoverage(), 500);
      }
    } else if (m.type === 'error') {
      genBar.classList.remove('indeterminate');
      genBar.style.width = '0%';
      document.getElementById('gen-pct').textContent = '0%';
      document.getElementById('gen-status').textContent = '❌ ' + m.detail;
      document.getElementById('gen-stop').style.display = 'none';
      genWs = null;
    }
  };

  genWs.onerror = () => {
    genBar.classList.remove('indeterminate');
    genBar.style.width = '0%';
    document.getElementById('gen-pct').textContent = '0%';
    document.getElementById('gen-status').textContent = '❌ WebSocket 連線失敗';
    document.getElementById('gen-stop').style.display = 'none';
  };
}

function stopGenerate() {
  if (genWs) { genWs.close(); genWs = null; }
  document.getElementById('gen-bar').classList.remove('indeterminate');
  document.getElementById('gen-stop').style.display = 'none';
  document.getElementById('gen-status').textContent = '已中止';
}

function startTargetGen() {
  const t = document.getElementById('gen-target').value.trim();
  if (!t) return alert('請輸入目標條件 ID');
  startGenerate(t);
}

// ── 測試套件 ──────────────────────────────────────────────────────────────
async function loadCases() {
  if (!envId) return;
  const cases = await apiFetch(`/testcases?env_id=${envId}`);
  document.getElementById('case-list').innerHTML = cases.length ? cases.map((tc, i) => `
    <tr>
      <td style="color:#94a3b8;">${i+1}</td>
      <td class="mono" style="font-size:11px; max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
          title="${esc(JSON.stringify(tc.inputs))}">${esc(JSON.stringify(tc.inputs))}</td>
      <td>${tc.expected_output ?? '<span style="color:#cbd5e1;">—</span>'}</td>
      <td><span class="badge b-${tc.created_by === 'auto' ? 'auto' : 'man'}">${tc.created_by === 'auto' ? '自動' : '手動'}</span></td>
      <td><span class="badge ${tc.expected_output ? 'b-pass' : 'b-cov'}">${tc.expected_output ? '已驗證' : '覆蓋率'}</span></td>
      <td><span class="badge ${tc.enabled ? 'b-pass' : 'b-fail'}">${tc.enabled ? '啟用' : '停用'}</span></td>
      <td style="display:flex; gap:4px;">
        <button onclick="setExpected('${tc.id}', '${esc(tc.expected_output ?? '')}')" class="btn btn-s btn-sm">設定預期</button>
        <button onclick="delCase('${tc.id}')" class="btn btn-d btn-sm">刪除</button>
      </td>
    </tr>`).join('') :
    '<tr><td colspan="7" style="text-align:center; color:#94a3b8; padding:20px;">尚無測試案例</td></tr>';
}

async function addCase() {
  if (!envId) return alert('請先選擇環境');
  let inputs;
  try { inputs = JSON.parse(document.getElementById('case-inp').value); }
  catch { return alert('輸入值格式錯誤，請輸入有效 JSON'); }
  const exp = document.getElementById('case-exp').value.trim() || null;
  await apiFetch('/testcases', {
    method: 'POST',
    body: JSON.stringify({ env_id: envId, inputs, expected_output: exp }),
  });
  document.getElementById('case-inp').value = '';
  document.getElementById('case-exp').value = '';
  loadCases();
}

async function setExpected(id, current) {
  const v = prompt('輸入預期輸出（留空 = 覆蓋率模式）：', current);
  if (v === null) return;
  await apiFetch(`/testcases/${id}`, {
    method: 'PUT',
    body: JSON.stringify({ expected_output: v || null, expected_source: v ? 'manual' : 'none' }),
  });
  loadCases();
}

async function delCase(id) {
  if (!confirm('確定刪除？')) return;
  await apiFetch(`/testcases/${id}`, { method: 'DELETE' });
  loadCases();
}

async function captureAll() {
  if (!runId) return alert('請先執行 Run All，再 Capture 結果');
  const data  = await apiFetch(`/runs/${runId}`);
  for (const r of data.results || []) {
    if (r.actual_output != null && r.status !== 'error') {
      await apiFetch(`/testcases/${r.case_id}`, {
        method: 'PUT',
        body: JSON.stringify({ expected_output: String(r.actual_output), expected_source: 'captured' }),
      });
    }
  }
  loadCases();
  alert('Capture 完成');
}

async function importCSV() {
  const file = document.getElementById('case-csv').files[0];
  if (!file || !envId) return;
  const text = await file.text();
  const lines = text.trim().split('\n');
  const header = lines[0].split(',').map(h => h.trim().replace(/"/g,''));
  for (let i = 1; i < lines.length; i++) {
    const vals = lines[i].split(',').map(v => v.trim().replace(/"/g,''));
    const inputs = {};
    let expected = null;
    header.forEach((h, idx) => {
      if (h.toLowerCase() === 'expected' || h.toLowerCase() === 'expected_output') {
        expected = vals[idx] || null;
      } else {
        const v = vals[idx];
        inputs[h] = v === 'true' ? true : v === 'false' ? false : isNaN(v) ? v : Number(v);
      }
    });
    await apiFetch('/testcases', {
      method: 'POST',
      body: JSON.stringify({ env_id: envId, inputs, expected_output: expected }),
    });
  }
  loadCases();
  alert(`匯入完成：${lines.length - 1} 個案例`);
}

// ── 執行 ──────────────────────────────────────────────────────────────────
async function runAll() {
  if (!envId) return alert('請先選擇環境');
  document.getElementById('run-txt').textContent = '執行中...';
  document.getElementById('run-rows').innerHTML =
    '<tr><td colspan="6" style="text-align:center; color:#94a3b8; padding:20px;">執行中，請稍候...</td></tr>';

  const data = await apiFetch('/runs', {
    method: 'POST',
    body: JSON.stringify({ env_id: envId }),
  });
  runId = data.run_id;
  pollRun();
}

async function pollRun() {
  if (!runId) return;
  const data = await apiFetch(`/runs/${runId}`);
  if (data.status === 'running') { setTimeout(pollRun, 2000); return; }
  renderRunResult(data);
  loadRunHistory();
}

function renderRunResult(data) {
  document.getElementById('run-txt').textContent = '執行完成';
  const s = data.summary || {};
  const sumDiv = document.getElementById('run-sum');
  sumDiv.style.display = 'grid';
  document.getElementById('sum-p').textContent   = s.pass   ?? 0;
  document.getElementById('sum-f').textContent   = s.fail   ?? 0;
  document.getElementById('sum-e').textContent   = s.error  ?? 0;
  document.getElementById('sum-cov').textContent = fmt100(s.mcdc_coverage);
  document.getElementById('sum-ver').textContent =
    data.version ? `${(data.version.git_hash||'').slice(0,7) || data.version.file_hash.slice(0,8)}` : '—';

  document.getElementById('run-rows').innerHTML = (data.results || []).map(r => `
    <tr>
      <td class="mono" style="color:#94a3b8; font-size:11px;">${r.case_id.slice(0,8)}</td>
      <td class="mono" style="font-size:11px; max-width:200px; overflow:hidden; text-overflow:ellipsis;" title="${esc(JSON.stringify(r.inputs||{}))}">${esc(JSON.stringify(r.inputs||{}))}</td>
      <td>${r.actual_output ?? '—'}</td>
      <td>${r.expected_output ?? '<span style="color:#cbd5e1;">—</span>'}</td>
      <td><span class="badge b-${r.status}">${{pass:'通過',fail:'失敗',error:'錯誤',coverage_only:'覆蓋率'}[r.status]||r.status}</span></td>
      <td style="color:#94a3b8; font-size:12px;">${(r.execution_time_ms||0).toFixed(1)} ms</td>
    </tr>`).join('');
}

async function loadRunHistory() {
  if (!envId) return;
  const runs = await apiFetch(`/runs?env_id=${envId}`);
  const opts = runs.map(r => `<option value="${r.id}">${r.id.slice(0,8)} (${fmtDate(r.started_at)})</option>`).join('');
  document.getElementById('reg-base').innerHTML = opts;
  document.getElementById('reg-curr').innerHTML = opts;
}

async function compareRuns() {
  const baseId = document.getElementById('reg-base').value;
  const currId = document.getElementById('reg-curr').value;
  if (!baseId || !currId || baseId === currId) return alert('請選擇兩個不同的執行記錄');
  const data = await apiFetch(`/runs/${currId}/compare/${baseId}`);
  const out  = document.getElementById('reg-out');
  out.style.display = 'block';
  if (!data.regressions.length && !data.fixes.length) {
    out.innerHTML = '<div style="color:#16a34a; font-size:13px;">O 無回退，覆蓋率穩定</div>';
  } else {
    out.innerHTML = `<div style="font-size:13px;">
      ${data.regressions.map(r => `<div style="color:#dc2626;">⚠ 案例 ${r.case_id.slice(0,8)}：通過 → 失敗</div>`).join('')}
      ${data.fixes.map(r => `<div style="color:#16a34a;">O 案例 ${r.case_id.slice(0,8)}：失敗 → 通過</div>`).join('')}
      ${data.coverage_delta != null ? `<div style="margin-top:6px;">覆蓋率變化：<b>${(data.coverage_delta*100).toFixed(1)}%</b></div>` : ''}
    </div>`;
  }
}

// ── 覆蓋率 ────────────────────────────────────────────────────────────────
async function loadCoverage(runIdOverride) {
  if (!envId) return;
  if (runIdOverride !== undefined) covRunId = runIdOverride;
  const qs   = covRunId ? `?run_id=${covRunId}` : '';
  const data = await apiFetch(`/coverage/environments/${envId}/coverage${qs}`);
  snapId = data.snapshot_id || null;

  const pct = Math.round((data.effective_coverage || 0) * 100);
  document.getElementById('cov-bar').style.width = pct + '%';
  document.getElementById('cov-pct').textContent = pct + '%';

  const covered    = new Set(data.covered_pairs   || []);
  const infeasible = new Set(data.infeasible_pairs || []);
  const excluded   = new Set(data.excluded_pairs  || []);

  const allKeys = [...covered, ...(data.uncovered_pairs||[]), ...infeasible, ...excluded];
  const conds   = [...new Set(allKeys.map(k => k.replace(/_F2T$|_T2F$/,'')))];

  document.getElementById('cov-rows').innerHTML = conds.map(cid => `
    <tr>
      <td><code>${cid}</code></td>
      <td style="color:#64748b; font-size:12px;">${data.flip_pair_matrix?.[cid]?.expression || ''}</td>
      <td style="text-align:center;">${flipCell(cid,'F2T', covered, infeasible, excluded)}</td>
      <td style="text-align:center;">${flipCell(cid,'T2F', covered, infeasible, excluded)}</td>
      <td>
        ${(!covered.has(cid+'_F2T') || !covered.has(cid+'_T2F')) && !infeasible.has(cid+'_F2T') ?
          `<button onclick="showHint('${cid}')" class="btn btn-s btn-sm">查看建議</button>` : ''}
      </td>
    </tr>`).join('');

  loadTrend();
}

function flipCell(cid, flip, covered, infeasible, excluded) {
  const k = `${cid}_${flip}`;
  if (covered.has(k))    return `<span class="flip-ok" style="cursor:pointer;" onclick="showPairDetail('${cid}','${flip}','covered')">O ${flip}</span>`;
  if (infeasible.has(k)) return `<span class="flip-inf">⚫ ${flip}</span>`;
  if (excluded.has(k))   return `<span class="flip-exc">${flip}</span>`;
  return `<span class="flip-no" onclick="showHint('${cid}','${flip}')">X ${flip}</span>`;
}

async function showPairDetail(condId, flip, status) {
  if (!envId) return;
  try {
    const qs   = covRunId ? `?run_id=${covRunId}` : '';
    const data = await apiFetch(`/coverage/environments/${envId}/pairs/${condId}/${flip}${qs}`);
    const modal = document.getElementById('hint-modal');
    document.getElementById('hint-title').textContent =
      `${condId} (${flip}) — ${status === 'covered' ? 'O 已覆蓋' : 'X 未覆蓋'}`;

    const fmtInputs = inputs => {
      const entries = Object.entries(inputs || {});
      if (!entries.length) return '<span style="color:#94a3b8;">（無資料）</span>';
      return `<table style="font-size:12px; font-family:monospace; border-collapse:collapse;">` +
        entries.map(([k, v]) =>
          `<tr>
            <td style="color:#64748b; padding:1px 8px 1px 0; white-space:nowrap;">${esc(k)}</td>
            <td style="font-weight:600; padding:1px 0;">${esc(String(v))}</td>
          </tr>`
        ).join('') + '</table>';
    };

    if (status === 'covered' || data.pairs?.length) {
      const { false_side = [], true_side = [] } = data.candidates || {};

      const fmtSide = (cases, label) => cases.length === 0
        ? `<div style="font-size:12px; color:#94a3b8; padding:4px 0;">${label}：無資料</div>`
        : `<div style="margin-bottom:10px;">
            <div style="font-size:12px; font-weight:500; color:#374151; margin-bottom:4px;">${label}</div>
            ${cases.map(c => `
              <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:6px; padding:8px; margin-bottom:4px;">
                <div style="font-size:11px; color:#64748b; margin-bottom:3px;">決策=${c.decision ?? '?'}</div>
                ${fmtInputs(c.inputs)}
              </div>`).join('')}
           </div>`;

      document.getElementById('hint-body').innerHTML =
        `<div style="font-size:13px; color:#16a34a; font-weight:500; margin-bottom:10px;">O 此 pair 已覆蓋，以下是填補此缺口的兩側案例：</div>` +
        fmtSide(false_side, '條件 = False 的案例') +
        fmtSide(true_side,  '條件 = True 的案例');
    } else if (data.candidates) {
      const { false_side = [], true_side = [] } = data.candidates;

      const fmtSide = (cases, label, hasData) => {
        if (!hasData) return `
          <div style="margin-bottom:10px;">
            <div style="font-size:12px; font-weight:500; color:#374151; margin-bottom:4px;">${label}</div>
            <div style="font-size:12px; color:#94a3b8; padding:6px 0;">❌ 無此側案例</div>
          </div>`;
        return `
          <div style="margin-bottom:10px;">
            <div style="font-size:12px; font-weight:500; color:#374151; margin-bottom:4px;">${label}</div>
            ${cases.map(c => `
              <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:8px; margin-bottom:4px;">
                <div style="font-size:11px; color:#64748b; margin-bottom:3px;">決策=${c.decision ?? '?'}</div>
                ${fmtInputs(c.inputs)}
              </div>`).join('')}
          </div>`;
      };

      const flipLabel = hintGap?.flip === 'F2T' ? 'F2T（False→True）' : 'T2F（True→False）';
      document.getElementById('hint-body').innerHTML =
        `<div style="font-size:13px; color:#64748b; margin-bottom:10px;">缺口 <b>${hintGap?.condId}</b> ${flipLabel} 尚未被覆蓋。需要以下兩側各一個案例，且其他條件相同：</div>` +
        fmtSide(false_side, '條件 = False 的案例', false_side.length > 0) +
        fmtSide(true_side,  '條件 = True 的案例',  true_side.length > 0) +
        `<div style="font-size:11px; color:#94a3b8; margin-top:6px;">若某側為空，請透過補強生成或手動新增案例</div>`;
    } else {
      document.getElementById('hint-body').innerHTML =
        '尚無 probe 資料，請先自動生成或執行 Run All。';
    }
    modal.style.display = 'flex';
  } catch(e) {
    console.error(e);
  }
}

async function showHint(condId, flip) {
  hintGap = { condId, flip: flip || 'F2T' };
  // 同步填入補強目標欄位
  document.getElementById('gen-target').value = condId;
  try {
    const qs   = covRunId ? `?run_id=${covRunId}` : '';
    const data = await apiFetch(`/coverage/environments/${envId}/gaps/${condId}/${hintGap.flip}/hint${qs}`);
    document.getElementById('hint-title').textContent = `條件 ${condId}（${hintGap.flip}）的 Z3 建議`;
    document.getElementById('hint-body').innerHTML =
      `<b>${data.z3_hint || '無具體建議'}</b>` +
      (data.bound_specs?.length
        ? '<br><br><b>建議變數範圍：</b><br>' +
          data.bound_specs.map(s => `• <code>${s.var}</code>：[${s.min ?? '?'}, ${s.max ?? '?'}]`).join('<br>')
        : '') +
      `<br><br><span style="color:#64748b; font-size:12px;">條件 ID 已填入生成頁面的「目標條件」欄位</span>`;
    document.getElementById('hint-modal').style.display = 'flex';
  } catch(e) {
    alert('取得建議失敗：' + e.message);
  }
}

function closeModal() {
  document.getElementById('hint-modal').style.display = 'none';
  hintGap = null;
}

function triggerSupplemental() {
  if (!hintGap) return;
  document.getElementById('gen-target').value = hintGap.condId;
  closeModal();
  showTab('gen');
  // 不自動開始，讓使用者確認設定後再點擊「補強生成」
}

async function doExcludeGap() {
  if (!hintGap || !snapId) return alert('無快照資料，請先執行 Run All');
  const reason = prompt('請輸入排除原因：');
  if (!reason) return;
  await apiFetch(`/coverage/gaps/${hintGap.condId}/${hintGap.flip}/exclude?snapshot_id=${snapId}`, {
    method: 'POST',
    body: JSON.stringify({ reason, excluded_by: 'user' }),
  });
  closeModal();
  loadCoverage();
}

async function doJustifyGap() {
  if (!hintGap || !snapId) return;
  const justification = prompt('請說明此路徑不可行的原因（將記錄為正當性說明）：');
  if (!justification) return;
  const signed_by = prompt('簽核人員名稱：') || 'user';
  await apiFetch(`/coverage/gaps/${hintGap.condId}/${hintGap.flip}/justify?snapshot_id=${snapId}`, {
    method: 'POST',
    body: JSON.stringify({ justification, signed_by }),
  });
  closeModal();
  loadCoverage();
}

async function loadTrend() {
  if (!envId) return;
  const data  = await apiFetch(`/coverage/environments/${envId}/trends`);
  const trend = data.coverage_trend || [];
  const maxH  = 64;
  document.getElementById('trend-bars').innerHTML = trend.map((t, i) => {
    const h = Math.round((t.mcdc_coverage || 0) * maxH);
    return `<div
      title="第 ${i+1} 次：${fmt100(t.mcdc_coverage)}（點擊查看詳情）"
      onclick="showRunFromTrend('${t.run_id}')"
      style="background:#4f46e5; width:20px; height:${h}px; border-radius:3px 3px 0 0;
             opacity:.7; flex-shrink:0; cursor:pointer; transition:opacity .15s;"
      onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=.7"></div>`;
  }).join('');
  document.getElementById('trend-lbls').innerHTML = trend.map((t, i) =>
    `<div style="width:20px; text-align:center; flex-shrink:0; font-size:10px;"
          title="${fmt100(t.mcdc_coverage)}">${i+1}</div>`
  ).join('');
}

async function showRunFromTrend(selectedRunId) {
  if (!selectedRunId) return;
  showTab('cov');
  await loadCoverage(selectedRunId);
  // 在覆蓋率頁標示當前查看的 run
  const header = document.querySelector('#page-cov .section');
  if (header) {
    header.textContent = `MC/DC 覆蓋率（Run: ${selectedRunId.slice(0,8)}）`;
  }
}

// ── 需求追溯 ──────────────────────────────────────────────────────────────
async function loadRequirements() {
  const reqs  = await apiFetch('/requirements');
  const cases = envId ? await apiFetch(`/testcases?env_id=${envId}`) : [];
  const container = document.getElementById('req-list');

  if (!reqs.length) {
    container.innerHTML = '<div style="color:#94a3b8; text-align:center; padding:20px;">尚無需求，請先新增</div>';
    return;
  }

  container.innerHTML = reqs.map(r => {
    const linked = new Set(r.linked_cases || []);
    const caseOpts = cases.map(c =>
      `<option value="${c.id}" ${linked.has(c.id) ? 'selected' : ''}>${c.id.slice(0,8)} - ${esc(JSON.stringify(c.inputs)).slice(0,40)}</option>`
    ).join('');
    return `
      <div style="border:1px solid #e2e8f0; border-radius:8px; padding:12px; margin-bottom:8px;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
          <div>
            <span class="tag" style="margin-right:6px;">${esc(r.req_id)}</span>
            <span style="font-size:13px; font-weight:500;">${esc(r.description)}</span>
            ${r.source ? `<span style="font-size:12px; color:#94a3b8; margin-left:6px;">${esc(r.source)}</span>` : ''}
          </div>
          <button onclick="delReq('${r.id}')" class="btn btn-d btn-sm">刪除</button>
        </div>
        ${cases.length ? `
        <div style="margin-top:8px;">
          <label class="lbl">掛載測試案例</label>
          <select multiple style="border:1px solid #e2e8f0; border-radius:6px; padding:4px; font-size:12px; width:100%; height:80px;"
            onchange="updateReqLinks('${r.id}', this)">${caseOpts}</select>
        </div>` : ''}
      </div>`;
  }).join('');
}

async function updateReqLinks(reqId, sel) {
  const selected = new Set([...sel.selectedOptions].map(o => o.value));
  const reqs = await apiFetch('/requirements');
  const req  = reqs.find(r => r.id === reqId);
  const linked = new Set(req?.linked_cases || []);

  for (const cid of selected) {
    if (!linked.has(cid)) await apiFetch(`/requirements/${reqId}/link/${cid}`, { method: 'POST', body: '{}' });
  }
  for (const cid of linked) {
    if (!selected.has(cid)) await apiFetch(`/requirements/${reqId}/link/${cid}`, { method: 'DELETE' });
  }
}

async function addReq() {
  const reqId = document.getElementById('req-id').value.trim();
  const desc  = document.getElementById('req-desc').value.trim();
  if (!reqId || !desc) return alert('請填寫需求編號和描述');
  await apiFetch('/requirements', {
    method: 'POST',
    body: JSON.stringify({ req_id: reqId, description: desc, source: document.getElementById('req-src').value }),
  });
  document.getElementById('req-id').value   = '';
  document.getElementById('req-desc').value = '';
  document.getElementById('req-src').value  = '';
  loadRequirements();
}

async function delReq(id) {
  if (!confirm('確定刪除？')) return;
  await apiFetch(`/requirements/${id}`, { method: 'DELETE' });
  loadRequirements();
}

// ── 報告匯出 ──────────────────────────────────────────────────────────────
function exportReport(fmt) {
  if (!envId) return alert('請先選擇環境');
  window.open(`${API}/runs/export?env_id=${envId}&format=${fmt}`, '_blank');
}

// ── LLM 設定 ──────────────────────────────────────────────────────────────
function applyCfgProvVisibility() {
  const p = document.getElementById('cfg-prov').value;
  document.getElementById('cfg-hint').style.display    = p === 'ollama' ? 'block' : 'none';
  document.getElementById('cfg-url-row').style.display = p === 'ollama' ? 'block' : 'none';
  document.getElementById('cfg-key-row').style.display = p === 'ollama' ? 'none'  : 'block';
}

// 使用者手動切換 provider 下拉選單時才重設成該 provider 的預設 model
function onCfgProvChange() {
  const p = document.getElementById('cfg-prov').value;
  document.getElementById('cfg-model').value = MODEL_DEFS[p] || '';
  applyCfgProvVisibility();
}

async function loadLLMCfg() {
  const data = await apiFetch('/settings/llm');
  document.getElementById('cfg-prov').value   = data.provider || 'openai';
  document.getElementById('cfg-model').value  = data.model    || '';
  document.getElementById('cfg-iter').value   = data.max_iterations || 50;
  document.getElementById('cfg-budget').value = data.token_budget   || 100000;
  document.getElementById('cfg-used').textContent = (data.total_tokens_used || 0).toLocaleString();
  if (data.base_url) document.getElementById('cfg-url').value = data.base_url;
  applyCfgProvVisibility();
}

async function saveLLMCfg() {
  await apiFetch('/settings/llm', {
    method: 'PUT',
    body: JSON.stringify({
      provider:       document.getElementById('cfg-prov').value,
      model:          document.getElementById('cfg-model').value,
      api_key:        document.getElementById('cfg-key').value,
      base_url:       document.getElementById('cfg-url').value,
      max_iterations: parseInt(document.getElementById('cfg-iter').value),
      token_budget:   parseInt(document.getElementById('cfg-budget').value),
    }),
  });
  const ok = document.getElementById('cfg-ok');
  ok.style.display = 'inline';
  setTimeout(() => ok.style.display = 'none', 2000);
}

async function loadGenDefaults() {
  try {
    const data = await apiFetch('/settings/llm');
    document.getElementById('gen-iter').value = data.max_iterations || 50;
  } catch (e) { /* 忽略，沿用目前值 */ }
}

// ── 初始化 ────────────────────────────────────────────────────────────────
(async () => {
  await refreshNavEnv();
  showTab('env');
})();
