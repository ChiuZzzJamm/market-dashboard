#!/usr/bin/env node
/**
 * check_fields.js —— 前端读 data.js「不存在的字段」探针（R100z315，2026-11-09）
 *
 * 用法：node check_fields.js [--verbose]
 *   exit 0 = 无可疑访问；exit 1 = 有（打印路径与次数）
 *
 * 为什么需要它：JS 读一个不存在的键**不报错**，只返回 undefined，然后页面显示「—」或
 * 悄悄走兜底分支——闸门全绿、页面看着也正常，但那段逻辑其实没生效。
 * 2026-11-09 首次跑就抓到真 bug：`fbIsDuanban` 把 `duanban.star`（对象）当数组元素
 * concat 进池子，遍历时多出一个 code=undefined 的垃圾项（实测被读 47 次）。
 *
 * 原理：把 window.DASHBOARD_DATA 包一层 Proxy，记录「键不在对象里」的 get，
 * 再用桩 DOM 跑一遍主要渲染函数，最后按命中次数排序输出。
 *
 * ⚠️ 白名单（KNOWN）里的路径是**已知且有意**的：
 *   - stkKlines.<code>：只有部分票内嵌 K 线，取不到就走 stkFetchKline 网络兜底；
 *   - chenPool.*.metrics.tailRisk：观察池不产出该字段，前端 `m.tailRisk ? ... : ''` 有兜底。
 * 新增白名单前先确认「前端确实有兜底」，否则就是把真 bug 藏起来。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HERE = __dirname;
const VERBOSE = process.argv.includes('--verbose');

// ---- 桩 DOM（够跑渲染即可） ----
const el = () => {
  const kids = {};
  return {
    innerHTML: '', textContent: '', value: '',
    classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
    style: {}, dataset: {},
    querySelector(s) { if (!kids[s]) kids[s] = el(); return kids[s]; },
    querySelectorAll() { return []; },
    appendChild(c) { if (c && c.id) ELS[c.id] = c; return c; },
    getBoundingClientRect() { return { bottom: 0, top: 0, left: 0, width: 0, height: 0 }; },
    addEventListener() {}, removeEventListener() {}, setAttribute() {}, removeAttribute() {},
    insertBefore() {}, insertAdjacentHTML() {}, remove() {}, closest() { return null; },
    children: [], parentNode: null, offsetWidth: 0, scrollTop: 0, scrollHeight: 0,
  };
};
const ELS = {};
const document = {
  getElementById: (id) => (ELS[id] = ELS[id] || el()),
  querySelector: () => el(), querySelectorAll: () => [], createElement: () => el(),
  createTextNode: () => ({}),
  body: { classList: { add() {}, remove() {}, contains() { return false; } }, appendChild() {}, style: {} },
  documentElement: el(), addEventListener() {}, head: el(),
};

const sandbox = {
  console, document, location: { hash: '' }, navigator: { userAgent: 'node' },
  setTimeout: (f) => { try { f(); } catch (e) {} }, clearTimeout() {},
  setInterval: () => 0, clearInterval() {},
  localStorage: { getItem: () => null, setItem() {} },
  requestAnimationFrame: (f) => f(), addEventListener() {},
  getComputedStyle: () => ({
    flexDirection: 'row', display: 'block', position: 'fixed', top: '0px', left: '0px',
    right: '0px', bottom: '0px', width: '100px', height: '100px', opacity: '1', visibility: 'visible',
  }),
  XMLHttpRequest: function () { this.open = () => {}; this.send = () => {}; this.setRequestHeader = () => {}; },
  Math, Date, JSON, parseInt, parseFloat, isFinite, isNaN, String, Number, Object, Array,
  RegExp, Error, Promise, encodeURIComponent, decodeURIComponent,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

// ---- 载入 data.js ----
const dataPath = path.join(HERE, 'data.js');
vm.runInContext(fs.readFileSync(dataPath, 'utf8'), sandbox, { filename: 'data.js' });

// ---- 载入页面内联 JS（截到 render('ashare') 之前，避免真去渲染） ----
const html = fs.readFileSync(path.join(HERE, 'index.html'), 'utf8');
const blocks = [...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)]
  .map((m) => m[1]);
if (!blocks.length) { console.error('index.html 里没有内联 script'); process.exit(2); }
const js = blocks.reduce((a, b) => (a.length > b.length ? a : b));
const cut = js.lastIndexOf("render('ashare');");
const pageJs = cut > 0 ? js.slice(0, cut) : js;

// ---- 包 Proxy 记录 miss ----
const misses = new Map();
const DEPTH = 5;
function wrap(obj, p, d) {
  if (obj === null || typeof obj !== 'object' || d > DEPTH) return obj;
  return new Proxy(obj, {
    get(t, k, r) {
      if (typeof k !== 'string') return Reflect.get(t, k, r);
      const key = p ? p + '.' + k : k;
      const has = (k in t);
      if (!has) {
        const v0 = misses.get(key) || { path: key, hits: 0 };
        v0.hits++;
        misses.set(key, v0);
      }
      const v = Reflect.get(t, k, r);
      if (has && v && typeof v === 'object') return wrap(v, key, d + 1);
      return v;
    },
  });
}
sandbox.window.DASHBOARD_DATA = wrap(sandbox.window.DASHBOARD_DATA, '', 0);
sandbox.DASHBOARD_DATA = sandbox.window.DASHBOARD_DATA;

vm.runInContext(pageJs, sandbox, { filename: 'page.js' });

// ---- 跑主要渲染 ----
const FNS = [
  'renderAshare', 'renderUs', 'renderDuanban', 'renderScoreView', 'renderPano',
  'renderSignalPerf', 'renderStructure', 'chenCompareHtml', 'ordBlockHtml',
  'renderReasons', 'renderTrend', 'renderGnews',
];
for (const f of FNS) {
  try { vm.runInContext(`(typeof ${f}==='function') ? ${f}() : null`, sandbox); } catch (e) {}
}
try { vm.runInContext("render('ashare')", sandbox); } catch (e) {}
try { vm.runInContext("render('us')", sandbox); } catch (e) {}

// ---- 去噪 + 白名单 ----
const NOISE = /(?:^|\.)(?:length|toJSON|then|valueOf|toString|constructor|prototype)$/;
const KNOWN = [
  /^stkKlines\.\d{6}$/,                      // 部分票没内嵌 K 线 → 走网络兜底
  /^chenPool\.\w+\.\d+\.metrics\.tailRisk$/, // 观察池不产出 tailRisk，前端有兜底
  /^us\.themePicks$/,                        // 美股题材掘金已下线（R98j），前端不读
  /\.mcap$/,                                 // mcapCutOf() 读的是 duanban.mcapCut[].mcap（数据在），
                                             // 报成 chenPool.*.mcap 是探针的路径串扰，不是真 miss
  /fundCheck\.staleAsOf$/,                   // 只有「跨日保留」的资金数据才带这个标记，当日数据没有
  /\.metrics\.shrinkT$/,                    // 观察池不产出 shrinkT（只 loose 有），前端判空后不渲染
  /^sectorMap\.\d{6}$/,                     // 代码→板块映射覆盖不全，取不到就不显示板块徽章
  /\.direction$/,                            // 只有 macroNews/intlNews/bankViews 逐条要求 direction，
                                             // bullNews/bearNews 的条件渲染会判空
  /\.horizon$/,                              // impacts[].horizon（影响时长）AI 未逐条写，前端判空
  /^duanban\.star\.picks\.\d+\.score$/,      // 明日可下单的 picks 只有「被技术池覆盖到」的票才由前端
                                             // 回填 score（index.html 里 `if (tp.score != null) p.score = ...`），
                                             // 未覆盖的读出 undefined；下游有 `!= null` 判空，不渲染即不显示
  /^us\.sectorRank$/,                        // 美股侧不产出板块排名（A股独有）
  /^us\.sectors(Up|Down)\.\d+\.(upCount|downCount|ztCount|pct5|netInflow|leadPct)$/,
                                             // 美股板块只有涨跌幅与成分股，A 股那套富化字段一律没有；
                                             // 前端共用渲染函数，判空即不显示（不是 bug，是两侧数据不同构）
];
/* 只报**高频** miss（默认 ≥3 次）。
   为什么：本项目「前端判空 + 数据不一定有」的写法很普遍（谐波 T1 到位、impact horizon、
   观察池 shrinkT…），全列出来 1300 条，等于把工具变成噪音源——2026-11-09 首跑就是这样，
   真正那一条 `duanban.star.code`（47 次）差点被淹掉。
   高频＝同一次渲染里被反复读，通常是「遍历整池读一个不存在的键」这类真 bug；
   低频多是条件字段。要看全部加 --verbose。 */
const MIN_HITS = VERBOSE ? 1 : 3;
const rows = [...misses.values()]
  .filter((r) => !NOISE.test(r.path))
  .filter((r) => !KNOWN.some((re) => re.test(r.path)))
  .filter((r) => r.hits >= MIN_HITS)
  .sort((a, b) => b.hits - a.hits);

if (!rows.length) {
  console.log('OK 前端没有读不存在的字段（去噪后 0 条）');
  process.exit(0);
}
console.log('发现 %d 条「读了但不存在」的字段访问：', rows.length);
for (const r of rows.slice(0, VERBOSE ? rows.length : 30)) {
  console.log('  %s  (×%d)', r.path, r.hits);
}
if (!VERBOSE && rows.length > 30) console.log('  … 还有 %d 条（加 --verbose 看全部）', rows.length - 30);
process.exit(1);
