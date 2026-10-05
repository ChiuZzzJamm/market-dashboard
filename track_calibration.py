#!/usr/bin/env python3
"""置信度后验校准台账（R100z11）——给「高确信」标签一个可核对的命中率账本。

背景（2026-09-30 实测）：当日 6 板块验证 hit 2 / miss 3 / 部分兑现 1（胜率 33%），
而 miss 的两条「光通信·光模块」「半导体设备材料」恰好都标了「高确信」走强。
标签与命中率背离时，系统没有任何机制去惩罚这个标签——本脚本就是把账本做出来。

用法：
    python3 track_calibration.py                 # 并入本轮 data.js 预测结果 + 打印分层命中率与门槛
    python3 track_calibration.py --view          # 只查看台账与统计，不并入
    python3 track_calibration.py --json          # 机器可读输出（自动化读取门槛用）
    python3 track_calibration.py --backfill 25   # 从 git 历史 data.js 回填样本（幂等）

数据（★文件名不带点：GitHub Pages 不发布 dotfile，前端要靠 fetch('calibration_log.json') 画命中横条）：
    台账 calibration_log.json     { log: { "<预测日期>": [ {date,sector,predicted,actualPct,excessPct,
                                              conf,hit,hitAbs,avgPct,benchPct,pos} ] },
                                    open: { "<预测日期>": {tendencyHit,bandHit,volHit,score,pos} } }
    门槛 calibration_state.json   { generatedAt, window, stat:{层:{n,hit,rate,ci,sampleEnough}}, gates:{...} }
    净值 portfolio_curve.json     { series:[{date,pf,bench,dd}], stat:{totRet,benchRet,maxDd,...} }

命中口径（R100z15 缺口①——2026-10-01 体检定稿）：
    基准 benchPct = 上证 / 深成 / 创业板 / 中证1000 四指数当日涨跌幅中位数（由 market_bench.py 产出，
    读 market_bench.json；该文件缺失时超额留空、不得补 0）。
    超额 excessPct = actualPct − benchPct。
    「hit」= 超额口径：预测走强/偏强 → 超额 > 0 命中；预测承压 → 超额 < 0 命中；
             |超额| < 0.2% 记 0.5（部分兑现）；方向不匹配记 0。
    「hitAbs」= 旧的方向口径（实际涨幅 ±），原样留在台账里做**对照**。
    为什么要两套：9-30 全市场 2824 家下跌时，某板块 −1.5% 其实是跑赢中位数的负 alpha，
    旧口径会把它判成「预测失败」。跑本脚本时会打印 hit 与 hitAbs 的差值——
    **差值越大说明旧口径越在自欺**，这个数字本身就是产品价值。

组合层（R100z15 缺口④）：每个预测日入一条快照（该日 8 只标的等权 avgPct），
    累乘成组合净值 pf 与基准净值 bench，同时给出最大回撤——命中 60% 但亏钱、命中 50% 但赚钱，
    光看命中率分不出来。
"""
import argparse
import contextlib
import io
import json
import os
import sys
from datetime import date

import stop_tracker  # R100z59：止损台账接线（见 main() 内「R100z59 止损台账」段）

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(ROOT, 'data.js')
# ★R100z13：一律不带点的文件名——GitHub Pages 不发布 dotfile，前端要 fetch('calibration_log.json')
LOG_FILE = os.path.join(ROOT, 'calibration_log.json')
STATE_FILE = os.path.join(ROOT, 'calibration_state.json')
BENCH_FILE = os.path.join(ROOT, 'market_bench.json')
CURVE_FILE = os.path.join(ROOT, 'portfolio_curve.json')

# 门槛阈值（命中率 < 阈值即触发降档；见 references/predict-calibration.md）
HARD_GATE = 0.25    # 低于此值：当日禁止使用「高确信」
SOFT_GATE = 0.40    # 低于此值：当日最多 1 条「高确信」
MIN_SAMPLE = 5      # 通用层：样本不足时不降档（避免小样本噪声误杀）
MIN_SAMPLE_HIGH = 8 # R100z13：「高确信」层专用硬门槛——高确信是最高承诺，
                    # 只有 ≥8 条样本才允许据此降档（3~5 条就下「禁用高确信」会天天跳、且容易过冲）
WINDOW = 20         # 统计窗口（交易日）

