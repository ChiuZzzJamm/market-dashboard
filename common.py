#!/usr/bin/env python3
"""看板脚本共用工具：定位 node、把 data.js 解析为 JSON。
供 push_notify.py 与 update_us_from_quotes.py 复用，避免重复实现。
"""
import os, shutil, glob, json, re, random, subprocess, sys, time
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


def purge_tmp(path):
    """把不再需要的临时文件**原地清空**，而不是删掉（R100z285）。

    为什么不删：自动化沙箱把 `os.remove` 接管成「移到废纸篓」（不报错、也不真删）。
    于是「清理半成品」这个动作本身变成垃圾制造机——一次 node --check 失败就往废纸篓
    塞一个几百 KB 的 data.js 副本。truncate 到 0 字节能把空间真还回来，语义也等价：
    这些 tmp 下次都被**同名覆盖写**，根本不需要「消失」。

    任何异常吞掉：保洁动作绝不能影响主流程（调用点本来就在 raise / except 里）。
    """
    try:
        os.truncate(path, 0)
    except OSError:
        pass


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
            purge_tmp(_tmp)
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
        # R100z285：原来这里 `if exists: os.remove` —— 沙箱把 remove 变废纸篓。
        purge_tmp(_tmp)
        raise
    return path


def load_json_quarantine(path, default=None, tag=''):
    """读 JSON；文件存在但解析失败时把坏文件改名留档 + 打 warn，返回 default。

    R100z273：四个模块的 `load_ledger()` 原来一律 `except: return 默认空`，而它们随后都用
    `save_ledger()` **整份覆盖写**。于是「文件写坏一次」＝「台账历史全丢，第二天起像个新账本」，
    而且整个过程不报错——台账是这些模块的记忆锚（入池日 / 放行记录 / 校准样本 / 止损轨迹），
    丢一次不会白屏，只会让之后所有结论悄悄偏掉（24.110 的同一类病：空着＝有结论没写）。

    这里不做「猜数据修回来」：改名留档，让人能从 .bad-<ts> 里恢复；本次按空账本继续，
    保证当次不被阻塞。纯缓存文件（K线/新闻/回测）不要用这个——它们坏了本来就该重抓，
    改名只会攒垃圾，直接用 try/except 返回 None 即可。
    """
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        bad = '%s.bad-%d' % (path, int(time.time()))
        try:
            os.replace(path, bad)
            note = '坏文件已改名留档 %s' % os.path.basename(bad)
        except OSError as e2:
            note = '坏文件改名也失败（%s），下次仍会读到它' % str(e2)[:60]
        print('[warn] %s 读取失败（%s）：%s；本次按空账本继续，历史请从留档恢复'
              % (tag or os.path.basename(path), str(e)[:80], note), file=sys.stderr)
        return default


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
    ('2026-02-16', '2026-02-23'),  # 春节（★R100z288 按深交所官方日历修正，原 02-15~02-21 漏 02-23）
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


