// Browser QA for the demo UI (Sprint 4), run by hand; not part of `node --test`.
// Needs Chrome and a running demo:  python scripts/demo.py --port 8020
//   node tests/js/browser_qa.mjs <outdir> http://127.0.0.1:8020/ [liveUrl]
// For liveUrl, start a second demo with live mode on but no way to spend, e.g.
//   MODEL_API_KEY=dummy LIVE_DATA_DIR=<scratch dir> python scripts/demo.py --port 8021 --live --confirm-paid-calls --budget-usd 0.01
// (the $0.01 budget is below any call's worst case, so even an accidental submit is refused).
// Checks at 390/768/1024/1280/1440px: page overflow, graph clipping, label/label, label/node and
// curve/label collisions, node text inside its box, inspector filled, console errors, keyboard focus.
// Never clicks "Run live investigation" or any review button.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

const [out, REPLAY, LIVE] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const port = 9334;
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${out}/profile`,
  "--no-first-run", "--disable-gpu", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 100 && !target; i++) {
  try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((t) => t.type === "page"); } catch { /* */ }
  if (!target) await sleep(250);
}
if (!target) { chrome.kill(); throw new Error("no DevTools target"); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0;
const pending = new Map();
const problems = [];
ws.addEventListener("message", (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  if (msg.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(msg.params.type)) {
    problems.push(`console.${msg.params.type}: ${msg.params.args.map((a) => a.value ?? a.description).join(" ")}`);
  }
  if (msg.method === "Runtime.exceptionThrown") problems.push(`exception: ${msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text}`);
  if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") problems.push(`log: ${msg.params.entry.text} ${msg.params.entry.url || ""}`);
});
const send = (method, params = {}) => new Promise((r) => { const n = ++id; pending.set(n, r); ws.send(JSON.stringify({ id: n, method, params })); });
const evaluate = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
await send("Runtime.enable");
await send("Log.enable");

const size = (width, height) => send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 600 });
const go = async (url) => { await send("Page.navigate", { url }); await sleep(1800); };
async function shot(name, full = true) {
  const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: full });
  writeFileSync(`${out}/${name}.png`, Buffer.from(r.result.data, "base64"));
}
const click = (sel) => evaluate(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return false; e.click(); return true; })()`);

// In-page checks: page overflow, graph clipping, label collisions, inspector, banner.
const CHECK = `(() => {
  const r = (e) => e.getBoundingClientRect();
  const overlap = (a, b) => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1;
  const svg = document.getElementById('graph');
  const svgShown = svg && getComputedStyle(svg).display !== 'none' && !document.getElementById('graph-wrap').hidden;
  const res = { vw: innerWidth, docW: document.documentElement.scrollWidth, svgShown, clipped: [], overlaps: [] };
  if (svgShown) {
    const box = r(svg);
    res.graphPx = Math.round(box.width) + 'x' + Math.round(box.height);
    for (const g of svg.querySelectorAll('.node rect, .edge-label, .node-label, .node-sub')) {
      const b = r(g);
      if (b.left < box.left - 1 || b.right > box.right + 1 || b.top < box.top - 1 || b.bottom > box.bottom + 1) res.clipped.push(g.textContent || 'node');
    }
    const labels = [...svg.querySelectorAll('.edge-label')];
    const nodes = [...svg.querySelectorAll('.node rect')];
    for (let i = 0; i < labels.length; i++) {
      for (let j = i + 1; j < labels.length; j++) if (overlap(r(labels[i]), r(labels[j]))) res.overlaps.push(labels[i].textContent + ' / ' + labels[j].textContent);
      for (const n of nodes) if (overlap(r(labels[i]), r(n))) res.overlaps.push(labels[i].textContent + ' / node');
    }
    // Does any curve pass through another edge's label? Sample each path in screen coordinates.
    res.curveThroughLabel = [];
    const edges = [...svg.querySelectorAll('.edge')];
    const ctm = svg.getScreenCTM();
    for (const e of edges) {
      const path = e.querySelector('.edge-line');
      const len = path.getTotalLength();
      const pts = [];
      for (let k = 0; k <= 60; k++) { const p = path.getPointAtLength(len * k / 60).matrixTransform(ctm); pts.push(p); }
      for (const other of edges) {
        if (other === e) continue;
        const b = r(other.querySelector('.edge-label'));
        if (pts.some((p) => p.x > b.left + 1 && p.x < b.right - 1 && p.y > b.top + 1 && p.y < b.bottom - 1)) {
          res.curveThroughLabel.push(other.querySelector('.edge-label').textContent);
        }
      }
    }
    res.textOutsideNode = [];
    for (const g of svg.querySelectorAll('.node')) {
      const box = r(g.querySelector('rect'));
      for (const t of g.querySelectorAll('text')) {
        const b = r(t);
        if (b.left < box.left || b.right > box.right) res.textOutsideNode.push(t.textContent);
      }
    }
    const lab = svg.querySelector('.edge-label');
    res.edgeLabelPx = lab ? Math.round(r(lab).height * 10) / 10 : null;
    const nl = svg.querySelector('.node-label');
    res.nodeLabelPx = nl ? Math.round(r(nl).height * 10) / 10 : null;
  }
  const list = document.querySelectorAll('#relationship-list .rel').length;
  const insp = document.getElementById('inspector').textContent;
  res.relationships = list;
  res.inspectorPopulated = /Source text verified/.test(insp);
  res.banner = (document.getElementById('mode-banner') || {}).textContent || null;
  res.secondBanner = Boolean(document.getElementById('live-banner') && !document.getElementById('live-banner').hidden && document.getElementById('live-banner').textContent);
  res.selectedScenario = [...document.querySelectorAll('.scenario.selected')].map((b) => b.dataset.scenario).join(',');
  const insTop = r(document.getElementById('inspector')).top + scrollY;
  const graphWrap = document.getElementById('graph-wrap');
  res.inspectorTopPx = Math.round(insTop);
  res.resultBottomPx = Math.round(r(document.querySelector('.panel.result')).bottom + scrollY);
  return res;
})()`;

