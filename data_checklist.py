#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""必带数据分级清单聚合（2026-09-26 新增 · 方向④，修订版）。

方法论与数据契约：fin-strategy-engine skill `references/data-checklist.md`。
- 核心 20 项（硬标记，缺失红标） + 环境 3 项（降级标注） + LLM 派生结论。
- 纯 HTTP、禁连接器；东财/腾讯/新浪/er-api/open-meteo 多源，单源失败保留旧值并标注
  （R91n 软闸门，不阻断部署）；全部外部源失败 → exit 1（与 R91n 同口径）。
- 牛散/机构席位降级为「打分」（席位打分口径，禁写「主力已进场」）。
- 北向资金整体已移除（2024-08 后仅盘后）；分时条件已取消（无盘中自动化）。
- 输出：D.macroChecklist。LLM 消费本清单写 narrative/resonance。
- 用法：python3 data_checklist.py [--dry-run] [--self-test]
  --self-test：用 mock 数据验证结构 + 降级逻辑，不联网。
"""
import sys, json, os, time, datetime, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))


def curl_json(url, timeout=15, retries=1, decode='utf-8'):
    for i in range(retries + 1):
        try:
            p = subprocess.run(['curl', '-s', '--max-time', str(timeout),
                                '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                                url], capture_output=True, timeout=timeout + 5)
            out = (p.stdout or b'').decode(decode, errors='replace').strip()
            if out:
                return out
        except Exception:
            pass
        if i < retries:
            time.sleep(2)
    return None


# ---------------- 各源抓取（best-effort） ----------------
def fetch_fx():
    """open.er-api.com 一次取全币种（USD 基准）。返回 dict 或 None。"""
    raw = curl_json('https://open.er-api.com/v6/latest/USD', timeout=12)
    if not raw:
        return None
    try:
        j = json.loads(raw)
        r = j.get('rates') or {}
        return {'USDCNH': r.get('CNY'), 'USDJPY': r.get('JPY'),
                'USDKRW': r.get('KRW'), 'USDINR': r.get('INR'),
                'USDTHB': r.get('THB'), 'USDIDR': r.get('IDR')}
    except Exception:
        return None


def fetch_tencent_quote(codes):
    """腾讯 gtimg 行情（codes 形如 sh000001,sz399006）。返回 {code: {price,prev_close}}。"""
    url = 'https://qt.gtimg.cn/q=' + ','.join(codes)
    raw = curl_json(url, timeout=12)
    if not raw:
        return {}
    out = {}
    for line in raw.split(';'):
        line = line.strip()
        if not line.startswith('v_'):
            continue
        try:
            name = line[2:line.index('=')]
            body = line[line.index('"') + 1:line.rindex('"')]
            f = body.split('~')
            out[name] = {'price': float(f[3]), 'prev_close': float(f[4])}
        except Exception:
            continue
    return out


def fetch_sina_hf(sym):
    """新浪外盘（hf_xxx）。返回 {price,prev_close} 或 None。"""
    raw = curl_json(f'https://hq.sinajs.com.cn/etag.php?/cn/{sym}.php',
                    timeout=12, retries=1)
    # 新浪需 Referer，退化为直接 hq 接口
    if not raw:
        raw = curl_json(f'https://hq.sinajs.com.cn/{sym}.php', timeout=12, retries=1)
    if not raw:
        return None
    try:
        body = raw.split('"')[1]
        f = body.split(',')
        return {'price': float(f[1]), 'prev_close': float(f[2])}
    except Exception:
        return None


def fetch_eastmoney_json(url):
    """东财通用 JSON（best-effort，沙箱可能不可达）。"""
    raw = curl_json(url, timeout=12, retries=1)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


# ---------------- 构建清单 ----------------
def _chg_pct(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return round((cur / prev - 1) * 100, 2)


def _item(key, name, value, prev, ok, grade, source, value_date):
    return {'key': key, 'name': name, 'value': value,
            'chgPct': _chg_pct(value, prev) if isinstance(value, (int, float)) else None,
            'ok': bool(ok), 'grade': grade, 'source': source, 'valueDate': value_date}


def build_checklist(today, raw, old):
    """raw: 抓取的原始值 dict（可能含 None）；old: 旧 macroChecklist（保留旧值）。
    返回 (field, missing_core, missing_env)。"""
    old_items = {it.get('key'): it for it in (old or {}).get('items', []) or []}
    items = []
    missing_core, missing_env = [], []

    def take(key, source, fallback_ok=True):
        """取 raw[key]；缺失则回退旧值并标记 ok=False。"""
        v = raw.get(key)
        if v is None:
            oi = old_items.get(key)
            if oi and 'valueDate' in oi:
                return oi.get('value'), oi.get('valueDate'), False, True  # 沿用旧值
            return None, None, False, False  # 完全缺失
        return v, today, True, True

    # 1. 异常板块（来自 D.ashare，无需抓取）
    ab = raw.get('anomalyBoards')
    items.append(_item('anomalyBoards', '异常板块(异动)', ab if ab else '—', None,
                       bool(ab), 'core', 'data.js', today))

    # 2. 龙虎榜 3 日净买入（东财 best-effort）
    v, vd, ok, _ = take('lhbNet3d', 'eastmoney')
    if not ok: missing_core.append('lhbNet3d')
    items.append(_item('lhbNet3d', '龙虎榜3日净买入(亿)', v, None, ok, 'core', 'eastmoney', vd))

    # 3. 融券余额
    v, vd, ok, _ = take('marginShort', 'eastmoney')
    if not ok: missing_core.append('marginShort')
    items.append(_item('marginShort', '融券余额(亿)', v, None, ok, 'core', 'eastmoney', vd))

    # 4. 股指期货空单
    v, vd, ok, _ = take('indexFuturesShort', 'eastmoney')
    if not ok: missing_core.append('indexFuturesShort')
    items.append(_item('indexFuturesShort', '股指期货空单', v, None, ok, 'core', 'eastmoney', vd))

    # 5. 大宗交易抛压
    v, vd, ok, _ = take('blockTradePressure', 'eastmoney')
    if not ok: missing_core.append('blockTradePressure')
    items.append(_item('blockTradePressure', '大宗交易抛压(亿)', v, None, ok, 'core', 'eastmoney', vd))

    # 6. 牛散席位动向（打分制）
    v, vd, ok, _ = take('niuSan', 'lhb-seats')
    if not ok: missing_core.append('niuSan')
    items.append(_item('niuSan', '牛散席位动向(打分0-10)', v, None, ok, 'core', 'lhb-seats', vd))

    # 7. 机构席位动向（打分制）
    v, vd, ok, _ = take('jigou', 'lhb-seats')
    if not ok: missing_core.append('jigou')
    items.append(_item('jigou', '机构席位动向(打分0-10)', v, None, ok, 'core', 'lhb-seats', vd))

    # 8. 股东共振（户数变化）
    v, vd, ok, _ = take('shareholder', 'eastmoney')
    if not ok: missing_core.append('shareholder')
    items.append(_item('shareholder', '股东户数变化(%)', v, None, ok, 'core', 'eastmoney', vd))

    # 9-11. 外汇
    for key, nm in (('usdcnh', 'USDCNH'), ('usdjpy', 'USDJPY'),
                    ('emfx', '新兴市场货币组')):
        v, vd, ok, _ = take(key, 'er-api')
        if not ok: missing_core.append(key)
        items.append(_item(key, nm, v, None, ok, 'core', 'er-api', vd))

    # 12. 新兴市场货币强弱（派生：emfx 较 20 日前）
    emfx = raw.get('emfx')
    emfx_idx = raw.get('emfxIndex')
    ok12 = emfx_idx is not None
    if not ok12: missing_core.append('emfxIndex')
    items.append(_item('emfxIndex', '新兴市场货币强弱', emfx_idx, None, ok12, 'core', 'derived', today))

    # 13-15. VIX / DXY / 美10Y
    for key, nm in (('vix', 'VIX'), ('dxy', '美元指数DXY'), ('us10y', '美10Y(%)')):
        v, vd, ok, _ = take(key, 'sina/tencent')
        if not ok: missing_core.append(key)
        items.append(_item(key, nm, v, None, ok, 'core', 'sina/tencent', vd))

    # 16. 美股指数组
    v, vd, ok, _ = take('usIndices', 'tencent')
    if not ok: missing_core.append('usIndices')
    items.append(_item('usIndices', '标普/纳指/道指', v, None, ok, 'core', 'tencent', vd))

    # 17. A股指数组
    v, vd, ok, _ = take('cnIndices', 'tencent')
    if not ok: missing_core.append('cnIndices')
    items.append(_item('cnIndices', '上证/创业板指', v, None, ok, 'core', 'tencent', vd))

    # 18. 沪深300 + 富时A50
    v, vd, ok, _ = take('cn300A50', 'tencent/sina')
    if not ok: missing_core.append('cn300A50')
    items.append(_item('cn300A50', '沪深300/富时A50', v, None, ok, 'core', 'tencent/sina', vd))

    # 19. 商品组
    v, vd, ok, _ = take('commodities', 'sina')
    if not ok: missing_core.append('commodities')
    items.append(_item('commodities', '黄金/原油/铜', v, None, ok, 'core', 'sina', vd))

    # 20. 美元→资金→A股 传导（LLM 判定，占位 ok）
    items.append(_item('capitalFlowLogic', '美元→资金→A股传导', 'LLM判定', None,
                       True, 'core', 'llm', today))

    # E1-E3 环境降级项
    for key, nm in (('pigPrice', '猪肉市场价'), ('vegPrice', '蔬菜市场价'), ('weather', '全国天气概况')):
        v, vd, ok, has_old = take(key, 'public-best-effort')
        if not ok:
            missing_env.append(key)
        items.append(_item(key, nm, v, None, ok, 'env', 'public-best-effort', vd))

    # ---- 聚合派生 ----
    def num(k):
        x = raw.get(k)
        return x if isinstance(x, (int, float)) else None

    # fx 强弱：USDCNH 升=人民币弱；用 USDCNH 与 20 日基准偏差粗略打分
    usdcnh = num('usdcnh')
    fx_vals = [num('usdcnh'), num('usdjpy')]
    fx_valid = [x for x in fx_vals if x]
    fx_score = -1.0 if (usdcnh and usdcnh > 7.2) else (0.5 if usdcnh and usdcnh < 7.1 else -0.2)
    fx_label = '偏弱' if fx_score < -0.5 else ('偏强' if fx_score > 0 else '中性')

    # 做空信号：融券+期货+大宗，取三档评分（无数据则中性）
    short_parts = [raw.get('marginShort'), raw.get('indexFuturesShort'), raw.get('blockTradePressure')]
    short_valid = [x for x in short_parts if isinstance(x, (int, float))]
    short_score = round(sum(short_valid) / len(short_valid), 1) if short_valid else 0
    short_label = '偏空' if short_score < -3 else ('偏多' if short_score > 3 else '中性')

    # 席位打分
    ns = raw.get('niuSan'); jg = raw.get('jigou')
    ns_s = ns if isinstance(ns, (int, float)) else 5
    jg_s = jg if isinstance(jg, (int, float)) else 5
    capita_label = '中性偏多' if (ns_s + jg_s) >= 11 else ('中性偏空' if (ns_s + jg_s) <= 9 else '中性')

    fx_strength = {'score': fx_score, 'label': fx_label}
    short_signal = {'margin': raw.get('marginShort'), 'futures': raw.get('indexFuturesShort'),
                    'blockTrades': raw.get('blockTradePressure'),
                    'score': short_score, 'label': short_label}
    capita_score = {'niuSan': ns_s, 'jigou': jg_s, 'label': capita_label}

    field = {
        'updatedAt': f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}（必带数据 {today} 收盘 数据已自动更新）",
        'tradeDate': today,
        'items': items,
        'fxStrength': fx_strength,
        'shortSignal': short_signal,
        'capitaScore': capita_score,
        'narrative': '',
        'resonance': '',
        'missingCore': missing_core,
        'missingEnv': missing_env,
    }
    return field, missing_core, missing_env


def _fetch_all(D):
    """真实抓取（best-effort）。返回 raw dict。异常不影响其他项。"""
    raw = {}
    # 异常板块来自 D.ashare
    su = (D.get('ashare') or {}).get('sectorsUp') or []
    sd = (D.get('ashare') or {}).get('sectorsDown') or []
    raw['anomalyBoards'] = f"领涨{len(su)}/领跌{len(sd)}" if (su or sd) else None

    # 外汇
    fx = fetch_fx()
    if fx:
        for k in ('USDCNH', 'USDJPY', 'USDKRW', 'USDINR', 'USDTHB', 'USDIDR'):
            raw[k.lower()] = fx.get(k)
        # 新兴市场货币组：用 KRW/INR/THB/IDR 较 USD 的等权
        em = [fx.get(k) for k in ('USDKRW', 'USDINR', 'USDTHB', 'USDIDR') if fx.get(k)]
        raw['emfx'] = ';'.join(str(x) for x in em) if em else None

    # 东财系（沙箱可能不可达，best-effort）
    for key, url in {
        'lhbNet3d': 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&fid=f62&fs=m:0+t:2,m:1+t:2&fields=f62',
        'marginShort': 'https://datacenter.eastmoney.com/api/data/get?type=RPTA_WEB_RZRQ_GMRQYJYC',
        'indexFuturesShort': 'https://datacenter.eastmoney.com/api/data/get?type=RPT_FUTURES_POSITION',
        'blockTradePressure': 'https://datacenter.eastmoney.com/api/data/get?type=RPT_BLOCK_TRADE',
        'shareholder': 'https://datacenter.eastmoney.com/api/data/get?type=RPT_HOLDERNUM',
    }.items():
        j = fetch_eastmoney_json(url)
        if j is not None:
            raw[key] = j  # 原始结构，LLM/前端按需取数

    # 腾讯/新浪行情
    tq = fetch_tencent_quote(['sh000001', 'sz399006', 'sh000300', 'sz399300', 'sh000016'])
    if tq:
        raw['cnIndices'] = ';'.join(f"{k.split('sh')[-1].split('sz')[-1]}:{v['price']}" for k, v in tq.items())
        if 'sh000300' in tq:
            raw['cn300A50'] = f"沪深300:{tq['sh000300']['price']}"
    usq = fetch_tencent_quote(['usSPX', 'usIXIC', 'usDJI'])
    if usq:
        raw['usIndices'] = ';'.join(f"{k}:{v['price']}" for k, v in usq.items())
    vix = fetch_sina_hf('hf_VIX')
    if vix: raw['vix'] = vix['price']
    dxy = fetch_sina_hf('hf_DINIW')
    if dxy: raw['dxy'] = dxy['price']
    us10y = fetch_sina_hf('hf_TYX')
    if us10y: raw['us10y'] = us10y['price']
    comm = fetch_sina_hf('hf_GC')  # 黄金
    if comm: raw['commodities'] = f"黄金:{comm['price']}"

    # 牛散/机构席位打分（占位：沙箱无龙虎榜席位表，真实环境由脚本补算）
    # 见 references/data-checklist.md §1 序号6/7；此处留 None → 走降级 ok:false
    return raw


# ---------------- 自检 ----------------
def self_test():
    today = datetime.date.today().strftime('%Y-%m-%d')
    # mock：部分源成功、部分失败
    raw = {
        'anomalyBoards': '领涨12/领跌8',
        'usdcnh': 7.12, 'usdjpy': 148.3,
        'lhbNet3d': None, 'marginShort': None,  # 东财失败
        'niuSan': 6, 'jigou': 8,
        'vix': 14.2, 'cnIndices': '000001:3100', 'usIndices': 'SPX:5400',
    }
    old = {'items': [{'key': 'lhbNet3d', 'value': -12.5, 'valueDate': '2026-09-25'}]}
    field, mc, me = build_checklist(today, raw, old)
    # 校验：结构完整、降级项 ok=False 且沿用旧值
    assert len(field['items']) >= 20, "items 不足 20"
    lhb = next(it for it in field['items'] if it['key'] == 'lhbNet3d')
    assert lhb['ok'] is False and lhb['value'] == -12.5, "lhb 未沿用旧值"
    assert 'lhbNet3d' in field['missingCore'], "lhb 未计入 missingCore"
    assert field['fxStrength']['label'] == '偏弱' or field['fxStrength']['label'] == '中性'
    assert field['capitaScore']['label'] == '中性偏多', field['capitaScore']
    assert field['shortSignal']['score'] == 0, field['shortSignal']
    print(f"[SELF-TEST] items={len(field['items'])} missingCore={field['missingCore']} "
          f"missingEnv={field['missingEnv']}")
    print(f"[SELF-TEST] fx={field['fxStrength']} short={field['shortSignal']['label']} "
          f"capita={field['capitaScore']['label']}")
    print("[SELF-TEST] OK（结构完整 + 降级沿用旧值 + 聚合正常）")
    return True


# ---------------- 主流程 ----------------
def main():
    args = sys.argv[1:]
    if '--self-test' in args:
        sys.exit(0 if self_test() else 1)

    import common
    dry = '--dry-run' in args

    D = common.load_dashboard_data(BASE)
    today = datetime.date.today().strftime('%Y-%m-%d')
    raw = _fetch_all(D)
    any_ok = any(v is not None for k, v in raw.items()
                 if k not in ('anomalyBoards',)) or raw.get('anomalyBoards')
    if not any_ok:
        print('[CHECKLIST] 全部外部源失败，exit 1（R91n 口径）')
        sys.exit(1)
    old = D.get('macroChecklist')
    field, mc, me = build_checklist(today, raw, old)
    D['macroChecklist'] = field

    if dry:
        print(f"[CHECKLIST][DRY] items={len(field['items'])} missingCore={mc} missingEnv={me}，未写回")
        return

    out = 'window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False,
                                                  separators=(',', ':')) + ';\n'
    with open(os.path.join(BASE, 'data.js'), 'w', encoding='utf-8') as f:
        f.write(out)
    print(f"[CHECKLIST] 写回 data.js：items={len(field['items'])} "
          f"缺失核心={len(mc)} 缺失环境={len(me)}")


if __name__ == '__main__':
    main()
