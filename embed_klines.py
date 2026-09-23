#!/usr/bin/env python3
"""R98c: 全站 A股标的 K 线内嵌（消除前端实时 fetch 失败「K线获取失败(Failed to fetch)」）。

背景：断板双池标的的 K 线已内嵌在 duanban.confirmed/watching[].kline，弹窗离线秒开；
但页面其余 A股标的（要闻股/题材掘金/AI预测股/板块领涨TOP/连板天梯）点击弹窗时需
前端实时 fetch 腾讯 ifzq，部分环境网络受限会失败。本脚本在自动化 AI 写回之后、
validate 之前运行，把所有 A股标的的近 60 根日 K 线一次性抓取并写入 data.js 顶层：

  stkKlines     : {"600519": [[date,open,close,high,low,volume], ...], ...}
  stkKlineNames : {"贵州茅台": "600519", ...}   # 板块领涨TOP 等无 code 条目的名称反查

数据源：
  - 腾讯 ifzq fqkline（个股日K，与前端同源）
  - 腾讯 smartbox s3（板块领涨股等仅有名称的标的 → 代码反查，GBK）

防清场（R91m 铁律）：本脚本只新增/更新 stkKlines、stkKlineNames 两个顶层字段；
全部抓取失败时保留既有值，绝不写空覆盖。其余字段一律不触碰。

用法：python3 embed_klines.py [--deadline 240]
挂载：16:00 / 07:30 / 周日23:00 自动化在 AI 写回之后运行（08:30 不写 A股标的，不挂）。
"""
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
IFZQ_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,60,qfq"
SMARTBOX_URL = "https://smartbox.gtimg.cn/s3/?v=2&q={q}&t=all"

BASE = __file__.rsplit("/", 1)[0] or "."


def curl_text(url, timeout=15, decode="utf-8"):
    try:
        p = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), "-H", "User-Agent: " + UA, url],
            capture_output=True, timeout=timeout + 5)
    except Exception:
        return None
    if not p or p.returncode != 0 or not p.stdout.strip():
        return None
    return p.stdout.decode(decode, errors="replace")


def load_data():
    s = open(f"{BASE}/data.js", encoding="utf-8").read()
    m = re.search(r"window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$", s)
    if not m:
        raise RuntimeError("data.js parse failed")
    return json.loads(m.group(1))


def save_data(D):
    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, indent=2) + ";\n"
    open(f"{BASE}/data.js", "w", encoding="utf-8").write(out)


def is_ashare_code(code):
    return bool(re.fullmatch(r"\d{6}", str(code or "").strip())) and str(code).startswith(("6", "0", "3"))


def collect_stocks(D):
    """返回 (codes:set[str], names:set[str])。codes=6位纯数字；names=待反查的领涨股名。"""
    codes, names = set(), set()

    def add_code(c):
        c = str(c or "").strip()
        if is_ashare_code(c):
            codes.add(c)
        else:
            m = re.search(r"(\d{6})", str(c or ""))
            if m and is_ashare_code(m.group(1)):
                codes.add(m.group(1))

    a = D.get("ashare") or {}
    # 1) 要闻五节 impacts stocks（有 code）
    for key in ("bullNews", "bearNews", "macroNews", "intlNews", "bankViews"):
        for grp in (a.get(key) or []):
            for imp in (grp.get("impacts") or []):
                for st in (imp.get("stocks") or []):
                    add_code(st.get("code"))
    # 2) 题材掘金（有 code）
    for tp in (a.get("themePicks") or []):
        for st in (tp.get("stocks") or []):
            add_code(st.get("code"))
    # 3) AI 预测 sectors stocks（有 code）
    for sec in ((D.get("aiPrediction") or {}).get("sectors") or []):
        for st in (sec.get("stocks") or []):
            add_code(st.get("code"))
    # 4) 连板天梯（有 code；断板池内嵌不覆盖 lianban 弹窗）
    for lb in (a.get("lianban") or []):
        add_code(lb.get("code"))
    # 5) 板块领涨/领跌 TOP（只有 name，需反查）
    for s in (a.get("sectorsUp") or []) + (a.get("sectorsDown") or []):
        for t in (s.get("tops") or []):
            nm = str(t.get("name") or "").strip()
            if nm:
                names.add(nm)
    return codes, names


