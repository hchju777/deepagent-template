"""흐름 그래프의 사람용 한 장 — `output/graph/<gbm>/flow.html`.

**외부 참조가 없다.** graphify의 `graph.html`은 그림 라이브러리(vis-network)를 unpkg.com에서
받아 오게 돼 있어 사내망에서는 빈 화면이다. 팀원에게 파일 하나만 건네고 어떤 설치도
요구하지 않으려면 SVG와 짧은 스크립트를 이 파일 안에 다 넣는 수밖에 없다.

그리는 것은 **오버레이**(서비스 ↔ 토픽·컬렉션·키)다. graphify의 심볼 그래프(사내 1만 노드)는
리드가 기계적으로 탐색하는 층이지 사람이 그림으로 볼 물건이 아니다.

배치는 흐름 방향이다: 왼쪽 열 = 쓰는 서비스, 가운데 = 자원(종류별), 오른쪽 = 읽는 서비스.
한 서비스가 쓰기도 읽기도 하면 양쪽에 다 나온다 — `render_path`의 "쓰기→자원→읽기"와 같은 방향.

색은 **레포마다 하나**, 이름순으로 고정 배정한다(같은 코드를 띄우는 서비스들이 같은 색).
참조 팔레트(다크 표면 #1a1a19 기준으로 검증된 8색)를 쓰되, 색상만으로 다섯 레포를 다 가르는
것은 색각 이상에서는 불가능하므로 모든 노드에 이름을 직접 적고, 레포 머리글과 클릭 초점을
둔다 — 색은 보조 신호다. 아홉 번째 레포부터는 회색이다(색을 돌려 쓰지 않는다).
"""
from __future__ import annotations

import json
from html import escape

REPO_COLORS = ("#3987e5", "#d95926", "#199e70", "#d55181",
               "#c98500", "#9085e9", "#008300", "#e66767")
OTHER_COLOR = "#8a8f98"
KIND_ORDER = ("endpoint", "topic", "collection", "rediskey", "group")
KIND_LABEL = {"endpoint": "끝점", "topic": "토픽", "collection": "컬렉션", "rediskey": "redis 키", "group": "컨슈머 그룹"}


_UP_WRITERS = ("writes", "produces")          # 자원 → 그 자원을 만드는 서비스
_UP_INPUTS = ("reads", "consumes")            # 서비스 → 그 서비스가 읽는 것
UPSTREAM_HOPS = 3


def upstream(overlay: dict, endpoint_id: str, *, hops: int = UPSTREAM_HOPS) -> list[dict]:
    """끝점 하나의 **상류 이야기** — 1홉 끝점이 읽는 자원(추적 엣지, 서빙 서비스는 맥락으로 같이),
    2홉 그 자원을 쓰는 서비스, 3홉 그 서비스가 읽는 것. 셋에서 끊는 이유: 그 너머는 다시 지도 전체다.
    `(source, relation, target, hop)` 목록. 추적 읽기가 없는 끝점(또는 끝점이 아닌 노드)이면 빈 목록 —
    그때는 그림이 1홉 초점으로 돌아간다."""
    links = overlay.get("links", [])
    reads = [e for e in links if e.get("origin") == "trace" and e["source"] == endpoint_id]
    if not reads:
        return []
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add(e: dict, hop: int) -> None:
        key = (e["source"], e["relation"], e["target"])
        if key not in seen:
            seen.add(key)
            out.append({"source": e["source"], "relation": e["relation"], "target": e["target"], "hop": hop})

    for e in links:
        if e["relation"] == "serves" and e["target"] == endpoint_id:
            add(e, 1)
    frontier: set[str] = set()
    for e in reads:
        add(e, 1)
        frontier.add(e["target"])
    first = set(frontier)
    hop = 2
    while frontier and hop <= hops:
        nxt: set[str] = set()
        for e in links:
            if e.get("origin") == "trace":
                continue
            if hop % 2 == 0 and e["target"] in frontier and e["relation"] in _UP_WRITERS:
                add(e, hop)
                nxt.add(e["source"])
            elif hop % 2 == 1 and e["source"] in frontier and e["relation"] in _UP_INPUTS \
                    and e["target"] not in first:
                add(e, hop)
                nxt.add(e["target"])
        frontier, hop = nxt - {endpoint_id}, hop + 1
    return out