def board_life(b):
    """板块生命周期（R100z310，2026-11-08 用户：「板块异动往往是涨好了才更新，马后炮」）。

    输入 `ashare.allBoards` 的一条：`pct`（今日）/ `pct5`（5 日累计）/ `ztCount`（板块内涨停数）
    / `upCount` `downCount`（扩散度）/ `netInflow`。
    输出 `{label, day, score, diffuse, fake, why}`——**score 用于排序，不用于胜率**。

    判据为什么用「5 日累计 vs 今日」而不是只今日涨幅：同样涨 2%，D1（今天才动）与 D6（涨了一周）
    的含义完全相反——前者是资金刚进来、后者是接力尾声。看板原来按今日 pct 降序，
    等于把「已经涨完的」排在最前，这就是用户说的马后炮。

    ⚠️ 参数是可回测的假设，不是定论：门槛写在返回值里（why），回测跑完按实测改。
    """
    if not isinstance(b, dict):
        return {"label": "未知", "day": None, "score": 40, "diffuse": None,
                "fake": False, "why": "无板块数据"}
    pct = _fnum(b.get("pct"))
    pct5 = _fnum(b.get("pct5"))
    zt = b.get("ztCount")
    zt = int(zt) if isinstance(zt, (int, float)) else 0
    up, down = _fnum(b.get("upCount")), _fnum(b.get("downCount"))
    diffuse = round(up / (up + down), 2) if (up is not None and down and (up + down) > 0) else None
    if pct is None:
        return {"label": "未知", "day": None, "score": 40, "diffuse": diffuse,
                "fake": False, "why": "今日涨幅缺失"}
    if pct5 is None:
        pct5 = pct
    # 假板块：涨幅靠一只票拉起来（板块内没涨停、上涨家数不过半）
    fake = bool(pct >= 1.5 and zt == 0 and (diffuse is None or diffuse < 0.6))
    if pct5 > 5 and pct < -0.5:
        label, day, score = "退潮", None, 0
    elif pct5 >= 15:
        label, day, score = "高位", None, 30
    elif pct > 0 and pct5 <= max(4.0, pct * 2.0) and (zt >= 1 or pct >= 2.5):
        # ⚠️ R100z311：「启动第几天」目前**算不出来**，所以 day 一律 None。
        # 原先固定写 day=1（启动）/ day=2（主升），前端显示成「启动 D1」「主升 D2」——
        # 那是**假精度**：它只是档位代号，不是真的连续上涨天数。真算需要板块日 K
        # （`sectorsUp[].kline` 有 30 根，但 `allBoards` 90 个板块没有），
        # 拿不到就别印数字——印了就等于告诉用户「这是第 1 天」，而实际可能是第 3 天。
        label, day, score = "启动", None, 100
    elif pct > 0 and pct5 <= 15 and zt >= 1:
        # 「主升」必须有涨停确认：2026-11-08 实测「种植业与林业 今日+1.67% / 涨停0只」
        # 也被判成主升——板块里一只板都没有，那是温和上行，不是主升（接力资金没进场）。
        label, day, score = "主升", None, 70
    elif pct > 0:
        # 实测「温和」（涨但板块内一只涨停都没有）胜率只有 61.5%，是全样本最差的一档——
        # 分数从 55 降到 40：涨 ≠ 有资金，没有涨停就没有接力盘。
        label, day, score = "温和", None, 40
    elif pct <= 0 and pct5 > 0:
        # 「今天跌、5 日还在涨」＝板块涨了一段今天歇一脚（缩量回踩）。
        # R100z310 回测（7 条样本）：这一档胜率只有 42.9%——买在下跌中继，不是买在启动。
        # ⚠️ 7 条样本的 95% 区间约 15%~75%，**不足以定论**，分数只略低于中位。
        label, day, score = "回踩", None, 48
    elif pct <= 0:
        # 「今天跌、5 日也在跌」＝板块阴跌。R100z310 回测（13 条样本）：胜率 92.3%，最好的一档。
        # 反直觉但成立：板块在跌、这只票还能走完断板反包全流程 → 说明它是**逆势强势股**，
        # 池子的形态筛选已经替我们做了「个股强度」这件事，板块跌反而是筛选器。
        # ⚠️ 同样是小样本（13 条，区间约 66%~99%），分数只略高于中位，不给极端值。
        label, day, score = "阴跌", None, 55
    else:
        label, day, score = "观察", None, 50
    if diffuse is not None:
        score = score * (0.6 + 0.4 * diffuse)      # 扩散度越低越像「一只票的行情」
    if fake:
        score *= 0.5
    why = "今日%+.2f%% / 5日%+.2f%% / 涨停%d只 / 扩散%s" % (
        pct, pct5, zt, ("%.0f%%" % (diffuse * 100)) if diffuse is not None else "—")
    if fake:
        why += "（假板块：无涨停且不过半）"
    return {"label": label, "day": day, "score": round(score, 1),
            "diffuse": diffuse, "fake": fake, "why": why}


def theme_words(reason, limit=3):
    """从连板梯队的 `reason` 里抽**题材词**（去掉业绩类）。

    R100z313（用户 2026-11-09 00:13：「浙文影业的连板难道不是 AI 短剧的原因吗？
    和纺织有什么关系？？？」）。

    实测：浙文影业 `hybk=纺织制造`、`reason=AI漫剧+微短剧+三季报增长`——同花顺行业
    还是它转型前的「鹿港文化·纺织」，**行业分类跟不上题材**。同类还有：
    闽发铝业（工业金属 / 光伏边框+液冷铝材）、润建股份（通信服务 / 词元出海+算力网络）、
    阳光照明（照明设备 / 商业航天+激光通信）。

    ⛔ **行业 ≠ 题材，这不是个别错标，是静态分类的系统性滞后。**
    所以连板徽章必须显示题材词，而不是只显示行业名——否则读者会以为「纺织制造有连板」
    就代表纺织在炒，进而误判同板块其他真纺织股会补涨。

    只剔「业绩类」后缀（季报/增长/扭亏…），因为那是驱动力但不是题材方向；
    不剔「XX布局」「XX收购」这类，那些常常正是炒作由头。
    """
    parts = re.split(r"[+＋、,，/]", str(reason or ""))
    out = []
    for p in parts:
        p = p.replace("“", "").replace("”", "").strip().strip("\"'")
        if not p or len(p) > 12:
            continue
        if re.search(r"季报|年报|中报|业绩|预增|预盈|扭亏|增长|净利润|同比", p):
            continue
        if p not in out:
            out.append(p)
    return out[:limit]


