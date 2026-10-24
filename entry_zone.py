#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块「资金三重确认 + 明日接盘区间」（R100z194，2026-10-24）。

## 为什么改

用户原话：「AI 预测根据要闻+隔夜美股+昨日 A 股情绪推荐的板块，再到该板块的标的胜率很低」。
数据上这条路走不通，原因有两层：

1. **「明天哪个板块涨」预测不了**。49 个板块 × 4312 个板块日：板块当日涨幅的次日自相关
   IC = −0.026；今日 Top10 明日仍在 Top10 的比例 24.3%，明日排名分位均值 51.4%（=随机）。
   资金今天进、明天出——这是用户的原话，也是数据。
2. **从预测板块里机械取 8 只，把一次错误放大 8 倍**。板块判错 → 8 只全错。

所以本脚本不预测方向，只做两件可核查的事：

- **A. 昨日资金三重确认**：净流入 / 上涨家数占比 / 放量倍数。
  把「我猜它明天走强」换成「昨天进它的钱是不是已成共识」。
- **B. 明日接盘区间**：承接价 / 不追上限 / 证伪价（全部用**百分比**，可映射到任意个股）。
  把「方向」换成「价位」。

`direction` 字段保留（前端与 prompt 词表依赖），但语义改为确认状态的映射：
passed=3 → 走强、passed=2 → 偏强、passed≤1 → 承压。**这是描述昨天，不是预言明天。**

## 数据来源（全部来自 data.js 的 ashare.allBoards，零新增外部依赖）

    {code,name,pct,netInflow(亿),upCount,downCount,lead,leadPct,ztCount,pct5,
     kline:[[day,open,close,high,low,vol],…30 根]}

## 用法

    python3 entry_zone.py                     # 打印 6 个预测板块的三重确认
    python3 entry_zone.py --json              # 给 AI 读的结构化结果（不写盘）
    python3 entry_zone.py --write             # 把 fundCheck/entry 补进 aiPrediction.sectors
    python3 entry_zone.py --selftest          # 自检
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

# 三重确认阈值（集中放这里）
INFLOW_MIN = 0.0      # 主力净流入 > 0（亿）
UPRATIO_MIN = 0.60    # 上涨家数占比 ≥ 60%（不是被一两只拉的）
VOLRATIO_MIN = 1.05   # 板块成交额 ≥ 5 日均额 ×1.05（要有量）

ATR_N = 14            # 波动率窗口
SUPPORT_K = 0.5       # 承接价 = 昨收 − 0.5×ATR
CHASE_K = 0.5         # 不追上限 = 昨收 + 0.5×ATR
INVALID_K = 1.0       # 证伪价 = 昨收 − 1.0×ATR
SUPPORT_CAP = 2.0     # 承接价最深 2.0%（再深就不是回踩）
CHASE_CAP = 1.2       # 不追上限最高 1.2%
INVALID_CAP = 3.5     # 证伪最深 3.5%

VERDICT = {3: ('资金已成共识', '走强'),
           2: ('分歧未合', '偏强'),
           1: ('资金不支持', '承压'),
           0: ('资金不支持', '承压')}


def atr_pct(kline, n=ATR_N):
    """板块指数 ATR%（真实波幅均值 / 收盘）。kline 行 = [day,open,close,high,low,vol]。"""
    rows = []
    for b in kline or []:
        try:
            rows.append((float(b[2]), float(b[3]), float(b[4])))
        except (TypeError, ValueError, IndexError):
            continue
    if len(rows) < 3:
        return None
    trs = []
    for i in range(1, len(rows)):
        c, h, l = rows[i]
        pc = rows[i-1][0]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    win = trs[-n:] if len(trs) >= n else trs
    close = rows[-1][0]
    if close <= 0 or not win:
        return None
    return sum(win) / len(win) / close * 100


def vol_ratio(kline, n=5):
    """板块成交额放量倍数 = 最后一根量 / 前 n 根均量。"""
    vols = []
    for b in kline or []:
        try:
            vols.append(float(b[5]))
        except (TypeError, ValueError, IndexError):
            continue
    if len(vols) < n + 1:
        return None
    base = sum(vols[-n-1:-1]) / n
    return (vols[-1] / base) if base > 0 else None


def ma_pct(kline, n):
    cl = []
    for b in kline or []:
        try:
            cl.append(float(b[2]))
        except (TypeError, ValueError, IndexError):
            continue
    if len(cl) < n:
        return None
    return sum(cl[-n:]) / n