def render(overlay: dict, *, title: str, built_at: str, commits: dict[str, str]) -> str:
    """오버레이(graphify 스키마) → 완결된 HTML 문자열."""
    services = sorted((n for n in overlay["nodes"] if n.get("type") == "service"),
                      key=lambda n: (n.get("repo", ""), n["label"]))
    repos = sorted({n.get("repo", "") for n in services})
    colors = {r: (REPO_COLORS[i] if i < len(REPO_COLORS) else OTHER_COLOR)
              for i, r in enumerate(repos)}
    data = {
        "title": title, "built_at": built_at, "commits": commits, "colors": colors,
        "kind_order": list(KIND_ORDER), "kind_label": KIND_LABEL,
        "nodes": [{"id": n["id"], "label": n["label"], "type": n.get("type", "?"),
                   "repo": n.get("repo", ""), "role": n.get("role", ""),
                   "key_path": n.get("key_path", "")} for n in overlay["nodes"]],
        "links": [{"source": e["source"], "target": e["target"], "relation": e["relation"],
                   "confidence": e.get("confidence", "?"), "origin": e.get("origin", "code"),
                   "via": e.get("via", ""),
                   "file": e.get("source_file", ""), "line": e.get("source_location", ""),
                   "text": e.get("text", "")}
                  # 끝점 → 자원 읽기(origin trace)도 싣는다 — 자원 열 안의 선이라 늘 그리면 지도를 흐리므로
                  # CSS로 숨겼다가 초점(클릭)일 때만 켠다. 끝점을 누르면 "무엇을 읽나"가 보여야 한다.
                  for e in overlay["links"] if e["relation"] != "runs"],
    }
    # 끝점마다 상류 3홉을 미리 계산해 싣는다 — 브라우저에서 같은 계산을 또 쓰면 테스트가 못 지킨다.
    data["upstream"] = {n["id"]: up for n in overlay["nodes"] if n.get("type") == "endpoint"
                        for up in [upstream(overlay, n["id"])] if up}
    # `</script>`가 데이터 안에 있으면 문서가 끊긴다 — JSON 안의 `</`를 전부 피한다.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (_TEMPLATE.replace("__TITLE__", escape(title, quote=True))
                     .replace("__DATA__", blob))


