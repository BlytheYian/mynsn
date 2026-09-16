"""Build a Markdown snapshot from all saved mutation_results*.json under results."""
import collections
import datetime
import json
import statistics as stats
from pathlib import Path

ROOT = Path(__file__).resolve().parent
paths = sorted((ROOT / 'results/experiment3/full').glob('*/mutation_results*.json'))
records = {}
for path in paths:
    for item in json.loads(path.read_text(encoding='utf-8')):
        row = dict(item, model=path.parent.name, source=path.relative_to(ROOT).as_posix())
        key = (row['model'], row['fixture'], row['method'], row.get('profile', 'full'), row['run_id'])
        if key in records:
            raise ValueError(f'Duplicate run: {key}')
        records[key] = row
def included(r):
    if (r['model'], r['fixture'], r['method']) == ('gemma', 'tcas_sir_copy', 'ifl_gemma'):
        return 2 <= r['run_id'] <= 31
    return True
excluded = [r for r in records.values() if not included(r)]
rows = [r for r in records.values() if included(r)]
def valid(r):
    return r.get('status') == 'completed' and isinstance(r.get('mutation_score'), (int, float))
def mean(rs, key):
    values = [r[key] for r in rs if isinstance(r.get(key), (int, float))]
    return stats.mean(values) if values else float('nan')
def pct(v):
    return f'{v * 100:.2f}%'
def table(headers, data):
    return ['| ' + ' | '.join(headers) + ' |', '|' + '|'.join(['---'] * len(headers)) + '|'] + [
        '| ' + ' | '.join(map(str, r)) + ' |' for r in data] + ['']

groups = collections.defaultdict(list)
for r in rows:
    groups[r['model'], r['fixture'], r['method'], r.get('profile', 'full')].append(r)
ok = [r for r in rows if valid(r)]
for r in ok:
    assert r['total_mutants'] > 0
    assert abs(r['mutation_score'] - r['killed'] / r['total_mutants']) < 1e-10
    assert sum(r['counts'].values()) == r['total_mutants']
    assert r['baseline_passed'] and r['unresolved'] == 0

lines = ['# Mutation 結果彙整', '',
    f'產生時間：{datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}（UTC）。', '',
    f'共 **{len(rows):,}** 筆；有有效分數 **{len(ok):,}** 筆；錯誤 **{sum(r.get("status") == "error" for r in rows)}** 筆。', '',
    '## 統計口徑', '',
    '- 來源為下列 mutation 結果索引；不重複累計 details 內的 report、request 或舊 attempt。',
    '- Gemma／tcas_sir_copy／ifl_gemma 正式樣本為 run 2–31（30 筆）；run 1 經使用者確認為測試資料，排除統計。其他組別維持原樣本。原始 run 1 紀錄保留供追溯。',
    '- 依模型、fixture、方法及 profile 分組；有效分數僅包含 completed 且 mutation_score 非 null 的紀錄。錯誤／未完成不當作 0 分。',
    '- Mutation score = killed / total_mutants；分母包含全部生成 mutant，未排除等價 mutant。',
    '- 平均分數為各 run 分數的算術平均；SD 為樣本標準差（以百分點表示）。案例及 killed/survived 平均也只使用有效 run。',
    '- full 表示使用保存測試集的全部去重案例，不表示各方法具有相同案例預算。',
    '- 模型總覽使用 fixture 等權宏平均，避免單一 fixture 因有效 run 數不同取得較大權重；不將不同 fixture 的 mutant 合併成單一分母。', '']
lines += [f'- [{p.relative_to(ROOT).as_posix()}]({p.relative_to(ROOT / "results/experiment3").as_posix()})' for p in paths] + ['']
lines += ['## 模型與方法總覽', '']
summary = []
for model, method, profile in sorted({(r['model'], r['method'], r.get('profile', 'full')) for r in rows}):
    subset = [r for r in rows if (r['model'], r['method'], r.get('profile', 'full')) == (model, method, profile)]
    good = [r for r in subset if valid(r)]
    fixture_means = [mean([r for r in good if r['fixture'] == f], 'mutation_score') for f in sorted({r['fixture'] for r in good})]
    summary.append([model, method, profile, f'{len(good)}/{len(subset)}', len(fixture_means), pct(stats.mean(fixture_means)), f'{mean(good,"cases"):.2f}'])
lines += table(['模型','方法','profile','有效/紀錄','fixtures','平均分數（fixture 等權）','平均案例數（run 等權）'], summary)

