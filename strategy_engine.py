# -*- coding: utf-8 -*-
"""strategy_engine.py — 盘前仓位带 + 组合读数（R100z117 建，R100z323 收缩职责）。

职责只剩两件事，都写在 data.js 顶层 `strategy`：
  1) `positionBand`：读 cal_factor.py 的日历/流动性窗口建议仓位带，再用 trendState 的
     趋势上限压一次（取更保守者）。前端唯一消费点是趋势条的「仓位上限」tooltip。
  2) `feedback`：读 portfolio_curve.json 相对**行业基准**的超额，落盘
     strategy_feedback.json，给复盘一个确定性读数（G49 校验两处符号同源）。

为什么删掉关注清单与风险提示（2026-11-09 用户拍板「下线 watchlist 分支」）：
  那两段聚出来的 10 条清单**全站零消费方**——前端不渲染、push_notify 不读，只有
  positionBand 被真正使用；内容又和「AI 预测板块 + 五节要闻 impacts」高度重复，
  用户原话「同一信息在多处给，看着都累都乱」。所以整段删除，**不留兼容分支**。
  高确信条数的后验闸门不受影响：硬约束在 validate_stocks.py 里按 maxHighConf
  校验 aiPrediction.sectors 的高确信条数，与本节点无关。

设计纪律：零网络除 cal_factor 子进程；失败兜底写空节点并 exit 0，绝不阻断流水线。
"""
import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import write_data_js, write_json_atomic  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CURVE_FILE = os.path.join(HERE, 'portfolio_curve.json')
FEEDBACK_FILE = os.path.join(HERE, 'strategy_feedback.json')


def _load(path):
    s = open(path, encoding='utf-8').read()
    m = re.search(r'window\.DASHBOARD_DATA\s*=\s*(\{.*\});?\s*$', s, re.S)
    if not m:
        raise RuntimeError('data.js 解析失败')
    return json.loads(m.group(1))


def _dump(path, D):
    # R100z179b：原来直接 open(path,'w')，而本脚本在 deploy.sh 链条中间跑 ——
    # 被 kill 即 0 字节 data.js。改走 common 的唯一写盘入口。
    write_data_js(path, D, label='[strategy_engine]')


def _cal_factor():
    """读 cal_factor.py --json 拿建议仓位带；失败返回默认 None（不阻断）。"""
    try:
        py = ('/Users/loccco/.workbuddy/binaries/python/versions/3.13.12/bin/python3'
              if os.path.exists('/Users/loccco/.workbuddy/binaries/python/versions/3.13.12/bin/python3')
              else 'python3')
        pr = subprocess.run([py, os.path.join(HERE, 'cal_factor.py'), '--json'],
                            capture_output=True, text=True, timeout=30)
        if pr.returncode == 0 and pr.stdout.strip():
            return json.loads(pr.stdout)
    except Exception:
        pass
    return None


def _curve_excess():
    """读组合回测超额——**必须用权威口径 excessSectorPct（行业基准）**。

    2026-10-27 实测的 bug：这里原先读 `excessPct`（指数基准，+0.19），
    而 AI 预测卡里的「组合超额」读的是 `excessSectorPct`（行业基准，−0.91）——
    同一张卡上出现「+0.19 跑赢」和「−0.91 跑输」两个符号相反的结论。

    根因是量纲错配：组合本身是一篮子**行业板块**的标的，拿综合指数当尺子，
    普涨日会虚高（上证 −4.42% vs 行业基准 −7.71%，指数跌得少，组合白捡 3 个点）。
    portfolio_curve.json 的 note 里已写明「★权威超额=excessSectorPct」，
    是这里没跟上。退回 excessPct 只在行业基准缺失时兜底，且不让它进入
    「跑输→收紧高确信」的判定（样本口径不一致时宁可不收紧，也不能因为尺子换了
    就多关掉几条关注项）。
    """
    try:
        st = (json.load(open(CURVE_FILE, encoding='utf-8')).get('stat') or {})
        for k in ('excessSectorPct', 'excessComparablePct', 'excessPct'):
            if st.get(k) is not None:
                return st.get(k)
        return None
    except Exception:
        return None


def _band_text(pct):
    """仓位百分比 → 「6-7 成」这种中文说法（页面只出中文，禁写 0.6-0.7）。"""
    def _c(x):
        v = x / 10.0
        if abs(v - round(v)) < 0.05:
            return str(int(round(v)))
        return ('%.1f' % v).rstrip('0').rstrip('.')
    return '%s 成' % _c(pct)