_TEMPLATE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · 데이터 흐름</title>
<style>
:root{color-scheme:dark;--surface:#1a1a19;--panel:#232322;--rule:#383835;--ink:#e8e6e3;--dim:#9a9791;--faint:#5d5b57;}
html,body{margin:0;background:var(--surface);color:var(--ink);font:13px/1.45 system-ui,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--surface);border-bottom:1px solid var(--rule);padding:10px 16px;display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center}
header h1{font-size:15px;margin:0 8px 0 0;font-weight:600}
header .meta{color:var(--dim)}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center}
.legend .repo{display:inline-flex;align-items:center;gap:6px;cursor:pointer;padding:2px 6px;border-radius:4px}
.legend .repo:hover{background:var(--panel)}
.legend .sw{width:12px;height:12px;border-radius:3px;display:inline-block}
.legend .rel{display:inline-flex;align-items:center;gap:6px;color:var(--dim)}
.legend .rel svg{width:34px;height:10px}
.filters{display:flex;flex-wrap:wrap;gap:4px 14px;color:var(--dim)}
.filters label{cursor:pointer;user-select:none}
.filters input{vertical-align:-2px;margin-right:4px}
#find{background:var(--panel);color:var(--ink);border:1px solid var(--rule);border-radius:4px;padding:3px 8px;min-width:200px}
main{display:grid;grid-template-columns:1fr 320px;gap:0}
#map{overflow:auto;padding:12px 16px 40px}
svg{display:block}
svg text{fill:var(--ink);font-size:12px;paint-order:stroke;stroke:var(--surface);stroke-width:3px;stroke-linejoin:round}
svg text.hdr{fill:var(--dim);font-size:11px;letter-spacing:.06em;text-transform:uppercase}
svg text.kind{fill:var(--dim);font-size:10px}
.node{cursor:pointer}
.node rect{fill:var(--panel);stroke:var(--rule);stroke-width:1}
.node.svc rect{stroke-width:2}
.node:hover rect{stroke:var(--ink)}
.node.focus rect{stroke:var(--ink);stroke-width:2.5}
.edge{fill:none;stroke-width:1.5;stroke-opacity:.72}
.edge.declares{stroke-dasharray:2 4;stroke-opacity:.45}
.edge.mentions{stroke-dasharray:6 4;stroke-opacity:.35}
.edge:hover{stroke-width:3;opacity:1;stroke-opacity:1}
svg.focused .edge:not(.on){opacity:.07}
svg.focused .node:not(.on):not(.focus) *{opacity:.18}
svg.focused .edge.on{stroke-opacity:1;stroke-width:2.2}
.edge.trace{display:none}
svg.focused .edge.trace.on{display:inline}
svg.focused .edge.on.hop2{stroke-opacity:.72;stroke-width:1.8}
svg.focused .edge.on.hop3{stroke-opacity:.45;stroke-width:1.5}
aside h3{font-size:12px;color:var(--dim);margin:12px 0 2px;font-weight:600}
aside{border-left:1px solid var(--rule);padding:12px 14px;position:sticky;top:53px;height:calc(100vh - 53px);overflow:auto;box-sizing:border-box}
aside h2{font-size:13px;margin:0 0 4px;font-weight:600}
aside .sub{color:var(--dim);margin-bottom:10px}
aside ul{list-style:none;margin:0;padding:0}
aside li{padding:6px 0;border-top:1px solid var(--rule)}
aside li .rel{color:var(--dim);font-family:ui-monospace,Consolas,monospace;font-size:11px}
aside li .ev{color:var(--dim);font-size:11px;font-family:ui-monospace,Consolas,monospace;word-break:break-all}
aside li .txt{color:var(--faint);font-size:11px;font-family:ui-monospace,Consolas,monospace;white-space:pre-wrap;word-break:break-all;margin-top:2px}
aside .hint{color:var(--dim)}
button.clear{background:var(--panel);color:var(--ink);border:1px solid var(--rule);border-radius:4px;padding:2px 8px;cursor:pointer;margin-left:8px}
</style></head>
<body>
<header>
  <h1>__TITLE__ · 데이터 흐름</h1><span class="meta" id="meta"></span>
  <div class="legend" id="legend"></div>
  <div class="filters" id="filters"></div>
  <input id="find" list="names" placeholder="이름으로 찾기 (Enter)"><datalist id="names"></datalist>
</header>
<main>
  <div id="map"></div>
  <aside id="side"><h2>노드를 클릭하면</h2><p class="hint">그 노드에 닿는 선만 남고 나머지는 흐려진다. 끝점을 클릭하면 상류 3홉 — 읽는 자원, 그 자원을 쓰는 서비스, 그 서비스가 읽는 것 — 이 켜진다. 오른쪽에 관계와 근거(파일:줄)가 나온다. 레포 이름을 클릭하면 그 레포 전체. 빈 곳을 클릭하거나 Esc로 되돌린다.</p></aside>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
const D = JSON.parse(document.getElementById('data').textContent);
const NS = 'http://www.w3.org/2000/svg';
const OUT = new Set(['writes','produces']), IN = new Set(['reads','consumes','consumes_as']);
const RELS = ['serves','produces','consumes','writes','reads','consumes_as','declares','mentions'];
const state = {rel: new Set(['serves','produces','consumes','writes','reads','consumes_as']), kind: new Set(D.kind_order), focus: null};
const byId = Object.fromEntries(D.nodes.map(n => [n.id, n]));
const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
const color = n => D.colors[n.repo] || '#8a8f98';

