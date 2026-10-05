#!/usr/bin/env python3
"""开盘前瞻兑现打分（R100z13）——把 openOutlook 从「每天生成、每天作废」变成可回测。

用户拍板（2026-10-01）：「当天开盘前瞻的结论在每天 16 点自动化里面更新吧，次日早上更新新的
开盘前瞻再覆盖掉即可」——即：
    T 日 08:30 写 openOutlook（含结构化假设 openOutlook.pos：倾向 / 点位区间 / 量能预期）
    T 日 16:00 本脚本按当日真实收盘给这份前瞻打分，结果写进台账 open[date]；
                AI 再把打分写回 openOutlook.verification（当日可见一天）
    T+1 08:30 新前瞻整字段覆盖旧的（verification 随之消失，历史留在台账里不会被冲掉）

打分口径（三项加权，权重写死在代码里，AI 不得改）：
    倾向 tendency  0.5  —— 预测「偏强 / 承压 / 中性」vs 上证当日实际涨跌
    点位 band      0.3  —— 收盘是否落在 08:30 给出的区间内（偏离 ≤1% 记 0.5）
    量能 vol       0.2  —— 用涨跌家数占比做量价一致性代理（08:30 未写量能预期时该项不计入，按剩余权重归一）

用法：
    python3 score_openoutlook.py                # 打分并写入台账 open[date]
    python3 score_openoutlook.py --json         # 机器可读输出（16:00 写 openOutlook.verification 用）
    python3 score_openoutlook.py --view         # 只看台账里已有的前瞻打分，不写
"""
import argparse
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import track_calibration as tc  # 复用台账读写（R100z13：不重复实现，避免两份口径）

NODE_SRC = r"""
const fs = require('fs');
const p = process.env.DATA_PATH;
let s = fs.readFileSync(p, 'utf8');
let m = s.match(/window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!m) { console.error('data.js parse failed'); process.exit(1); }
let d; try { d = eval('(' + m[1] + ')'); } catch (e) { console.error('eval failed', e.message); process.exit(1); }
const oo = d.openOutlook || {};
const a = d.ashare || {};
process.stdout.write(JSON.stringify({
  openDate: oo.date || null,
  pos: oo.pos || null,
  indices: a.indices || [],
  breadth: a.breadth || null,
  tradeDate: a.tradeDate || null
}));
"""

# 权重（写死，避免 AI 通过调整权重让打分变好看）
W_TENDENCY, W_BAND, W_VOL = 0.5, 0.3, 0.2


def load_state(path=None):
    import subprocess
    node = ('/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node'
            if os.path.exists('/Users/loccco/.workbuddy/binaries/node/versions/22.22.2-3/bin/node') else 'node')
    pr = subprocess.run([node, '-e', NODE_SRC],
                        env=dict(os.environ, DATA_PATH=path or os.environ.get('DATA_PATH') or tc.DATA_JS),
                        capture_output=True, text=True)
    if pr.returncode != 0:
        print('[ERR] 读取 data.js 失败：' + (pr.stderr or '').strip()[:200], file=sys.stderr)
        return None
    return json.loads(pr.stdout)


def score_tendency(tendency, pct):
    if pct is None:
        return None
    t = str(tendency or '')
    if '承压' in t or '看淡' in t or '走弱' in t:
        want = -1
    elif '偏强' in t or '走强' in t or '看多' in t or '上行' in t:
        want = 1
    else:
        want = 0                     # 中性 / 震荡：方向判断本身不占优
    if abs(pct) < 0.2:
        return 0.5
    if want == 0:
        return 1 if abs(pct) < 0.5 else 0
    return 1 if (pct > 0) == (want > 0) else 0


def score_band(lo, hi, point):
    if lo is None or hi is None or point is None:
        return None
    if lo <= point <= hi:
        return 1
    dev = min(abs(point - lo), abs(point - hi)) / max(lo, 1) * 100.0
    return 0.5 if dev <= 1.0 else 0


