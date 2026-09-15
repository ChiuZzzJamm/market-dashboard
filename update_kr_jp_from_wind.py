#!/usr/bin/env python3
"""
根据 /tmp/kr_jp_wind.json 更新 data.js 中的 panorama.kr / panorama.jp。
由 08:30（早盘）与 16:00（收盘）自动化调用：AI 负责用 Wind MCP 取数并把结果
写入 /tmp/kr_jp_wind.json，本脚本负责校验、计算板块合成、写回 data.js。
避免依赖 AI 直接修改 data.js 导致漏写或格式错误。

JSON 格式示例：
{
  "date": "2026-09-15",
  "label": "早盘",
  "indices": {
    "KS11.GI": {"name": "韩国综合指数", "changePct": 0.05},
    "N225.GI": {"name": "日经225", "changePct": 0.35}
  },
  "stocks": {
    "005930.KS": {"name": "三星电子", "changePct": 0.60},
    "000660.KS": {"name": "SK海力士", "changePct": 1.12},
    "9984.T": {"name": "软银集团", "changePct": 8.99},
    "8035.T": {"name": "东京电子", "changePct": 0.24},
    "285A.T": {"name": "铠侠", "changePct": 2.31}
  }
}
"""
import json, os, sys, argparse
from common import load_dashboard_data, find_node

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

JSON_PATH = '/tmp/kr_jp_wind.json'


def read_input(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f'{path} 不存在，需先用 Wind MCP 取数并写入该文件')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def today_cn():
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d')


def md(date_str):
    """2026-09-15 -> 9/15"""
    return f"{int(date_str[5:7])}/{int(date_str[8:10])}"


def validate(data):
    """校验 Wind 数据合理性；返回清洗后的 indices/stocks"""
    today = today_cn()
    d = data.get('date', today)
    label = data.get('label', '盘中')

    raw_indices = data.get('indices', {})
    raw_stocks = data.get('stocks', {})

    indices = {}
    for k, v in raw_indices.items():
        pct = v.get('changePct')
        if pct is None:
            continue
        indices[k] = {'name': v.get('name', k), 'changePct': round(float(pct), 2)}

    stocks = {}
    for k, v in raw_stocks.items():
        pct = v.get('changePct')
        if pct is None:
            continue
        stocks[k] = {'name': v.get('name', k), 'changePct': round(float(pct), 2)}

    # 日期校验：允许传入日期与今天相同或相邻（跨日边界）
    if d != today:
        print(f'[warn] 传入日期 {d} 与今天 {today} 不一致，可能为旧数据')

    # 方向一致性校验：指数 vs 对应龙头
    def same_direction(a, b):
        return (a >= 0) == (b >= 0)

    kr_idx = indices.get('KS11.GI', {}).get('changePct')
    kr_leaders = [stocks.get('005930.KS', {}).get('changePct'),
                  stocks.get('000660.KS', {}).get('changePct')]
    kr_leaders = [x for x in kr_leaders if x is not None]
    if kr_idx is not None and kr_leaders:
        matched = sum(1 for x in kr_leaders if same_direction(kr_idx, x))
        if matched / len(kr_leaders) < 0.5:
            print(f'[warn] KOSPI 方向 ({kr_idx:+.2f}%) 与韩股龙头 {kr_leaders} 严重背离，保留原值')
            return None, None, label

    jp_idx = indices.get('N225.GI', {}).get('changePct')
    jp_leaders = [stocks.get('9984.T', {}).get('changePct'),
                  stocks.get('8035.T', {}).get('changePct'),
                  stocks.get('285A.T', {}).get('changePct')]
    jp_leaders = [x for x in jp_leaders if x is not None]
    if jp_idx is not None and jp_leaders:
        matched = sum(1 for x in jp_leaders if same_direction(jp_idx, x))
        if matched / len(jp_leaders) < 0.5:
            print(f'[warn] 日经225 方向 ({jp_idx:+.2f}%) 与日经龙头 {jp_leaders} 严重背离，保留原值')
            return None, None, label

    # 异常值校验
    for k, v in list(indices.items()):
        if abs(v['changePct']) > 15:
            print(f'[warn] 指数 {k} 涨跌幅 {v["changePct"]}% 超过 15%，视为异常，丢弃')
            del indices[k]
    for k, v in list(stocks.items()):
        if abs(v['changePct']) > 20:
            print(f'[warn] 个股 {k} 涨跌幅 {v["changePct"]}% 超过 20%，视为异常，丢弃')
            del stocks[k]

    return indices, stocks, label