def board_ladder(lianban):
    """按板块聚合连板梯队 → {板块名: {n, maxLb, names, codes, topName}}。

    R100z312（用户 2026-11-08：「题材最好和连板梯队里面找，或者单独增加个标的所在板块
    是否有连板的，这样就可以猜可不可以补涨」）。

    为什么这件事比「同花顺行业 + 今日涨幅」更接近题材：**连板梯队里的票才是当天资金真正在
    做的方向**。一个行业今天涨 2% 可能只是权重股拉的；但如果这个行业里有 3 只连板（含一只
    4 板），那就是有主线、有龙头、有接力。反过来，行业分类是静态的——「种植业与林业」不会
    告诉你今天有没有资金在做它。

    ⚠️ `hybk` 是**同花顺行业**口径，与断板池的 `sector` 同一套（已核对匹配率）。
    它不是真题材词（「光伏边框+液冷铝材」那种在 `reason` 里），但同一套分类才能对上池子。
    """
    out = {}
    for x in (lianban or []):
        if not isinstance(x, dict):
            continue
        sec = str(x.get("hybk") or "").strip()
        if not sec:
            continue
        g = out.setdefault(sec, {"n": 0, "maxLb": 0, "names": [], "codes": [],
                                 "topReason": "", "topThemes": []})
        g["n"] += 1
        try:
            lbc = int(x.get("lbc") or 1)
        except (TypeError, ValueError):
            lbc = 1
        _words = theme_words(x.get("reason"))
        # 最高板那只的题材词才是「这个板块的连板在炒什么」——后排 2 板的不代表主线
        if lbc >= int(g.get("maxLb") or 0):
            g["maxLb"] = lbc
            g["topName"] = str(x.get("name") or "")
            g["topReason"] = str(x.get("reason") or "")
            g["topThemes"] = _words
        if x.get("name"):
            g["names"].append(str(x["name"]))
        if x.get("code"):
            g["codes"].append(str(x["code"]))
        g.setdefault("themes", [])
        for w in _words:
            if w not in g["themes"]:
                g["themes"].append(w)
    for g in out.values():
        g.setdefault("topName", g["names"][0] if g["names"] else "")
        g.setdefault("topThemes", [])
    return out


def ladder_score(entry, ladder, is_lianban=False):
    """板块连板档 → (score 0~100, label, why)。**没有连板不等于没机会，只是没有主线。**

    分档为什么这么给：
      · 4 板以上龙头 → 100，这是「有主线」的强证据（接力资金已经用真金白银投票）；
      · 3 板 → 85，主线确立但高度还没打满；
      · 2 板 → 70，有资金试盘，能不能成看第三天；
      · 只有首板（lbc=1）→ 50，**首板不算连板**，因为首板每天几十家，不构成「主线」；
      · 无连板 → 25，只说明「这个板块今天没有资金在做」，不是「这只票差」。

    `is_lianban`：本票自己就在连板梯队里 → 那它不是「补涨」而是「接力」，语义不同，
    分数不再叠加（它已经拿满了）。
    """
    if is_lianban:
        return 100.0, "本票在梯队", "本票本身就在连板梯队，属于接力而非补涨"
    sec = str((entry or {}).get("sector") or "")
    g = (ladder or {}).get(sec)
    if not g or not g.get("n"):
        return 25.0, "无连板", "板块 %s 今日无连板股——没有资金在做这个方向，补涨只能跟大盘" % (sec or "—")
    mx = int(g.get("maxLb") or 1)
    n = int(g.get("n") or 0)
    if mx >= 4:
        sc, lab = 100.0, "有龙头"
    elif mx == 3:
        sc, lab = 85.0, "3板"
    elif mx == 2:
        sc, lab = 70.0, "2板"
    else:
        sc, lab = 50.0, "仅首板"
    _themes = g.get("topThemes") or []
    _top = g.get("topName") or ""
    why = "板块 %s 有 %d 只连板、最高 %d 板（%s）" % (sec, n, mx, "、".join(g.get("names") or [])[:24])
    if _themes:
        # R100z313：必须写「这只连板在炒什么」，不能只写行业名——
        # 浙文影业行业=纺织制造、题材=AI漫剧，只写行业会让读者以为纺织在炒。
        why += "，%s 的题材是「%s」" % (_top or "龙头", "+".join(_themes))
    if mx < 2:
        why += "　⚠️ 只有首板，不构成主线（首板每日几十家）"
    return sc, lab, why


