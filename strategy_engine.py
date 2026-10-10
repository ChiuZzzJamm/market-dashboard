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
    with open(path, 'w', encoding='utf-8') as f:
        f.write('window.DASHBOARD_DATA = ' +
                json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';\n')


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
    try:
        c = json.load(open(CURVE_FILE, encoding='utf-8'))
        return (c.get('stat') or {}).get('excessPct')
    except Exception:
        return None


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
                    item = dict(item, confidence='中等确信',
                                note=item.get('note', '') + '（受后验闸门限制，由高确信降级）')
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
                'note': '',
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
                'logic': (s.get('reason') or s.get('logic') or '')[:80],
                'catalyst': '次日板块强弱兑现',
                'note': '',
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
                'note': '',
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
            json.dump(feedback, open(FEEDBACK_FILE, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)
        except Exception:
            pass

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
