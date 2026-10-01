#!/usr/bin/env python3
"""置信度后验校准台账（R100z11）——给「高确信」标签一个可核对的命中率账本。

背景（2026-09-30 实测）：当日 6 板块验证 hit 2 / miss 3 / 部分兑现 1（胜率 33%），
而 miss 的两条「光通信·光模块」「半导体设备材料」恰好都标了「高确信」走强。
标签与命中率背离时，系统没有任何机制去惩罚这个标签——本脚本就是把账本做出来。

用法：
    python3 track_calibration.py                 # 并入本轮 data.js 预测结果 + 打印分层命中率与门槛
    python3 track_calibration.py --view          # 只查看台账与统计，不并入
    python3 track_calibration.py --json          # 机器可读输出（自动化读取门槛用）

数据：
    台账 .calibration_log.json  { log: { "<预测日期>": [ {date,sector,predicted,actualPct,result,conf,hit} ] } }
    门槛 .calibration_state.json { generatedAt, window, rate:{高:..,中:..,低:..}, gate:{...} }

命中口径（与 16:00 生成 verification 时一致）：
    预测「走强/偏强」→ 板块当日涨幅 > 0 命中；预测「承压」→ 涨幅 < 0 命中；
    |涨幅| < 0.2% 记 partial（部分兑现，计 0.5）；方向不匹配的记 0。
"""
import argparse
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(ROOT, 'data.js')
LOG_FILE = os.path.join(ROOT, '.calibration_log.json')
STATE_FILE = os.path.join(ROOT, '.calibration_state.json')

# 门槛阈值（命中率 < 阈值即触发降档；见 references/predict-calibration.md）
HARD_GATE = 0.25    # 低于此值：当日禁止使用「高确信」
SOFT_GATE = 0.40    # 低于此值：当日最多 1 条「高确信」
MIN_SAMPLE = 5      # 样本不足时不降档（避免小样本噪声误杀）
WINDOW = 20         # 统计窗口（交易日）

NODE_SRC = r"""
const fs = require('fs');
const p = process.env.DATA_PATH;
let s = fs.readFileSync(p, 'utf8');
let m = s.match(/window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!m) { console.error('data.js parse failed'); process.exit(1); }
let d; try { d = eval('(' + m[1] + ')'); } catch (e) { console.error('eval failed', e.message); process.exit(1); }
const ap = d.aiPrediction || {};
process.stdout.write(JSON.stringify({
  date: ap.date || null,
  confBySector: (ap.sectors || []).map((x) => ({ sector: x.sector, conf: x.confidence || null })),
  verification: ap.verification || null
}));
"""


def load_prediction(path=DATA_JS):
    import subprocess
    node = ('/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node'
            if os.path.exists('/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node') else 'node')
    env = dict(os.environ, DATA_PATH=path)
    pr = subprocess.run([node, '-e', NODE_SRC], env=env, capture_output=True, text=True)
    if pr.returncode != 0:
        print('[ERR] 读取 data.js 失败：' + (pr.stderr or '').strip()[:200])
        return None
    try:
        return json.loads(pr.stdout)
    except Exception as e:  # noop
        print('[ERR] 解析失败：' + str(e))
        return None


def score(predicted, actual_pct):
    """返回 1 / 0.5 / 0（部分兑现 0.5）。"""
    if actual_pct is None:
        return None
    up = str(predicted or '').find('承压') == -1
    if abs(actual_pct) < 0.2:
        return 0.5
    return (1 if up and actual_pct > 0 else (1 if (not up) and actual_pct < 0 else 0))


