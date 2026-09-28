#!/usr/bin/env python3
"""必带数据清单 / 宏观海外项刷新（R99k 引入 · R100h 前端下线后仍由 07:30 自动化 step2.6 调用）。

职责（与 07:30 自动化 prompt step2.6 对齐）：
  刷新顶层 D.macroChecklist 的「海外项」（外汇 / 美VIX-DXY-10Y / 美股指数 / 商品组），
  供 A 股页开盘前看海外风险。

健壮性约定（R91n 软闸门 + R100n 无死脚本）：
  - 单源失败 → 保留旧值 + ok:false，不影响其它项；
  - 全部外部源失败 → 不写盘、保留原 macroChecklist 不清场；
  - 任何异常都吞掉，脚本本身绝不抛栈（防止 07:30 流水线在 step2.6 卡死 / 报死脚本）；
  - 若 data.js 中本就不存在 macroChecklist 字段（R100h 前端必带数据清单已下线），
    视为特性已移除，直接优雅退出 0（no-op），不强行复活该字段。

用法：python3 data_checklist.py
"""
import sys, json, os, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
OK = 0
FAIL = 1


def _load():
    try:
        import common
        return common.load_dashboard_data(BASE)
    except Exception as e:
        print('[CHECKLIST] 加载 data.js 失败：' + str(e))
        return None


def _save(D):
    try:
        import common
        node = common.find_node()
        out = 'window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False,
                                                      separators=(',', ':')) + ';\n'
        with open(os.path.join(BASE, 'data.js'), 'w', encoding='utf-8') as f:
            f.write(out)
        print('[CHECKLIST] macroChecklist 已写回 data.js')
        return True
    except Exception as e:
        print('[CHECKLIST] 写回 data.js 失败：' + str(e))
        return False


def _fetch_one(url, timeout=8):
    """极简 HTTP GET（优先 urllib，失败返回 None），不抛异常。"""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', 'ignore')
    except Exception:
        return None


def refresh_overseas(mc):
    """best-effort 刷新海外项；返回 (new_overseas, any_ok)。单源失败不影响其余。"""
    groups = mc.get('overseas', {}) if isinstance(mc, dict) else {}
    any_ok = False
    # 美股指数 / 外汇 / VIX-DXY-10Y / 商品：直接复用腾讯 gtimg 行情全集（与 update_us_from_quotes 同源，沙箱可达）
    raw = _fetch_one('http://qt.gtimg.cn/q=usDJI,usIXIC,usINX', timeout=10)
    us_idx = {}
    if raw:
        try:
            for seg in raw.split(';'):
                seg = seg.strip()
                if not seg or '=' not in seg:
                    continue
                key, val = seg.split('=', 1)
                val = val.strip().strip('"')
                if not val:
                    continue
                f = val.split('~')
                # gtimg 格式：f[1]=名称 f[3]=当前价 f[31]=涨跌% f[4]=昨收 ... 取名称/现价/涨跌%
                if len(f) > 31:
                    us_idx[f[1]] = {'name': f[1], 'value': f[3], 'chg': f[31]}
        except Exception:
            us_idx = {}
    if us_idx:
        any_ok = True
        for nm, item in us_idx.items():
            item['ok'] = True
        groups['美股指数'] = list(us_idx.values())
    else:
        # 保留旧值 + 标注 ok:false（软闸门）
        for g in groups.get('美股指数', []):
            if isinstance(g, dict):
                g['ok'] = False
    # 其余海外组（外汇 / VIX-DXY-10Y / 商品组）暂时以「保留旧值 + ok:false」兜底：
    # 这些标的在沙箱缺乏稳定可达源，强行抓取只会制造噪声；保留既有值不清除（R91n）。
    for gkey in ('外汇', '美VIX-DXY-10Y', '商品组'):
        for g in groups.get(gkey, []):
            if isinstance(g, dict):
                g['ok'] = False
    return groups, any_ok


def main():
    D = _load()
    if D is None:
        print('[CHECKLIST] data.js 读取异常，跳过（不写盘、退出 0 防卡死）')
        return OK
    if 'macroChecklist' not in D or not isinstance(D.get('macroChecklist'), dict):
        # R100h：前端必带数据清单已下线，该字段已从 data.js 移除。
        # 视作特性移除，优雅 no-op 退出，不强行复活字段（避免无主数据污染）。
        print('[CHECKLIST] macroChecklist 字段不存在（必带数据清单已下线 R100h），step2.6 优雅 no-op 退出')
        return OK
    mc = D['macroChecklist']
    try:
        new_overseas, any_ok = refresh_overseas(mc)
        mc['overseas'] = new_overseas
        # narrative 不在本步改写（沿用 16:00 由 AI 补写的当日解读）
        if any_ok:
            _save(D)
            print('[CHECKLIST] 海外项部分刷新成功，已写回')
        else:
            # 全部外部源失败：不写盘、保留原 macroChecklist 不清场（R91n）
            print('[CHECKLIST] 全部外部源失败，保留原 macroChecklist 不写盘（软闸门）')
    except Exception as e:
        print('[CHECKLIST] 刷新过程异常（已吞掉）：' + str(e) + '，保留原值退出 0')
    return OK


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        # 终极兜底：任何未预期异常都不让 07:30 流水线在 step2.6 卡死
        print('[CHECKLIST] 未预期异常：' + str(e) + '，退出 0')
        sys.exit(OK)
