const $ = (selector) => document.querySelector(selector);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const names = {
  claude: "Claude Code",
  codex: "Codex",
  opencode: "OpenCode",
  goose: "Goose",
  manual: "Imported",
};
const paths = {
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  settings:
    '<path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
  map: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="15" width="6" height="6" rx="1"/><path d="M9 6h9v9M6 9v9h9"/>',
  folder: '<path d="M3 7V5h6l2 2h10v13H3Z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  fit: '<path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/>',
  refresh: '<path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5"/>',
  moon: '<path d="M20 15A8 8 0 0 1 9 4a8.5 8.5 0 1 0 11 11Z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  circle: '<circle cx="12" cy="12" r="8"/>',
  link: '<path d="m10 13 4-4M8 15l-1 1a3.5 3.5 0 0 1-5-5l5-5a3.5 3.5 0 0 1 5 0M16 9l1-1a3.5 3.5 0 0 1 5 5l-5 5a3.5 3.5 0 0 1-5 0"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
};
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.circle}</svg>`;
const logo = (h) =>
  `<img class="logo" src="/assets/${Object.hasOwn(names, h) && h !== "manual" ? h : "relay"}.svg" alt="">`;
const button = (action, label, symbol, extra = "") =>
  `<button data-action="${action}" ${label ? `aria-label="${esc(label)}"` : ""} ${extra}>${symbol ? icon(symbol) : ""}${label ? `<span>${label}</span>` : ""}</button>`;
const num = (n) => new Intl.NumberFormat().format(n);
const ago = (date) => {
  if (!date) return "Indexed locally";
  const m = Math.max(0, Math.floor((Date.now() - Date.parse(date)) / 60000));
  return m < 1
    ? "Changed just now"
    : m < 60
      ? `${m}m ago`
      : m < 1440
        ? `${Math.floor(m / 60)}h ago`
        : `${Math.floor(m / 1440)}d ago`;
};
let token =
  new URLSearchParams(location.hash.slice(1)).get("token") ||
  sessionStorage.getItem("relay-token") ||
  "";
if (location.hash) {
  history.replaceState(null, "", location.pathname);
  if (token) sessionStorage.setItem("relay-token", token);
}
document.documentElement.dataset.theme =
  localStorage.getItem("relay-theme") ||
  (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
const state = {
  data: null,
  project: "",
  harness: "",
  search: "",
  selected: new Set(),
  detail: null,
  panel: null,
  draft: null,
  events: new Map(),
  positions: {},
  view: { x: 50, y: 80, z: 1 },
  drag: null,
  fetching: false,
  busy: false,
  export: null,
  revision: 0,
};
let timer,
  noticeTimer,
  pollTimer,
  mapSnapshot = "";
function notice(message) {
  $("#notice").textContent = message;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => ($("#notice").textContent = ""), 6000);
}
async function api(path, body) {
  const response = await fetch("/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: "Bearer " + token,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(20000),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) {
      token = "";
      sessionStorage.removeItem("relay-token");
      login();
    }
    throw Error(data.error?.message || "The local request failed.");
  }
  return data;
}
function login() {
  clearTimeout(pollTimer);
  $("#app").innerHTML =
    `<main class="login"><img src="/assets/relay.svg" width="44" height="44" alt="Context Relay"><h1>Your threads, connected.</h1><p>Open this workspace from <code>relay ui</code> to connect securely to your local service. You can also paste its local access token below.</p><form id="login"><label>Local access token<input name="token" type="password" autocomplete="off" required></label><button class="primary">Open thread map</button></form></main>`;
  $("#login").onsubmit = (event) => {
    event.preventDefault();
    token = new FormData(event.currentTarget).get("token");
    sessionStorage.setItem("relay-token", token);
    start().catch((e) => notice(e.message));
  };
}
function sessions() {
  return state.data.sessions
    .filter(
      (s) =>
        (!state.project || s.projectId === state.project) &&
        (!state.harness || s.harness === state.harness) &&
        (!state.search ||
          `${s.title} ${names[s.harness]} ${project(s.projectId)?.name} ${s.externalId}`
            .toLowerCase()
            .includes(state.search.toLowerCase())),
    )
    .sort((a, b) =>
      (b.activityAt || b.updatedAt).localeCompare(a.activityAt || a.updatedAt),
    );
}
const session = (id) => state.data.sessions.find((s) => s.id === id);
const project = (id) => state.data.projects.find((p) => p.id === id);
function layout() {
  for (const s of sessions().slice(0, 80)) {
    if (state.positions[s.id]) continue;
    let slot = 0,
      p;
    do {
      p = {
        x: (slot % 3) * 365,
        y: Math.floor(slot / 3) * 260 + (slot % 3 === 1 ? 45 : 0),
      };
      slot++;
    } while (
      Object.values(state.positions).some(
        (other) =>
          Math.abs(other.x - p.x) < 300 && Math.abs(other.y - p.y) < 210,
      )
    );
    state.positions[s.id] = p;
  }
}
function shell() {
  $("#app").innerHTML =
    `<div class="shell"><header class="topbar"><div class="brand"><img src="/assets/relay.svg" alt="">Context Relay<small>LOCAL WORKSPACE</small></div><div class="top-actions"><div id="sync-status" class="status"></div>${button("refresh", "Refresh", "refresh")}${button("sources", "Sources", "settings")}${button("theme", "", "moon", 'aria-label="Toggle color theme" title="Toggle color theme"')}</div></header><aside class="sidebar" aria-label="Workspace navigation" id="sidebar"></aside><main class="main"><header class="workspace-heading"><div><h1>Thread map</h1><p>Your coding sessions, connected by the context you choose.</p></div><select class="mobile-project" id="mobile-project" aria-label="Filter by project"></select><label class="searchbox">${icon("search")}<input id="search" type="search" placeholder="Find a thread…" aria-label="Find a thread"></label></header><div class="workarea"><section id="canvas" class="canvas" tabindex="0" aria-label="Thread map. Drag cards to move; drag an output dot onto a thread to relay context. Use the card buttons for keyboard connections."><div class="canvas-top"><span class="eyebrow" id="map-caption"></span></div><div class="world" id="world"><svg class="wires" viewBox="-10000 -10000 20000 20000" aria-label="Context connections"><defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs><g id="edges"></g></svg><div id="nodes"></div></div><div id="empty"></div><div class="canvas-controls">${button("zoom-out", "", "minus", 'aria-label="Zoom out"')}<span class="zoom-label" id="zoom-label">100%</span>${button("zoom-in", "", "plus", 'aria-label="Zoom in"')}${button("fit", "", "fit", 'aria-label="Fit threads to view" title="Fit to view (F)"')}</div><div class="canvas-legend">Drag to move · Scroll to pan · Ctrl + scroll to zoom</div><div id="selection"></div></section><div id="panel-host"></div></div></main></div>`;
  $("#search").oninput = (e) => {
    state.search = e.target.value;
    renderMap();
    fit(0.65);
  };
  $("#mobile-project").onchange = (e) => filterProject(e.target.value);
  $("#canvas").addEventListener("pointerdown", pointerDown);
  $("#canvas").addEventListener("wheel", wheel, { passive: false });
  $("#canvas").addEventListener("keydown", (event) => {
    if (event.target !== $("#canvas")) return;
    if (event.key.toLowerCase() === "f") {
      event.preventDefault();
      fit();
    }
    const delta = {
      ArrowLeft: [40, 0],
      ArrowRight: [-40, 0],
      ArrowUp: [0, 40],
      ArrowDown: [0, -40],
    }[event.key];
    if (delta) {
      event.preventDefault();
      state.view.x += delta[0];
      state.view.y += delta[1];
      transform();
    }
  });
  renderChrome();
  renderMap();
}
function renderChrome() {
  const { discovery: d } = state.data;
  const status = $("#sync-status");
  if (!status) return;
  status.className =
    "status " + (!d.enabled ? "off" : d.scanning ? "busy" : "");
  status.textContent = d.scanning
    ? "Discovering threads…"
    : d.enabled
      ? "Local sync on"
      : "Local sync paused";
  $("#sidebar").innerHTML =
    `<section><div class="eyebrow">Workspace</div><button data-project="" class="${!state.project ? "active" : ""}">${icon("map")}All threads<span class="count">${state.data.sessions.length}</span></button></section><section><div class="eyebrow">Harnesses</div>${Object.entries(
      names,
    )
      .filter(
        ([h]) =>
          h !== "manual" || state.data.sessions.some((s) => s.harness === h),
      )
      .map(
        ([h, name]) =>
          `<button data-harness="${h}" class="${state.harness === h ? "active" : ""}">${logo(h)}${name}<span class="count">${state.data.sessions.filter((s) => s.harness === h && (!state.project || s.projectId === state.project)).length}</span></button>`,
      )
      .join(
        "",
      )}</section><section><div class="eyebrow">Projects</div>${state.data.projects.map((p) => `<button data-project="${esc(p.id)}" class="${state.project === p.id ? "active" : ""}" title="${esc(p.root)}">${icon("folder")}<span class="project-name">${esc(p.name)}</span><span class="count">${state.data.sessions.filter((s) => s.projectId === p.id).length}</span></button>`).join("") || '<p class="muted">Projects appear as threads are found.</p>'}</section><div class="sidebar-footer">${icon("lock")}<p>On your machine.<br>Context moves when you choose.</p></div>`;
  $("#mobile-project").innerHTML =
    `<option value="">All projects</option>${state.data.projects.map((p) => `<option value="${esc(p.id)}" ${state.project === p.id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}`;
}
function transform() {
  const { x, y, z } = state.view;
  $("#world").style.transform = `translate(${x}px, ${y}px) scale(${z})`;
  $("#zoom-label").textContent = Math.round(z * 100) + "%";
}
function renderMap() {
  if (!$("#nodes")) return;
  layout();
  const visible = sessions().slice(0, 80);
  $("#nodes").innerHTML = visible
    .map(
      (s) =>
        `<article class="thread ${state.selected.has(s.id) || state.detail === s.id ? "selected" : ""}" data-node="${esc(s.id)}" tabindex="0" role="button" aria-label="Open thread: ${esc(s.title)}"><div class="thread-top"><div class="logo-wrap">${logo(s.harness)}</div><div><div class="harness-label">${names[s.harness]}</div><div class="time">${esc(ago(s.activityAt))}</div></div><button class="pick ${state.selected.has(s.id) ? "picked" : ""}" data-pick="${esc(s.id)}" aria-label="${state.selected.has(s.id) ? "Deselect" : "Select"} source: ${esc(s.title)}" aria-pressed="${state.selected.has(s.id)}">${icon(state.selected.has(s.id) ? "check" : "plus")}</button></div><h3>${esc(s.title)}</h3><div class="thread-bottom"><span>${esc(project(s.projectId)?.name)}</span><code>${s.captureError ? "Needs attention" : !s.captured ? "Indexing…" : num(s.eventCount) + " events" + (s.captureWarning ? " · excerpts" : "")}</code></div><span class="port in"></span><button class="port" data-port="${esc(s.id)}" aria-label="Connect from ${esc(s.title)}" title="Drag onto another thread to relay context"></button></article>`,
    )
    .join("");
  for (const node of document.querySelectorAll("[data-node]")) {
    const p = state.positions[node.dataset.node];
    node.style.transform = `translate(${p.x}px, ${p.y}px)`;
  }
  $("#map-caption").textContent =
    `${project(state.project)?.name || "Your local sessions"} / ${visible.length}${sessions().length > 80 ? " of " + sessions().length : ""} threads`;
  $("#empty").innerHTML = visible.length
    ? ""
    : `<div class="empty"><div class="empty-art">${["claude", "codex", "opencode"].map((h) => `<div class="logo-wrap">${logo(h)}</div>`).join("")}</div><h2>${state.search || state.harness || state.project ? "No matching threads" : state.data.discovery.scanning ? "Finding your local threads…" : "Your next idea starts here."}</h2><p>${state.search || state.harness || state.project ? "Try another project, harness, or search." : state.data.discovery.scanning ? "Relay is looking in your harnesses’ local session stores. Threads will appear as they are discovered." : "Open a session in a supported harness, then refresh. Sources shows which harnesses are available and how to connect them."}</p>${button("sources", "View sources", "settings")}</div>`;
  mapSnapshot = JSON.stringify([state.data.sessions, state.data.relays]);
  renderSelection();
  drawEdges();
  transform();
}
function renderSelection() {
  $("#selection").innerHTML = state.selected.size
    ? `<div class="selection-bar"><small>${state.selected.size} source${state.selected.size > 1 ? "s" : ""} selected</small>${button("compose", "Relay context", "link", 'class="primary"')}${button("clear", "", "close", 'aria-label="Clear selection"')}</div>`
    : "";
}
function bezier(a, b) {
  const bend = Math.max(75, Math.abs(b.x - a.x) * 0.48);
  return `M ${a.x} ${a.y} C ${a.x + bend} ${a.y}, ${b.x - bend} ${b.y}, ${b.x} ${b.y}`;
}
function relayStatus(h) {
  return h.status === "offered"
    ? h.freshness?.current === false
      ? "stale"
      : "awaiting target"
    : h.status;
}
function drawEdges() {
  const points = sessions()
    .slice(0, 80)
    .map((s) => state.positions[s.id])
    .filter(Boolean);
  if (state.drag?.kind === "connect") points.push(state.drag.end);
  const minX = Math.min(0, ...points.map((p) => p.x)) - 500,
    minY = Math.min(0, ...points.map((p) => p.y)) - 500;
  const width = Math.max(0, ...points.map((p) => p.x)) + 800 - minX,
    height = Math.max(0, ...points.map((p) => p.y)) + 500 - minY;
  const svg = $(".wires");
  svg.setAttribute("viewBox", `${minX} ${minY} ${width} ${height}`);
  Object.assign(svg.style, {
    left: minX + "px",
    top: minY + "px",
    width: width + "px",
    height: height + "px",
  });
  const visible = new Set(
    sessions()
      .slice(0, 80)
      .map((s) => s.id),
  );
  let html = "";
  for (const relay of state.data.relays) {
    const target = relay.packet.destination.sessionId;
    if (!visible.has(target)) continue;
    for (const source of relay.packet.sources) {
      if (!visible.has(source.sessionId)) continue;
      const a = state.positions[source.sessionId],
        b = state.positions[target];
      html += `<path class="wire ${relayStatus(relay)} ${state.detail === relay.id ? "selected" : ""}" data-edge="${esc(relay.id)}" d="${bezier({ x: a.x + 274, y: a.y + 94 }, { x: b.x - 7, y: b.y + 94 })}" marker-end="url(#arrowhead)"><title>${esc(relayStatus(relay))}: ${esc(relay.packet.goal)}</title></path><text class="edge-label" data-edge="${esc(relay.id)}" x="${(a.x + 274 + b.x - 7) / 2}" y="${(a.y + b.y) / 2 + 82}" text-anchor="middle">${esc(relayStatus(relay))}</text>`;
    }
  }
  if (state.drag?.kind === "connect") {
    const a = state.positions[state.drag.id];
    html += `<path class="wire preview" d="${bezier({ x: a.x + 274, y: a.y + 94 }, state.drag.end)}"/>`;
  }
  $("#edges").innerHTML = html;
}
function fit(minScale = 0.15) {
  const canvas = $("#canvas");
  if (!canvas) return;
  const nodes = sessions()
    .slice(0, 80)
    .map((s) => state.positions[s.id])
    .filter(Boolean);
  if (!nodes.length) return;
  const left = Math.min(...nodes.map((p) => p.x)),
    right = Math.max(...nodes.map((p) => p.x)) + 266,
    top = Math.min(...nodes.map((p) => p.y)),
    bottom = Math.max(...nodes.map((p) => p.y)) + 200;
  const z = Math.min(
    1.1,
    Math.max(
      minScale,
      Math.min(
        (canvas.clientWidth - 100) / (right - left),
        (canvas.clientHeight - 130) / (bottom - top),
      ),
    ),
  );
  state.view = {
    z,
    x: (canvas.clientWidth - (right - left) * z) / 2 - left * z,
    y:
      (bottom - top) * z > canvas.clientHeight - 100
        ? 80 - top * z
        : (canvas.clientHeight - (bottom - top) * z) / 2 - top * z,
  };
  transform();
}
function zoom(factor, cx, cy) {
  const canvas = $("#canvas");
  cx ??= canvas.clientWidth / 2;
  cy ??= canvas.clientHeight / 2;
  const old = state.view.z,
    z = Math.min(1.8, Math.max(0.15, old * factor));
  state.view.x = cx - ((cx - state.view.x) * z) / old;
  state.view.y = cy - ((cy - state.view.y) * z) / old;
  state.view.z = z;
  transform();
}
function wheel(e) {
  if (e.target.closest(".canvas-controls,.empty,.selection-bar")) return;
  e.preventDefault();
  if (e.ctrlKey || e.metaKey) {
    const r = $("#canvas").getBoundingClientRect();
    zoom(Math.exp(-e.deltaY * 0.007), e.clientX - r.left, e.clientY - r.top);
  } else {
    state.view.x -= e.deltaX;
    state.view.y -= e.deltaY;
    transform();
  }
}
function worldPoint(e) {
  const r = $("#canvas").getBoundingClientRect();
  return {
    x: (e.clientX - r.left - state.view.x) / state.view.z,
    y: (e.clientY - r.top - state.view.y) / state.view.z,
  };
}
let suppressClick = false;
function pointerDown(e) {
  if (
    e.button !== 0 ||
    e.target.closest(".canvas-controls,.empty,.selection-bar,[data-pick]")
  )
    return;
  const port = e.target.closest("[data-port]"),
    node = e.target.closest("[data-node]");
  if (e.target.closest("[data-edge]")) return;
  const kind = port ? "connect" : node ? "move" : "pan";
  state.drag = {
    kind,
    id: (port || node)?.dataset[port ? "port" : "node"],
    startX: e.clientX,
    startY: e.clientY,
    view: { ...state.view },
    origin: node ? { ...state.positions[node.dataset.node] } : null,
    moved: false,
    end: worldPoint(e),
  };
  $("#canvas").setPointerCapture(e.pointerId);
}
window.addEventListener("pointermove", (e) => {
  const d = state.drag;
  if (!d) return;
  const dx = e.clientX - d.startX,
    dy = e.clientY - d.startY;
  if (Math.hypot(dx, dy) > 4) d.moved = true;
  if (!d.moved) return;
  if (d.kind === "pan") {
    state.view.x = d.view.x + dx;
    state.view.y = d.view.y + dy;
    transform();
  }
  if (d.kind === "move") {
    const p = {
      x: Math.max(-9900, Math.min(9600, d.origin.x + dx / state.view.z)),
      y: Math.max(-9900, Math.min(9600, d.origin.y + dy / state.view.z)),
    };
    state.positions[d.id] = p;
    const node = document.querySelector(`[data-node="${CSS.escape(d.id)}"]`);
    node.style.transform = `translate(${p.x}px, ${p.y}px)`;
    drawEdges();
  }
  if (d.kind === "connect") {
    d.end = worldPoint(e);
    drawEdges();
    document
      .querySelectorAll(".target-hover")
      .forEach((n) => n.classList.remove("target-hover"));
    const target = document
      .elementFromPoint(e.clientX, e.clientY)
      ?.closest("[data-node]");
    if (target && target.dataset.node !== d.id)
      target.classList.add("target-hover");
  }
});
window.addEventListener("pointerup", async (e) => {
  const d = state.drag;
  if (!d) return;
  state.drag = null;
  suppressClick = d.moved;
  setTimeout(() => (suppressClick = false), 0);
  document
    .querySelectorAll(".target-hover")
    .forEach((n) => n.classList.remove("target-hover"));
  if (d.kind === "move" && d.moved)
    api("/graph/position", { sessionId: d.id, ...state.positions[d.id] }).catch(
      (e) => notice(e.message),
    );
  if (d.kind === "connect" && d.moved) {
    const target = document
      .elementFromPoint(e.clientX, e.clientY)
      ?.closest("[data-node]");
    if (target) {
      if (!state.selected.has(d.id)) state.selected = new Set([d.id]);
      await compose(target.dataset.node);
    } else notice("Drop the connector onto a destination thread.");
  }
  drawEdges();
});
window.addEventListener("pointercancel", () => cancelDrag());
function cancelDrag() {
  if (state.drag?.kind === "move")
    state.positions[state.drag.id] = state.drag.origin;
  state.drag = null;
  document
    .querySelectorAll(".target-hover")
    .forEach((n) => n.classList.remove("target-hover"));
  renderMap();
}
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (state.drag) cancelDrag();
    else closePanel();
  }
  if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-node]")) {
    e.preventDefault();
    openThread(e.target.dataset.node);
  }
});
function filterProject(id) {
  state.project = id;
  state.harness = "";
  state.selected.clear();
  closePanel();
  renderChrome();
  renderMap();
  fit(0.65);
}
function pick(id) {
  const s = session(id);
  if (state.selected.has(id)) state.selected.delete(id);
  else {
    const first = session([...state.selected][0]);
    if (first && first.projectId !== s.projectId) {
      notice("Choose source threads from the same project.");
      return;
    }
    if (state.selected.size >= 20) {
      notice("Choose up to 20 source threads.");
      return;
    }
    state.selected.add(id);
  }
  renderMap();
}
function panel(title, body, footer = "") {
  const wasOpen = document.body.classList.contains("panel-open");
  document.body.classList.add("panel-open");
  $("#panel-host").innerHTML =
    `<aside class="drawer" aria-label="${esc(title)}"><header class="drawer-head"><h2>${title}</h2>${button("close", "", "close", 'aria-label="Close panel"')}</header><div class="drawer-body">${body}</div>${footer ? `<footer class="drawer-foot">${footer}</footer>` : ""}</aside>`;
  if (!wasOpen) requestAnimationFrame(() => fit(0.65));
}
function closePanel() {
  state.panel = null;
  state.detail = null;
  state.draft = null;
  state.export = null;
  state.revision++;
  document.body.classList.remove("panel-open");
  if ($("#panel-host")) $("#panel-host").innerHTML = "";
  renderMap();
}
async function loadEvents(s, all = false) {
  const offset = all ? 0 : Math.max(0, s.eventCount - 40);
  const data = await api(
    `/sessions/${encodeURIComponent(s.id)}/events?offset=${offset}&limit=${all ? 20000 : 40}`,
  );
  state.events.set(s.id, { revision: s.revision, events: data, offset });
  return data;
}
function eventMarkup(e, s, selectable = false) {
  const key = JSON.stringify([s.id, e.id]);
  const checked = state.draft?.evidence.has(key);
  return `<div class="event ${checked ? "selected" : ""}"><label class="event-label">${selectable ? `<input type="checkbox" data-evidence="${esc(key)}" ${checked ? "checked" : ""}>` : ""}<span>${esc(e.role)} · ${esc(names[s.harness])}</span></label>${e.omittedCharacters ? `<p class="description">Excerpt · ${num(e.omittedCharacters)} characters omitted</p>` : ""}<pre>${esc(e.text)}</pre></div>`;
}
async function openThread(id) {
  state.panel = "thread";
  state.detail = id;
  state.draft = null;
  const revision = ++state.revision;
  const s = session(id);
  renderMap();
  panel(
    "Thread details",
    `<p class="description">Loading local transcript…</p>`,
  );
  try {
    await loadEvents(s);
    if (state.revision === revision) threadPanel();
  } catch (e) {
    if (state.revision === revision)
      panel("Thread details", `<p class="note warn">${esc(e.message)}</p>`);
  }
}
function threadPanel() {
  const s = session(state.detail);
  if (!s) return;
  const data = state.events.get(s.id),
    related = state.data.relays.filter(
      (h) =>
        h.packet.destination.sessionId === s.id ||
        h.packet.sources.some((x) => x.sessionId === s.id),
    );
  panel(
    "Thread details",
    `<div class="route"><span>${logo(s.harness)}${names[s.harness]}</span><h2>${esc(s.title)}</h2></div><div class="detail-grid"><div><small>Project</small><strong>${esc(project(s.projectId)?.name)}</strong></div><div><small>Captured events</small><strong>${num(s.eventCount)}</strong></div></div><p class="native-id">${esc(s.externalId)}</p>${s.captureError ? `<p class="note warn">${esc(s.captureError)}</p>` : ""}${s.captureWarning ? `<p class="note warn">${esc(s.captureWarning)}</p>` : ""}${!s.captured ? '<p class="note">This thread is queued for capture. It will update automatically.</p>' : ""}${related.length ? `<section class="relay-list"><div class="eyebrow">Connections</div>${related.map((h) => `<button data-relay="${esc(h.id)}">${esc(h.packet.goal.slice(0, 60))}<span class="pill">${relayStatus(h)}</span></button>`).join("")}</section>` : ""}<section><div class="section-label"><h3>Recent captured context</h3><small>${data?.offset ? "Last 40 events" : "All captured events"}</small></div>${data?.events.map((e) => eventMarkup(e, s)).join("") || '<p class="description">No completed messages have been captured yet.</p>'}${data?.offset ? button("all-events", "Load earlier messages") : ""}</section>`,
    button("use-source", "Use as source", "plus", 'class="primary"') +
      button("compose-thread", "Relay context", "link"),
  );
}
async function compose(targetId = "") {
  if (!state.selected.size) {
    notice("Select a source thread first.");
    return;
  }
  const sources = [...state.selected].map(session).filter(Boolean),
    projectId = sources[0].projectId;
  if (sources.some((s) => !s.captured || s.captureError)) {
    notice(
      "Wait for the source threads to finish indexing or resolve their capture errors.",
    );
    return;
  }
  if (
    targetId &&
    (state.selected.has(targetId) || session(targetId)?.projectId !== projectId)
  ) {
    notice("Choose another thread in the same project as the sources.");
    return;
  }
  state.panel = "compose";
  state.detail = null;
  state.export = null;
  const revision = ++state.revision;
  state.draft = {
    projectId,
    sources: sources.map((s) => ({ sessionId: s.id, revision: s.revision })),
    target: targetId,
    goal: `Continue: ${sources[0].title}`,
    openWork: "",
    files: "",
    evidence: new Set(),
    loading: true,
  };
  panel(
    "Review context",
    `<p class="description">Preparing source messages…</p>`,
  );
  renderMap();
  try {
    await Promise.all(sources.map((s) => loadEvents(s)));
    if (state.revision !== revision) return;
    for (const s of sources)
      for (const e of state.events.get(s.id).events.slice(-6))
        state.draft.evidence.add(JSON.stringify([s.id, e.id]));
    state.draft.loading = false;
    composePanel();
  } catch (e) {
    if (state.revision === revision) {
      state.draft = null;
      panel("Review context", `<p class="note warn">${esc(e.message)}</p>`);
    }
  }
}
function composePanel() {
  const d = state.draft;
  if (!d) return;
  const sourceIds = new Set(d.sources.map((s) => s.sessionId)),
    targets = state.data.sessions.filter(
      (s) => s.projectId === d.projectId && !sourceIds.has(s.id),
    );
  panel(
    "Review context",
    `<p class="description">Choose the context this thread should carry forward. Messages remain source evidence; your continuation goal is shown separately.</p><div class="route">${d.sources
      .map(({ sessionId }) => {
        const s = session(sessionId);
        return `<span>${logo(s.harness)}${esc(s.title)}</span>`;
      })
      .join(
        "",
      )}</div><label><span class="field-label">Destination thread</span><select id="target"><option value="">Choose a thread…</option>${targets.map((s) => `<option value="${esc(s.id)}" ${d.target === s.id ? "selected" : ""}>${esc(names[s.harness])} · ${esc(s.title)}</option>`).join("")}</select></label>${!targets.length ? '<p class="note">Start another session in this project, then refresh to use it as a destination.</p>' : ""}<label><span class="field-label">What should the next agent do?</span><textarea id="goal" maxlength="4000">${esc(d.goal)}</textarea></label><details><summary>Open work & file references</summary><label><span class="field-label">Open work, one item per line</span><textarea id="open-work">${esc(d.openWork)}</textarea></label><label><span class="field-label">Project-relative files, one per line</span><textarea id="files" placeholder="src/server.ts">${esc(d.files)}</textarea></label><p class="description">Relay records file digests and Git state to check freshness. It does not move files.</p></details><div class="section-label"><h3>Messages to carry forward</h3><small id="evidence-count">${d.evidence.size} selected</small></div>${d.sources
      .map(({ sessionId }) => {
        const s = session(sessionId),
          data = state.events.get(sessionId);
        return `<section><div class="section-label"><h3>${esc(s.title)}</h3><small>${data.offset ? "Last 40" : ""}</small></div>${data.events.map((e) => eventMarkup(e, s, true)).join("")}${data.offset ? `<button data-earlier="${esc(s.id)}">Load earlier messages</button>` : ""}</section>`;
      })
      .join(
        "",
      )}<p class="note">This creates a handoff for the chosen thread. Paste the continuation prompt there so the receiving agent can inspect and accept it.</p>`,
    button(
      "send",
      d.replacesHandoffId ? "Update connection" : "Create handoff",
      "link",
      'class="primary"',
    ) + button("close", "Cancel"),
  );
  $("#target").onchange = (e) => (d.target = e.target.value);
  $("#goal").oninput = (e) => (d.goal = e.target.value);
  $("#open-work").oninput = (e) => (d.openWork = e.target.value);
  $("#files").oninput = (e) => (d.files = e.target.value);
}
async function send() {
  const d = state.draft;
  if (!d || state.busy) return;
  if (!d.target || !d.goal.trim() || !d.evidence.size) {
    notice(
      "Choose a destination, add a goal, and select at least one message.",
    );
    return;
  }
  if (d.evidence.size > 100) {
    notice("Select at most 100 messages per handoff.");
    return;
  }
  state.busy = true;
  const btn = $('[data-action="send"]');
  btn.disabled = true;
  btn.textContent = "Creating handoff…";
  try {
    const h = await api("/relays", {
      projectId: d.projectId,
      title: d.goal.slice(0, 200),
      goal: d.goal,
      sources: d.sources,
      evidence: [...d.evidence].map((key) => {
        const [sessionId, eventId] = JSON.parse(key);
        return { sessionId, eventId };
      }),
      openWork: d.openWork
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean),
      files: d.files
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean),
      destinationSessionId: d.target,
      ...(d.replacesHandoffId
        ? { replacesHandoffId: d.replacesHandoffId }
        : {}),
    });
    state.selected.clear();
    state.draft = null;
    await refresh();
    await openRelay(h.id);
    notice("Handoff ready. Open it in the destination thread to continue.");
  } catch (e) {
    notice(e.message);
    if (state.draft) composePanel();
  } finally {
    state.busy = false;
  }
}
async function openRelay(id) {
  state.panel = "relay";
  state.detail = id;
  state.draft = null;
  const revision = ++state.revision;
  panel("Connection", `<p class="description">Loading handoff…</p>`);
  renderMap();
  try {
    const data = await api(`/handoffs/${encodeURIComponent(id)}/export`);
    if (state.revision !== revision) return;
    state.export = data;
    relayPanel();
  } catch (e) {
    notice(e.message);
  }
}
function relayPanel() {
  const h =
    state.data.relays.find((h) => h.id === state.detail) ||
    state.export?.handoff;
  if (!h || !state.export) return;
  const target = session(h.packet.destination.sessionId);
  const c = state.data.connections.find(
    (c) =>
      c.projectId === target?.projectId &&
      c.harness === target.harness &&
      c.configureMcp,
  );
  panel(
    "Connection",
    `<span class="pill ${h.freshness?.current === false ? "warn" : ""}">${esc(relayStatus(h))}</span><h2>${esc(h.packet.goal)}</h2><div class="route">${h.packet.sources
      .map((s) => {
        const source = session(s.sessionId);
        return `<span>${logo(source.harness)}${esc(source.title)}</span>`;
      })
      .join(
        "",
      )}<span>${icon("arrow")}To ${esc(names[target.harness])}</span><strong>${esc(target.title)}</strong></div>${h.freshness?.current === false ? `<p class="note warn">The source context has changed. Review a new handoff before continuing.<br>${h.freshness.reasons.map(esc).join("<br>")}</p>` : ""}${h.status === "offered" ? `<p class="description">Paste this into the destination thread. Its agent will inspect the handoff and acknowledge it through Relay’s MCP tools.</p>${!c ? `<p class="note">Enable Relay’s MCP tools for ${esc(names[target.harness])} in this project first. Reload the harness’s MCP configuration when prompted.</p>${button("enable-mcp", "Enable MCP for this project", "settings")}` : c.harness === "goose" ? '<p class="note">Goose needs the stdio extension command from Sources. The generated manifest does not register an extension automatically.</p>' : '<p class="description">MCP configuration is installed. If the thread was already open, reload its MCP connection before pasting.</p>'}<textarea class="prompt" aria-label="Continuation prompt" readonly>${esc(state.export.prompt)}</textarea><p class="description">Acceptance is reported by the receiving harness with the destination ID. Relay cannot independently authenticate which native chat called an MCP tool.</p>` : h.status === "accepted" ? '<p class="note">The receiving agent acknowledged this handoff. Acceptance records receipt, not completion of the task.</p>' : '<p class="note">This handoff was cancelled. Its history remains visible.</p>'}<div class="detail-grid"><div><small>Source threads</small><strong>${h.packet.sources.length}</strong></div><div><small>Messages included</small><strong>${h.packet.evidence.length}</strong></div></div><details><summary>Inspect packet contents</summary><pre class="native-id">${esc(JSON.stringify(h.packet, null, 2))}</pre></details>`,
    (h.status === "offered"
      ? button("copy", "Copy continuation prompt", null, 'class="primary"')
      : "") +
      button("export", "Export Markdown") +
      (h.status === "offered"
        ? button("rewire", "Change destination") +
          button("cancel-relay", "Cancel handoff", null, 'class="danger"')
        : "") +
      (h.freshness?.current === false
        ? button("renew-relay", "Review updated context", "refresh")
        : ""),
  );
}
function sourcesPanel() {
  state.panel = "sources";
  state.detail = null;
  state.draft = null;
  const d = state.data.discovery;
  panel(
    "Sources & settings",
    `<p class="description">Relay discovers local session history and captures completed messages. Discovery continues while the local Relay service runs.</p><div class="note"><strong>${d.scanning ? "Discovery in progress" : d.enabled ? "Background discovery is on" : "Background discovery is paused"}</strong><br>${d.lastScan ? "Last scan " + new Date(d.lastScan).toLocaleTimeString() : "Waiting for the first scan."}${d.error ? `<br><span class="danger">${esc(d.error)}</span>` : ""}</div><div>${[
      "claude",
      "codex",
      "opencode",
      "goose",
    ]
      .map((h) => {
        const source = d.sources.find((s) => s.harness === h);
        return `<section class="source-card"><div class="source-card-header"><div class="logo-wrap">${logo(h)}</div><strong>${names[h]}</strong><label><input type="checkbox" data-source-toggle="${h}" aria-label="Discover ${names[h]}" ${d.harnesses.includes(h) ? "checked" : ""}></label></div><p data-source-message="${h}">${esc(source?.message || "Checking local availability…")}</p><p data-source-count="${h}">${num(state.data.sessions.filter((s) => s.harness === h).length)} indexed threads · ${esc(source?.state || "checking")}</p></section>`;
      })
      .join(
        "",
      )}</div><p class="description">Completed messages stay in this local workspace. No LLM or cloud account is needed for discovery. Pausing stops new imports; existing history remains available.</p><div>${button("pause", d.enabled ? "Pause background discovery" : "Resume discovery", d.enabled ? null : "refresh")}</div><details><summary>Project MCP connections</summary>${
      state.data.connections
        .filter((c) => c.configureMcp)
        .map(
          (c) =>
            `<div class="source-card"><strong>${esc(names[c.harness])} · ${esc(project(c.projectId)?.name)}</strong><p>${esc(c.configPath || "Command manifest")}</p>${c.harness === "goose" ? `<button data-command="${esc(c.id)}">Show Goose extension command</button>` : ""}</div>`,
        )
        .join("") ||
      '<p class="description">Enable MCP when creating your first handoff. Discovery does not edit harness settings.</p>'
    }</details><p class="description">Custom source paths, checkpoint history, and advanced capture controls are available in the <a href="/advanced">advanced workspace</a>.</p>`,
    button("refresh", "Scan now", "refresh", 'class="primary"'),
  );
}
async function refresh() {
  if (state.fetching || !token) return;
  state.fetching = true;
  try {
    const data = await api("/graph");
    state.data = data;
    if (state.panel === "sources")
      for (const source of data.discovery.sources) {
        const message = document.querySelector(
            `[data-source-message="${source.harness}"]`,
          ),
          count = document.querySelector(
            `[data-source-count="${source.harness}"]`,
          );
        if (message) message.textContent = source.message;
        if (count)
          count.textContent = `${data.sessions.filter((s) => s.harness === source.harness).length} indexed threads · ${source.state}`;
      }
    for (const [id, p] of Object.entries(data.positions))
      if (!state.positions[id]) state.positions[id] = p;
    if ($("#canvas")) {
      renderChrome();
      if (
        !state.drag &&
        mapSnapshot !== JSON.stringify([state.data.sessions, state.data.relays])
      )
        renderMap();
      if (state.panel === "relay" && state.export) {
        const h = data.relays.find((h) => h.id === state.detail);
        if (h) state.export.handoff = h;
        relayPanel();
      }
    }
    return data;
  } finally {
    state.fetching = false;
  }
}
async function poll() {
  try {
    if (!document.hidden) await refresh();
  } catch (e) {
    if (token && $("#sync-status")) {
      $("#sync-status").textContent = "Service unavailable · retrying";
      $("#sync-status").className = "status off";
    }
  } finally {
    if (token) pollTimer = setTimeout(poll, 3000);
  }
}
async function start() {
  if (!token) {
    login();
    return;
  }
  await refresh();
  if (!state.data.discovery.configured) {
    await api("/workspace", { enabled: true });
    await refresh();
  }
  shell();
  fit(0.65);
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, 1500);
}
document.addEventListener("change", async (e) => {
  if (e.target.matches("[data-evidence]")) {
    const d = state.draft,
      key = e.target.dataset.evidence;
    e.target.checked ? d.evidence.add(key) : d.evidence.delete(key);
    e.target.closest(".event").classList.toggle("selected", e.target.checked);
    $("#evidence-count").textContent = d.evidence.size + " selected";
  }
  if (e.target.matches("[data-source-toggle]")) {
    const enabled = [
      ...document.querySelectorAll("[data-source-toggle]:checked"),
    ].map((e) => e.dataset.sourceToggle);
    try {
      await api("/workspace", {
        enabled: state.data.discovery.enabled,
        harnesses: enabled,
      });
      await refresh();
    } catch (error) {
      notice(error.message);
    }
  }
});
document.addEventListener("click", async (e) => {
  if (suppressClick) return;
  const pickButton = e.target.closest("[data-pick]");
  if (pickButton) {
    pick(pickButton.dataset.pick);
    return;
  }
  const port = e.target.closest("[data-port]");
  if (port) {
    if (!state.selected.has(port.dataset.port))
      state.selected = new Set([port.dataset.port]);
    await compose();
    return;
  }
  const node = e.target.closest("[data-node]");
  if (node) {
    if (e.shiftKey) pick(node.dataset.node);
    else await openThread(node.dataset.node);
    return;
  }
  const edge = e.target.closest("[data-edge],[data-relay]");
  if (edge) {
    await openRelay(edge.dataset.edge || edge.dataset.relay);
    return;
  }
  const p = e.target.closest("[data-project]");
  if (p) {
    filterProject(p.dataset.project);
    return;
  }
  const h = e.target.closest("[data-harness]");
  if (h) {
    state.harness =
      state.harness === h.dataset.harness ? "" : h.dataset.harness;
    renderChrome();
    renderMap();
    fit();
    return;
  }
  const earlier = e.target.closest("[data-earlier]");
  if (earlier) {
    try {
      await loadEvents(session(earlier.dataset.earlier), true);
      if (state.draft) composePanel();
    } catch (error) {
      notice(error.message);
    }
    return;
  }
  const command = e.target.closest("[data-command]");
  if (command) {
    try {
      const args = await api(
        `/connections/${encodeURIComponent(command.dataset.command)}/command`,
      );
      notice("Goose command loaded below.");
      command.insertAdjacentHTML(
        "afterend",
        `<pre class="native-id">${esc(args.map((x) => JSON.stringify(x)).join(" "))}</pre>`,
      );
    } catch (error) {
      notice(error.message);
    }
    return;
  }
  const action = e.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  try {
    switch (action) {
      case "theme": {
        const theme =
          document.documentElement.dataset.theme === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = theme;
        localStorage.setItem("relay-theme", theme);
        break;
      }
      case "refresh":
        if (!state.data.discovery.enabled) {
          notice(
            "Discovery is paused. Resume it in Sources to scan for new threads.",
          );
          break;
        }
        await api("/workspace/scan", {});
        await refresh();
        if (state.panel === "sources") sourcesPanel();
        break;
      case "sources":
        state.revision++;
        sourcesPanel();
        break;
      case "pause":
        await api("/workspace", { enabled: !state.data.discovery.enabled });
        await refresh();
        sourcesPanel();
        break;
      case "close":
        closePanel();
        break;
      case "fit":
        fit();
        break;
      case "zoom-in":
        zoom(1.2);
        break;
      case "zoom-out":
        zoom(1 / 1.2);
        break;
      case "clear":
        state.selected.clear();
        renderMap();
        break;
      case "use-source":
        pick(state.detail);
        break;
      case "compose-thread":
        state.selected = new Set([state.detail]);
        await compose();
        break;
      case "compose":
        await compose();
        break;
      case "all-events":
        await loadEvents(session(state.detail), true);
        threadPanel();
        break;
      case "send":
        await send();
        break;
      case "copy":
        await navigator.clipboard.writeText(state.export.prompt);
        notice(
          "Continuation prompt copied. Paste it into the destination thread.",
        );
        break;
      case "export": {
        const a = document.createElement("a"),
          url = URL.createObjectURL(
            new Blob([state.export.markdown], { type: "text/markdown" }),
          );
        a.href = url;
        a.download = `relay-handoff-${state.export.handoff.id}.md`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        break;
      }
      case "cancel-relay":
        await api(`/handoffs/${encodeURIComponent(state.detail)}/cancel`, {});
        await refresh();
        notice("Handoff cancelled. The connection remains in history.");
        break;
      case "rewire": {
        const h = state.export.handoff;
        state.selected = new Set(h.packet.sources.map((s) => s.sessionId));
        await compose(h.packet.destination.sessionId);
        if (state.draft) {
          state.draft.replacesHandoffId = h.id;
          state.draft.goal = h.packet.goal;
          composePanel();
        }
        break;
      }
      case "renew-relay": {
        const h = state.export.handoff;
        state.selected = new Set(h.packet.sources.map((s) => s.sessionId));
        await compose(h.packet.destination.sessionId);
        break;
      }
      case "enable-mcp": {
        const target = session(
            state.export.handoff.packet.destination.sessionId,
          ),
          existing = state.data.connections.find(
            (c) =>
              c.projectId === target.projectId && c.harness === target.harness,
          );
        await api("/connections", {
          projectId: target.projectId,
          harness: target.harness,
          configureMcp: true,
          capture: existing?.capture ?? true,
          ...(existing?.source ? { source: existing.source } : {}),
          ...(existing?.executable ? { executable: existing.executable } : {}),
        });
        await refresh();
        notice(
          "MCP configuration saved. Reload the receiving harness’s MCP connection.",
        );
        break;
      }
    }
  } catch (error) {
    notice(error.message);
  }
});
window.addEventListener("resize", () => {
  clearTimeout(timer);
  timer = setTimeout(() => {
    if ($("#canvas")) transform();
  }, 150);
});
start().catch((e) => {
  if (token) {
    $("#app").innerHTML =
      `<main class="login"><h1>Couldn’t open the local workspace.</h1><p>${esc(e.message)}</p><p>Run <code>relay ui</code> again to reconnect.</p></main>`;
  }
  notice(e.message);
});
