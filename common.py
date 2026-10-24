#!/usr/bin/env python3
"""看板脚本共用工具：定位 node、把 data.js 解析为 JSON。
供 push_notify.py 与 update_us_from_quotes.py 复用，避免重复实现。
"""
import os, shutil, glob, json, re, random, subprocess, time
import datetime as _dt


def http_get(url, timeout=15, retries=3, decode='utf-8'):
    """用 curl 抓取（urllib 会被部分源拒连）；失败返回 None。decode='gb2312' 用于腾讯行情。"""
    cmd = ['curl', '-s', '--max-time', str(timeout),
           '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)']
    for i in range(1, retries + 1):
        try:
            p = subprocess.run(cmd + [url], capture_output=True, timeout=timeout + 5)
        except Exception:
            p = None
        out = p.stdout if (p and p.returncode == 0) else b''
        if out.strip():
            return out.decode(decode, errors='replace')
        if i < retries:
            time.sleep(2)
    return None


def fetch_names(codes, timeout=8):
    """腾讯行情批量取股票名 → {code: name}（R100z167）。

    背景：powerScreen.failPool 里出现过 4 条 `name=空串`（600860/603009/603418/603179），
    前端失效池渲染成空行 —— 根因是 `collect_pool_names` 的 code→name 反查只覆盖
    「池内 + stkKlineNames」标的，九门/吸筹扩展扫描域里的标的天然查不到名。
    这里用腾讯行情（GBK，60 只/批，实测 75/80 命中）补一次，失败返回已取到的部分，
    调用方负责「取不到就降级」——绝不写空串进 data.js。
    """
    out = {}
    cs = [str(c).strip() for c in (codes or []) if str(c).strip()]
    for i in range(0, len(cs), 60):
        chunk = cs[i:i + 60]
        q = ','.join(('sh' if c.startswith(('6', '9')) else 'sz') + c for c in chunk)
        txt = http_get('https://qt.gtimg.cn/q=' + q, timeout=timeout, retries=1, decode='gb18030')
        if not txt:
            continue
        for line in txt.split(';'):
            if '="' not in line:
                continue
            try:
                f = line.split('="', 1)[1].rstrip('"').split('~')
                code, nm = f[2].strip(), f[1].strip()
                if code and nm:
                    out[code] = nm
            except Exception:                                # noqa: BLE001
                continue
    return out


def find_node():
    """优先 PATH，其次 workbuddy 管理的多版本目录，最后兜底系统路径。"""
    p = shutil.which('node')
    if p:
        return p
    cands = sorted(glob.glob('/Users/loccco/.workbuddy/binaries/node/versions/*/bin/node'))
    if cands:
        return cands[-1]
    for c in ['/usr/local/bin/node', '/usr/bin/node']:
        if os.path.exists(c):
            return c
    raise RuntimeError('node not found in PATH or known locations')


