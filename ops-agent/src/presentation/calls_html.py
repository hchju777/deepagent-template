"""코드 호출 흐름의 사람용 한 장 — `output/graph/<gbm>/calls.html` (11d 6c-1b).

심볼 인덱스(사내 심볼 7천)를 한 장에 다 그리면 읽을 수 없다 — flow.html이 심볼 층을 안 그리는 것과 같은 이유다. 그래서
**골라서 펼쳐 보는** 화면이다: 왼쪽에서 출발점(자원·진입점·검색)을 고르면 가운데에 그 주변만 열(단계)로 펼친다.

- 자원을 고르면: 진입점 → … → 쓰는 함수 → [자원] → 읽는 함수 → … → 진입점(라우트). 데이터가 어디서 와서 어디로 나가나.
- 함수를 고르면: 왼쪽에 부르는 쪽(진입점까지), 오른쪽에 부르는 대상과 그 함수들이 쓰고 읽는 자원.

샘플 몇 개를 CLI로 확인하는 것으로는 안심이 안 된다는 사내 요청에서 나왔다 — 사람이 넓게 훑어 끊긴 곳을 눈으로 찾는다.

외부 참조가 없다(사내망에서 CDN이 안 열린다 — flow.html과 같다). **판정은 파이썬이 한다**: 자원 쪽 진입점 경로는 질의
(`query.callers`)로 미리 계산해 싣고, 진입점·베이스·라우트 표시도 싣는다 — 브라우저에서 같은 판정을 새로 짜면 시험이 그걸
못 지킨다. 함수 쪽은 질의 그래프의 인접 목록만 싣고 브라우저는 단계별로 펼치기만 한다(심볼마다 미리 펼치면 수십 MB다).
코드에서 온 문자열(이름·데코레이터)은 전부 `textContent`로만 넣는다.
"""
from __future__ import annotations

import json
from html import escape
from typing import Callable

from src.knowledge import query as qy
from src.knowledge.index import Index, Symbol
from src.presentation.flow_html import OTHER_COLOR, REPO_COLORS

KINDS = ("function", "method", "class")
PATHS_PER_USE = 8          # 쓰는(읽는) 함수 하나당 싣는 진입점 경로 — 그 너머는 "+N"으로 말한다


def calls_data(index: Index, *, service_of: Callable[[Symbol], str | None]) -> dict:
    graph = qy.Graph(index)
    ids = [s.id for s in index.symbols if s.kind in KINDS]
    pos = {sid: i for i, sid in enumerate(ids)}
    res: list[dict] = []
    res_at: dict[tuple[str, str], int] = {}
    nodes: list[dict] = []
    for sid in ids:
        s = index.symbols[sid]
        entry = ""
        if not graph.inc.get(sid):
            # 부르는 쪽이 없는데 디스패치로 구현에 닿는 베이스·포트 메서드는 진입점이 아니다(질의와 같은 판정).
            entry = "base" if any(l.mark == qy.DISPATCH for l in graph.out.get(sid, [])) else "entry"
        node = {"s": qy.short(index, sid, graph.modules), "q": s.qualname, "f": s.file, "l": s.line,
                "r": s.repo, "v": service_of(s) or "", "k": s.kind, "rt": qy.route(index, sid) or "", "e": entry}
        uses, seen = [], set()
        for r in s.resources:
            key = (r.kind, r.name)
            if key not in res_at:
                res_at[key] = len(res)
                res.append({"k": r.kind, "n": r.name, "w": [], "r": []})
            d = "w" if r.direction == "writes" else "r"
            if (key, d) not in seen:
                seen.add((key, d))
                uses.append([res_at[key], d, r.line])
        if uses:
            node["u"] = uses
        nodes.append(node)

    def conv(path: list[qy.Link]) -> list | None:
        if any(l.src not in pos or l.dst not in pos for l in path):
            return None
        return [[pos[l.src], pos[l.dst], l.mark] for l in path]

    flows: dict[int, dict] = {}
    for sid in ids:
        for ri, d, line in nodes[pos[sid]].get("u", []):
            if sid not in flows:
                c = qy.callers(graph, [sid])
                entries = [p for p in (conv(x) for x in c.entries) if p is not None]
                dead = [p for p in (conv(x) for x in c.dead_ends) if p is not None]
                flows[sid] = {"p": entries[:PATHS_PER_USE], "d": dead[:PATHS_PER_USE],
                              "more": max(0, len(entries) - PATHS_PER_USE), "cut": c.cut}
            res[ri]["w" if d == "w" else "r"].append({"n": pos[sid], "l": line, **flows[sid]})
    links = [[pos[l.src], pos[l.dst], l.mark, l.line]
             for ls in graph.out.values() for l in ls if l.src in pos and l.dst in pos]
    return {"nodes": nodes, "links": links, "res": res, "paths_per_use": PATHS_PER_USE}