def score_vol(vol_expect, up_rate):
    if not vol_expect or up_rate is None:
        return None
    v = str(vol_expect)
    if '放量' in v or '放大' in v:
        return 1 if up_rate > 0.5 else 0
    if '缩量' in v or '萎缩' in v:
        return 1 if up_rate < 0.5 else 0
    return 1 if abs(up_rate - 0.5) <= 0.15 else 0    # 平量


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--view', action='store_true')
    a = ap.parse_args()

    if a.view:
        op = tc.load_log().get('open') or {}
        print(json.dumps({'open': op}, ensure_ascii=False, indent=1) if op else '台账 open 段为空（尚无前瞻打分）')
        return 0

    st = load_state()
    if not st:
        # R100z72：台账状态缺失是「08:30 尚未写入 openOutlook.pos」的常态（非错误），
        # 本步本就是 16:00 里的非关键打分环节——返回 0（跳过）而非 1，否则会被自动化
        # 当成「步骤失败」中断部署。
        print('[SKIP] 前瞻台账状态缺失（08:30 尚未写入 openOutlook.pos），本步跳过（不计入失败）')
        return 0
    # pos 读取口径（R100z53 修订，勿再改）：
    #   NODE_SRC 第 42 行输出 `pos: oo.pos || null` —— top-level 的 `pos` 键
    #   已经是 openOutlook.pos 的映射，且经 node 提取后已是 dict。
    #   早先误改成 `st.get('openOutlook')['pos']` 是错的：NODE_SRC 从不输出
    #   openOutlook 这个键 → 恒 {} → 本脚本永远 [SKIP]，R100z13 闭环一天没跑通
    #   （calibration_state.json 的 open 台账 0 条）。此处必须是 st.get('pos')。
    pos = st.get('pos') or {}
    if not pos:
        print('[SKIP] openOutlook.pos 不存在——08:30 未写结构化前瞻假设，无从打分'
              '（口径：openOutlook.pos = {tendency, bandLo, bandHi, volExpect}）。'
              '本行不入台账，不算漏打分。')
        print(json.dumps({'ok': False, 'reason': 'no openOutlook.pos'}, ensure_ascii=False))
        return 0

    ids = st.get('indices') or []
    # R100z51：原写法 `next((x for x in ids if ...endswith('000001')) or ids[0] or {}, {})`
    # 是错的——生成器对象恒真，`or ids[0]` / `or {}` 永不执行；而 next() 没给默认值，
    # ids 非空却无 000001（上证，08:30 漏写/口径变更）时抛 StopIteration 直接崩。
    # 改成显式遍历：优先上证指数，其次首条，都没有才是 {}。
    idx = {}
    for x in ids:
        if isinstance(x, dict) and str(x.get('code', '')).endswith('000001'):
            idx = x
            break
    if not idx and ids:
        idx = ids[0]
    point, pct = idx.get('point'), idx.get('changePct')
    br = st.get('breadth') or {}
    up, down = br.get('up'), br.get('down')
    up_rate = round(up / (up + down), 4) if isinstance(up, (int, float)) and isinstance(down, (int, float)) and (up + down) else None

    t_hit = score_tendency(pos.get('tendency'), pct)
    b_hit = score_band(pos.get('bandLo'), pos.get('bandHi'), point)
    v_hit = score_vol(pos.get('volExpect'), up_rate)

    num, den = 0.0, 0.0
    if t_hit is not None:
        num += W_TENDENCY * t_hit
        den += W_TENDENCY
    if b_hit is not None:
        num += W_BAND * b_hit
        den += W_BAND
    if v_hit is not None:
        num += W_VOL * v_hit
        den += W_VOL
    score = round(num / den, 3) if den else None
    if score is None:
        verdict = '未打分（无实际值）'
    elif score >= 0.9:
        verdict = '命中'
    elif score >= 0.5:
        verdict = '部分兑现'
    else:
        verdict = '未命中'

    day = st.get('tradeDate') or st.get('openDate') or date.today().isoformat()
    rec = {'date': day, 'openDate': st.get('openDate'),
           'tendency': pos.get('tendency'), 'bandLo': pos.get('bandLo'), 'bandHi': pos.get('bandHi'),
           'volExpect': pos.get('volExpect'),
           'actual': {'point': point, 'changePct': pct,
                      'upRate': up_rate, 'volumeText': br.get('volumeText')},
           'tendencyHit': t_hit, 'bandHit': b_hit, 'volHit': v_hit,
           'score': score, 'verdict': verdict,
           'note': '权重：倾向 0.5 / 点位 0.3 / 量能 0.2；量能为涨跌家数占比代理，口径固定不可调'}

    log = tc.load_log()
    log.setdefault('open', {})[day] = rec
    tc.save_log(log)

    if a.json:
        print(json.dumps({'ok': True, 'writtenToLedger': True, 'record': rec}, ensure_ascii=False))
    else:
        print(f"[OK] 开盘前瞻兑现打分 {day}：{verdict}（倾向 {t_hit} / 点位 {b_hit} / 量能 {v_hit}，综合 {score}）")
        print('      实际：上证 %s 点 %s%%；涨跌家数占比 %s；%s' %
              (point, pct, up_rate, br.get('volumeText') or ''))
        print('      已写入台账 open[%s]；请由 16:00 把此结果写回 data.js 的 openOutlook.verification' % day)
    return 0


if __name__ == '__main__':
    sys.exit(main())
