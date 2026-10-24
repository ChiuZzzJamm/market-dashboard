# -*- coding: utf-8 -*-
"""pipeline_lock.py — 16:00 数据步 / 19:30 叙事步的工序锁（R100z178，2026-10-23）。

为什么需要这把锁（两个都真实发生过/随时会发生的场景）：

  ① 两条任务时间余量为零。A1600 数据步 10-21 运行账实测「整链约 60 分钟」，而 A1700
     叙事步就排在整整 60 分钟之后。两条任务都会对 data.js 做**整份读-改-写**
     （apply_narrative / merge_duanban / embed_klines / enrich_policy / macro_calendar
     都是 read → 改 → dump 整份），`os.replace` 只保证单次写入是原子的，
     **不保证两个写者不互相覆盖**：后写方拿着开跑前读到的快照整份写回，
     前写方刚落的字段会被整段抹掉。症状是「某个模块突然变回旧值」，且日志里查不出谁干的。

  ② 平台在模型流式超时后会**自动重试**同一条任务（10-23 用户遇到的模型断开就是这个机制）。
     重试实例与未退出的原实例会并行跑同一条 prompt，同样两个写者。

用法（两处调用点都必须在写 data.js 之前拿锁、任务结束放锁）：

    run-python pipeline_lock.py acquire --wait 300     # 拿锁，被占则最多等 300 秒
    run-python pipeline_lock.py beat                   # 心跳，长步骤之间调用以续命
    run-python pipeline_lock.py release                # 放锁（含「禁止部署」等提前结束分支）
    run-python pipeline_lock.py status                 # 只读查看持有者与心跳年龄

退出码：0=已持锁/操作成功；3=锁被他人持有或已被他人抢走；1=参数或环境错误。

死锁治理：锁文件带心跳时间戳，超过 --ttl（默认 2700 秒＝45 分钟）无心跳即判定持有者已死，
下一个 acquire 会打印 `[LOCK][STALE-STOLEN]` 抢占并把旧锁留档。45 分钟这个值是算出来的：
平台硬上限 90 分钟、两段最大无心跳间隔（full_scan→accumulation→harmonic→power 四模块）
运行账实测约 43 分钟 —— 45 分钟既能覆盖正常的长步骤，又不会让一个崩掉的任务把 19:30
永久挡在门外。

反向自检（改判据后必须证明它真会拦）见文件末尾 __main__ 下面的注释说明。
"""
import argparse
import json
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_NAME = "data"
DEFAULT_TTL = 2700          # 45 分钟无心跳判死
POLL = 15                   # 等锁轮询间隔（秒）


def _path(name):
    return os.path.join(HERE, ".pipeline-%s.lock" % name)


def _read(name):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                       # noqa: BLE001
        return None


def _age(meta):
    try:
        return max(0.0, time.time() - float(meta.get("beat") or meta.get("ts") or 0))
    except Exception:                                       # noqa: BLE001
        return 0.0


def _try_create(name, task):
    """O_CREAT|O_EXCL 原子建锁：内核保证同一时刻只有一个赢家。"""
    p = _path(name)
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    now = time.time()
    meta = {"pid": os.getpid(), "host": socket.gethostname(), "task": task,
            "ts": now, "beat": now}
    try:
        os.write(fd, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(fd)
    return True


def _beat(name):
    """更新心跳（原子替换，避免半截 JSON）。"""
    p = _path(name)
    meta = _read(name) or {}
    meta["beat"] = time.time()
    tmp = p + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, p)
    return meta


def _describe(meta):
    if not meta:
        return "（锁文件内容无法解析）"
    return ("task=%s pid=%s host=%s 心跳年龄 %.0fs"
            % (meta.get("task"), meta.get("pid"), meta.get("host"), _age(meta)))