document.getElementById('meta').textContent = `만든 시각 ${D.built_at} · 서비스 ${D.nodes.filter(n=>n.type==='service').length} · 자원 ${D.nodes.filter(n=>n.type!=='service'&&n.type!=='repo').length} · 관계 ${D.links.length}`;
const legend = document.getElementById('legend');
for (const repo of Object.keys(D.colors)) {
  const s = document.createElement('span'); s.className = 'repo'; s.title = `${repo} 전체에 초점`;
  s.innerHTML = `<span class="sw" style="background:${D.colors[repo]}"></span>${repo}`;
  s.onclick = () => focusRepo(repo); legend.appendChild(s);
}
const relLegend = [['서비스 → 자원: produces / writes',''], ['자원 → 서비스: consumes / reads',''], ['declares (config 선언)','2 4'], ['mentions (코드에 이름만)','6 4']];
for (const [name, dash] of relLegend) {
  const s = document.createElement('span'); s.className = 'rel';
  s.innerHTML = `<svg viewBox="0 0 34 10"><line x1="1" y1="5" x2="33" y2="5" stroke="#9a9791" stroke-width="1.5" ${dash?`stroke-dasharray="${dash}"`:''}/></svg>${name}`;
  legend.appendChild(s);
}
const filters = document.getElementById('filters');
const addToggle = (label, on, fn) => { const l = document.createElement('label'); const c = document.createElement('input'); c.type='checkbox'; c.checked=on; c.onchange=()=>{fn(c.checked); draw();}; l.appendChild(c); l.appendChild(document.createTextNode(label)); filters.appendChild(l); };
for (const r of RELS) addToggle(r, state.rel.has(r), v => v ? state.rel.add(r) : state.rel.delete(r));
for (const k of D.kind_order) addToggle(D.kind_label[k] || k, true, v => v ? state.kind.add(k) : state.kind.delete(k));
const names = document.getElementById('names');
for (const n of D.nodes) if (n.type !== 'repo') { const o = document.createElement('option'); o.value = n.label; names.appendChild(o); }
document.getElementById('find').addEventListener('keydown', ev => { if (ev.key === 'Enter') { const n = D.nodes.find(x => x.label === ev.target.value); if (n) focusNode(n.id); } });
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') clearFocus(); });

const ROW = 24, W_SVC = 210, W_RES = 300, GAP = 150, PAD = 20, HDR = 30;
let svg, edgeEls = [], nodeEls = {};

function visibleLinks() {
  const seen = new Map();
  for (const e of D.links) {
    if (!state.rel.has(e.relation) || !state.kind.has((byId[e.target]||{}).type)) continue;
    const k = e.source + '|' + e.relation + '|' + e.target;
    if (seen.has(k)) seen.get(k).evidence.push(e); else seen.set(k, {...e, evidence: [e]});
  }
  return [...seen.values()];
}

function layoutColumn(items, groupKey, y0) {
  // 그룹(레포 또는 종류) 머리글 + 행. y 좌표를 준다.
  let y = y0; const rows = [], groups = [];
  let cur = null;
  for (const it of items) {
    const g = groupKey(it);
    if (g !== cur) { cur = g; groups.push({key: g, y}); y += HDR; }
    rows.push({item: it, y}); y += ROW;
  }
  return {rows, groups, height: y};
}