NODE_SRC = r"""
const fs = require('fs');
const p = process.env.DATA_PATH;
let s = fs.readFileSync(p, 'utf8');
let m = s.match(/window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!m) { console.error('data.js parse failed'); process.exit(1); }
let d; try { d = eval('(' + m[1] + ')'); } catch (e) { console.error('eval failed', e.message); process.exit(1); }
const ap = d.aiPrediction || {};
const oo = d.openOutlook || {};
process.stdout.write(JSON.stringify({
  date: ap.date || null,
  confBySector: (ap.sectors || []).map((x) => ({ sector: x.sector, conf: x.confidence || null })),
  verification: ap.verification || null,
  // R100z15 缺口①/④：标的层平均涨幅（8 只等权）→ 组合净值曲线；stockCheck.fund 保留
  stockCheck: (ap.verification && ap.verification.stockCheck) || null,
  // R100z13：建议仓位（8:30/16:00 写下的可回测数字）+ 今日开盘前瞻结构化假设（供前瞻兑现打分）
  // （R100z51 键名修正）：建议仓位实际写在 **openOutlook.pos**（score_openoutlook.py:42 读的也是这个），
  // data.js 顶层从来没有 `posAdvice` 键 → 原写法恒为 null，台账每条 pos / 组合曲线的仓位归因一直是空的。
  pos: (oo && oo.pos) || null,
  openPos: oo.pos || null,
  openVerification: oo.verification || null
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


# ---------------- R100z52：方向判定词表（替掉「只认『承压』两个字」的旧口径） ----------------
# 旧实现 `up = str(predicted).find('承压') == -1`：AI 写「偏弱 / 回撤 / 震荡 / 下行 /
# 降温」这些同样明确看空的词时 up=True，一律算成看多 → 命中率被系统性抬高。
# 实测口径：verification.details[].predicted 是极短文本（如「走强」/「承压」/「震荡偏弱」），
# 词表按「最长/最特殊优先」匹配，命中多词时判中性，宁可漏判也不硬凑方向。
DIR_FLAT = ('震荡', '横盘', '胶着', '犹豫', '整理', '分歧', '观望', '僵持', '拉锯', '中性')
DIR_UP = ('走强', '上行', '上涨', '偏强', '看多', '走高', '提振', '回暖', '反弹',
          '突破', '扩张', '接力', '活跃', '走俏', '领涨')
DIR_DOWN = ('承压', '偏弱', '下行', '下跌', '回落', '回调', '回撤', '走弱', '降温',
            '退潮', '减仓', '抛压', '避险', '收窄', '领跌')


def classify_dir(predicted):
    """把 AI 的板块方向文本判定为 'up' / 'down' / 'flat'。

    flat（震荡/观望/多空各半）**不计入命中统计**（返回 None），
    因为「没方向」被算成「看多」正是上一版把命中率抬高的主因。
    """
    s = str(predicted or '')
    for k in DIR_FLAT:
        if k in s:
            return 'flat'
    hu = any(k in s for k in DIR_UP)
    hd = any(k in s for k in DIR_DOWN)
    if hu and hd:
        return 'flat'          # 又看多又看空 = 没方向
    if hu:
        return 'up'
    if hd:
        return 'down'
    return 'flat'


def score(predicted, actual_pct, excess_pct=None):
    """R100z15：命中判定改**超额口径**。

    excess_pct 为 None（基准缺失）时退回旧的绝对方向口径，并把结果同时标成 hitAbs，
    台账里两条都留着，宁可多存一个字段也不要伪造结论。
    返回 1 / 0.5 / 0；方向未明确（flat）时返回 None（不计入命中统计）。
    """
    up = classify_dir(predicted) == 'up'
    if classify_dir(predicted) == 'flat':
        return None
    if excess_pct is not None:
        if abs(excess_pct) < 0.2:
            return 0.5
        return (1 if up and excess_pct > 0 else (1 if (not up) and excess_pct < 0 else 0))
    if actual_pct is None:
        return None
    if abs(actual_pct) < 0.2:
        return 0.5
    return (1 if up and actual_pct > 0 else (1 if (not up) and actual_pct < 0 else 0))


def score_abs(predicted, actual_pct):
    """旧的「只判方向」口径，保留进台账作对照（R100z15 缺口①的证据）。

    R100z52：这里同样换用 classify_dir —— 对照的价值是「超额 vs 绝对」两种统计口径，
    不是「有 bug vs 无 bug」。方向判定本身的 bug 已在 score() 里修掉。
    """
    if actual_pct is None:
        return None
    if classify_dir(predicted) == 'flat':
        return None
    up = classify_dir(predicted) == 'up'
    if abs(actual_pct) < 0.2:
        return 0.5
    return (1 if up and actual_pct > 0 else (1 if (not up) and actual_pct < 0 else 0))


def load_bench(on_date=None):
    """读 market_bench.py 产出的市场基准。

    ★口径铁律（2026-10-01 踩到）：market_bench.json 里只有**当天**一个基准值。
      把 10-01 的基准去算 09-30 的超额，等于拿昨天的尺子量前天的身高——历史超额会全是假的
      （第一版就犯了这个错，跑出来「旧口径 42% vs 超额 58%」这个数根本不可信）。
      所以：bench 的 date 必须与待入账的预测日一致，否则一律返回 None（超额留空，绝不补 0）。
    """
    if os.path.exists(BENCH_FILE):
        try:
            b = json.load(open(BENCH_FILE, encoding='utf-8'))
            if b.get('benchPct') is not None and (on_date is None or b.get('date') == on_date):
                return b
        except Exception:
            pass
    return None


def avg_of(sc_list, sector, bench):
    """取该板块 stockCheck 里 avgPct（8 只等权），算超额。取不到返回 (None, None)。"""
    if not sc_list:
        return None, None
    for sc in sc_list:
        if (sc.get('sector') or '').strip() == sector:
            a = sc.get('avgPct')
            a = float(a) if isinstance(a, (int, float)) else None
            e = round(a - bench, 3) if (a is not None and bench is not None) else None
            return a, e
    return None, None


def load_log():
    if os.path.exists(LOG_FILE):
        try:
            return json.load(open(LOG_FILE, encoding='utf-8'))
        except Exception:
            pass
    return {'log': {}, 'open': {}}


def save_log(log):
    """R100z13：供 score_openoutlook.py 直接 import 本模块后落盘（避免重复实现）。"""
    log.setdefault('log', {})
    log.setdefault('open', {})
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=1)


def build_items(date, dets, conf_map, bench, sc_list):
    """把 verification.details 落成台账条目（R100z15：同时落超额口径与旧方向口径）。"""
    items = []
    for d in dets:
        nm = d.get('sector')
        act = d.get('actualPct')
        act = float(act) if isinstance(act, (int, float)) else None
        ex = round(act - bench, 3) if (act is not None and bench is not None) else None
        avg, ex_avg = avg_of(sc_list, nm, bench)
        items.append({'date': date, 'sector': nm,
                      'predicted': d.get('predicted'),
                      'dirClass': classify_dir(d.get('predicted')),
                      'actualPct': act,
                      'benchPct': bench, 'excessPct': ex,
                      'result': d.get('result'),
                      'conf': conf_map.get(nm, 'unknown'),
                      'avgPct': avg, 'excessAvg': ex_avg,
                      'hit': score(d.get('predicted'), act, ex),
                      'hitAbs': score_abs(d.get('predicted'), act),
                      'pos': None})
    return items


def build_curve(log):
    """R100z15 缺口④：组合等权净值 vs 基准净值 + 回撤（命中率再高，净值不涨就是白忙）。"""
    series, pf, bn, peak = [], 1.0, 1.0, 1.0
    for dt in sorted(log.get('log', {}).keys()):
        items = log['log'][dt] or []
        avg = next((x.get('avgPct') for x in items if x.get('avgPct') is not None), None)
        if avg is None:
            avg = next((x.get('actualPct') for x in items if x.get('actualPct') is not None), None)
        if avg is None:
            continue
        b = next((x.get('benchPct') for x in items if x.get('benchPct') is not None), None)
        pf *= (1 + avg / 100.0)
        if b is not None:
            bn *= (1 + b / 100.0)
        peak = max(peak, pf)
        dd = round((pf - peak) / peak * 100.0, 2)
        series.append({'date': dt, 'avgPct': round(avg, 3), 'pf': round(pf, 4),
                       'bench': round(bn, 4), 'dd': dd})
    if not series:
        return None
    max_dd = min(series, key=lambda x: x['dd'])
    tot = (series[-1]['pf'] - 1) * 100
    bn_tot = (series[-1]['bench'] - 1) * 100
    stat = {'days': len(series), 'totRetPct': round(tot, 2), 'benchRetPct': round(bn_tot, 2),
            'excessPct': round(tot - bn_tot, 2),
            'maxDdPct': max_dd['dd'], 'maxDdDate': max_dd['date'],
            'note': '组合=各预测日 8 只标的等权平均涨跌幅累乘；基准=同日市场基准累乘；回撤按组合净值峰谷算'}
    return {'ok': True, 'series': series, 'stat': stat}


def min_sample_for(key):
    """「高确信」层样本门槛更高：最高承诺不能拿 3 条样本下结论（R100z13）。"""
    return MIN_SAMPLE_HIGH if key == '高' else MIN_SAMPLE


def ci_of(p, n):
    """命中率 ±1σ 区间（二项分布正态近似）；n 太小则区间失去意义，直接返回 full=False。"""
    if not n:
        return None
    s = (p * (1 - p) / n) ** 0.5
    lo, hi = max(0.0, p - s), min(1.0, p + s)
    return {'lo': round(lo, 3), 'hi': round(hi, 3), 'sigma': round(s, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--view', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--backfill', type=int, default=0,
                    help='从 git 历史 data.js 回填最近 N 个版本的预测结果，快速攒够分层样本（默认不回填）')
    ap.add_argument('--recalc', action='store_true',
                    help='用当前口径重算台账全量 hit / hitAbs（R100z52 方向词表修版专用，幂等）')
    a = ap.parse_args()

    log = load_log()
    bench = (load_bench() or {}).get('benchPct')

    # --recalc：R100z52 方向词表修版后，历史上用「只认『承压』」坏口径算出的命中率
    # 全在台账里，不重算就永远带着偏高值往下滚。台账本身存了 predicted / actualPct /
    # excessPct，重算是纯函数回放、不依赖外部数据，幂等可反复跑。
    if a.recalc:
        fixed = 0
        for _dt, items in log.get('log', {}).items():
            for it in items:
                act = it.get('actualPct')
                act = float(act) if isinstance(act, (int, float)) else None
                ex = it.get('excessPct')
                ex = float(ex) if isinstance(ex, (int, float)) else None
                before = (it.get('hit'), it.get('hitAbs'))
                it['hit'] = score(it.get('predicted'), act, ex)
                it['hitAbs'] = score_abs(it.get('predicted'), act)
                it['dirClass'] = classify_dir(it.get('predicted'))
                if (it.get('hit'), it.get('hitAbs')) != before:
                    fixed += 1
        save_log(log)
        print(f"[OK] --recalc 完成：{fixed} 条命中分被修正，其余本就一致；"
              f"台账共 {sum(len(v) for v in log['log'].values())} 条已按新词表重算")
        return 0

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
                log['log'][pd['date']] = build_items(
                    pd['date'], pd['verification']['details'], conf_map, bench, pd.get('stockCheck') or [])
                # R100z13：开盘前瞻兑现打分一并入账（16:00 已打分的才并入，没打分的跳过）
                ov = pd.get('openVerification')
                if ov:
                    log.setdefault('open', {})[pd['date']] = ov
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
            bench = (load_bench(pred['date']) or {}).get('benchPct')
            items = build_items(pred['date'], dets, conf_map, bench,
                                pred.get('stockCheck') or [])
            for it in items:
                it['pos'] = pred.get('pos')
            log['log'][pred['date']] = items
            save_log(log)
            if not a.json:
                print(f"[OK] 已并入台账：{pred['date']}（{len(items)} 条）"
                      f"{'' if bench is not None else '（市场基准缺失，本轮只落旧方向口径）'}")

    # ---------------- R100z59 止损台账：接上「只算不跟踪」的补丁 ----------------
    # 7 项硬约束里的止损纪律，此前只有 stop_tracker.py 在算、却从没被调用过（死脚本），
    # stop_ledger.json 永远是空的——等于止损位算出来没人回扫，纸面纪律。
    # 这里只在**真实跑**（无 --view、且非 --json 读门槛模式）时接一次：
    #   登记当日各模块止损位（同码不覆盖，幂等）+ 回扫所有已登记条的触及情况。
    # --json 是 08:30 / 周末任务读门槛用的**只读**模式，不接：避免多一轮 K 线网络抓取、
    # 也避免任何写盘把「只读校验」变成副作用。stop_tracker.main() 内部会 argparse 读
    # sys.argv，调用前必须把 argv 换成它自己的（否则会拿走本脚本的参数直接 SystemExit）。
    if (not a.view) and (not a.json) and pred and pred.get('date'):
        _argv = sys.argv
        try:
            _buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(_buf), contextlib.redirect_stderr(_buf):
                    stop_tracker.main()
            except Exception as _e:
                # R100z72：止损台账是校准主流程的旁路副作用，即便它异常也绝不应中断校准。
                print(f"[WARN] 止损台账登记异常（不影响校准主流程）：{_e}", file=sys.stderr)
            if not a.json:
                for _ln in _buf.getvalue().splitlines():
                    if _ln.startswith(('[OK]', '[info]', '[SKIP]')):
                        print('  ' + _ln)
        finally:
            sys.argv = _argv

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
        rate = round(got / n, 3) if n else None
        need = min_sample_for(k)
        stat[k] = {'n': n, 'hit': got, 'rate': rate,
                   'minSample': need, 'sampleEnough': n >= need,
                   'ci': ci_of(rate, n) if rate is not None else None}

    def gate_for(key):
        s = stat.get(key)
        need = min_sample_for(key)
        if not s or not s.get('rate'):
            return {'maxHighConf': 2, 'maxConf': '高确信',
                    'reason': f'「{key}」层样本不足（{s["n"] if s else 0}/{need}），暂不降档'}
        ci = s.get('ci') or {}
        band = f'±1σ [{ci.get("lo", 0):.0%}, {ci.get("hi", 0):.0%}]'
        if not s['sampleEnough']:
            # R100z13：样本没到该层门槛，即使命中率看着低也不降档——小样本噪声会天天跳门槛
            return {'maxHighConf': 2, 'maxConf': '高确信',
                    'reason': f'「{key}」层命中率 {s["rate"]:.0%}（{band}）但样本 {s["n"]}<{need}，'
                              '未达降档门槛，暂不降档（继续攒样本后再评）'}
        if s['rate'] < HARD_GATE:
            return {'maxHighConf': 0, 'maxConf': '中等确信',
                    'reason': f'「{key}」层近 {len(dates)} 日命中率 {s["rate"]:.0%} {band} < {HARD_GATE:.0%}（样本≥{need}），当日禁用该层'}
        if s['rate'] < SOFT_GATE:
            return {'maxHighConf': 1, 'maxConf': '中等确信',
                    'reason': f'「{key}」层近 {len(dates)} 日命中率 {s["rate"]:.0%} {band} < {SOFT_GATE:.0%}（样本≥{need}），当日最多 1 条该层'}
        return {'maxHighConf': 2, 'maxConf': '高确信',
                'reason': f'「{key}」层近 {len(dates)} 日命中率 {s["rate"]:.0%} {band}（≥{SOFT_GATE:.0%}，样本≥{need}），可正常使用'}

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
        ci = s.get('ci') or {}
        print(f"  {k}确信：{s['n']} 条 / 命中分 {s['hit']} / 命中率 {s['rate']:.0%}"
              f"（±1σ {ci.get('lo', 0):.0%}~{ci.get('hi', 0):.0%}）"
              f"{'' if s['sampleEnough'] else '（样本 %d<%d，暂不降档）' % (s['n'], s['minSample'])}")
        print('      门槛：' + gates[k]['reason'])
    op = log.get('open') or {}
    if op:
        print(f"  开盘前瞻兑现：{len(op)} 个交易日已打分（{'、'.join(sorted(op)[-5:])}）")

    # 缺口①的证据：旧方向口径 vs 新超额口径，差多大就说明「只判方向」自欺了多少
    allx = [x for dt in dates for x in log['log'][dt]]
    hs = [x['hit'] for x in allx if x.get('hit') is not None]
    ha = [x['hitAbs'] for x in allx if x.get('hitAbs') is not None]
    if hs and ha:
        rw, ra = sum(hs) / len(hs), sum(ha) / len(ha)
        print('  命中口径对照：超额口径 %.0f%%（%d 条） vs 旧方向口径 %.0f%%（%d 条），差 %+.0f 个百分点'
              % (rw * 100, len(hs), ra * 100, len(ha), (rw - ra) * 100))
        if ra - rw >= 0.15:
            print('      ⚠️ 旧口径明显虚高：全市场普跌时「某板块跌幅 lesser 仍算命中」，'
                  '这就是为什么必须扣掉基准再判（R100z15）')

    # 缺口④：组合净值曲线（8 只等权 vs 基准）
    curve = build_curve(log)
    if curve:
        with open(CURVE_FILE, 'w', encoding='utf-8') as f:
            json.dump(curve, f, ensure_ascii=False, indent=1)
        s = curve['stat']
        print('  组合净值：%d 个交易日，累计 %+.2f%%（基准 %+.2f%%，超额 %+.2f%%），'
              '最大回撤 %.2f%%（%s）' %
              (s['days'], s['totRetPct'], s['benchRetPct'], s['excessPct'],
               s['maxDdPct'], s['maxDdDate']))
        print('      已写入 ' + os.path.basename(CURVE_FILE) + '（前端画净值与回撤）')

    print('[OK] 门槛已写入 ' + os.path.basename(STATE_FILE))
    return 0


if __name__ == '__main__':
    sys.exit(main())