def _kline_of(board):
    """板块日线：优先用 data.js 里已附的 kline，没有就抓同花顺行业日线。

    2026-10-24 实测：`ashare.allBoards` 的 90 条**只有 10 个字段、没有 kline**
    （kline 只挂在 sectorsUp/sectorsDown 的 top5 上）。所以 ATR 必须现抓，
    数据源与 update_ashare_sectors.py 同源（thsCode 就是 allBoards[].code）。
    """
    kl = board.get('kline') or []
    if len(kl) >= 5:
        return kl
    code = board.get('code')
    if not code:
        return []
    try:
        import update_ashare_sectors as UAS
        return UAS.fetch_ths_board_kline(str(code), days=30) or []
    except Exception:                                       # noqa: BLE001
        return []


def analyze(board):
    """单板块 → (fundCheck, entry)。字段名与 data.js 对齐，别改。"""
    inflow = board.get('netInflow')
    up, dn = board.get('upCount'), board.get('downCount')
    up_ratio = None
    if isinstance(up, (int, float)) and isinstance(dn, (int, float)) and (up + dn) > 0:
        up_ratio = up / (up + dn)
    kl = _kline_of(board)
    vr = vol_ratio(kl)
    ap = atr_pct(kl)

    flags = [bool(inflow is not None and inflow > INFLOW_MIN),
             bool(up_ratio is not None and up_ratio >= UPRATIO_MIN),
             bool(vr is not None and vr >= VOLRATIO_MIN)]
    passed = sum(flags)
    verdict, dir_hint = VERDICT[passed]

    detail = []
    detail.append(f'净流入 {inflow:+.2f} 亿{"✔" if flags[0] else "✘"}' if inflow is not None
                  else '净流入缺失✘')
    detail.append(f'上涨家数占比 {up_ratio*100:.0f}%{"✔" if flags[1] else "✘"}' if up_ratio is not None
                  else '上涨家数缺失✘')
    detail.append(f'成交额 {vr:.2f} 倍{"✔" if flags[2] else "✘"}' if vr is not None
                  else '成交额放量缺失✘')
    fund = dict(inflow=round(inflow, 2) if inflow is not None else None,
                upRatio=round(up_ratio, 3) if up_ratio is not None else None,
                volRatio=round(vr, 2) if vr is not None else None,
                passed=passed, verdict=verdict, dirHint=dir_hint,
                text=f'昨日资金三重确认 {passed}/3：' + '、'.join(detail))

    entry = None
    if ap is not None:
        s = min(ap * SUPPORT_K, SUPPORT_CAP)
        c = min(ap * CHASE_K, CHASE_CAP)
        v = min(ap * INVALID_K, INVALID_CAP)
        entry = dict(supportPct=-round(s, 2), chaseCeilPct=round(c, 2),
                     invalidPct=-round(v, 2), atrPct=round(ap, 2),
                     ma5=round(ma_pct(kl, 5), 2) if ma_pct(kl, 5) else None,
                     ma10=round(ma_pct(kl, 10), 2) if ma_pct(kl, 10) else None,
                     close=round(float(kl[-1][2]), 2),
                     basis=(f'板块指数昨收 {float(kl[-1][2]):.2f}、'
                            f'{ATR_N} 日 ATR {ap:.2f}%：回踩 {s:.2f}% 视为有承接（再深就不是回踩），'
                            f'涨过 {c:.2f}% 视为追高（不追），跌破 {v:.2f}% 视为逻辑失效'))
    return fund, entry


def build(D):
    """→ {板块名: {fundCheck, entry}}，按 aiPrediction.sectors 的板块名。"""
    boards = {str(b.get('name')): b for b in (D.get('ashare', {}).get('allBoards') or [])}
    out = {}
    miss = []
    for s in (D.get('aiPrediction', {}).get('sectors') or []):
        nm = str(s.get('sector') or '')
        b = boards.get(nm)
        if not b:
            miss.append(nm)
            continue
        fund, entry = analyze(b)
        out[nm] = dict(fundCheck=fund, entry=entry)
    return out, miss


def write_back(D, res):
    """只补 fundCheck/entry 两个字段，其余一字不动（含 direction/logic/exp/stocks）。"""
    n = 0
    for s in (D.get('aiPrediction', {}).get('sectors') or []):
        r = res.get(str(s.get('sector') or ''))
        if not r:
            continue
        s['fundCheck'] = r['fundCheck']
        if r['entry']:
            s['entry'] = r['entry']
        n += 1
    return n