def main():
    path = os.path.join(HERE, 'data.js')
    if not os.path.exists(path):
        print('[strategy_engine] data.js 不存在，跳过')
        return 0
    try:
        D = _load(path)
        today = datetime.date.today()
        cal = _cal_factor()
        excess = _curve_excess()

        # 仓位纪律（来自日历/流动性窗口因子；缺失时用中性默认）
        pos = {'lo': None, 'hi': None, 'text': '以日历窗口因子为准（非交易日/缺失）', 'source': '默认'}
        if cal and cal.get('posText'):
            pos = {'lo': cal.get('posLo'), 'hi': cal.get('posHi'),
                   'text': cal.get('posText'), 'source': 'cal_factor'}

        # ---- 趋势闸门（2026-10-27）：日历因子只回答「今天什么日子」，回答不了
        # 「大盘站在 MA20 哪一侧」。实测教训：2026-10-27 五个指数全部空头排列、
        # 上证在 MA20 下方 2.52%、60 日分位 12%，而纯日历口径仍然给出「6-7 成」。
        # 所以日历给的是**基础带**，趋势给的是**上限**，取两者更保守的那个。
        # 趋势数据缺失时（trendState 未生成）不降级、不猜，保持原样并在 pos 里标出来。
        ts = D.get('trendState') or {}
        if ts.get('marketCapPct') is not None and pos.get('hi') is not None:
            cap = float(ts['marketCapPct']) / 100.0
            if pos['hi'] > cap:
                pos = dict(pos, lo=min(pos.get('lo') or 0, cap), hi=cap,
                           text='%s（趋势档「%s」压到 %d%%：%s）'
                                % (_band_text(cap * 100), ts.get('marketLevel'), cap * 100,
                                   (ts.get('text') or '')[:60]),
                           source='cal_factor+trend', trendCapPct=ts['marketCapPct'],
                           trendLevel=ts.get('marketLevel'))
            else:
                pos = dict(pos, trendCapPct=ts['marketCapPct'],
                           trendLevel=ts.get('marketLevel'),
                           trendNote='趋势上限 %d%% 高于日历带，不生效'
                                     % ts['marketCapPct'])
        lock_in = (cal or {}).get('lockIn')
        out_weight = (cal or {}).get('outWeight')

        underperform = (excess is not None and excess < 0)

        # 组合读数（跑赢/跑输行业基准）——G49 校验它与 portfolio_curve 同源
        feedback = {
            'underperform': underperform,
            'excessPct': excess,
            'note': ('组合净值%s行业基准（超额 %s%%）'
                     % ('跑输' if underperform else '跑赢',
                        ('%+.2f' % excess) if excess is not None else '—'))
            if excess is not None else '回测样本不足，超额读数留空（不补 0）',
        }
        # 把反馈落盘（复盘卡的数据来源，亦是「跑输→写回」的确定性产物）
        try:
            write_json_atomic(FEEDBACK_FILE, feedback, label='[strategy_engine.feedback]')
        except Exception as e:
            # R100z273：反馈落盘失败原来静默 pass。写不进去就等于当天没有反馈，
            # 而页面上看不出缺的是哪一天（24.110：空着＝有结论没写）。
            print(f'[strategy_engine] 反馈落盘失败（复盘数据将缺本日一条）：{str(e)[:120]}',
                  flush=True)

        D['strategy'] = {
            'generatedAt': today.isoformat(),
            'positionBand': pos,
            'lockIn': lock_in,
            'outWeight': out_weight,
            'feedback': feedback,
            'note': '仓位带取自日历窗口因子，再由趋势档压上限（取更保守者）；'
                    'feedback 为组合净值相对行业基准的读数。',
        }
        _dump(path, D)
        print('[strategy_engine] 仓位带 %s；组合读数 %s'
              % (pos.get('text'), feedback['note']))
        return 0
    except Exception as e:  # noqa: BLE001
        try:
            D = _load(path)
            D['strategy'] = {'generatedAt': datetime.date.today().isoformat(),
                             'note': '生成异常，已写空', 'positionBand': {}}
            _dump(path, D)
        except Exception:
            pass
        print(f'[strategy_engine] 异常已兜底（写空）：{e!r}')
        return 0


def _help_guard():
    """R100z296：`--help` / `-h` 只打用法，绝不执行主流程。

    本脚本会写 data.js 顶层 strategy（仓位带 + 组合读数）。手写 sys.argv
    解析时 `--help` 会被当普通参数忽略、直奔默认流程（validate_stocks 同类坑）。
    """
    import sys as _sys
    if '--help' in _sys.argv or '-h' in _sys.argv:
        print(__doc__ or '(本脚本无说明)')
        raise SystemExit(0)


if __name__ == '__main__':
    _help_guard()
    sys.exit(main())