lines += ['## 各模型、fixture 詳細統計', '']
for model in sorted({r['model'] for r in rows}):
    lines += [f'### {model}', '']
    data = []
    for (m, fixture, method, profile), subset in sorted(groups.items()):
        if m != model:
            continue
        good = [r for r in subset if valid(r)]
        scores = [r['mutation_score'] for r in good]
        banks = {r['bank_id'] for r in good}
        assert len(banks) <= 1, f'Mixed banks in {m}/{fixture}/{method}'
        data.append([fixture, method.removesuffix('_' + model), profile, f'{len(good)}/{len(subset)}',
            pct(stats.mean(scores)), f'{stats.stdev(scores)*100:.2f}' if len(scores)>1 else '—',
            pct(stats.median(scores)), f'{pct(min(scores))}–{pct(max(scores))}',
            f'{mean(good,"cases"):.2f}', '/'.join(map(str,sorted({r['total_mutants'] for r in good}))),
            f'{mean(good,"killed"):.2f}', f'{stats.mean(r["counts"].get("survived",0) for r in good):.2f}'])
    lines += table(['fixture','方法','profile','有效/紀錄','平均分數','SD（百分點）','中位數','最小–最大','平均案例數','mutants/run','平均 killed','平均 survived'], data)

lines += ['## IFL 與 LLM-only 比較', '', '差值為 IFL 平均分數減去 LLM-only 平均分數（百分點）；只比較相同 fixture、profile 與 bank_id。這是描述統計，未作顯著性檢定。', '']
comparisons = []
for model, fixture, profile in sorted({(r['model'],r['fixture'],r.get('profile','full')) for r in rows}):
    a = [r for r in groups.get((model,fixture,'ifl_'+model,profile),[]) if valid(r)]
    b = [r for r in groups.get((model,fixture,'llm_only_'+model,profile),[]) if valid(r)]
    if not a or not b:
        continue
    assert len({r['bank_id'] for r in a+b}) == 1
    comparisons.append([model,fixture,profile,f'{len(a)}/{len(b)}',pct(mean(a,'mutation_score')),pct(mean(b,'mutation_score')),f'{100*(mean(a,"mutation_score")-mean(b,"mutation_score")):+.2f}',f'{mean(a,"cases"):.2f} / {mean(b,"cases"):.2f}'])
lines += table(['模型','fixture','profile','有效 n（IFL/LLM）','IFL','LLM-only','差值（百分點）','平均案例數（IFL/LLM）'], comparisons)
lines += ['## Mutant bank 一致性', '', '下列 bank_id 以完整雜湊核對；表內顯示前 12 碼。同 fixture 的有效紀錄跨模型、方法使用相同 bank 才可直接比較。', '']
banks = []
for fixture in sorted({r['fixture'] for r in ok}):
    subset = [r for r in ok if r['fixture']==fixture]
    ids = {r['bank_id'] for r in subset}
    assert len(ids)==1, f'Mixed fixture banks: {fixture}'
    banks.append([fixture, next(iter(ids))[:12], '/'.join(map(str,sorted({r['total_mutants'] for r in subset}))), ', '.join(sorted({r['mutmut_version'] for r in subset})),len(subset),'一致'])
lines += table(['fixture','bank_id 前綴','mutants/run','mutmut','有效 runs','跨模型/方法'],banks)
lines += ['## 錯誤與未納入分數的紀錄', '']
errors = [[r['model'],r['fixture'],r['method'],r['run_id'],r['status'],str(r.get('error','mutation_score 為 null')).replace('|','\\|').replace('\n',' ')] for r in rows if not valid(r)]
lines += table(['模型','fixture','方法','run','狀態','原因'],errors) if errors else ['無。','']
lines += ['## 排除的測試資料', '',
          '下列紀錄不計入正式樣本總數、錯誤數或平均值。', '']
lines += table(['模型','fixture','方法','run','原始狀態','排除原因'],
               [[r['model'],r['fixture'],r['method'],r['run_id'],r['status'],'使用者確認為測試資料；正式採樣 run 2–31'] for r in excluded])
target = ROOT / 'results/experiment3/MUTATION_SUMMARY.md'
target.write_text('\n'.join(lines),encoding='utf-8')
print(f'Wrote {target}: {len(rows)} records, {len(ok)} valid, {len(groups)} groups')
for row in summary:
    print(' | '.join(map(str,row)))