def selftest():
    ok, bad = 0, []

    def chk(n, c, e=''):
        nonlocal ok
        if c:
            ok += 1
        else:
            bad.append(f'{n} {e}')

    # 造一个板块：净流入 5 亿、26 涨 0 跌、最后一根量是前 5 均量 2 倍
    kl = [[f'd{i}', 100, 100 + i, 101 + i, 99 + i, 1000.0] for i in range(20)]
    kl[-1][5] = 2000.0
    b = dict(name='测试板', netInflow=5.0, upCount=26, downCount=0, kline=kl)
    f, e = analyze(b)
    chk('三重确认全过', f['passed'] == 3 and f['verdict'] == '资金已成共识', str(f['passed']))
    chk('dirHint 走强', f['dirHint'] == '走强')
    chk('entry 有区间', e is not None and e['supportPct'] < 0 < e['chaseCeilPct'] > 0)
    chk('support 不为正', e['supportPct'] < 0)
    chk('invalid 比 support 更深', e['invalidPct'] < e['supportPct'])

    # 只过一项：净流出、上涨占比低、缩量
    kl2 = [[f'd{i}', 100, 100, 101, 99, 1000.0] for i in range(20)]
    kl2[-1][5] = 500.0
    b2 = dict(name='弱板', netInflow=-3.0, upCount=3, downCount=20, kline=kl2)
    f2, _ = analyze(b2)
    chk('三项全不过', f2['passed'] == 0 and f2['verdict'] == '资金不支持', str(f2['passed']))
    chk('dirHint 承压', f2['dirHint'] == '承压')

    # 缺字段：不许当 0 分算过，也不许炸
    f3, e3 = analyze(dict(name='空板'))
    chk('缺字段 passed=0', f3['passed'] == 0)
    chk('缺字段 inflow=None', f3['inflow'] is None)
    chk('缺 kline entry=None', e3 is None)

    # ATR 上限封顶
    kl3 = [[f'd{i}', 100, 100, 120, 80, 1000.0] for i in range(20)]
    _, e4 = analyze(dict(name='高波', netInflow=1, upCount=5, downCount=5, kline=kl3))
    chk('support 封顶 2.0', e4['supportPct'] >= -2.0, str(e4['supportPct']))
    chk('chase 封顶 1.2', e4['chaseCeilPct'] <= 1.2, str(e4['chaseCeilPct']))

    # write_back 只动两个字段
    D = {'aiPrediction': {'sectors': [{'sector': '测试板', 'direction': '走强',
                                       'logic': '原文不许动', 'exp': {'layer': 'F'}}]}}
    res = {'测试板': dict(fundCheck=f, entry=e)}
    n = write_back(D, res)
    s = D['aiPrediction']['sectors'][0]
    chk('write_back 计数', n == 1)
    chk('其余字段不动', s['direction'] == '走强' and s['logic'] == '原文不许动' and s['exp'] == {'layer': 'F'})
    chk('补上 fundCheck', s['fundCheck']['passed'] == 3)

    print(f'[entry_zone] 自检 {ok}/{ok+len(bad)} 通过')
    for x in bad:
        print('  FAIL', x)
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    import common
    D = common.load_dashboard_data(BASE)
    res, miss = build(D)
    if a.write:
        n = write_back(D, res)
        common.write_data_js(BASE, D, label='entry_zone')
        print(f'[entry_zone] 已补 {n} 个板块的 fundCheck/entry')
        if miss:
            print(f'[entry_zone][WARN] {len(miss)} 个预测板块在 allBoards 里找不到同名板块：'
                  f'{"、".join(miss)}（不补，前端显示「—」，严禁编造数值）')
        # 复验落盘
        D2 = common.load_dashboard_data(BASE)
        got = {str(s.get('sector')): bool(s.get('entry')) for s in
               (D2.get('aiPrediction', {}).get('sectors') or [])}
        print(f'[entry_zone] 落盘复验：{sum(got.values())}/{len(got)} 个板块带 entry')
        return
    if a.json:
        print(json.dumps({'ok': True, 'sectors': res, 'missing': miss}, ensure_ascii=False))
        return
    if not res:
        print('[entry_zone] aiPrediction.sectors 为空或无一匹配，无输出')
        return
    print(f'{"板块":<12}{"确认":<6}{"方向提示":<8}{"承接":>8}{"不追":>8}{"证伪":>8}  ATR')
    for nm, r in res.items():
        f, e = r['fundCheck'], r['entry'] or {}
        print(f'{nm:<12}{f["passed"]}/3   {f["dirHint"]:<8}'
              f'{e.get("supportPct", 0):>+7.2f}%{e.get("chaseCeilPct", 0):>+7.2f}%'
              f'{e.get("invalidPct", 0):>+7.2f}%  {e.get("atrPct", 0):.2f}%   {f["verdict"]}')
    if miss:
        print(f'[WARN] 未匹配板块：{"、".join(miss)}')


if __name__ == '__main__':
    main()