const report = { checks: {}, problems };
const WIDTHS = [[390, 844], [768, 1024], [1024, 768], [1280, 720], [1440, 900]];
for (const [w, h] of WIDTHS) {
  await size(w, h);
  await go(REPLAY);
  await shot(`landing_${w}`, w < 1024);
  await shot(`landing_${w}_full`);
  report.checks[`landing_${w}`] = await evaluate(CHECK);
  for (const sid of ["E12", "E05", "E13", "E01"]) {
    await click(`[data-scenario="${sid}"]`);
    await sleep(1200);
    report.checks[`${sid}_${w}`] = await evaluate(CHECK);
    if (w === 1280 || w === 1440) await shot(`${sid}_${w}`, false);
  }
}

// Keyboard: Tab through the first controls and record the focused element and its outline.
await size(1280, 720);
await go(REPLAY);
const focus = [];
for (let i = 0; i < 6; i++) {
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  await sleep(150);
  focus.push(await evaluate(`(() => { const e = document.activeElement; const s = getComputedStyle(e);
    return (e.dataset.scenario || e.dataset.view || e.tagName) + ' outline=' + s.outlineStyle + ' ' + s.outlineWidth; })()`));
}
await shot("keyboard_focus_1280", false);
// Enter on a focused scenario card switches scenario.
await evaluate(`document.querySelector('[data-scenario="E05"]').focus()`);
await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" });
await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
await sleep(1200);
report.keyboard = { focus, enterSelects: await evaluate(`[...document.querySelectorAll('.scenario.selected')].map(b => b.dataset.scenario).join(',')`) };

await size(1440, 900);
await go(REPLAY);
await click('[data-view="evaluation"]');
await sleep(1200);
await shot("evaluation_1440");
report.checks.evaluation_1440 = await evaluate(`({ docW: document.documentElement.scrollWidth, vw: innerWidth, banner: document.getElementById('mode-banner')?.textContent })`);

await size(390, 844);
await go(REPLAY);
await click('[data-view="evaluation"]');
await sleep(1200);
await shot("evaluation_390");
report.checks.evaluation_390 = await evaluate(`({ docW: document.documentElement.scrollWidth, vw: innerWidth })`);

if (LIVE) {
  for (const [w, h] of [[1280, 720], [390, 844]]) {
    await size(w, h);
    await go(LIVE);
    await shot(`live_mode_${w}`, true);
    report.checks[`live_${w}`] = await evaluate(CHECK);
  }
  await click('[data-view="evaluation"]');
  await sleep(800);
  report.checks.live_evaluation_banner = await evaluate(`document.getElementById('mode-banner')?.textContent`);
}

writeFileSync(`${out}/report.json`, JSON.stringify(report, null, 2));
ws.close();
chrome.kill();
console.log(JSON.stringify(report, null, 1));
