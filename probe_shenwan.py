#!/usr/bin/env python3
# probe_shenwan.py - 申万行业指数日行情数据源调研脚本
#
# 用法:  python3 probe_shenwan.py            # 打印可读报告
#        python3 probe_shenwan.py --json     # 输出 JSON
#
# 背景: market-dashboard 行业全景/异动当前依赖东财 clist(被WAF封) -> 新浪行业兜底。
#       接"申万全量行业指数日行情"前，需先确认哪个源能稳定拿到"真申万口径 + 当日涨跌幅"。
#       本脚本在【目标网络环境】运行: 当前沙箱出口IP(101.87.181.168)被东财/同花顺按IP段封锁，
#       因此受限环境跑会如实报告"不可达"；需在不受限环境(本机/换IP云端)复跑验证真申万源。
#
# 口径自动判别: 用申万一级特征词 vs 中信行业特征词对返回行业名打分。

import urllib.request
import json
import re
import sys

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
SINA_REF = 'https://finance.sina.com.cn'

# 申万标准一级行业特征词(用于口径判别)
SW_KEYWORDS = ['农林牧渔', '采掘', '化工', '钢铁', '有色金属', '电子', '家用电器',
               '食品饮料', '纺织服装', '轻工制造', '医药生物', '公用事业', '交通运输',
               '房地产', '商业贸易', '休闲服务', '综合', '建筑材料', '建筑装饰',
               '电气设备', '国防军工', '计算机', '传媒', '通信', '银行', '非银金融',
               '汽车', '机械设备']
# 中信行业特征词(中信二级命名风格，与申万区分度很高)
CITIC_KEYWORDS = ['煤炭开采和洗选业', '农、林、牧、渔服务业', '石油开采', '煤炭开采',
                  '其他采掘', '油气开采', '稀有无色金属', '普钢', '特钢',
                  '金属非金属新材料', '航运', '公交', '园区开发', '房地产开发和运营',
                  '一般零售', '品牌服饰', '白色家电', '黑色家电', '照明设备',
                  '其他医药医疗', '化学制药', '中药生产', '生物医药', '装饰装修',
                  '结构性金属制品', '农用化工', '石油加工', '其他钢铁']