def mood_state(cur, prev=None):
    """情绪状态与边际（R100z310）。`cur`/`prev` 为涨跌/连板指标 dict。

    为什么要有这个：看板原来只报「今天涨停 32 家」这种绝对值——但反包能不能做，
    看的不是今天几家涨停，而是**比昨天更好还是更差**。同一个 32 家，昨天 15 家和昨天 50 家
    是两种完全不同的盘面。

    返回 `{label, score, deltas:[{name,cur,prev,diff,txt}], text}`；
    `label` 四档：回暖 / 分歧 / 退潮 / 冰点（判据写死，可回测）。
    """
    cur = cur or {}
    prev = prev or {}

    def g(d, k):
        v = d.get(k)
        return float(v) if isinstance(v, (int, float)) else None

    lu, lu0 = g(cur, "limitUp"), g(prev, "limitUp")
    ld, ld0 = g(cur, "limitDown"), g(prev, "limitDown")
    hb, hb0 = g(cur, "maxLb"), g(prev, "maxLb")
    lbn, lbn0 = g(cur, "lianbanCount"), g(prev, "lianbanCount")
    dv, dv0 = g(cur, "divRatio"), g(prev, "divRatio")
    rl, rl0 = g(cur, "relayRatio"), g(prev, "relayRatio")

    def mk(name, a, b, unit=""):
        if a is None or b is None:
            return {"name": name, "cur": a, "prev": b, "diff": None, "txt": "%s —" % name}
        diff = a - b
        arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "→")
        fmt = ("%.0f" if abs(a) >= 3 or unit == "" else "%.2f")
        return {"name": name, "cur": a, "prev": b, "diff": round(diff, 3),
                "txt": "%s %s%s（昨 %s，%s）" % (name, fmt % a, unit, fmt % b, arrow)}

    deltas = [mk("涨停", lu, lu0), mk("跌停", ld, ld0), mk("最高板", hb, hb0),
              mk("连板股", lbn, lbn0), mk("分歧率", (dv * 100 if dv is not None else None),
                                     (dv0 * 100 if dv0 is not None else None), "%"),
              mk("接力率", (rl * 100 if rl is not None else None),
                 (rl0 * 100 if rl0 is not None else None), "%")]

    # 判定顺序＝从最坏往最好：有明确恶化证据先判坏，避免把退潮读成回暖。
    # ⚠️ 2026-11-08 实测修正：初版要求「涨停降 **且** 最高板降」才算退潮，
    #    结果 11-06 那天涨停 83→32（腰斩）、连板 20→10、跌停 0→15，
    #    只因最高板 4→5（独苗连板）就被判成「中性」——**最高板是幸存者，不是盘面**。
    #    改成看「涨停变化率」和「跌停增量」，最高板只作辅助。
    lu_chg = ((lu - lu0) / lu0) if (lu is not None and lu0) else None
    lbn_chg = ((lbn - lbn0) / lbn0) if (lbn is not None and lbn0) else None
    lower_hb = (hb is not None and hb0 is not None and hb < hb0)
    dt_add = (ld - ld0) if (ld is not None and ld0 is not None) else None
    if (lu is not None and lu < 20 and ld is not None and ld >= lu):
        label, score = "冰点", 10
    elif (((lu_chg is not None and lu_chg <= -0.30) or (dt_add is not None and dt_add >= 10))
          and (lbn_chg is None or lbn_chg <= -0.20)):
        label, score = "退潮", 25
    elif (lower_hb or (dv is not None and dv0 is not None and dv - dv0 >= 0.10)
          or (lu_chg is not None and lu_chg <= -0.15)):
        label, score = "分歧", 55
    elif ((lu_chg is not None and lu_chg >= 0.20) and not lower_hb
          and (rl is None or rl0 is None or rl >= rl0)):
        label, score = "回暖", 85
    else:
        label, score = "中性", 65
    text = " · ".join(x["txt"] for x in deltas if x["cur"] is not None)
    # R100z311：判定依据要能读出来。11-06 那天「最高板 4→5↑」「接力率 24→31↑」两个指标是改善的，
    # 却判「退潮」——因为涨停家数腰斩。读者只看到六个数字，会以为判反了。
    if label == "退潮":
        why = "涨停家数腰斩（%s→%s）+ 连板股收缩，高度和接力率是幸存者指标，不改变结论" % (
            (("%.0f" % lu0) if lu0 is not None else "—"), (("%.0f" % lu) if lu is not None else "—"))
    elif label == "冰点":
        why = "涨停不足 20 家且跌停不少于涨停，赚钱效应基本消失"
    elif label == "分歧":
        why = "炸板/分歧率抬升或涨停家数明显回落，但还没到退潮"
    elif label == "回暖":
        why = "涨停家数明显增加、连板高度没降、接力率没恶化"
    else:
        why = "各项指标没有明确方向，按中性处理"
    return {"label": label, "score": score, "deltas": deltas, "text": text, "why": why}


