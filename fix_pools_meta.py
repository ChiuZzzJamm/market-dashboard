#!/usr/bin/env python3
"""R100z4f: 选股池缺失名称回填 + themePicks 行情字段校准（确定性脚本，可独立运行或被 embed_klines 导入）。

背景（2026-09-28 用户反馈）：
1. 日线九门/吸筹/谐波池部分标的只有代码没有名称（fullScan 候选域不含昨日涨停今日出界的股，
   stkKlineNames 也没覆盖）→ 用腾讯 qt.gtimg.cn 实时行情接口（GBK）批量回补 name。
2. themePicks 题材掘金 stocks 的 note 被 AI 写入「·今日涨停」「·涨X%」等行情字样，与 chg 字段
   重复且可能虚构（前一交易日事件冒充当日）→ note 只保留概念归属；chg 用 stkKlines 当日
   日K（embed_klines 已内嵌题材掘金标的）重算校准，日期非当日则保留 AI 值。

防清场（R91m 铁律）：仅回填空 name、校准 themePicks 行情字段，不触碰其他任何字段；
行情接口失败时保留原值。

用法：
  python3 fix_pools_meta.py                # 直接改写 data.js 并汇报
  或 from fix_pools_meta import patch; patch(D)
挂载：16:00 / 周日 embed_klines.py 末尾自动调用；也可独立跑。
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/540"
GTIMG_URL = "https://qt.gtimg.cn/q={q}"
BASE = __file__.rsplit("/", 1)[0] or "."
TZ8 = timezone(timedelta(hours=8))

NOTE_TAIL_RE = re.compile(r"·(今日)?(涨停|[涨跌][0-9.]+%)")


def _today():
    return datetime.now(TZ8).strftime("%Y-%m-%d")


def _sym(code):
    c = str(code).strip()
    if c.startswith(("sh", "sz")):
        return c
    return ("sh" if c.startswith("6") else "sz") + c


def fetch_names(codes, timeout=10):
    """腾讯 qt.gtimg 批量查名称（GBK）。返回 {code: name}，失败的代码不在结果里。"""
    out = {}
    codes = [str(c).strip() for c in codes if re.fullmatch(r"\d{6}", str(c).strip())]
    for i in range(0, len(codes), 30):
        batch = codes[i:i + 30]
        url = GTIMG_URL.format(q=",".join(_sym(c) for c in batch))
        try:
            p = subprocess.run(
                ["curl", "-s", "--max-time", str(timeout), "-H", "User-Agent: " + UA, url],
                capture_output=True, timeout=timeout + 5)
            if not p or p.returncode != 0 or not p.stdout.strip():
                continue
            txt = p.stdout.decode("gbk", errors="replace")
        except Exception:
            continue
        for m in re.finditer(r'v_(?:sh|sz)(\d{6})="([^"]*)"', txt):
            fields = m.group(2).split("~")
            if len(fields) > 1 and fields[1].strip():
                out[m.group(1)] = fields[1].strip()
    return out


def _iter_pools(D):
    """产出 (容器描述, 条目列表)：四个选股池。"""
    ps = D.get("powerScreen") or {}
    if isinstance(ps.get("passed"), list):
        yield "powerScreen.passed", ps["passed"]
    acc = D.get("accumulation") or {}
    if isinstance(acc.get("scored"), list):
        yield "accumulation.scored", acc["scored"]
    hz = D.get("harmonic") or {}
    for key in ("confirmPool", "watchPool"):
        if isinstance(hz.get(key), list):
            yield f"harmonic.{key}", hz[key]


def _need_name(it):
    return isinstance(it, dict) and it.get("code") and not (str(it.get("name") or "").strip()) \
        or (isinstance(it, dict) and it.get("name") == it.get("code"))


def patch(D, fetch=True):
    """就地修补 D：缺名回填 + themePicks 校准。返回 (补名数, chg校准数, note净化数)。"""
    renamed = chg_fixed = note_cleaned = 0
    # 1) 池内缺名回填
    missing = []
    for _, items in _iter_pools(D):
        for it in items:
            if _need_name(it):
                missing.append(str(it["code"]).strip())
    missing = sorted(set(missing))
    if missing and fetch:
        names = fetch_names(missing)
        for _, items in _iter_pools(D):
            for it in items:
                c = str(it.get("code") or "").strip()
                if _need_name(it) and names.get(c):
                    it["name"] = names[c]
                    renamed += 1
    # 2) themePicks：note 净化 + chg 用当日日K校准
    today = _today()
    kl = D.get("stkKlines") if isinstance(D.get("stkKlines"), dict) else {}
    a = D.get("ashare") or {}
    for tp in (a.get("themePicks") or []):
        for st in (tp.get("stocks") or []):
            if not isinstance(st, dict):
                continue
            note = str(st.get("note") or "")
            cleaned = NOTE_TAIL_RE.sub("", note).strip("· ").strip()
            if cleaned != note:
                st["note"] = cleaned
                note_cleaned += 1
            code = str(st.get("code") or "").strip()
            arr = kl.get(code)
            if isinstance(arr, list) and len(arr) >= 2:
                last, prev = arr[-1], arr[-2]
                try:
                    if str(last[0]) == today:
                        pct = (float(last[2]) / float(prev[2]) - 1) * 100
                        st["chg"] = round(pct, 2)
                        chg_fixed += 1
                except (ValueError, ZeroDivisionError, TypeError, IndexError):
                    pass
    return renamed, chg_fixed, note_cleaned


def main():
    s = open(f"{BASE}/data.js", encoding="utf-8").read()
    m = re.search(r"window\s*\.\s*DASHBOARD_DATA\s*=\s*(\{[\s\S]*\});?\s*$", s)
    if not m:
        print("[FAIL] data.js parse failed")
        return 1
    D = json.loads(m.group(1))
    renamed, chg_fixed, note_cleaned = patch(D)
    out = "window.DASHBOARD_DATA = " + json.dumps(D, ensure_ascii=False, separators=(",", ":")) + ";\n"
    open(f"{BASE}/data.js", "w", encoding="utf-8").write(out)
    print(f"[info] fix_pools_meta：补名 {renamed} 只，themePicks chg 校准 {chg_fixed} 条，note 净化 {note_cleaned} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