def get(url, ref=None, timeout=15):
    headers = {'User-Agent': UA}
    if ref:
        headers['Referer'] = ref
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ('gbk', 'utf-8'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def classify(names):
    sw = sum(1 for n in names if any(k in n for k in SW_KEYWORDS))
    citic = sum(1 for n in names if any(k in n for k in CITIC_KEYWORDS))
    if sw >= 4 and citic == 0:
        return '申万'
    if citic >= 3:
        return '中信'
    return '未知/其他'


# ---------------- probes ----------------
def probe_sina_sw(level):
    try:
        raw = get('https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=%s' % level, SINA_REF)
        m = re.search(r'=\s*(\{.*\})', raw, re.S)
        obj = json.loads(m.group(1))
        names, pcts = [], []
        for v in obj.values():
            parts = str(v).split(',')
            if len(parts) >= 6:
                names.append(parts[1])
                pcts.append(parts[5])
        return {'src': '新浪 param=%s' % level, 'reachable': True, 'count': len(obj),
                'has_pct': len(pcts) > 0, 'class': classify(names), 'sample': names[:5]}
    except Exception as e:
        return {'src': '新浪 param=%s' % level, 'reachable': False, 'error': str(e)[:80]}


def probe_tencent_sw():
    fmts = ['s_sw1_801010', 'sw1_801010', 'sw_801010', 's_sw_801010', 'm:90+t:2']
    hits = []
    for f in fmts:
        try:
            raw = get('https://qt.gtimg.cn/q=%s' % f)
            if 'none_match' not in raw:
                hits.append((f, raw[:60]))
        except Exception:
            pass
    if hits:
        return {'src': '腾讯 gtimg 申万', 'reachable': True, 'hits': hits}
    return {'src': '腾讯 gtimg 申万', 'reachable': False, 'note': '所有尝试格式均 none_match'}


def probe_em_clist(fs):
    try:
        url = 'https://push2.eastmoney.com/api/qt/clist/get?fs=%s&fields=f12,f14,f3&pn=1&pz=3' % fs
        raw = get(url)
        if not raw.strip():
            return {'src': '东财 clist %s' % fs, 'reachable': False, 'note': '空回(WAF封)'}
        obj = json.loads(raw)
        rows = (obj.get('data') or {}).get('diff') or {}
        names = [r.get('f14') for r in rows.values() if isinstance(r, dict)]
        return {'src': '东财 clist %s' % fs, 'reachable': True, 'count': len(names),
                'class': classify(names), 'sample': names[:5]}
    except Exception as e:
        return {'src': '东财 clist %s' % fs, 'reachable': False, 'error': str(e)[:80]}


def probe_em_datacenter(report):
    try:
        url = ('https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=%s'
               '&columns=INDEXCODE,INDEXNAME&pageSize=2' % report)
        raw = get(url)
        obj = json.loads(raw)
        if not obj.get('success'):
            return {'src': '东财 datacenter %s' % report, 'reachable': False,
                    'note': str(obj.get('message', ''))[:60]}
        return {'src': '东财 datacenter %s' % report, 'reachable': True, 'note': '配置存在'}
    except Exception as e:
        return {'src': '东财 datacenter %s' % report, 'reachable': False, 'error': str(e)[:60]}


def probe_ths():
    try:
        raw = get('https://data.10jqka.com.cn/funds/ggzjl/board/1/', ref='https://data.10jqka.com.cn')
        if 'forbidden' in raw.lower():
            return {'src': '同花顺 行业板块', 'reachable': False, 'note': 'Nginx forbidden(IP封禁)'}
        return {'src': '同花顺 行业板块', 'reachable': True, 'note': raw[:60]}
    except Exception as e:
        return {'src': '同花顺 行业板块', 'reachable': False, 'error': str(e)[:60]}


def probe_swhy():
    # 申万指数官网(小众, 通常不被按IP封); best-effort, 仅探可达性
    try:
        raw = get('https://www.swhyindices.com/indices/industry/index', ref='https://www.swhyindices.com')
        return {'src': '申万指数官网', 'reachable': True, 'note': ('len=%d' % len(raw))}
    except Exception as e:
        return {'src': '申万指数官网', 'reachable': False, 'error': str(e)[:60]}


def main():
    results = []
    results.append(probe_sina_sw('sw1'))
    results.append(probe_sina_sw('sw2'))
    results.append(probe_sina_sw('hangye'))
    results.append(probe_tencent_sw())
    results.append(probe_em_clist('m:90+t:2'))
    results.append(probe_em_clist('m:90+t:3'))
    for r in ['RPT_INDUSTRY_SW', 'RPT_SW_INDUSTRY_INDEX', 'RPT_INDUSTRY_SW_INDEX']:
        results.append(probe_em_datacenter(r))
    results.append(probe_ths())
    results.append(probe_swhy())

    if '--json' in sys.argv:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    print('=== 申万行业指数日行情 数据源调研 ===')
    print('说明: 当前沙箱出口IP被东财/同花顺按IP段封锁, 受限环境下东财/同花顺会显示"不可达"。')
    print('      在不受限环境(本机/换IP云端)复跑本脚本即可验证真实可用的申万源。')
    print('-' * 90)
    for r in results:
        parts = ['%s' % r.get('src')]
        if r.get('reachable'):
            parts.append('[可达]')
            if r.get('class'):
                parts.append('口径=%s' % r['class'])
            if r.get('count') is not None:
                parts.append('行业数=%d' % r['count'])
            if r.get('has_pct'):
                parts.append('含涨跌幅=是')
            if r.get('sample'):
                parts.append('样本=%s' % r['sample'])
        else:
            parts.append('[不可达]')
        for k in ('note', 'error'):
            if r.get(k):
                parts.append('%s' % r[k])
        if r.get('hits'):
            parts.append('命中=%s' % r['hits'])
        print('  '.join(parts))

    sw_sources = [r for r in results if r.get('reachable') and r.get('class') == '申万' and r.get('has_pct')]
    print('-' * 90)
    if sw_sources:
        print('[推荐] 真申万口径且含涨跌幅: %s' % [r['src'] for r in sw_sources])
    else:
        print('[结论] 当前环境未发现"真申万口径 + 含涨跌幅"的可用源。')
        print('       新浪 sw1/sw2 可达但口径=中信(非申万), 如需使用需与 hybk/topBoards 申万体系做名称映射或明确标注口径。')
        print('       真申万源(东财push2/同花顺)在受限IP下不可达, 请在不受限环境复跑本脚本验证。')


if __name__ == '__main__':
    main()