def _fnum(v):
    """宽松取数：非数字一律 None（不补 0）。"""
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def last_bar_day(kl):
    """K 线最后一根的日期 'YYYY-MM-DD'；空/异常一律返回 ''。

    兼容两种结构：`[{'day':..}]`（腾讯/新浪原始）与 `[['2026-11-04', o, c, h, l, v], ...]`
    （data.js 的 stkKlines 内嵌格式）——两处都用得上，各写一份必然有一处写错。
    """
    if not kl:
        return ''
    last = kl[-1]
    try:
        if isinstance(last, dict):
            return str(last.get('day') or last.get('date') or '')[:10]
        return str(last[0] or '')[:10]
    except Exception:
        return ''


def market_last_day(D, klines=None):
    """市场最近交易日：优先 `ashare.tradeDate`，缺失时取 stkKlines 里出现最多的末日。

    为什么不信 `datetime.today()`：跨午夜跑批时今天已经是「明天」，而数据还是昨天的；
    停牌判定一旦拿错基准，全市场的票都会被判成停牌。
    """
    d = str(((D or {}).get('ashare') or {}).get('tradeDate') or '')[:10]
    if d:
        return d
    kl = klines if klines is not None else ((D or {}).get('stkKlines') or {})
    cnt = {}
    for v in (kl or {}).values():
        day = last_bar_day(v)
        if day:
            cnt[day] = cnt.get(day, 0) + 1
    return max(cnt, key=cnt.get) if cnt else ''


def is_suspended(kl, market_day):
    """「末根 K 线早于市场最近交易日」= 当天没有成交（停牌/退市残壳）。

    ⚠️ 为什么不用「成交量 == 0」：16:00 收盘后跑池子时没有实时行情，K 线日期是唯一稳的判据；
    成交量字段在停牌当天压根不会生出一根新 K 线，用成交量为 0 反而会漏。
    9:45 那份有实时行情，再叠加 `行情状态 == 'S'` 兜一层（见 update_star）。
    """
    if not market_day:
        return False
    day = last_bar_day(kl)
    return bool(day) and day < market_day


def split_suspended(items, klines, market_day, code_key='code', kl_key='kl'):
    """把停牌票从候选里挑出来 → (kept, dropped)。

    R100z309（2026-11-05 用户：「你推荐了几天亚玛顿，但这只标的停牌几天了，这个 bug 你没修复」）：
    亚玛顿 002623 的 K 线停在 11-02，却连续几天出现在断板确认池 / 开盘精选 / 买点清单里，
    买区还拿「停牌前最后一根收盘价」当现价、标成「在买区内」——**不可交易的票被当成能下单的**。

    `klines` 可以是 {code: kl} 映射，也可以是 None（此时用 items 自带 `kl_key` 字段）。
    `dropped` 元素为 {code, name, lastDay, why}，调用方**必须**把它打进日志或页面：
    池子少了几只而页面不说，读者只会以为是抓数失败（G: 抓不到 ≠ 没有）。
    """
    kept, dropped = [], []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        code = str(it.get(code_key) or '')
        kl = (klines or {}).get(code) if klines is not None else it.get(kl_key)
        if is_suspended(kl, market_day):
            dropped.append({'code': code, 'name': str(it.get('name') or ''),
                            'lastDay': last_bar_day(kl),
                            'why': '停牌：最后一根 K 线 %s，早于最近交易日 %s'
                                   % (last_bar_day(kl) or '—', market_day)})
            continue
        kept.append(it)
    return kept, dropped


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