def cmd_acquire(name, task, wait, ttl):
    deadline = time.time() + max(0, wait)
    bad_reads = 0
    while True:
        if _try_create(name, task):
            print("[LOCK][OK] 已持有 %s 锁（task=%s pid=%d；未心跳超过 %ds 视为僵死）"
                  % (name, task, os.getpid(), ttl))
            return 0
        meta = _read(name)
        if meta is None:
            # 文件存在却读不出：半截写入或被手工 touch。删掉重试，最多 3 次防死循环。
            bad_reads += 1
            if bad_reads > 3:
                print("[LOCK][HELD] %s 锁文件反复无法解析（%d 次），需人工检查 %s"
                      % (name, bad_reads, _path(name)))
                return 3
            try:
                os.remove(_path(name))
            except OSError:
                pass
            print("[LOCK][STALE-REMOVED] 锁文件无法解析，已删除后重试（第 %d 次）" % bad_reads)
            continue
        if _age(meta) > ttl:
            old = _path(name) + ".stale-%d" % int(time.time())
            # 旧锁留档只留最近一份：留档是为排查「谁被判死了」，不是攒文件夹。
            try:
                import glob
                for f in glob.glob(_path(name) + ".stale-*"):
                    os.remove(f)
            except OSError:
                pass
            try:
                os.replace(_path(name), old)
            except OSError:
                pass
            print("[LOCK][STALE-STOLEN] 原持有者已 %.0f 秒无心跳（>%ds），判定已死并抢占："
                  "%s；旧锁留档 %s" % (_age(meta), ttl, _describe(meta), os.path.basename(old)))
            continue
        if time.time() >= deadline:
            print("[LOCK][HELD] 未能拿到 %s 锁：%s。**禁止在未持锁状态下写 data.js**——"
                  "并发整份写回会整段覆盖对方刚落的字段。" % (name, _describe(meta)))
            return 3
        time.sleep(min(POLL, max(1, deadline - time.time())))
        print("[LOCK][WAIT] %s 锁被占用（%s），继续等待，剩余 %.0fs"
              % (name, _describe(meta), max(0, deadline - time.time())))


def cmd_beat(name, task):
    # 归属按 task 标签判，不按 pid：automation 每次 Bash 调用都是新进程，
    # 用 pid 判归属会让「自己 acquire 完、下一步 beat」必然误判成被抢。
    meta = _read(name)
    if meta is None:
        print("[LOCK][GONE] %s 锁已不存在 —— 心跳失败。若本任务仍在写 data.js，"
              "说明锁已被判死抢走，**必须立即停止写回**并如实汇报。" % name)
        return 3
    if task and meta.get("task") != task:
        print("[LOCK][LOST] %s 锁已被他人持有（%s）—— 本实例的锁被抢走，"
              "**必须立即停止写回**，否则会与对方并发覆盖 data.js。" % (name, _describe(meta)))
        return 3
    _beat(name)
    print("[LOCK][BEAT] 心跳已续期（%s）" % _describe(_read(name)))
    return 0


def cmd_release(name, task):
    meta = _read(name)
    if meta is None:
        print("[LOCK][RELEASE-NOOP] %s 锁不存在（可能已被判死抢走），无需释放" % name)
        return 0
    if task and meta.get("task") != task:
        print("[LOCK][NOT-OWNER] %s 锁不属于 %s（实为 %s），不删他人锁"
              % (name, task, _describe(meta)))
        return 3
    try:
        os.remove(_path(name))
    except OSError as e:
        print("[LOCK][RELEASE-FAIL] 删锁失败：%r" % (e,))
        return 1
    print("[LOCK][RELEASED] %s 锁已释放" % name)
    return 0


def cmd_status(name):
    meta = _read(name)
    if meta is None:
        print("[LOCK][FREE] %s 锁空闲（无锁文件）" % name)
        return 0
    print("[LOCK][BUSY] %s = %s" % (name, _describe(meta)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="流水线工序锁（16:00/19:30 互斥）")
    ap.add_argument("action", choices=["acquire", "beat", "release", "status"])
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--task", default="", help="持有者标签，例如 a1600-data / a1700-narrative")
    ap.add_argument("--wait", type=int, default=0, help="等锁上限（秒），默认 0 立即返回")
    ap.add_argument("--ttl", type=int, default=DEFAULT_TTL,
                    help="无心跳多久判死可抢（秒），默认 %d" % DEFAULT_TTL)
    a = ap.parse_args(argv)
    task = a.task
    if a.action == "acquire":
        if not task:
            print("[LOCK][ERR] acquire 必须带 --task <标签>（如 a1600-data / a1700-narrative），"
                  "否则后续 beat/release 无法识别归属")
            return 1
        return cmd_acquire(a.name, task, a.wait, a.ttl)
    if a.action == "beat":
        return cmd_beat(a.name, task)
    if a.action == "release":
        return cmd_release(a.name, task)
    return cmd_status(a.name)


if __name__ == "__main__":
    sys.exit(main())
