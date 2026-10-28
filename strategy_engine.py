# -*- coding: utf-8 -*-
"""strategy_engine.py — 盘前策略闭环（R100z117，item 1）。

定位（fin-strategy-engine 方法论的「确定性聚合」层）：
  不重新抓数、不做 LLM 推演，只把看板已产出的信号做**可证伪的聚合**，产出
  盘前关注清单 + 仓位纪律，写回 data.js 顶层 strategy，供前端渲染卡片。

闭环如何实现（不碰自动化 DB / prompt，规避 cwds 序列化风险）：
  1) 读取 track_calibration 产出的 calibration_state.json「后验闸门」——
     高确信层命中率 < 阈值时 maxHighConf=0/1，引擎据此**收紧高确信条数**，
     等于「回测跑输 → 次日自动降档」的闭环，由数据驱动而非由 prompt 文本驱动。
  2) 读取 portfolio_curve.json 的超额收益，写入 feedback，给盘前一张「近期含金量」读数。
  3) 读取 cal_factor.py 的 posText（日历/流动性窗口建议仓位带），作为仓位纪律直接来源。

信号来源（data.js 已产出）：
  - policySignals（政策解码，tier S/A/B/C）→ 政策驱动项
  - aiPrediction.sectors（conf 高/中/低）→ AI 高确信项
  - duanban.confirmed（利好/中性/利空）→ 形态确认项
聚合规则（策略复杂度最优 + 可证伪）：
  - 每只带 source + confidence（高确信/中等确信/推测）+ 一句逻辑 + 催化剂；
  - 高确信总数受 calibration 闸门 maxHighConf 限制，超出部分降级为中等确信；
  - 利空（bear）只进「风险提示」区，不进关注清单。

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
CAL_STATE = os.path.join(HERE, 'calibration_state.json')
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


def _gates():
    try:
        st = json.load(open(CAL_STATE, encoding='utf-8'))
        return st.get('gates') or {}
    except Exception:
        return {}


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


def _clip(t, limit):
    """截到 limit 内并落到句子边界（避免出现「若今日成交额不能回到 1.45 万亿上方，」这种半句）。"""
    t = str(t or "").strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    idx = max(cut.rfind("。"), cut.rfind("；"), cut.rfind("！"), cut.rfind("？"))
    if idx >= 40:
        return cut[:idx + 1]
    return cut.rstrip("，、,;；:： ") + "…"


def _a_share_clause(text, limit=120):
    """从「美股隔夜驱动 + A股映射」两段式 logic 里取 **A 股那半段**（R100z157，BUG 修复）。

    旧实现 `(s.get('reason') or s.get('logic') or '')[:80]`：AI 预测 sector 根本没有 reason 键，
    于是恒定走 logic[:80]——而 logic 是「隔夜费半收涨 1.73%…西部数据 +6.09%」＋「对应 A 股…」
    两段式，80 字恰好把**A 股映射那段结论截掉**，用户看到的永远是美股隔夜行情。
    这里按显式标记切段；没有标记时按分句取最后一个含「A股/A 股」的句子；都没有才退回前缀。
    截断一律落到句末标点，不出半句。
    """
    t = str(text or "").strip()
    if not t:
        return ""
    for mark in ("A股映射", "A股对应", "对应A股", "映射A股", "A股受益", "A股方面",
                 "国内映射", "对应标的", "A 股映射", "对应 A 股"):
        i = t.find(mark)
        if i > 0:
            return _clip(t[i:], limit)
    parts = [p.strip() for p in re.split(r"[；;。]", t) if p.strip()]
    for p in reversed(parts):
        if "A股" in p or "A 股" in p:
            return _clip(p, limit)
    return _clip(t, limit)


def main():
    path = os.path.join(HERE, 'data.js')
    if not os.path.exists(path):
        print('[strategy_engine] data.js 不存在，跳过')
        return 0
    try:
        D = _load(path)
        today = datetime.date.today()
        gates = _gates()
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

        # 高确信条数上限（后验闸门）
        high_max = (gates.get('高') or {}).get('maxHighConf')
        if high_max is None:
            high_max = 2
        underperform = (excess is not None and excess < 0)

        watch = []     # 关注清单
        risks = []     # 风险提示（利空项）
        n_high = 0

        def add(item):
            nonlocal n_high
            if item['confidence'] == '高确信':
                if n_high >= high_max:
                    # R100z150：原实现写 note=...+'（受后验闸门限制…）'，但 item 本就没有
                    # note 字段 → 拼出的是纯括号句；且未降级的条目 note 恒为空串，
                    # 全站空内容扫描每次报 strategy.watchlist[*].note 空串（9+ 处噪音）。
                    # watchlist 前端并不渲染、无任何消费方 → 直接去掉 note 字段，用布尔标记降级。
                    item = dict(item, confidence='中等确信', demoted=True)
                else:
                    n_high += 1
            watch.append(item)

        # 1) 政策驱动：tier S/A
        for ps in (D.get('policySignals') or []):
            t = ps.get('tier')
            if t not in ('S', 'A'):
                continue
            conf = '高确信' if t == 'S' else '中等确信'
            secs = ps.get('sectors') or []
            sec_str = ('；受益板块：' + '、'.join(secs[:4])) if secs else ''
            add({
                'source': '政策信号', 'name': (ps.get('issuer') or '') + ' · ' + (ps.get('docType') or ''),
                'confidence': conf,
                'logic': '力度分 %s（%s）；%s' % (ps.get('score'), ps.get('signalClass'), ps.get('binding') or ''),
                'catalyst': '受益板块盘面验证：' + (secs[0] if secs else '—') + sec_str,
                'invalidate': '次日受益板块未进入两市涨幅前 20%，或政策细则一周内未落地即视为证伪',
            })

        # 2) AI 高确信板块
        for s in ((D.get('aiPrediction') or {}).get('sectors') or []):
            c = s.get('confidence')
            if c not in ('高', '中'):
                continue
            conf = '高确信' if c == '高' else '中等确信'
            add({
                'source': 'AI预测',
                'name': s.get('sector') or '',
                'confidence': conf,
                # R100z157：取 A 股映射段，不再被美股隔夜的 80 字吃掉结论
                'logic': _a_share_clause(s.get('logic') or s.get('reason')),
                'catalyst': '次日板块强弱兑现',
                # R100z157：与断板 falsify 对称的**引擎层反证条件**——主推不是只说好，
                # 必须写明「什么情况下算被推翻」，否则事后无法归因是运气还是判断。
                'invalidate': '次日该板块超额收益 ≤ 0（未跑赢上证/深成/创业板/中证1000 中位数）即视为证伪',
            })

        # 3) 断板反包确认池：利好/中性进关注、利空进风险
        for e in ((D.get('duanban') or {}).get('confirmed') or []):
            sent = e.get('sentiment')
            nm = e.get('name') or e.get('code') or ''
            if sent == 'bear':
                risks.append({'source': '断板反包·确认池', 'name': nm,
                              'reason': (e.get('story') or '')[:80]})
                continue
            if sent == 'neutral':
                continue
            add({
                'source': '断板反包·确认池',
                'name': nm,
                'confidence': '中等确信',
                'logic': (e.get('story') or e.get('form') or '')[:80],
                'catalyst': '反包确认（放量突破断板价）',
            })

        # 反馈（闭环读数）
        feedback = {
            'underperform': underperform,
            'excessPct': excess,
            'highConfCap': high_max,
            'note': ('近期回测%s基准（超额 %s%%），高确信层已按后验闸门收紧至 %d 条'
                     % ('跑输' if underperform else '跑赢',
                        ('%+.2f' % excess) if excess is not None else '—', high_max))
            if excess is not None else '回测样本不足，高确信上限按默认 %d 条' % high_max,
        }
        # 把反馈落盘（供后续审计/人工复盘，亦是「跑输→写回」的确定性产物）
        try:
            write_json_atomic(FEEDBACK_FILE, feedback, label='[strategy_engine.feedback]')
        except Exception as e:
            # R100z273：反馈落盘失败原来静默 pass。「跑输 → 写回」这条链是复盘卡的数据来源，
            # 写不进去就等于当天没有反馈，而页面上看不出缺的是哪一天（24.110：空着＝有结论没写）。
            print(f'[strategy_engine] 反馈落盘失败（复盘数据将缺本日一条）：{str(e)[:120]}',
                  flush=True)

        D['strategy'] = {
            'generatedAt': today.isoformat(),
            'positionBand': pos,
            'lockIn': lock_in,
            'outWeight': out_weight,
            'watchlist': watch[:12],
            'risks': risks[:8],
            'feedback': feedback,
            'note': '盘前关注清单由看板已产出信号确定性聚合而成（政策/AI预测/断板反包），'
                    '非投资建议；仓位纪律取自日历窗口因子，高确信条数受后验校准闸门约束。',
        }
        _dump(path, D)
        print('[strategy_engine] 关注清单 %d 条（高确信 %d，受闸门≤%d）/ 风险提示 %d 条；仓位带 %s'
              % (len(watch), n_high, high_max, len(risks), pos.get('text')))
        return 0
    except Exception as e:  # noqa: BLE001
        try:
            D = _load(path)
            D['strategy'] = {'generatedAt': datetime.date.today().isoformat(), 'watchlist': [],
                             'risks': [], 'note': '生成异常，已写空', 'positionBand': {}}
            _dump(path, D)
        except Exception:
            pass
        print(f'[strategy_engine] 异常已兜底（写空）：{e!r}')
        return 0


if __name__ == '__main__':
    sys.exit(main())