function draw() {
  const links = visibleLinks();
  const deg = {}; for (const e of links) { deg[e.source]=(deg[e.source]||0)+1; deg[e.target]=(deg[e.target]||0)+1; }
  const resources = D.nodes.filter(n => n.type!=='service' && n.type!=='repo' && state.kind.has(n.type) && deg[n.id])
    .sort((a,b) => D.kind_order.indexOf(a.type)-D.kind_order.indexOf(b.type) || (deg[b.id]||0)-(deg[a.id]||0) || a.label.localeCompare(b.label));
  const svcs = D.nodes.filter(n => n.type==='service').sort((a,b) => a.repo.localeCompare(b.repo) || a.label.localeCompare(b.label));
  // `serves`도 왼쪽 열의 이유다 — 서빙만 하는 서비스가 왼쪽에 없으면 끝점으로 가는 serves 선이 아예 안 그려진다
  // (초점 DOM 덤프로 잡았다: 끝점을 눌러도 서빙 서비스 선이 없었다).
  const writers = svcs.filter(s => links.some(e => e.source===s.id && (OUT.has(e.relation) || e.relation==='declares' || e.relation==='mentions' || e.relation==='serves')));
  const readers = svcs.filter(s => links.some(e => e.source===s.id && IN.has(e.relation)));
  const mid = layoutColumn(resources, n => n.type, PAD);
  const midY = Object.fromEntries(mid.rows.map(r => [r.item.id, r.y]));
  // 서비스는 연결된 자원들의 평균 높이에 두되(가까운 선), 레포 순서와 최소 간격은 지킨다.
  const place = (list, pick) => {
    const want = list.map(s => { const ys = links.filter(e => e.source===s.id && pick(e)).map(e => midY[e.target]).filter(v => v!==undefined); return {s, y: ys.length ? ys.reduce((a,b)=>a+b,0)/ys.length : PAD}; });
    const rows = []; let last = -Infinity, curRepo = null; const groups = [];
    for (const w of want) {
      if (w.s.repo !== curRepo) { curRepo = w.s.repo; const gy = Math.max(w.y - HDR, last + ROW); groups.push({key: curRepo, y: gy}); last = gy + HDR - ROW; }
      const y = Math.max(w.y, last + ROW); rows.push({item: w.s, y}); last = y;
    }
    return {rows, groups, height: last + ROW};
  };
  const left = place(writers, e => !IN.has(e.relation));
  const right = place(readers, e => IN.has(e.relation));
  const H = Math.max(mid.height, left.height, right.height) + PAD;
  const xL = PAD, xM = xL + W_SVC + GAP, xR = xM + W_RES + GAP, W = xR + W_SVC + PAD;

  const map = document.getElementById('map'); map.innerHTML = '';
  svg = el('svg', {width: W, height: H, viewBox: `0 0 ${W} ${H}`}, map);
  svg.addEventListener('click', ev => { if (ev.target === svg) clearFocus(); });
  const defs = el('defs', {}, svg);
  for (const [repo, c] of Object.entries(D.colors)) {
    const m = el('marker', {id: 'arrow-'+slug(repo), viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse'}, defs);
    el('path', {d: 'M0,1 L9,5 L0,9 z', fill: c}, m);
  }
  const m0 = el('marker', {id: 'arrow-dim', viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse'}, defs);
  el('path', {d: 'M0,1 L9,5 L0,9 z', fill: '#9a9791'}, m0);

  const leftY = Object.fromEntries(left.rows.map(r => [r.item.id, r.y])), rightY = Object.fromEntries(right.rows.map(r => [r.item.id, r.y]));
  edgeEls = [];
  for (const e of links) {
    const s = byId[e.source], ry = midY[e.target]; if (!s || ry === undefined) continue;
    const c = e.origin === 'trace' ? '#9a9791' : color(s); let d, cls = 'edge ' + e.relation + (e.origin === 'trace' ? ' trace' : '');
    if (e.origin === 'trace') { const sy = midY[e.source]; if (sy === undefined) continue;
      // 끝점과 자원이 같은 열이라 직선은 노드 위를 지난다 — 열 오른쪽 바깥으로 휘는 호.
      const x = xM + W_RES, y1 = sy + ROW/2 - 2, y2 = ry + ROW/2 - 2, bulge = GAP * 0.55 + Math.min(Math.abs(y2 - y1) / 6, 40);
      d = `M${x},${y1} C${x+bulge},${y1} ${x+bulge},${y2} ${x},${y2}`; }
    else if (IN.has(e.relation)) { const sy = rightY[e.source]; if (sy === undefined) continue;
      const x1 = xM + W_RES, y1 = ry + ROW/2 - 2, x2 = xR, y2 = sy + ROW/2 - 2; d = `M${x1},${y1} C${x1+GAP/2},${y1} ${x2-GAP/2},${y2} ${x2},${y2}`; }
    else { const sy = leftY[e.source]; if (sy === undefined) continue;
      const x1 = xL + W_SVC, y1 = sy + ROW/2 - 2, x2 = xM, y2 = ry + ROW/2 - 2; d = `M${x1},${y1} C${x1+GAP/2},${y1} ${x2-GAP/2},${y2} ${x2},${y2}`; }
    const p = el('path', {d, 'class': cls, stroke: c}, svg);
    if (e.origin === 'trace') p.setAttribute('marker-end', 'url(#arrow-dim)');
    else if (OUT.has(e.relation) || IN.has(e.relation)) p.setAttribute('marker-end', `url(#arrow-${slug(s.repo)})`);
    el('title', {}, p).textContent = `${s.label} —${e.relation}→ ${byId[e.target].label} [${byId[e.target].type}]` + e.evidence.map(v => `\n${v.confidence} · ${v.file}:${v.line}${v.text ? '  ' + v.text : ''}`).join('');
    p.__edge = e; p.addEventListener('click', ev => { ev.stopPropagation(); focusNode(e.source); });
    edgeEls.push(p);
  }
  nodeEls = {};
  const drawSvc = (col, x) => {
    for (const g of col.groups) { const t = el('text', {x, y: g.y + 18, 'class': 'hdr'}, svg); t.textContent = g.key; t.style.fill = D.colors[g.key] || '#9a9791'; t.style.cursor='pointer'; t.onclick = () => focusRepo(g.key); }
    for (const r of col.rows) {
      const n = r.item, g = el('g', {'class': 'node svc', 'data-id': n.id}, svg);
      el('rect', {x, y: r.y, width: W_SVC, height: ROW - 4, rx: 5, stroke: color(n)}, g);
      const t = el('text', {x: x + 10, y: r.y + 14}, g); t.textContent = n.label;
      el('title', {}, g).textContent = `${n.label} · ${n.repo}${n.role ? '\n' + n.role : ''}`;
      g.addEventListener('click', ev => { ev.stopPropagation(); focusNode(n.id); });
      (nodeEls[n.id] = nodeEls[n.id] || []).push(g);
    }
  };
  drawSvc(left, xL); drawSvc(right, xR);
  for (const g of mid.groups) { const t = el('text', {x: xM, y: g.y + 18, 'class': 'hdr'}, svg); t.textContent = D.kind_label[g.key] || g.key; }
  for (const r of mid.rows) {
    const n = r.item, g = el('g', {'class': 'node res', 'data-id': n.id}, svg);
    el('rect', {x: xM, y: r.y, width: W_RES, height: ROW - 4, rx: n.type === 'rediskey' ? 10 : 3}, g);
    const k = el('text', {x: xM + 8, y: r.y + 14, 'class': 'kind'}, g); k.textContent = n.type;
    const t = el('text', {x: xM + 72, y: r.y + 14}, g); t.textContent = n.label;
    el('title', {}, g).textContent = `${n.label} [${n.type}]${n.key_path ? '\n' + n.key_path : ''}\n관계 ${deg[n.id]||0}`;
    g.addEventListener('click', ev => { ev.stopPropagation(); focusNode(n.id); });
    (nodeEls[n.id] = nodeEls[n.id] || []).push(g);
  }
  if (state.focus) applyFocus();
}

function slug(s) { return String(s).replace(/[^A-Za-z0-9]+/g, '_'); }
function focusNode(id) { state.focus = {kind: 'node', id}; applyFocus(); }
function focusRepo(repo) { state.focus = {kind: 'repo', repo}; applyFocus(); }
function clearFocus() { state.focus = null; svg.classList.remove('focused'); for (const p of edgeEls) p.classList.remove('on'); for (const id in nodeEls) nodeEls[id].forEach(g => g.classList.remove('on','focus')); side(null, []); }
const ekey = e => e.source + '|' + e.relation + '|' + e.target;
function applyFocus() {
  const f = state.focus; if (!f) return;
  // 끝점이면 미리 계산한 상류 3홉(파이썬 `upstream`)으로, 아니면 그 노드에 닿는 선(1홉)으로.
  const up = f.kind === 'node' && D.upstream && D.upstream[f.id] ? Object.fromEntries(D.upstream[f.id].map(e => [ekey(e), e.hop])) : null;
  const isMine = e => up ? up[ekey(e)] !== undefined : (f.kind === 'node' ? (e.source === f.id || e.target === f.id) : (byId[e.source].repo === f.repo));
  svg.classList.add('focused'); const on = new Set(); const hits = [];
  for (const p of edgeEls) { const m = isMine(p.__edge); p.classList.toggle('on', m); p.classList.remove('hop1','hop2','hop3');
    if (m) { const hop = up ? up[ekey(p.__edge)] : 1; p.classList.add('hop' + hop); on.add(p.__edge.source); on.add(p.__edge.target); hits.push({...p.__edge, hop}); } }
  for (const id in nodeEls) nodeEls[id].forEach(g => { g.classList.toggle('on', on.has(id)); g.classList.toggle('focus', f.kind === 'node' ? id === f.id : byId[id].repo === f.repo); });
  side(f, hits, !!up);
}
const HOP_LABEL = {1: '1홉 — 이 끝점이 읽는 자원 (서빙 서비스 포함)', 2: '2홉 — 그 자원을 쓰는 서비스', 3: '3홉 — 그 서비스가 읽는 것'};
function side(f, hits, byHop) {
  const a = document.getElementById('side');
  if (!f) { a.innerHTML = '<h2>노드를 클릭하면</h2><p class="hint">그 노드에 닿는 선만 남고 나머지는 흐려진다. 끝점을 클릭하면 상류 3홉이 켜진다. 오른쪽에 관계와 근거(파일:줄)가 나온다. 레포 이름을 클릭하면 그 레포 전체. 빈 곳을 클릭하거나 Esc로 되돌린다.</p>'; return; }
  const head = f.kind === 'node' ? byId[f.id] : null;
  const title = head ? `${head.label}${head.type !== 'service' ? ' [' + head.type + ']' : ''}` : f.repo;
  const sub = head ? (head.type === 'service' ? `${head.repo}${head.role ? ' · ' + head.role : ''}` : (head.key_path || '')) : `레포 전체 · 서비스 ${D.nodes.filter(n => n.repo === f.repo && n.type === 'service').map(n => n.label).join(', ')}`;
  const order = r => RELS.indexOf(r);
  hits.sort((x, y) => (x.hop || 1) - (y.hop || 1) || order(x.relation) - order(y.relation) || byId[x.target].label.localeCompare(byId[y.target].label));
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const item = e => `<li><span class="rel">${esc(e.relation)}${e.via ? ' · ' + esc(e.via) : ''}</span> ${esc(byId[e.source].label)} → ${esc(byId[e.target].label)} <span class="rel">[${esc(byId[e.target].type)}]</span>` +
      e.evidence.map(v => `<div class="ev">${esc(v.confidence)} · ${esc(v.file)}:${esc(v.line)}</div>${v.text ? `<div class="txt">${esc(v.text)}</div>` : ''}`).join('') + '</li>';
  let body = '';
  if (byHop) { let cur = 0; for (const e of hits) { if (e.hop !== cur) { cur = e.hop; body += `</ul><h3>${esc(HOP_LABEL[cur] || cur + '홉')}</h3><ul>`; } body += item(e); } }
  else body = hits.map(item).join('');
  a.innerHTML = `<h2>${esc(title)}<button class="clear" id="clear">되돌리기</button></h2><div class="sub">${esc(sub)} · 관계 ${hits.length}${byHop ? ' · 상류 3홉' : ''}</div><ul>` + body + '</ul>';
  document.getElementById('clear').onclick = clearFocus;
}
draw();
// `#node=<id>`·`#repo=<이름>`으로 열면 그 초점으로 시작한다 — 팀원에게 "여기 봐"를 링크로 건네기 위해서다.
const h = new URLSearchParams(location.hash.slice(1));
if (h.get('node') && byId[h.get('node')]) focusNode(h.get('node')); else if (h.get('repo')) focusRepo(h.get('repo'));
})();
</script>
</body></html>
"""