def resolve_names(names, deadline):
    """腾讯 smartbox 反查：名称 → 6位代码。GBK 编码，v_hint="sz~301595~太力科技~tlkj~GP-A"。"""
    out = {}
    for nm in sorted(names):
        if time.time() > deadline:
            break
        raw = curl_text(SMARTBOX_URL.format(q=__import__("urllib.parse", fromlist=["quote"]).quote(nm)),
                        decode="gbk")
        if not raw:
            continue
        m = re.search(r'v_hint="([^"]*)"', raw)
        if not m:
            continue
        # smartbox 返回名称为 \uXXXX 转义形式（如 \u592a\u529b\u79d1\u6280=太力科技），需先解码
        hint = m.group(1)
        try:
            hint = hint.encode("ascii", errors="ignore").decode("unicode_escape")
        except Exception:
            pass
        for rec in hint.split("^"):
            p = rec.split("~")
            if len(p) >= 3 and p[2] == nm and re.fullmatch(r"\d{6}", p[1] or ""):
                if p[1].startswith(("6", "0", "3")):
                    out[nm] = p[1]
                break
    return out


def fetch_kline(code, timeout=15):
    sym = ("sh" if code.startswith("6") else "sz") + code
    raw = curl_text(IFZQ_URL.format(sym=sym), timeout=timeout)
    if not raw:
        return None
    try:
        j = json.loads(raw)
    except Exception:
        return None
    d = (j.get("data") or {}).get(sym) or {}
    rows = d.get("qfqday") or d.get("day") or []
    out = []
    for r in rows:
        if not isinstance(r, list) or len(r) < 6:
            continue
        try:
            o, c, h, l, v = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        except (TypeError, ValueError):
            continue
        out.append([str(r[0]), o, c, h, l, v])
    return out if len(out) > 1 else None


def main():
    deadline = time.time() + 240
    if "--deadline" in sys.argv:
        try:
            deadline = time.time() + float(sys.argv[sys.argv.index("--deadline") + 1])
        except Exception:
            pass
    D = load_data()
    codes, names = collect_stocks(D)
    print(f"[info] 待内嵌：{len(codes)} 只代码标的 + {len(names)} 个待反查名称")

    # 名称反查（串行，smartbox 轻量）
    name_map = resolve_names(names, deadline)
    if name_map:
        print(f"[info] smartbox 反查成功 {len(name_map)}/{len(names)}: {name_map}")
    codes |= set(name_map.values())

    # 既有内嵌保留（防清场），只补缺/刷新
    kl = D.get("stkKlines") if isinstance(D.get("stkKlines"), dict) else {}
    kl_names = D.get("stkKlineNames") if isinstance(D.get("stkKlineNames"), dict) else {}

    ok, fail = 0, []

    def work(c):
        return c, fetch_kline(c)

    with ThreadPoolExecutor(max_workers=6) as exe:
        futs = {exe.submit(work, c): c for c in sorted(codes)}
        for fu in as_completed(futs):
            if time.time() > deadline:
                for f in futs:
                    f.cancel()
                break
            c, k = fu.result()
            if k:
                kl[c] = k
                ok += 1
            else:
                fail.append(c)

    # 名称映射仅保留抓到 K 线的代码
    kl_names = {nm: c for nm, c in {**kl_names, **name_map}.items() if c in kl}

    if ok == 0 and not kl:
        print("[warn] 全部 K 线抓取失败且无既有数据，保留空 stkKlines（不清其他字段）")
        D["stkKlines"] = {}
        D["stkKlineNames"] = {}
    else:
        D["stkKlines"] = kl
        D["stkKlineNames"] = kl_names
        save_data(D)
        print(f"[info] stkKlines 内嵌完成：成功 {ok} 只 / 失败 {len(fail)} 只 / 总计 {len(kl)} 只，名称映射 {len(kl_names)} 条")
        if fail:
            print("[warn] 抓取失败:", ",".join(sorted(fail)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
