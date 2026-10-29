#!/usr/bin/env python3
"""全站时间戳归一（R100z167，2026-10-21 用户实报「怎么那么多遗留问题」）。

问题（当天真实发生）：
    页面头部「数据更新」读的是 data.js **顶层** `updatedAt`，而有 5 个脚本各自覆盖它
    （update_ashare_sectors / update_us_from_quotes / update_kr_jp / stop_tracker / 各四模块）。
    谁最后跑谁赢 —— 实测 17:20 A股数据已更新，而头部还停在 `16:01（韩股 10/21 收盘 已更新…）`；
    同时 `ashare.updatedAt` 当时靠 AI 手写、没有脚本维护，于是报告里长期挂着
    「已知遗留：ashare.updatedAt 仍显示 10-20 17:22」。一条真问题被当成免责声明糊过去。

修法（两个方向一起收口）：
    1) **分格戳**：ashare / us / panorama / 四模块 / stopTrack 各自维护自己的戳，
       哪个脚本更新了哪块数据，就只写自己那一格（见 update_*.py 末尾 `stamp_self()`）；
    2) **顶层戳**：不再由任何脚本直写语义，部署时由本脚本从各格取**最大时间戳**生成——
       单一来源、幂等、不累积、不撒谎。头部因此永远等于「真正最新的那块数据」。

顶层文案格式（固定，便于闸门 G28 机器校验）：
    `YYYY-MM-DD HH:MM（<格名> 已更新）`  例：`2026-10-21 17:20（A股收盘 已更新）`
    —— 刻意不照抄格内长文案（A股那格有 70 字来源说明，塞进页头会撑爆布局）。

约定：
    - 只改顶层 `updatedAt` 一个值；**对原文做定点替换**，不整份重新序列化
      （data.js 7.2MB 单行，json.dumps 往返会把浮点/空白重排，制造无意义 diff）。
    - 内容没变就不写盘（幂等，可反复跑）。
    - 任何异常 exit 0（部署链里不阻断），但会打印原因。
    - 写盘走 `_tmp + os.replace`（硬约束第 11 条）。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(HERE, "data.js")

# 分格戳：(路径组, 页头显示名)。路径组里多条路径的值按序拼成一段文本再匹配时间戳，
# 用来兼容「日期和时间拆在两个字段」的数据（如 duanban.star 的 date + time）。
# 顺序即同刻平手时的优先级。
SLOTS = [
    ([("ashare", "updatedAt")], "A股收盘"),
    ([("us", "updatedAt")], "美股收盘"),
    ([("panorama", "updatedAt")], "全球市场全景"),
    ([("harmonic", "updatedAt")], "谐波形态"),
    ([("accumulation", "updatedAt")], "吸筹打分"),
    ([("breakoutPool", "updatedAt")], "横盘启动"),
    ([("fullScan", "updatedAt")], "全量扫描"),
    ([("stopTrack", "updatedAt")], "止损跟踪"),
    # R100z276（2026-10-29 用户实报「页头还停在 08:36」）：09:45 那条自动化只写
    #   duanban.star（开盘精选），但它拆成 date='2026-10-29' + time='09:56' 两个字段，
    #   没有 updatedAt，所以从来没被本脚本看见 → 精选 09:56 更新完了，页头还挂着 08:36。
    #   这里把两个字段拼起来当一格时间戳（拼成 '2026-10-29 09:56' 正好喂给 TS_RE）。
    ([("duanban", "star", "date"), ("duanban", "star", "time")], "开盘精选"),
]

TS_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})")
HEAD_RE = re.compile(r"(window\.DASHBOARD_DATA\s*=\s*\{\s*\"updatedAt\"\s*:\s*)(\"(?:[^\"\\]|\\.)*\")")


def get(D, path):
    cur = D
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def main():
    try:
        raw = open(DATA_JS, encoding="utf-8").read()
    except Exception as e:                                   # noqa: BLE001
        print("[STAMP] 读 data.js 失败，跳过：%r" % (e,))
        return 0

    hm = HEAD_RE.search(raw)
    if not hm:
        print("[STAMP] 顶层 updatedAt 不在 data.js 首位，结构异常，跳过")
        return 0

    m = re.search(r"window\.DASHBOARD_DATA\s*=\s*", raw)
    try:
        D = json.loads(raw[m.end():].lstrip().rstrip().rstrip(";"))
    except Exception as e:                                   # noqa: BLE001
        print("[STAMP] data.js 解析失败，跳过：%r" % (e,))
        return 0

    best_ts, best_label = None, None
    detail = []
    for paths, label in SLOTS:
        vals = []
        for path in paths:
            v = get(D, path)
            if isinstance(v, str) and v.strip():
                vals.append(v.strip())
        if not vals:
            continue
        mm = TS_RE.search(" ".join(vals))
        if not mm:
            continue
        ts = "%s %s" % (mm.group(1), mm.group(2))
        detail.append("%s=%s" % (label, ts))
        # 严格大于才替换 → 同刻时按 SLOTS 顺序取第一个
        if best_ts is None or ts > best_ts:
            best_ts, best_label = ts, label

    if not best_ts:
        print("[STAMP] 各格都没有可解析的时间戳，跳过")
        return 0

    new = "%s（%s 已更新）" % (best_ts, best_label)
    try:
        old = json.loads(hm.group(2))
    except Exception:                                        # noqa: BLE001
        old = hm.group(2)
    if old == new:
        print("[STAMP] 顶层已最新：%s（各格 %s）" % (new, "; ".join(detail)))
        return 0

    quoted = json.dumps(new, ensure_ascii=False)
    out = raw[:hm.start(2)] + quoted + raw[hm.end(2):]
    tmp = DATA_JS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    os.replace(tmp, DATA_JS)
    print("[STAMP] 顶层 updatedAt：%r → %r（各格 %s）" % (old, new, "; ".join(detail)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