def render(index: Index, *, title: str, built_at: str, commits: dict[str, str],
           service_of: Callable[[Symbol], str | None] = lambda s: None) -> str:
    data = calls_data(index, service_of=service_of)
    repos = sorted({n["r"] for n in data["nodes"]})
    data.update(title=title, built_at=built_at, commits=commits,
                colors={r: (REPO_COLORS[i] if i < len(REPO_COLORS) else OTHER_COLOR) for i, r in enumerate(repos)})
    # `</script>`가 데이터 안에 있으면 문서가 끊긴다 — JSON 안의 `</`를 전부 피한다(flow.html과 같다).
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (_TEMPLATE.replace("__TITLE__", escape(title, quote=True))
                     .replace("__DATA__", blob))


_TEMPLATE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · 코드 흐름</title>
<style>
:root{color-scheme:dark;--surface:#1a1a19;--panel:#232322;--rule:#383835;--ink:#e8e6e3;--dim:#9a9791;--faint:#5d5b57;--write:#d95926;--read:#3987e5;}
html,body{margin:0;height:100%;background:var(--surface);color:var(--ink);font:13px/1.45 system-ui,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif}
body{display:flex;flex-direction:column}
header{flex:none;background:var(--surface);border-bottom:1px solid var(--rule);padding:10px 16px;display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center}
header h1{font-size:15px;margin:0 8px 0 0;font-weight:600}
header .meta{color:var(--dim)}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;color:var(--dim)}
.legend .sw{width:12px;height:12px;border-radius:3px;display:inline-block;vertical-align:-2px;margin-right:5px}
.legend svg{width:34px;height:10px;vertical-align:-1px;margin-right:5px}
.controls{display:flex;gap:12px;align-items:center;color:var(--dim)}
.controls select,.controls button{background:var(--panel);color:var(--ink);border:1px solid var(--rule);border-radius:4px;padding:2px 6px;cursor:pointer}
/* 머리줄이 접혀 두 줄이 되어도 양옆 칸이 가려지지 않게 — 고정 오프셋(53px)으로 두었다가 좁은 창에서 검색칸이 가렸다. */
main{flex:1;min-height:0;display:grid;grid-template-columns:260px 1fr 320px}
nav,aside{overflow:auto;min-height:0;box-sizing:border-box;padding:10px 12px}
nav{border-right:1px solid var(--rule)}
aside{border-left:1px solid var(--rule)}
nav input{width:100%;box-sizing:border-box;background:var(--panel);color:var(--ink);border:1px solid var(--rule);border-radius:4px;padding:4px 8px}
.tabs{display:flex;gap:4px;margin:8px 0}
.tabs button{flex:1;background:var(--panel);color:var(--dim);border:1px solid var(--rule);border-radius:4px;padding:3px 0;cursor:pointer}
.tabs button.on{color:var(--ink);border-color:var(--ink)}
ul{list-style:none;margin:0;padding:0}
nav li,aside li{padding:5px 4px;border-top:1px solid var(--rule);cursor:pointer;word-break:break-all}
nav li:hover,aside li:hover{background:var(--panel)}
.sub{color:var(--dim);font-size:11px;word-break:break-all}
.ev{color:var(--dim);font-size:11px;font-family:ui-monospace,Consolas,monospace;word-break:break-all}
aside h2{font-size:13px;margin:0 0 4px;font-weight:600;word-break:break-all}
aside h3{font-size:12px;color:var(--dim);margin:12px 0 2px;font-weight:600}
#map{overflow:auto;min-height:0;padding:8px 12px 40px}
svg{display:block}
svg text{fill:var(--ink);font-size:12px;font-family:ui-monospace,Consolas,monospace}
svg text.hdr{fill:var(--dim);font-size:11px;font-family:inherit}
svg text.more{fill:var(--dim);font-size:11px}
.node{cursor:pointer}
.node rect.box{fill:var(--panel);stroke:var(--rule)}
.node:hover rect.box{stroke:var(--ink)}
.node.cur rect.box{stroke:var(--ink);stroke-width:2.5}
.edge{fill:none;stroke:#9a9791;stroke-width:1.4;stroke-opacity:.85}
.edge.guess{stroke-dasharray:5 4;stroke-opacity:.6}
.edge.dispatch{stroke-dasharray:2 3;stroke:#d8d4cc}
.edge.w{stroke:var(--write)}
.edge.r{stroke:var(--read)}
.edge:hover{stroke-width:3;stroke-opacity:1}
.hint{color:var(--dim)}
</style></head>
<body>
<header>
  <h1>__TITLE__ · 코드 흐름</h1><span class="meta" id="meta"></span>
  <div class="legend" id="legend"></div>
  <div class="controls">
    <label>단계 <select id="depth"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>5</option></select></label>
    <label><input type="checkbox" id="noguess"> 추정 숨기기</label>
    <button id="back">← 뒤로</button>
  </div>
</header>
<main>
  <nav>
    <input id="find" placeholder="함수·자원 이름으로 찾기">
    <div class="tabs"><button id="tab-res" class="on">자원</button><button id="tab-entry">진입점</button></div>
    <ul id="list"></ul>
  </nav>
  <div id="map"></div>
  <aside id="side"><h2>출발점을 고르면</h2><p class="hint">왼쪽에서 자원을 고르면 진입점 → … → 쓰는 함수 → 자원 → 읽는 함수 → … → 진입점이, 함수를 고르면 부르는 쪽(왼쪽)과 부르는 대상·자원(오른쪽)이 열로 펼쳐진다. 상자를 누르면 그 자리에서 다시 펼친다. 화살표는 호출 방향이고, 함수 사이 선은 한 단계씩만 그린다 — 전부는 이 칸의 목록에 있다.</p></aside>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
const D = JSON.parse(document.getElementById('data').textContent);
const NS = 'http://www.w3.org/2000/svg';
const N = D.nodes, R = D.res;
const out = N.map(() => []), inc = N.map(() => []);
for (const [a, b, m, line] of D.links) { const l = {a, b, m, line}; out[a].push(l); inc[b].push(l); }
const st = {depth: 3, noguess: false, cur: null, hist: []};
const $ = id => document.getElementById(id);
const h = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
const s = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
const color = r => D.colors[r] || '#8a8f98';
const resLabel = r => `[${r.k}] ${r.n}`;
const mark = n => n.e === 'entry' ? '▶ ' : n.e === 'base' ? '◇ ' : '';
// 앞을 자른다 — 코드 이름은 끝(클래스.메서드)이 중요하다. 뒤를 잘랐더니 `deployed_code.DeployedCod…`만 남았다.
const clip = (t, k) => t.length > k ? '…' + t.slice(t.length - k + 1) : t;

$('meta').textContent = `만든 시각 ${D.built_at} · 함수 ${N.length} · 호출 ${D.links.length} · 자원 ${R.length} · ` +
  Object.entries(D.commits).map(([r, c]) => `${r}@${String(c).slice(0, 12)}`).join(', ');
const lg = $('legend');
for (const repo of Object.keys(D.colors)) { const sp = h('span', repo); const sw = h('span', undefined, 'sw'); sw.style.background = D.colors[repo]; sp.prepend(sw); lg.appendChild(sp); }
for (const [name, cls] of [['확실 →', ''], ['추정 ?→', 'guess'], ['디스패치 =>', 'dispatch'], ['쓰기', 'w'], ['읽기', 'r']]) {
  const sp = h('span', name); const sv = s('svg', {viewBox: '0 0 34 10'}); s('line', {x1: 1, y1: 5, x2: 33, y2: 5, class: 'edge ' + cls}, sv); sp.prepend(sv); lg.appendChild(sp);
}
lg.appendChild(h('span', '▶ 진입점 · ◇ 부르는 쪽 없는 베이스'));
$('depth').onchange = ev => { st.depth = +ev.target.value; redraw(); };
$('noguess').onchange = ev => { st.noguess = ev.target.checked; redraw(); };
$('back').onclick = () => { if (st.hist.length) { st.cur = st.hist.pop(); redraw(); } };

let tab = 'res';
const resOrder = R.map((r, i) => i).sort((a, b) => (R[b].w.length + R[b].r.length) - (R[a].w.length + R[a].r.length) || R[a].n.localeCompare(R[b].n));
const entryOrder = N.map((n, i) => i).filter(i => N[i].e === 'entry' && (out[i].length || N[i].u))
  .sort((a, b) => (N[b].rt ? 1 : 0) - (N[a].rt ? 1 : 0) || out[b].length - out[a].length || N[a].q.localeCompare(N[b].q));
function fill() {
  const ul = $('list'); ul.innerHTML = '';
  const q = $('find').value.trim().toLowerCase(), items = [];
  if (q) {
    R.forEach((r, i) => { if (r.n.toLowerCase().includes(q)) items.push(['r', i]); });
    N.forEach((n, i) => { if (n.q.toLowerCase().includes(q)) items.push(['f', i]); });
  } else if (tab === 'res') resOrder.forEach(i => items.push(['r', i]));
  else entryOrder.forEach(i => items.push(['f', i]));
  for (const [kind, i] of items.slice(0, 300)) {
    const li = h('li');
    if (kind === 'r') { li.appendChild(h('div', resLabel(R[i]))); li.appendChild(h('div', `쓰기 ${R[i].w.length} · 읽기 ${R[i].r.length}`, 'sub')); li.onclick = () => go({r: i}); }
    else { const n = N[i]; li.appendChild(h('div', mark(n) + n.s)); li.appendChild(h('div', [n.r, n.v, n.rt].filter(Boolean).join(' · '), 'sub')); li.onclick = () => go({f: i}); }
    ul.appendChild(li);
  }
  if (items.length > 300) ul.appendChild(h('li', `… 외 ${items.length - 300} — 검색으로 좁힌다`, 'sub'));
  if (!items.length) ul.appendChild(h('li', '없다', 'sub'));
}
$('find').oninput = fill;
for (const [id, name] of [['tab-res', 'res'], ['tab-entry', 'entry']]) $(id).onclick = () => {
  tab = name; $('tab-res').classList.toggle('on', name === 'res'); $('tab-entry').classList.toggle('on', name === 'entry'); $('find').value = ''; fill();
};

function go(target) { if (st.cur) st.hist.push(st.cur); st.cur = target; redraw(); }

const CAP = 25, COLW = 236, BOXW = 202, BOXH = 26, ROWH = 32, HDR = 26, PAD = 14;
function model() {
  const cols = new Map(), at = new Map(), edges = [], more = new Map();
  const put = (ci, key, item) => {
    if (at.has(key)) return true;
    const col = cols.get(ci) || [];
    if (col.length >= CAP) { more.set(ci, (more.get(ci) || 0) + 1); return false; }
    col.push({key, ...item}); cols.set(ci, col); at.set(key, ci); return true;
  };
  return {cols, at, edges, more, put};
}
const hidden = m => st.noguess && m === '?→';
function fnView(i) {
  // 부르는 쪽은 왼쪽 열(−단계), 부르는 대상은 오른쪽 열(+단계), 그 함수들이 쓰고 읽는 자원은 맨 오른쪽.
  const M = model(); M.put(0, 'f' + i, {f: i});
  for (const [sign, adj, far] of [[-1, inc, 'a'], [1, out, 'b']]) {
    let front = [i];
    for (let d = 1; d <= st.depth; d++) {
      const next = [];
      for (const x of front) for (const l of adj[x]) {
        if (hidden(l.m)) continue;
        const y = l[far], k = 'f' + y, fresh = !M.at.has(k);
        if (!M.put(sign * d, k, {f: y})) continue;
        M.edges.push(sign < 0 ? {from: k, to: 'f' + x, m: l.m, line: l.line} : {from: 'f' + x, to: k, m: l.m, line: l.line});
        if (fresh) next.push(y);
      }
      front = next;
    }
  }
  // 자원은 실제로 쓰인 마지막 열 바로 다음 — "단계+1"에 고정하면 대상이 얕을 때 빈 열을 건너뛰어 화면 밖으로 밀렸다.
  const placed = [...M.at];
  M.resCol = Math.max(0, ...placed.map(([, ci]) => ci)) + 1;
  for (const [key, ci] of placed) {
    if (ci < 0) continue;
    const x = +key.slice(1);
    for (const [ri, dir, line] of (N[x].u || [])) if (M.put(M.resCol, 'r' + ri, {r: ri})) M.edges.push({from: key, to: 'r' + ri, m: dir, line});
  }
  return M;
}
function resView(ri) {
  // 쓰는 함수는 −1, 그걸 부르는 쪽은 진입점까지 더 왼쪽. 읽는 함수는 +1, 그걸 부르는 쪽은 진입점까지 더 오른쪽.
  // 경로는 파이썬 질의(`query.callers`)가 계산해 실은 것이다.
  const M = model(), r = R[ri]; M.put(0, 'r' + ri, {r: ri});
  for (const [uses, sign, dir] of [[r.w, -1, 'w'], [r.r, 1, 'r']]) for (const u of uses) {
    const uk = 'f' + u.n;
    if (M.put(sign, uk, {f: u.n})) M.edges.push(dir === 'w' ? {from: uk, to: 'r' + ri, m: 'w', line: u.l} : {from: 'r' + ri, to: uk, m: 'r', line: u.l});
    for (const p of u.p.concat(u.d)) {
      if (p.some(x => hidden(x[2]))) continue;
      p.forEach(([a, b, m], j) => { const ak = 'f' + a; if (M.put(sign * (1 + p.length - j), ak, {f: a})) M.edges.push({from: ak, to: 'f' + b, m}); });
    }
  }
  return M;
}
function header(ci, M) {
  if (st.cur.r !== undefined) return ci === 0 ? '자원' : ci === -1 ? '쓰는 함수' : ci === 1 ? '읽는 함수' : ci < 0 ? `← 부르는 쪽 ${-ci - 1}` : `부르는 쪽 ${ci - 1} →`;
  return ci === 0 ? '고른 함수' : ci === M.resCol ? '쓰고 읽는 자원' : ci < 0 ? `부르는 쪽 ${-ci}` : `부르는 대상 ${ci}`;
}
function edgeTitle(e) {
  const nm = k => k[0] === 'r' ? resLabel(R[+k.slice(1)]) : N[+k.slice(1)].q;
  const what = {'→': '부른다', '?→': '부른다(추정)', '=>': '디스패치(베이스 => 구현)', w: '쓴다', r: '읽힌다'}[e.m] || e.m;
  return `${nm(e.from)}\n  ${what}${e.line ? ' (L' + e.line + ')' : ''}\n${nm(e.to)}`;
}
function draw(M, curKey) {
  const map = $('map'); map.innerHTML = '';
  const cis = [...M.cols.keys()].sort((a, b) => a - b), minC = cis[0];
  const pos = new Map(); let H = 0;
  for (const ci of cis) {
    const x = PAD + (ci - minC) * COLW;
    M.cols.get(ci).forEach((it, row) => pos.set(it.key, {x, y: PAD + HDR + row * ROWH, it}));
    H = Math.max(H, PAD + HDR + (M.cols.get(ci).length + (M.more.get(ci) ? 1 : 0)) * ROWH);
  }
  const W = PAD * 2 + (cis[cis.length - 1] - minC) * COLW + BOXW;
  const svg = s('svg', {width: W, height: H + PAD, viewBox: `0 0 ${W} ${H + PAD}`}, map);
  const mk = s('marker', {id: 'ar', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse'}, s('defs', {}, svg));
  s('path', {d: 'M0,0 L10,5 L0,10 z', fill: '#9a9791'}, mk);
  for (const ci of cis) s('text', {x: PAD + (ci - minC) * COLW, y: PAD + 12, class: 'hdr'}, svg).textContent = header(ci, M);
  for (const e of M.edges) {
    const A = pos.get(e.from), B = pos.get(e.to); if (!A || !B) continue;
    // 함수 사이 선은 바깥으로 한 단계씩만 — 이미 다른 열에 놓인 상자로 돌아가는 선이 엉켜 읽을 수 없었다(부르는 곳 28개).
    // 전체 목록은 오른쪽 칸에 있다. 자원으로 가는 선은 다 그린다.
    if (e.from[0] === 'f' && e.to[0] === 'f' && Math.abs(M.at.get(e.from) - M.at.get(e.to)) !== 1) continue;
    const y1 = A.y + BOXH / 2, y2 = B.y + BOXH / 2; let d;
    if (B.x > A.x) { const x1 = A.x + BOXW, x2 = B.x, c = (x2 - x1) / 2; d = `M${x1},${y1} C${x1 + c},${y1} ${x2 - c},${y2} ${x2},${y2}`; }
    else if (B.x < A.x) { const x1 = A.x, x2 = B.x + BOXW, c = (x1 - x2) / 2; d = `M${x1},${y1} C${x1 - c},${y1} ${x2 + c},${y2} ${x2},${y2}`; }
    else { const x = A.x + BOXW; d = `M${x},${y1} C${x + 40},${y1} ${x + 40},${y2} ${x},${y2}`; }
    const cls = {'?→': 'guess', '=>': 'dispatch', w: 'w', r: 'r'}[e.m] || '';
    s('title', {}, s('path', {d, class: 'edge ' + cls, 'marker-end': 'url(#ar)'}, svg)).textContent = edgeTitle(e);
  }
  for (const [key, P] of pos) {
    const it = P.it, g = s('g', {class: 'node' + (key === curKey ? ' cur' : ''), transform: `translate(${P.x},${P.y})`}, svg);
    if (it.r !== undefined) {
      const r = R[it.r];
      s('rect', {class: 'box', width: BOXW, height: BOXH, rx: 13}, g);
      s('text', {x: 12, y: 17}, g).textContent = clip(resLabel(r), 26);
      s('title', {}, g).textContent = `${r.k} ${r.n}\n쓰기 ${r.w.length} · 읽기 ${r.r.length}`;
      g.onclick = () => go({r: it.r});
    } else {
      const n = N[it.f];
      s('rect', {class: 'box', width: BOXW, height: BOXH, rx: 4}, g);
      s('rect', {width: 5, height: BOXH, fill: color(n.r)}, g);
      s('text', {x: 11, y: 17}, g).textContent = mark(n) + clip(n.s, n.e ? 24 : 26);
      s('title', {}, g).textContent = [n.q, `${n.f}:L${n.l}`, [n.r, n.v].filter(Boolean).join(' · '), n.rt ? '라우트 ' + n.rt : ''].filter(Boolean).join('\n');
      g.onclick = () => go({f: it.f});
    }
  }
  // 고른 상자가 화면 가운데에 오게 — 진입점까지 펼치면 열이 화면보다 넓다.
  const P = pos.get(curKey);
  if (P) map.scrollLeft = Math.max(0, P.x + BOXW / 2 - map.clientWidth / 2);
  for (const [ci, k] of M.more) s('text', {x: PAD + (ci - minC) * COLW, y: PAD + HDR + M.cols.get(ci).length * ROWH + 14, class: 'more'}, svg).textContent = `+${k} 더 — 이 열은 ${CAP}개까지`;
}
function sidePanel() {
  const a = $('side'); a.innerHTML = '';
  const item = (top, sub, onclick) => { const li = h('li'); li.appendChild(h('div', top)); li.appendChild(h('div', sub, 'ev')); li.onclick = onclick; return li; };
  if (st.cur.r !== undefined) {
    const r = R[st.cur.r];
    a.appendChild(h('h2', resLabel(r))); a.appendChild(h('div', `쓰기 ${r.w.length} · 읽기 ${r.r.length}`, 'sub'));
    for (const [label, uses] of [['쓰는 함수', r.w], ['읽는 함수', r.r]]) {
      if (!uses.length) continue;
      a.appendChild(h('h3', label)); const ul = a.appendChild(h('ul'));
      for (const u of uses) {
        const n = N[u.n], notes = [`${n.f}:L${u.l}`, `진입점 ${u.p.length}${u.more ? '+' + u.more : ''}`];
        if (u.d.length) notes.push(`부르는 쪽 없는 베이스 ${u.d.length}`); if (u.cut) notes.push('상한에 걸림');
        ul.appendChild(item(mark(n) + n.q, notes.join(' · '), () => go({f: u.n})));
      }
    }
    return;
  }
  const i = st.cur.f, n = N[i];
  a.appendChild(h('h2', mark(n) + n.q));
  a.appendChild(h('div', [`${n.f}:L${n.l}`, n.r, n.v].filter(Boolean).join(' · '), 'sub'));
  if (n.rt) a.appendChild(h('div', '라우트 ' + n.rt, 'sub'));
  if (n.e === 'entry') a.appendChild(h('div', '부르는 곳이 없다 — 진입점', 'sub'));
  if (n.e === 'base') a.appendChild(h('div', '부르는 곳 없는 베이스·포트 메서드 — 프레임워크가 부를 수 있다', 'sub'));
  for (const [label, links, far] of [['바로 부르는 곳', inc[i], 'a'], ['부르는 대상', out[i], 'b']]) {
    if (!links.length) continue;
    a.appendChild(h('h3', `${label} ${links.length}`)); const ul = a.appendChild(h('ul'));
    for (const l of links.slice(0, 200)) {
      const j = l[far], where = l.line ? `${(far === 'a' ? N[j] : n).f}:L${l.line}` : `${N[j].f}:L${N[j].l} (정의)`;
      ul.appendChild(item(`${l.m} ${N[j].q}`, where, () => go({f: j})));
    }
  }
  if (n.u) {
    a.appendChild(h('h3', '쓰고 읽는 자원')); const ul = a.appendChild(h('ul'));
    for (const [ri, d, line] of n.u) ul.appendChild(item(`${d === 'w' ? '쓰기' : '읽기'} ${resLabel(R[ri])}`, `${n.f}:L${line}`, () => go({r: ri})));
  }
}
function redraw() {
  if (!st.cur) return;
  const isRes = st.cur.r !== undefined, key = isRes ? 'r' + st.cur.r : 'f' + st.cur.f;
  draw(isRes ? resView(st.cur.r) : fnView(st.cur.f), key);
  sidePanel();
  // 주소에 남긴다 — 팀원에게 "이 화면"을 건넬 수 있게.
  const tag = isRes ? 'r=' + encodeURIComponent(R[st.cur.r].k + ':' + R[st.cur.r].n) : 'f=' + encodeURIComponent(N[st.cur.f].r + ':' + N[st.cur.f].q);
  try { history.replaceState(null, '', '#' + tag); } catch (e) {}
}
fill();
const want = decodeURIComponent(location.hash.slice(1));
let start = null;
if (want.startsWith('r=')) { const i = R.findIndex(r => r.k + ':' + r.n === want.slice(2)); if (i >= 0) start = {r: i}; }
if (want.startsWith('f=')) { const i = N.findIndex(n => n.r + ':' + n.q === want.slice(2)); if (i >= 0) start = {f: i}; }
if (!start && resOrder.length) start = {r: resOrder[0]};
if (!start && entryOrder.length) start = {f: entryOrder[0]};
if (start) { st.cur = start; redraw(); }
})();
</script>
</body></html>
"""