def load_log():
    if os.path.exists(LOG_FILE):
        try:
            return json.load(open(LOG_FILE, encoding='utf-8'))
        except Exception:
            pass
    return {'log': {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--view', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--backfill', type=int, default=0,
                    help='从 git 历史 data.js 回填最近 N 个版本的预测结果，快速攒够分层样本（默认不回填）')
    a = ap.parse_args()

    log = load_log()

    # --backfill：从 git 历史提交里的 data.js 提取 aiPrediction.verification，快速攒样本。
    # 历史快照中的 actualPct/result 是当时写下的真实收盘结果，conf 随 sectors 一并保留，口径可信；
    # 同一日期若已入账则跳过（幂等）。
    if a.backfill and not a.view:
        import subprocess
        try:
            hashes = subprocess.run(['git', '-C', ROOT, 'log', '--format=%H', '-n', str(a.backfill)],
                                    capture_output=True, text=True).stdout.split()
        except Exception as e:  # noop
            hashes = []
            print('[WARN] git 历史读取失败：' + str(e))
        added = 0
        for h in hashes:
            try:
                blob = subprocess.run(['git', '-C', ROOT, 'show', f'{h}:data.js'],
                                      capture_output=True, text=True)
                if blob.returncode != 0 or not blob.stdout:
                    continue
                tmp = os.path.join(ROOT, '.calibration_tmp.js')
                open(tmp, 'w', encoding='utf-8').write(blob.stdout)
                pd = load_prediction(tmp)
                if os.path.exists(tmp):
                    os.remove(tmp)
                if not pd or not pd.get('date') or not (pd.get('verification') or {}).get('details'):
                    continue
                if pd['date'] in log['log']:
                    continue
                conf_map = {c['sector']: c['conf'] for c in (pd.get('confBySector') or [])}
                items = []
                for d in pd['verification']['details']:
                    items.append({'date': pd['date'], 'sector': d.get('sector'),
                                  'predicted': d.get('predicted'), 'actualPct': d.get('actualPct'),
                                  'result': d.get('result'), 'conf': conf_map.get(d.get('sector'), 'unknown'),
                                  'hit': score(d.get('predicted'), d.get('actualPct'))})
                log['log'][pd['date']] = items
                added += 1
            except Exception:
                continue
        if added:
            json.dump(log, open(LOG_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            if not a.json:
                print(f"[OK] 回填 {added} 个历史交易日入账")
    if not a.view:
        pred = load_prediction()
        if not pred:
            sys.exit(1)
        conf_map = {c['sector']: c['conf'] for c in (pred.get('confBySector') or [])}
        dets = (pred.get('verification') or {}).get('details') or []
        if pred.get('date') and dets:
            items = []
            for d in dets:
                conf = conf_map.get(d.get('sector'), 'unknown')
                sc = score(d.get('predicted'), d.get('actualPct'))
                items.append({'date': pred['date'], 'sector': d.get('sector'),
                              'predicted': d.get('predicted'), 'actualPct': d.get('actualPct'),
                              'result': d.get('result'), 'conf': conf, 'hit': sc})
            log['log'][pred['date']] = items
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(log, f, ensure_ascii=False, indent=1)
            if not a.json:
                print(f"[OK] 已并入台账：{pred['date']}（{len(items)} 条）")

    # 统计：按 conf 层聚合，取最近 WINDOW 个预测日
    dates = sorted(log['log'].keys())[-WINDOW:]
    buckets = {'高': [], '中': [], '低': [], 'unknown': []}
    for dt in dates:
        for it in log['log'][dt]:
            buckets.setdefault(str(it.get('conf') or 'unknown'), []).append(it)

    stat = {}
    for k, v in buckets.items():
        if not v:
            continue
        got = sum(x['hit'] for x in v if x['hit'] is not None)
        n = len([x for x in v if x['hit'] is not None])
        stat[k] = {'n': n, 'hit': got, 'rate': round(got / n, 3) if n else None,
                   'sampleEnough': n >= MIN_SAMPLE}

    def gate_for(key):
        s = stat.get(key)
        if not s or not s.get('rate'):
            return {'maxHighConf': 2, 'maxConf': '高确信', 'reason': '样本不足，暂不降档'}
        if s['rate'] < HARD_GATE and s['sampleEnough']:
            return {'maxHighConf': 0, 'maxConf': '中等确信',
                    'reason': f'「{key}」层近 {len(dates)} 日命中率 {s["rate"]:.0%} < {HARD_GATE:.0%}，当日禁用该层'}
        if s['rate'] < SOFT_GATE and s['sampleEnough']:
            return {'maxHighConf': 1, 'maxConf': '中等确信',
                    'reason': f'「{key}」层近 {len(dates)} 日命中率 {s["rate"]:.0%} < {SOFT_GATE:.0%}，当日最多 1 条该层'}
        return {'maxHighConf': 2, 'maxConf': '高确信',
                'reason': f'「{key}」层近 {len(dates)} 日命中率 {s["rate"]:.0%}（≥{SOFT_GATE:.0%}），可正常使用'}

    gates = {k: gate_for(k) for k in ('高', '中', '低')}
    state = {'generatedAt': date.today().isoformat(), 'window': dates, 'stat': stat, 'gates': gates}
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=1)

    if a.json:
        print(json.dumps(state, ensure_ascii=False))
        return 0
    print('=== 置信度后验校准台账（近 %d 个预测日：%s）===' % (len(dates), '、'.join(dates[-6:])))
    for k in ('高', '中', '低'):
        s = stat.get(k)
        if not s:
            print(f'  {k}确信：无样本')
            continue
        print(f"  {k}确信：{s['n']} 条 / 命中分 {s['hit']} / 命中率 {s['rate']:.0%}"
              f"{'' if s['sampleEnough'] else '（样本不足 %d，暂不降档）' % MIN_SAMPLE}")
        print('      门槛：' + gates[k]['reason'])
    print('[OK] 门槛已写入 ' + os.path.basename(STATE_FILE))
    return 0


if __name__ == '__main__':
    sys.exit(main())