# ---------- 美股交易日历（R100z167：从 update_us_from_quotes.py 下沉，单一来源） ----------
# 原表只活在 07:00 的取数脚本里，audit G10 想核对「美股最新收盘日」只能另抄一份 —— 
# 这正是 G13（A股节假日表两份手工副本）踩过的坑，故此处收口：谁需要就 import。
def _nth_weekday(year, month, weekday, n):
    """返回 year 年 month 月第 n 个 weekday（0=周一…6=周日）的日期"""
    d = _dt.datetime(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d = d + _dt.timedelta(days=offset) + _dt.timedelta(days=7 * (n - 1))
    return d.date()


def _last_weekday(year, month, weekday):
    if month == 12:
        nxt = _dt.datetime(year + 1, 1, 1)
    else:
        nxt = _dt.datetime(year, month + 1, 1)
    d = nxt - _dt.timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return (d - _dt.timedelta(days=offset)).date()


def _easter(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return _dt.datetime(year, month, day).date()


def us_holidays(year):
    """返回该年度美股休市日期集合（NYSE 规则，含周末顺延）"""
    h = set()
    jan1 = _dt.datetime(year, 1, 1).date()
    if jan1.weekday() == 5:
        h.add(jan1 - _dt.timedelta(days=1))
    elif jan1.weekday() == 6:
        h.add(jan1 + _dt.timedelta(days=1))
    else:
        h.add(jan1)
    h.add(_nth_weekday(year, 1, 0, 3))              # 马丁路德金日（1月第3周一）
    h.add(_nth_weekday(year, 2, 0, 3))              # 总统日（2月第3周一）
    h.add(_easter(year) - _dt.timedelta(days=2))        # 耶稣受难日
    h.add(_last_weekday(year, 5, 0))                # 阵亡将士纪念日（5月最后周一）
    j6 = _dt.datetime(year, 6, 19).date()               # 六月节
    if j6.weekday() == 5:
        h.add(j6 - _dt.timedelta(days=1))
    elif j6.weekday() == 6:
        h.add(j6 + _dt.timedelta(days=1))
    else:
        h.add(j6)
    j4 = _dt.datetime(year, 7, 4).date()                # 独立日
    if j4.weekday() == 5:
        h.add(j4 - _dt.timedelta(days=1))
    elif j4.weekday() == 6:
        h.add(j4 + _dt.timedelta(days=1))
    else:
        h.add(j4)
    h.add(_nth_weekday(year, 9, 0, 1))              # 劳动节（9月第1周一）
    h.add(_nth_weekday(year, 11, 3, 4))             # 感恩节（11月第4周四）
    d25 = _dt.datetime(year, 12, 25).date()             # 圣诞
    if d25.weekday() == 5:
        h.add(d25 - _dt.timedelta(days=1))
    elif d25.weekday() == 6:
        h.add(d25 + _dt.timedelta(days=1))
    else:
        h.add(d25)
    return h


_HOLIDAYS = {}
def _holidays_for(year):
    if year not in _HOLIDAYS:
        _HOLIDAYS[year] = us_holidays(year)
        _HOLIDAYS[year + 1] = us_holidays(year + 1)
    return _HOLIDAYS[year]


def is_us_trading_day(d):
    if d.weekday() >= 5:
        return False
    return d not in _holidays_for(d.year)


def prior_us_trading_day(from_date):
    """返回 from_date 之前（不含 from_date 当天）最近的一个美股交易日"""
    d = from_date - _dt.timedelta(days=1)
    for _ in range(14):
        if is_us_trading_day(d):
            return d
        d -= _dt.timedelta(days=1)
    return None


# ---------- R100z173/z174：deduce 文本自前缀清洗 ----------
# 背景：外层固定补「主推：/反证：」标签，AI 却常自己再写一层，实测三种变体：
#   ① `反证：…`      （R99o 已拦）
#   ② `反证是…`      （R100z173，无冒号，19 条）
#   ③ `反证在资金：…`（R100z174，带标记词+冒号，页面显示「反证：反证在资金：…」）
# 约束：「反证/主推」后面**必须**有分隔/标记才剥，绝不裸剥二字——否则
# 「反证成立」「主推逻辑不成立」这类正常句会被削成「成立」「逻辑不成立」。
# 唯一例外是 ③ 的「主推+宾语」裸拼（如「主推资金避险切换至…」），用零宽断言**只吃
# 「主推」两字不吃宾语**，白名单里刻意排除「逻辑」——「主推逻辑不成立」剥了会反转语义。
# 与 index.html 的 _ddClean()、fix_dd_prefix.py 的 PAT 三处同口径，改动必须同步（G31 校验）。
# 唯一例外：这几个词前面的「反证/主推」是**谓语**（「反证成立」「主推不成立」），
# 剥掉会变成「反证：成立」，句子就废了。除此之外「主推/反证」出现在值开头一律剥——
# 因为外层无条件补同名标签，内层留着必然重复（「反证证券跌1.21%」→「反证：反证证券跌」）。
_DD_LABELS = r'(?:资金面?|逻辑|基本面|业绩|估值|情绪|技术面?|板块|消息面|催化|盘面|量能|价格|供给|需求|数据|行业|政策|节奏|筹码|事实|现实)'
_DD_KEEP = r'(?:成立|不成立|失效|已失效|站得住|证伪|已被证伪|并未?成立|难以?成立)'
_DD_PREFIX_RE = re.compile(
    r'^[\s\u3000\u200b]*(?:'
    r'(?:主推|反证)\s*在(?:于)?\s*' + _DD_LABELS + r'?\s*[:：]'   # 反证在资金：→ 连标记词整段剥
    r'|(?:主推|反证)\s*(?:[:：]|是)'                              # 反证：/ 反证是
    r'|(?:主推|反证)(?!\s*' + _DD_KEEP + r')'                    # 其余裸拼 → 只剥标签二字
    r')\s*'
)


def strip_dd_prefix(t, rounds=4):
    """剥掉 deduce 文本自带的「主推：/反证：/主推是/反证是」前缀（循环剥，防叠标签）。"""
    s = str(t or '')
    for _ in range(rounds):
        before = s
        s = _DD_PREFIX_RE.sub('', s.strip())
        if s == before:
            break
    return s.strip()


def int_ify(o):
    """把「值一定是整数」的 float 写成 int —— 纯序列化瘦身，不动数值（R100z167）。

    实测 2026-10-24 的 data.js：14 万个 `123.0`（成交量/股数最常见），每个白占 2 字节
    = 274 KB。JS 里 `123.0 === 123` 且 JSON.parse 后两者同为 number，
    所以前端读到的值逐位相同 —— 这是**唯一一处能改却绝对不改语义**的瘦身点。

    判据极窄：只处理 `o.is_integer()` 且在 2^53 内（超了 int 会失真，原样返回）。
    非整数浮点一律原样返回，不四舍五入 —— 精度归数据源负责，这里不碰。
    """
    if isinstance(o, float):
        if o.is_integer() and -9007199254740992 <= o <= 9007199254740992:
            return int(o)
        return o
    if isinstance(o, dict):
        return {k: int_ify(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [int_ify(v) for v in o]
    return o


def write_data_js(path_or_base, D, label=''):
    """原子写 data.js —— 全项目唯一写盘入口（R100z179b）。

    为什么收口：硬约束 11 写的是「必须 `_tmp`+`os.replace`」，但一直是**每个脚本各写一遍**，
    于是「谁漏了」只能靠人看。2026-10-23 全量扫出来 3 个漏的（`macro_calendar` /
    `strategy_engine` / `sweep_news`），其中前两个就在 deploy.sh 的链条中间 ——
    进程被杀即 0 字节 data.js，全站白屏、日志一行字、闸门全绿。
    现在所有新老写者都可以只调这一个函数，`check_code.py` 的 K3 会反过来盯「谁没调」。

    参数：path_or_base 传 base 目录或 data.js 路径都行（历史上两种写法都有）。
    校验：落盘前用 `node --check` 验语法（data.js 是 JS 包裹，JSON 合法不代表 JS 合法）；
    临时名必须是 `*.tmp.js` —— Node 22 对未知扩展名直接 `ERR_UNKNOWN_FILE_EXTENSION`，
    写成 `*.tmp` 会变成「每天静默失败、永不落盘」（24.28e 实测）。
    """
    path = path_or_base
    if os.path.isdir(path) or not path.endswith('.js'):
        path = os.path.join(path, 'data.js')
    out = ('window.DASHBOARD_DATA = '
           + json.dumps(int_ify(D), ensure_ascii=False, separators=(',', ':')) + ';\n')
    _tmp = path + '.tmp.js'
    with open(_tmp, 'w', encoding='utf-8') as f:
        f.write(out)
    try:
        node = find_node()
    except Exception as e:                      # node 缺失：显式降级，不静默（不阻断部署链）
        node = None
        print('[WARN] %s node 不可用（%s），跳过语法校验' % (label or '[write_data_js]', e))
    if node:
        try:
            p = subprocess.run([node, '--check', _tmp], capture_output=True, text=True)
        except Exception as e:
            p = None
            print('[WARN] %s node 调用失败（%s），跳过语法校验' % (label or '[write_data_js]', e))
        if p is not None and p.returncode != 0:
            os.remove(_tmp)
            raise RuntimeError('%s data.js 语法校验失败（未落盘）：%s'
                               % (label or '[write_data_js]', (p.stderr or '')[:300]))
    os.replace(_tmp, path)
    return path


def write_json_atomic(path, obj, label='', indent=1):
    """原子写「关键 JSON 状态文件」（R100z179c）。

    为什么也要收口：硬约束 11 只点了 `data.js` / `stock_pool.json`，但 24.29 全量扫发现
    `calibration_state.json` / `portfolio_curve.json` / `calibration_log.json` /
    `strategy_feedback.json` 共 5 处仍是裸 `open(...,'w')`。它们被杀不会白屏，却会变成
    **静默降级**：`_cal_factor()` 读到半截 JSON → 走 except → 用默认阈值 1.0 继续跑，
    于是「后验校准」这个功能安静地不再生效 —— 比白屏更难发现。
    纯 JSON 不需要 `node --check`（`json.dump` 产物本身就是合法 JSON），`os.replace` 即足够。
    """
    _tmp = path + '.tmp'
    try:
        with open(_tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
        os.replace(_tmp, path)
    except Exception:
        if os.path.exists(_tmp):
            try:
                os.remove(_tmp)
            except OSError:
                pass
        raise
    return path


def load_dashboard_data(base):
    """把 data.js 解析为 Python dict。

    R100z156：优先用 Python json.loads 直接解析文本——完整保留 int/float 字面量，
    加载→写回可做到字节级不变（实测 6.25MB 文件往返完全一致）。旧实现一律走
    node eval 通道，JS 的 JSON.stringify 会把 164437075.0 归一成 164437075，
    凡「读-改-写」一次就整文件重排：2026-10-20 08:30 的 update_kr_jp 只改
    panorama.kr/jp，却因这一层产生 23 万字符的无意义 diff（把真改动全淹了）。
    仅当文本不是纯 JSON（历史遗留 JS 字面量 / NaN / Infinity）时才回退 node 通道，
    回退路径保持与旧实现完全一致，不改变任何调用方语义。
    """
    try:
        with open(os.path.join(base, "data.js"), encoding="utf-8") as f:
            s = f.read()
        m = re.search(r"window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$", s)
        if m:
            return json.loads(m.group(1))
    except Exception:
        pass  # 落到 node 通道；两条路都失败时由 node 通道抛 RuntimeError
    node = find_node()
    node_src = r'''
const fs = require('fs');
let s = fs.readFileSync('data.js','utf8');
let m = s.match(/window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!m) { console.error('data.js parse failed'); process.exit(1); }
process.stdout.write(JSON.stringify(eval('(' + m[1] + ')')));
'''
    p = subprocess.run([node, '-e', node_src], capture_output=True, text=True, cwd=base)
    if p.returncode != 0:
        raise RuntimeError('data.js parse failed: ' + p.stderr)
    return json.loads(p.stdout)


# ---------- R100z44：节假日感知的交易日计数（失效池「自失效日起保留 5 个交易日」窗口专用） ----------
# 与 cal_factor.HOLIDAYS 同源（★年度维护项：跨年须对照国务院放假通知同步更新两处）。
# 原 _trading_days_between 只数周一~周五，国庆/春节这类长假会被数成交易日，
# 导致节后首日运行时失效池被提前清空（09-30 归档到 10-08 会被数成 8「交易日」>5 而误删）。
# 变量名已去年份化（R100z77）：表内可同时持有多年的休市区间，避免「2026 名为表却装 2027」的口径漂移。
HOLIDAY_RANGES = [
    ('2026-01-01', '2026-01-03'),  # 元旦
    ('2026-02-15', '2026-02-21'),  # 春节
    ('2026-04-04', '2026-04-06'),  # 清明
    ('2026-05-01', '2026-05-05'),  # 劳动节
    ('2026-06-19', '2026-06-21'),  # 端午
    ('2026-09-25', '2026-09-27'),  # 中秋
    ('2026-10-01', '2026-10-07'),  # 国庆（10-08 起照常开市，交易所公告）
    # ---- 2027 年度（R100z77 补）：元旦/清明/劳动/国庆为法定固定，直接按放假办法填；
    #      春节/端午/中秋为农历，须待国务院放假通知公布后补（届时本闸门会重新 WARN）----
    ('2027-01-01', '2027-01-03'),  # 元旦（2027-01-01 周五，连休3天）
    ('2027-04-04', '2027-04-06'),  # 清明（2027-04-05 为清明，连休3天）
    ('2027-05-01', '2027-05-05'),  # 劳动节（固定5天）
    ('2027-10-01', '2027-10-07'),  # 国庆（固定7天）
]


def is_trade_day(d):
    """d 为 datetime.date；周一~周五且不在法定节假日区间内。"""
    if d.weekday() >= 5:
        return False
    s = d.strftime('%Y-%m-%d')
    for a, b in HOLIDAY_RANGES:
        if a <= s <= b:
            return False
    return True


def last_trade_day(d):
    """把任意日期回退到「不晚于它」的最近交易日（d 本身是交易日则原样返回）。

    ⚠️ R100z57：生成侧绝不能再拿 `datetime.date.today()` 当交易日写 tradeDate/entryDate。
    长假/周末跑一次（手工补跑、自动化误触发、跨时区）就会把一个**不存在的交易日**
    印进模块标题、写进 entryDate，直接后果是：
      · 页面标题出现「周六收盘 / 国庆休市日收盘」这类假日期；
      · entryDate === modDate → 全池标的当天集体挂「新」徽章（整屏刷黄）。
    d 支持 'YYYY-MM-DD' 字符串或 datetime.date。
    """
    import datetime as _dt
    cur = d if isinstance(d, _dt.date) else _dt.datetime.strptime(str(d)[:10], '%Y-%m-%d').date()
    for _ in range(15):  # 最多回退两周，跨长假足够
        if is_trade_day(cur):
            return cur.strftime('%Y-%m-%d')
        cur -= _dt.timedelta(days=1)
    return cur.strftime('%Y-%m-%d')


def today_trade_date():
    """今天对应的交易日——今天休市（周末/法定假日）就回退到最近一个交易日。"""
    # R100z64 修复：本文件顶层是 `import datetime as _dt`（R100z57 为免函数内 import 遮蔽
    # 模块级命名而改的），这里原先却写 `datetime.date.today()` → NameError。
    # 该函数被 harmonic_detect / power_screener 当主路径日期口径调用，而 10-04 改完后
    # 恰逢国庆休市没跑过一次，错误一直休眠，10-08 复牌首日会直接崩掉 A 股流水线。
    return last_trade_day(_dt.date.today())


def trading_days_between(d0, d1):
    """[d0, d1] 闭区间内的真实交易日数（含两端，节假日感知）。d0/d1 为 'YYYY-MM-DD'。"""
    import datetime as _dt
    a = _dt.datetime.strptime(d0, '%Y-%m-%d').date()
    b = _dt.datetime.strptime(d1, '%Y-%m-%d').date()
    if b < a:
        a, b = b, a
    n, cur, one = 0, a, _dt.timedelta(days=1)
    while cur <= b:
        if is_trade_day(cur):
            n += 1
        cur += one
    return n


def roll_fail_pool(old_fail, new_entries, today, keep_days=5, exclude_codes=None,
                   name_map=None):
    """失效归档池滚动窗口（R100z56，用户 2026-10-04 要求「失效当天入池、5 个交易日后剔除」）。

    - 保留：自 entryDate 起 keep_days 个交易日内的旧归档 + 今日新失效（同 code 以新条目为准）；
    - 剔除：trading_days_between(entryDate, today) > keep_days 的超期归档；
    - 剔除：当前已在「在池」集合里的 code（exclude_codes，R100z120）。
      ⚠️ 口径修正（用户 2026-10-12 截图反馈「同一标的同时挂确认池与失效池」）：
      旧归档滚动保留 5 日，若某票先失效归档、之后又重新达标回到在池（九门/吸筹逐日重算、
      断板重走形态），旧归档仍会留着 → 同一标的既在确认池又在失效池。失效池语义 =
      「已失效 **且当前不在池**」，故在池 code 一律从失效池移除。
    - ⚠️ 一律用上面节假日感知的 trading_days_between，不许自己数周一~周五
      （国庆/春节会被数成交易日，节后首日跑就把窗口内归档提前清空了）。
    - name_map（可选 code→name）：补空名字用；补不到的条目直接丢弃（R100z167）。
    返回新列表（不改动入参）。old_fail/new_entries 元素均为 dict，非 dict 一律跳过。
    """
    excl = {str(c) for c in (exclude_codes or set()) if str(c)}
    out = []
    for f in (old_fail or []):
        if not isinstance(f, dict):
            continue
        if excl and str(f.get('code')) in excl:
            continue  # R100z120：当前已在池 → 不进失效池
        ed = f.get('entryDate')
        try:
            if ed and trading_days_between(ed, today) > keep_days:
                continue
        except Exception:
            pass  # 日期格式异常：保守保留，宁多不删
        out.append(f)
    # R100z122（2026-10-13 体检发现的历史真 bug）：原写法
    #   new_codes = {x.code for x in new_entries}
    #   out += [x for x in new_entries if x.code not in new_codes]
    # 里 new_codes 就是 new_entries 自己的 code 全集 → 过滤条件恒 False → new_entries
    # **永远被丢弃**（九门/吸筹「本轮新失效」从未真正进过失效池，R100z56 起潜伏）。
    # 正确口径（与 docstring「同 code 以新条目为准」一致）：carried 先剔除将被新条目覆盖的 code，
    # 再整体并入 new_entries。new_fails 的 code 由调用方保证 ∉ 在池集合（≠exclude_codes），无需再滤。
    new_list = [x for x in (new_entries or []) if isinstance(x, dict)]
    if new_list:
        new_codes = {str(x.get('code')) for x in new_list}
        out = [f for f in out if str(f.get('code')) not in new_codes]
        out += new_list

    # R100z167：失效池空名字治理（用户截图实报的空行根因）。
    # 旧归档 name 可能是空串（源头查名失败），前端照渲染成空行。这里统一：
    #   ① name 空 → 用 name_map（code→name）补；② 补不到的整条丢弃（无名归档对读者零信息）。
    # 丢弃是「宁缺毋滥」：失效池是归档展示位，缺一条不影响任何硬约束。
    fixed = []
    for f in out:
        nm = str(f.get('name') or '').strip()
        if not nm and name_map:
            nm = str(name_map.get(str(f.get('code'))) or '').strip()
        if not nm:
            continue
        if nm != f.get('name'):
            f = dict(f, name=nm)
        fixed.append(f)
    return fixed


# ---------- R100z6: 同花顺抓取统一收口 ----------
# 原 update_ashare_sectors.py（curl_ths_url/curl_ths/parse_ths_industries/fetch_ths_industries/curl_ths_json）
# 与 check_duanban.py（curl_ths_text/curl_ths_json/parse_ths_industries/fetch_ths_industries）双副本合一，
# 避免再出现「修了 A 副本漏了 B 副本」的口径漂移（R100z5d 90 行业分页即此教训）。
THS_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
THS_URL = "https://q.10jqka.com.cn/thshy/"
THS_REFERER = "https://q.10jqka.com.cn/"
THS_DATA_REFERER = "https://data.10jqka.com.cn/"
THS_DATACENTER_REFERER = "https://data.10jqka.com.cn/datacenterph/limitup/limtupInfo.html"


def ths_get(url, timeout=20):
    """同花顺页面抓取：curl + UA + Referer + Accept-Language。返回 bytes（失败 b""）。"""
    try:
        r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                            "-H", "User-Agent: " + THS_UA,
                            "-H", "Referer: " + THS_REFERER,
                            "-H", "Accept-Language: zh-CN,zh;q=0.9",
                            url], capture_output=True, timeout=timeout + 10)
    except Exception:
        return b""
    return r.stdout or b""


def ths_json(url, timeout=15, referer=THS_DATA_REFERER):
    """同花顺 JSON 接口：curl + Referer，失败返回 None。utf-8 失败回退 gbk 解码。"""
    try:
        r = subprocess.run(["curl", "-s", "--max-time", str(timeout),
                            "-H", "User-Agent: " + THS_UA,
                            "-H", "Referer: " + referer, url],
                           capture_output=True, timeout=timeout + 10)
    except Exception:
        return None
    out = r.stdout or b""
    if isinstance(out, (bytes, bytearray)):
        try:
            out = out.decode("utf-8")
        except UnicodeDecodeError:
            out = out.decode("gbk", errors="replace")
    if not isinstance(out, str) or not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def parse_ths_industries(html):
    """解析同花顺行业一览表。返回 [{code,name,pct,netInflow,up,down,lead,leadPct}]；失败/不足返回 []。
    列顺序（相对行业名所在列 ni）：ni+1 涨跌幅 / ni+4 净流入(亿) / ni+5 上涨家数 / ni+6 下跌家数
        / ni+8 领涨股 / ni+10 领涨股涨跌幅。涨跌幅做 [-15,15] 合理性校验防列偏移错位。
    （duanban 只用 code/name/pct 子集，兼容。）"""
    if isinstance(html, (bytes, bytearray)):
        # 同花顺行业页为 GBK 编码，utf-8 直接解会乱码；先试 utf-8 失败回退 gbk
        try:
            text = html.decode("utf-8")
        except UnicodeDecodeError:
            text = html.decode("gbk", errors="replace")
    else:
        text = str(html)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S)
    out = []
    for row in rows:
        m = re.search(r'thshy/detail/code/(\d+)[^>]*>([^<]+)</a>', row)
        if not m:
            continue
        code, name = m.group(1), m.group(2).strip()
        if not name:
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        tds = [re.sub(r"<[^>]+>", "", t).strip() for t in tds]
        ni = None
        for i, t in enumerate(tds):
            if t == name:
                ni = i
                break
        if ni is None or ni + 10 >= len(tds):
            continue
        def num(s):
            s = s.replace("%", "").replace("亿", "").replace("万", "").replace(",", "")
            try:
                return float(s)
            except Exception:
                return None
        pct = num(tds[ni + 1])
        if pct is None or pct < -15 or pct > 15:
            continue
        def i2(s):
            v = num(s)
            return int(v) if v is not None else None
        out.append({
            "code": code, "name": name, "pct": round(pct, 2),
            "netInflow": num(tds[ni + 4]),
            "up": i2(tds[ni + 5]),
            "down": i2(tds[ni + 6]),
            "lead": (tds[ni + 8] if ni + 8 < len(tds) else None),
            "leadPct": num(tds[ni + 10]),
        })
    return out


def fetch_ths_industries():
    """同花顺行业一览表分页抓全（第1页50 + 第2页40 = 90 行业；R98h/R100z5d 合一）。
    第2页起用非 ajax 整页 URL（/thshy/index/page/N/；ajax/1/ 会触发反爬跳转）。
    按 code 去重合并；第 1 页 <10 个视为异常原样返回（调用方自行走备源/兜底）。"""
    seen, out = set(), []
    for x in parse_ths_industries(ths_get(THS_URL)):
        if x["code"] not in seen:
            seen.add(x["code"])
            out.append(x)
    if len(out) < 10:
        return out
    for page in range(2, 4):  # 最多抓到第3页防死循环
        time.sleep(1.0 + random.random())
        rows = parse_ths_industries(ths_get(f"https://q.10jqka.com.cn/thshy/index/page/{page}/"))
        fresh = [x for x in rows if x["code"] not in seen]
        if not fresh:
            break
        for x in fresh:
            seen.add(x["code"])
            out.append(x)
        if len(rows) < 10:  # 不足一页说明已到尾页
            break
    return out