def build_kr(indices, stocks):
    kospi = indices.get('KS11.GI', {}).get('changePct')
    samsung = stocks.get('005930.KS', {}).get('changePct')
    skhynix = stocks.get('000660.KS', {}).get('changePct')

    items = []
    if samsung is not None and skhynix is not None:
        avg = round((samsung + skhynix) / 2, 2)
        items.append({
            'name': '半导体/存储',
            'pct': avg,
            'ref': f'SK海力士 {skhynix:+.2f}%、三星电子 {samsung:+.2f}%（2龙头均值）'
        })
    elif skhynix is not None:
        items.append({
            'name': '半导体/存储',
            'pct': skhynix,
            'ref': f'SK海力士 {skhynix:+.2f}%（单一龙头）'
        })
    elif samsung is not None:
        items.append({
            'name': '半导体/存储',
            'pct': samsung,
            'ref': f'三星电子 {samsung:+.2f}%（单一龙头）'
        })

    # KB金融当前不在 Wind 常规查询中；不显示固定历史数据，避免每天都是 -0.98% 的误导
    kb = stocks.get('KB금융지주') or stocks.get('105560.KS')
    if kb is not None:
        items.append({
            'name': '金融',
            'pct': kb['changePct'],
            'ref': f"KB金融 {kb['changePct']:+.2f}%（单一龙头）"
        })

    return {
        'key': 'kr',
        'name': '韩股',
        'date': 'PLACEHOLDER_DATE',
        'indices': [{'name': '韩国综合指数 KOSPI', 'changePct': kospi}] if kospi is not None else [],
        'items': items
    }


def build_jp(indices, stocks):
    n225 = indices.get('N225.GI', {}).get('changePct')
    softbank = stocks.get('9984.T', {}).get('changePct')
    tokyoelec = stocks.get('8035.T', {}).get('changePct')
    kioxia = stocks.get('285A.T', {}).get('changePct')

    items = []
    if softbank is not None:
        items.append({
            'name': 'AI/科技投资',
            'pct': softbank,
            'ref': f'软银集团 {softbank:+.2f}%（单一龙头）'
        })
    if tokyoelec is not None:
        items.append({
            'name': '半导体设备',
            'pct': tokyoelec,
            'ref': f'东京电子 {tokyoelec:+.2f}%（单一龙头）'
        })
    if kioxia is not None:
        items.append({
            'name': '存储',
            'pct': kioxia,
            'ref': f'铠侠 {kioxia:+.2f}%（单一龙头）'
        })

    return {
        'key': 'jp',
        'name': '日经',
        'date': 'PLACEHOLDER_DATE',
        'indices': [{'name': '日经225', 'changePct': n225}] if n225 is not None else [],
        'items': items
    }


def main():
    parser = argparse.ArgumentParser(description='更新 panorama.kr/jp 的 Wind 日韩数据')
    parser.add_argument('--label', choices=['早盘', '收盘', '盘中'], default=None,
                        help='数据时点标签；默认根据当前时间判断（16:00 后视为收盘，否则早盘）')
    parser.add_argument('--input', default=JSON_PATH, help='Wind JSON 输入路径')
    parser.add_argument('--dry-run', action='store_true', help='只打印，不写回 data.js')
    args = parser.parse_args()

    # 默认标签：北京时间 14:00 之后日韩已收盘，视为收盘；否则早盘
    if args.label is None:
        from datetime import datetime
        now = datetime.now()
        args.label = '收盘' if now.hour >= 14 else '早盘'

    try:
        data = read_input(args.input)
    except FileNotFoundError as e:
        print(f'[error] {e}')
        sys.exit(1)

    # 命令行标签优先于 JSON 中的 label
    if args.label:
        data['label'] = args.label

    indices, stocks, label = validate(data)
    if indices is None:
        print('[info] 数据校验未通过，保留 panorama.kr/jp 原值')
        sys.exit(0)

    D = load_dashboard_data(BASE)
    date_str = data.get('date', today_cn())
    date_display = f"{md(date_str)} {label}"

    kr = build_kr(indices, stocks)
    kr['date'] = date_display
    jp = build_jp(indices, stocks)
    jp['date'] = date_display

    # 替换 panorama.markets 中的 kr / jp
    markets = D.setdefault('panorama', {}).setdefault('markets', [])
    replaced = {'kr': False, 'jp': False}
    for i, m in enumerate(markets):
        if m.get('key') == 'kr':
            markets[i] = kr
            replaced['kr'] = True
        elif m.get('key') == 'jp':
            markets[i] = jp
            replaced['jp'] = True

    if not replaced['kr']:
        markets.append(kr)
    if not replaced['jp']:
        markets.append(jp)

    print(f'[info] panorama.kr/jp -> {date_display}')
    print(f'       KOSPI {indices.get("KS11.GI", {}).get("changePct"):+.2f}%, 日经225 {indices.get("N225.GI", {}).get("changePct"):+.2f}%')

    if args.dry_run:
        print('[dry-run] 不会写回 data.js')
        print(json.dumps({'kr': kr, 'jp': jp}, ensure_ascii=False, indent=2))
        return

    with open('data.js', 'w', encoding='utf-8') as f:
        f.write('window.DASHBOARD_DATA = ' + json.dumps(D, ensure_ascii=False, indent=2) + ';\n')
    print('[info] data.js 已更新')


if __name__ == '__main__':
    main()
