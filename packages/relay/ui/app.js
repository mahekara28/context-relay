const refKey = (r) => r.sessionId + ":" + r.eventId;
const $ = (s) => document.querySelector(s);
const escape = (value) =>
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
  manual: "Manual",
};
const icon = (h) =>
  h === "manual"
    ? "◈"
    : `<img class="logo" src="/assets/${escape(h)}.svg" alt="">`;
const badge = (text, type = "") =>
  `<span class="pill ${type}">${escape(text)}</span>`;
const num = (n) => new Intl.NumberFormat().format(n);
const lines = (s) =>
  String(s ?? "")
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
const state = {
  view: "threads",
  projects: [],
  projectId: "",
  sessions: [],
  checkpoints: [],
  handoffs: [],
  connections: [],
  jobs: [],
  provider: {},
  sessionId: "",
  events: [],
  selected: new Set(),
  search: "",
  draft: null,
  merge: new Set(),
  busy: false,
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
async function api(path, body) {
  const response = await fetch("/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: "Bearer " + token,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(45000),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) {
      token = "";
      sessionStorage.removeItem("relay-token");
      login();
    }
    throw Error(data.error?.message || "The request failed.");
  }
  return data;
}
let noticeTimer;
function notice(message) {
  $("#notice").textContent = message;
  $("#notice").classList.add("visible");
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(
    () => $("#notice").classList.remove("visible"),
    7000,
  );
}
function modal(content) {
  $("#modal").innerHTML = content;
  $("#modal").showModal();
}
function closeModal() {
  $("#modal").close();
}
function login() {
  $("#app").innerHTML =
    `<main class="welcome"><div class="brand"><img src="/assets/relay.svg" alt="">Context Relay</div><h1>Your context stays with you.</h1><p class="muted">Open this workspace with <code>relay ui</code>, or enter your local workspace token.</p><form id="login-form"><label>Local access token<input name="token" type="password" required autocomplete="off"></label><p class="help">Stored in this browser tab for this local service only.</p><button class="primary" type="submit">Open workspace</button></form></main>`;
}
function currentProject() {
  return state.projects.find((p) => p.id === state.projectId);
}
function currentSession() {
  return state.sessions.find((s) => s.id === state.sessionId);
}
async function refresh() {
  state.projects = await api("/projects");
  if (!state.projects.some((p) => p.id === state.projectId))
    state.projectId = state.projects[0]?.id || "";
  const q = "?projectId=" + encodeURIComponent(state.projectId);
  if (state.projectId) {
    [state.sessions, state.checkpoints, state.connections] = await Promise.all([
      api("/sessions" + q),
      api("/checkpoints" + q),
      api("/connections" + q),
    ]);
  } else {
    state.sessions = [];
    state.checkpoints = [];
    state.connections = [];
  }
  [state.handoffs, state.jobs, state.provider] = await Promise.all([
    api("/handoffs"),
    api("/jobs"),
    api("/provider"),
  ]);
  state.handoffs = state.handoffs.filter(
    (h) => h.packet.project.id === state.projectId,
  );
  state.jobs = state.jobs.filter((j) =>
    state.connections.some((c) => c.id === j.connectionId),
  );
  if (!state.sessions.some((s) => s.id === state.sessionId)) {
    state.sessionId = state.sessions[0]?.id || "";
    state.selected.clear();
  }
  state.events = state.sessionId
    ? await api("/sessions/" + state.sessionId + "/events?limit=200")
    : [];
  render();
}
const views = [
  ["threads", "≋", "Threads"],
  ["review", "◈", "Review handoff"],
  ["checkpoints", "▣", "Checkpoints"],
  ["handoffs", "↗", "Handoffs"],
  ["connections", "⌘", "Connections"],
  ["architecture", "⌑", "Architecture"],
];
function render() {
  if (!token) return login();
  const project = currentProject();
  $("#app").innerHTML =
    `<div class="shell"><aside class="sidebar"><div class="brand"><img src="/assets/relay.svg" alt="">Context Relay</div><div class="project"><label>Workspace<select id="project-select" aria-label="Project">${state.projects.map((p) => `<option value="${escape(p.id)}" ${p.id === state.projectId ? "selected" : ""}>${escape(p.name)}</option>`).join("") || "<option>No projects yet</option>"}</select></label><button class="small quiet" data-action="add-project" aria-label="Add project">+</button></div><nav class="nav" aria-label="Workspace">${views.map(([id, symbol, label]) => `<button data-view="${id}" ${state.view === id ? 'aria-current="page"' : ""}><span class="symbol" aria-hidden="true">${symbol}</span>${label}</button>`).join("")}</nav><div class="sidefooter"><div class="row"><span class="localdot"></span><span class="tagline">Local workspace</span></div><p class="tagline">Capture → review → continue.<br>Your next thread starts informed.</p><div class="row"><button class="small quiet" data-action="theme">Toggle theme</button><button class="small quiet" data-action="lock">Lock</button></div><span class="tagline mono">v0.3.0-beta.1</span></div></aside><main class="main"><header class="topbar"><div class="breadcrumb">Workspace <span>/ ${escape(project?.name || "Get started")}</span></div><div class="row">${badge("● Local only", "good")}<button class="small" data-action="refresh">Refresh</button><button class="small quiet" data-action="theme" aria-label="Toggle color theme">◐</button></div></header><div id="content" class="content" tabindex="-1">${!project ? onboarding() : { threads: threadsView, review: reviewView, checkpoints: checkpointsView, handoffs: handoffsView, connections: connectionsView, architecture: architectureView }[state.view]()}</div></main></div>`;
}
function head(kicker, title, description, actions = "") {
  return `<div class="pagehead"><div><div class="eyebrow">${kicker}</div><h1>${title}</h1><p>${description}</p></div>${actions}</div>`;
}
function empty(title, text, action = "") {
  return `<div class="panel empty"><span class="mark" aria-hidden="true">↗</span><h2>${title}</h2><p>${text}</p>${action}</div>`;
}
function onboarding() {
  return (
    head(
      "A fresh start",
      "Keep the work. Change the thread.",
      "Connect a local project to find its coding sessions and prepare a reviewed continuation.",
    ) +
    empty(
      "Start with a project",
      "Choose a project directory. Relay captures sessions only when you request it.",
      '<button class="primary" data-action="add-project">Add local project</button><p class="help">Want a guided sample? Run <code>relay demo</code> and refresh.</p>',
    )
  );
}
function growth() {
  if (!state.events.length) return "";
  let sum = 0;
  const values = state.events.map((e) => (sum += e.text.length)),
    max = values.at(-1) || 1;
  const points = values
    .map(
      (v, i) =>
        `${((i / Math.max(values.length - 1, 1)) * 600).toFixed(1)},${(65 - (v / max) * 55).toFixed(1)}`,
    )
    .join(" ");
  return `<div class="growth"><div class="row between"><span class="eyebrow">Captured history growth</span><span class="help">${num(sum)} characters shown</span></div><svg viewBox="0 0 600 80" role="img" aria-label="Cumulative characters across the displayed source events" preserveAspectRatio="none"><polygon class="area" points="0,75 ${points} 600,75"></polygon><polyline class="line" points="${points}"></polyline></svg><div class="row between"><span class="help">First captured event</span><span class="help">${state.events.length} events shown · not model context usage</span></div></div>`;
}
function threadsView() {
  const session = currentSession();
  const visible = state.sessions.filter((s) =>
    (s.title + " " + names[s.harness])
      .toLowerCase()
      .includes(state.search.toLowerCase()),
  );
  return (
    head(
      "01 / Your working history",
      "Pick up where you left off.",
      "A long thread should not become a dead end. Select the evidence your next session needs.",
      '<button class="primary" data-view="connections">Connect a harness</button>',
    ) +
    `<div class="stats"><div class="stat"><span class="eyebrow">Captured threads</span><strong>${num(state.sessions.length)}</strong><small>Across ${new Set(state.sessions.map((s) => s.harness)).size} harnesses</small></div><div class="stat"><span class="eyebrow">Source events</span><strong>${num(state.sessions.reduce((n, s) => n + s.eventCount, 0))}</strong><small>Visible messages & tool results</small></div><div class="stat"><span class="eyebrow">Reviewed checkpoints</span><strong>${state.checkpoints.length}</strong><small>Immutable, with source references</small></div></div>` +
    (state.sessions.length
      ? `<div class="threadlayout"><aside class="panel threadlist" aria-label="Captured sessions"><div class="search"><label class="sr-only" for="session-search">Search threads</label><input id="session-search" type="search" placeholder="Search threads…" value="${escape(state.search)}"></div>${visible.map((s) => `<button class="thread" data-session="${s.id}" aria-pressed="${s.id === state.sessionId}"><div class="row">${icon(s.harness)}${names[s.harness]}${badge("r" + s.revision)}</div><strong>${escape(s.title)}</strong><div class="meta"><span>${s.eventCount} events</span><span>${num(s.characters)} chars</span></div></button>`).join("") || '<div class="empty">No matching threads.</div>'}</aside><section class="panel"><div class="panelhead row between"><div><div class="row">${icon(session.harness)}<h2>${escape(session.title)}</h2></div><p class="help">Captured source · revision ${session.revision}</p></div>${badge(names[session.harness])}</div>${growth()}<div class="timeline">${state.events.map((e, i) => `<article class="event"><div class="row">${badge(e.kind === "compaction" ? "compaction" : e.role, e.role === "user" ? "warn" : "")}<span class="muted mono">${String(i + 1).padStart(2, "0")}</span><label class="check"><input type="checkbox" data-evidence="${escape(e.id)}" ${state.selected.has(e.id) ? "checked" : ""}>Use as evidence</label></div>${e.text.length > 1200 ? `<details><summary>${escape(e.text.slice(0, 180))}…</summary><p>${escape(e.text)}</p></details>` : `<p>${escape(e.text)}</p>`}<div class="sourceid">source / ${escape(e.id)}</div></article>`).join("")}</div><div class="toolbar"><span class="help" id="selected-count">${state.selected.size} selected · source text stays inspectable</span><div class="row">${state.events.length < session.eventCount ? '<button data-action="more-events">Load more</button>' : ""}<button class="primary" data-action="new-draft">Review handoff →</button></div></div></section></div>`
      : empty(
          "No captured threads yet",
          "Connect a harness, discover matching sessions, then capture the ones you want to carry forward.",
          '<button class="primary" data-view="connections">Set up a connection</button>',
        ))
  );
}
function reviewView() {
  const d = state.draft;
  if (!d)
    return (
      head(
        "02 / Make the context explicit",
        "Review before you relay.",
        "A checkpoint records what matters now: the goal, current instructions, evidence and remaining work.",
      ) +
      empty(
        "Start with source evidence",
        "Select events in a captured thread, or merge existing checkpoints.",
        '<button class="primary" data-view="threads">Choose a thread</button><button data-view="checkpoints">Browse checkpoints</button>',
      )
    );
  return (
    head(
      "02 / Review handoff",
      "Carry the decisions forward.",
      "Check the current instructions against their sources. Saving creates an immutable checkpoint.",
    ) +
    `<div class="reviewlayout"><div class="panel"><div class="section stack"><h3><span class="number">01</span>What are we continuing?</h3><label>Checkpoint title<input data-draft="title" value="${escape(d.title)}" maxlength="200"></label><label>Goal<textarea data-draft="goal">${escape(d.goal)}</textarea></label>${d.parents.length ? `<div class="banner">Combining ${d.parents.length} checkpoints. Original checkpoints and native threads remain available.</div>` : ""}</div><div class="section"><div class="row between"><h3><span class="number">02</span>Current instructions</h3><button class="small" data-action="add-instruction">+ Add instruction</button></div><p class="help">Each instruction cites its evidence. Use the latest explicit correction when sources disagree.</p>${Object.entries(
      d.conflicts,
    )
      .map(
        ([key, items]) =>
          `<div class="conflict"><strong>Resolve: ${escape(key)}</strong>${items.map((item, i) => `<label class="check"><input type="radio" name="conflict-${escape(key)}" data-conflict="${escape(key)}" value="${i}" ${d.resolutions[key] === item.text ? "checked" : ""}>${escape(item.text)}</label>`).join("")}</div>`,
      )
      .join(
        "",
      )}${d.instructions.map((item, i) => `<div class="instruction"><div class="row between"><label>Instruction key<input data-instruction="${i}" data-field="key" value="${escape(item.key)}" pattern="[a-z0-9_.-]+"></label><button class="small quiet" data-remove-instruction="${i}">Remove</button></div><label>Instruction<textarea data-instruction="${i}" data-field="text">${escape(item.text)}</textarea></label><label>Source evidence<select data-instruction-source="${i}">${d.events.map((e, n) => `<option value="${n}" ${refKey(item.sources[0]) === refKey(e.ref) ? "selected" : ""}>${escape(e.role + " · " + e.text.slice(0, 90))}</option>`).join("")}</select></label><div class="cite">${item.sources.map((r) => escape(r.eventId)).join(" · ")}</div></div>`).join("") || '<p class="help">No instructions yet. Add only the constraints the next session must retain.</p>'}</div><div class="section stack"><h3><span class="number">03</span>What does done mean?</h3><label>Acceptance criteria · one per line<textarea data-draft="criteria">${escape(d.criteria.join("\n"))}</textarea></label><label>Open work · one item per line<textarea data-draft="openWork">${escape(d.openWork.join("\n"))}</textarea></label><label>Files to verify · project-relative paths, one per line<textarea data-draft="files" placeholder="src/webhooks.ts">${escape(d.files.join("\n"))}</textarea></label><p class="help">Relay records file hashes and Git HEAD. Files are referenced, not copied.</p></div><div class="section stack"><h3><span class="number">04</span>Where should this continue?</h3><label>Destination<select data-draft="recipient">${Object.entries(
      names,
    )
      .map(
        ([key, name]) =>
          `<option value="${key}" ${d.recipient === key ? "selected" : ""}>${name}</option>`,
      )
      .join(
        "",
      )}</select></label><label class="check"><input type="checkbox" id="review-confirm" ${d.reviewed ? "checked" : ""}>I reviewed the instructions, source evidence and open work.</label><div class="row"><button class="primary" data-action="save-handoff">Save checkpoint & offer handoff</button><button data-action="discard-draft" class="quiet">Discard draft</button></div></div></div><aside class="stack"><div class="panel pad"><div class="row between"><h2>Evidence tray</h2>${badge(d.events.length + " sources")}</div><p class="help">Selected quotations are data, not instructions to follow automatically.</p>${d.events.map((e) => `<div class="evidencecard"><div class="row">${badge(e.role)}</div><p>${escape(e.text.slice(0, 1000))}${e.text.length > 1000 ? "…" : ""}</p><div class="mono">${escape(e.ref.eventId)}</div>${e.text.length > 1000 ? `<details><summary>Full source</summary><p>${escape(e.text)}</p></details>` : ""}</div>`).join("")}</div><div class="panel pad stack"><div><h3>Draft with Nebius</h3><p class="help">Optional. Preview the selected evidence before it leaves this computer. The response fills this editor for review.</p></div>${badge(state.provider.configured ? "Provider configured" : "Provider not configured", state.provider.configured ? "good" : "")}<button data-action="proposal-preview" ${state.provider.configured ? "" : "disabled"}>Preview evidence to send</button>${!state.provider.configured ? '<p class="help">Set NEBIUS_API_KEY and NEBIUS_MODEL in the service environment, then restart Relay. Manual review works without them.</p>' : ""}</div></aside></div>`
  );
}
function checkpointsView() {
  return (
    head(
      "03 / Durable working state",
      "Checkpoints, not chat dumps.",
      "Combine reviewed context without modifying the original sessions. Conflicting instructions require an explicit decision.",
      `<button class="primary" data-action="merge">Merge selected (${state.merge.size})</button>`,
    ) +
    (state.checkpoints.length
      ? `<div class="cards">${state.checkpoints.map((c) => `<article class="panel checkpoint"><div class="row between"><label class="check"><input type="checkbox" data-merge="${c.id}" ${state.merge.has(c.id) ? "checked" : ""}><strong>${escape(c.title)}</strong></label>${badge(c.sources.length + " sources")}</div><p class="summary">${escape(c.goal)}</p><div class="row between"><span class="help">${c.instructions.length} instructions · ${c.openWork.length} open items · ${c.artifacts.length} file references</span><div class="row"><button class="small" data-inspect="${c.id}">Inspect</button><button class="small" data-revise="${c.id}">Review a new revision</button></div></div></article>`).join("")}</div>`
      : empty(
          "No checkpoints yet",
          "Build the first reviewed handoff from a captured thread.",
          '<button class="primary" data-view="threads">Browse threads</button>',
        ))
  );
}
function handoffsView() {
  return (
    head(
      "04 / Continue elsewhere",
      "A clean start, with the context.",
      "Open a new chat in the destination harness and paste its continuation prompt. MCP records acceptance when that agent claims it.",
    ) +
    `<div class="banner warning">Acceptance confirms delivery. It does not mean the task or its tests have passed. A Markdown export is a portable fallback and does not record acceptance.</div><div class="cards spaced">${state.handoffs.map((h) => `<article class="panel handoff"><div class="row between"><div class="route row">${icon(h.recipient)}To ${names[h.recipient]}</div>${badge(h.status, h.status === "accepted" ? "good" : h.status === "offered" ? "warn" : "")}</div><h3>${escape(h.packet.goal)}</h3><p class="id">${escape(h.id)} · ${h.packet.instructions.length} instructions · ${h.packet.omitted.evidence} evidence omitted</p><p class="prompt">Ask ${names[h.recipient]} to inspect Context Relay handoff <strong>${escape(h.id)}</strong>, verify its sources and local state, then accept it before continuing.</p><div class="row"><button class="primary" data-copy="${h.id}">Copy continuation prompt</button><button data-export="${h.id}">Export Markdown</button><button data-freshness="${h.checkpointId}">Check freshness</button>${h.status === "offered" ? `<button class="quiet danger" data-cancel="${h.id}">Cancel offer</button>` : ""}</div></article>`).join("") || '<div class="empty"><h2>No handoffs yet</h2><p>Create a reviewed checkpoint to offer it to another session.</p></div>'}</div>`
  );
}
function connectionsView() {
  return (
    head(
      "05 / Plug in your harnesses",
      "Different tools. One working context.",
      "Capture reads native history. MCP gives the destination agent a project-scoped way to retrieve and accept context.",
      '<button class="primary" data-action="connect">Add connection</button>',
    ) +
    `<div class="grid2">${state.connections.map((c) => `<article class="panel connection"><div class="row between"><div class="row">${icon(c.harness)}<strong>${names[c.harness]}</strong></div>${badge(c.capture ? "Capture enabled" : "MCP only", c.capture ? "good" : "")}</div><p class="detail">${escape(c.configPath || "Capture only · no harness settings changed")}<br>${c.lastSync ? "Last capture: " + escape(new Date(c.lastSync).toLocaleString()) : "Not captured yet"}</p>${c.lastError ? `<p class="help error">${escape(c.lastError)}</p>` : ""}<div class="row"><button class="primary" data-discover="${c.id}">Discover sessions</button><button data-command="${c.id}" class="small">MCP command</button><button class="quiet small" data-disconnect="${c.id}">Disconnect</button></div></article>`).join("") || '<div class="panel empty"><h2>No connections yet</h2><p>Choose a harness and review the project settings it will use.</p></div>'}<div class="panel pad stack"><h3>Manual import</h3><p class="help">Import a normalized JSON session from another tool. The selected project must match the file’s projectId.</p><label>Session JSON<input id="import-file" type="file" accept="application/json,.json"></label><p class="help mono">Project ID: ${escape(state.projectId)}</p></div></div><section class="panel pad stack jobs-panel"><h2>Capture jobs</h2>${
      state.jobs
        .slice(0, 10)
        .map(
          (j) =>
            `<div class="job"><div class="row between"><span>${escape(names[state.connections.find((c) => c.id === j.connectionId)?.harness] || "Capture")} · ${j.completed}/${j.total} sessions</span>${badge(j.state, j.state === "completed" && !j.errors?.length ? "good" : "warn")}${j.state === "running" ? `<button class="small" data-cancel-job="${j.id}">Cancel</button>` : ""}</div><progress max="${j.total || 1}" value="${j.completed}"></progress>${j.error ? `<p class="help">${escape(j.error)}</p>` : ""}${(j.errors || []).map((e) => `<p class="help">${escape(e.externalId)}: ${escape(e.message)}</p>`).join("")}</div>`,
        )
        .join("") || '<p class="help">No capture jobs in this project.</p>'
    }</section>`
  );
}
function architectureView() {
  return (
    head(
      "06 / How it fits together",
      "One local service. Three ways in.",
      "The CLI, workspace and MCP server share the same project state and handoff rules.",
    ) +
    `<section class="panel architecture"><div class="harnesses">${Object.entries(
      names,
    )
      .filter(([h]) => h !== "manual")
      .map(
        ([h, name]) =>
          `<div class="harness">${icon(h)}<strong>${name}</strong><span class="help">Native sessions</span></div>`,
      )
      .join(
        "",
      )}</div><div class="grid2"><div class="lane"><h3>↓ Capture adapters</h3><p class="help">Explicit discovery and sync<br>Completed messages · source IDs · project roots</p></div><div class="lane"><h3>↑ MCP tools & resources</h3><p class="help">Inspect evidence and reviewed packets<br>Recipient-bound acceptance</p></div></div><div class="core"><div class="eyebrow">Context Relay</div><h2>Capture → Review → Checkpoint → Handoff</h2><p class="help">Immutable snapshots · explicit conflict resolution · file freshness · bounded packets</p></div><div class="grid2"><div class="lane"><h3>Private SQLite workspace</h3><p class="help">Projects, events, checkpoints, receipts<br>Loopback service · authenticated API</p></div><div class="lane"><h3>Optional Nebius Token Factory</h3><p class="help">Selected evidence → validated proposal<br>Explicit preview and consent · human review</p></div></div><div class="banner">MCP standardizes access to Relay's context tools. Each capture adapter still handles its harness's native history format. Relay does not modify native chat databases or transfer hidden model state.</div></section>`
  );
}
async function setSession(id) {
  state.sessionId = id;
  state.selected.clear();
  state.events = await api("/sessions/" + id + "/events?limit=200");
  render();
}
function newDraft() {
  const s = currentSession();
  const chosen = state.events.filter((e) => state.selected.has(e.id));
  if (!chosen.length) throw Error("Select at least one source event first.");
  state.draft = {
    projectId: state.projectId,
    title: "Continue " + s.title,
    goal: "",
    sources: [{ sessionId: s.id, revision: s.revision }],
    instructions: [],
    criteria: [],
    openWork: [],
    files: [],
    parents: [],
    resolutions: {},
    conflicts: {},
    evidence: chosen.map((e) => ({ sessionId: s.id, eventId: e.id })),
    events: chosen.map((e) => ({
      ...e,
      ref: { sessionId: s.id, eventId: e.id },
    })),
    recipient: "codex",
    reviewed: false,
  };
  state.view = "review";
  render();
}
async function draftFrom(ids) {
  const checkpoints = ids.map((id) =>
    state.checkpoints.find((c) => c.id === id),
  );
  const refs = new Map(),
    sources = new Map(),
    instructions = new Map(),
    conflicts = {};
  for (const c of checkpoints) {
    for (const source of c.sources)
      sources.set(source.sessionId, {
        sessionId: source.sessionId,
        revision:
          state.sessions.find((s) => s.id === source.sessionId)?.revision ??
          source.revision,
      });
    for (const ref of [
      ...c.evidence,
      ...c.instructions.flatMap((i) => i.sources),
    ])
      refs.set(refKey(ref), ref);
    for (const item of c.instructions) {
      if (!instructions.has(item.key)) instructions.set(item.key, item);
      else if (instructions.get(item.key).text !== item.text) {
        conflicts[item.key] ??= [instructions.get(item.key)];
        conflicts[item.key].push(item);
      }
    }
  }
  const all = new Map();
  for (const sessionId of sources.keys()) {
    const events = await api("/sessions/" + sessionId + "/events?limit=20000");
    for (const e of events)
      all.set(refKey({ sessionId, eventId: e.id }), {
        ...e,
        ref: { sessionId, eventId: e.id },
      });
  }
  state.draft = {
    projectId: state.projectId,
    title:
      checkpoints.length > 1
        ? "Merged working context"
        : "Revise " + checkpoints[0].title,
    goal: checkpoints[0].goal,
    sources: [...sources.values()],
    instructions: structuredClone([...instructions.values()]),
    criteria: [...new Set(checkpoints.flatMap((c) => c.criteria))],
    openWork: [...new Set(checkpoints.flatMap((c) => c.openWork))],
    files: [
      ...new Set(checkpoints.flatMap((c) => c.artifacts.map((a) => a.path))),
    ],
    parents: ids,
    resolutions: {},
    conflicts,
    evidence: [...refs.values()],
    events: [...refs.keys()].map((k) => all.get(k)).filter(Boolean),
    recipient: "codex",
    reviewed: false,
  };
  if (!state.draft.events.length) {
    state.draft = null;
    throw Error(
      "These checkpoints contain no source quotations. Select fresh source evidence in Threads.",
    );
  }
  state.view = "review";
  render();
  notice(
    "Sources use their latest captured revisions. Review changed work and files before saving.",
  );
}
function connectionDialog() {
  modal(
    `<h2>Connect a coding harness</h2><p class="help">Scoped to ${escape(currentProject().name)}. Discovery and capture run only when requested.</p><form id="connect-form"><label>Harness<select name="harness">${Object.entries(
      names,
    )
      .filter(([k]) => k !== "manual")
      .map(([k, n]) => `<option value="${k}">${n}</option>`)
      .join(
        "",
      )}</select></label><label>Source path or OpenCode loopback URL · optional<input name="source" placeholder="Use the harness default"></label><label>Codex or Goose executable path · optional<input name="executable" placeholder="Use executable from PATH"></label><label class="check"><input name="configure" type="checkbox" checked>Configure project MCP access. Goose receives a command manifest; enable it in Goose yourself.</label><p class="help">Existing unrelated settings are preserved. Disconnect removes only Relay’s recorded entry. Reconnecting updates this project's connection.</p><div class="actions"><button type="button" data-action="close-modal">Cancel</button><button type="submit" class="primary">Connect harness</button></div></form>`,
  );
}
function download(name, text) {
  const url = URL.createObjectURL(
    new Blob([text], { type: "text/markdown;charset=utf-8" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    notice("Continuation prompt copied.");
  } catch {
    modal(
      '<h2>Copy continuation prompt</h2><textarea readonly aria-label="Continuation prompt">' +
        escape(text) +
        '</textarea><div class="actions"><button data-action="close-modal">Done</button></div>',
    );
  }
}
async function action(button) {
  const d = button.dataset;
  if (d.view) {
    state.view = d.view;
    render();
    return;
  }
  if (d.session) return setSession(d.session);
  if (d.removeInstruction !== undefined) {
    state.draft.instructions.splice(Number(d.removeInstruction), 1);
    render();
    return;
  }
  if (d.inspect) {
    const c = state.checkpoints.find((c) => c.id === d.inspect);
    const fresh = await api("/checkpoints/" + c.id + "/freshness");
    modal(
      `<h2>${escape(c.title)}</h2>${badge(fresh.current ? "Current" : "Needs review", fresh.current ? "good" : "warn")}<p class="help">${escape(fresh.reasons.join(" "))}</p><pre>${escape(JSON.stringify(c, null, 2))}</pre><div class="actions"><button data-action="close-modal">Close</button></div>`,
    );
    return;
  }
  if (d.revise) return draftFrom([d.revise]);
  if (d.copy || d.export) {
    const value = await api("/handoffs/" + (d.copy || d.export) + "/export");
    if (d.copy) return copy(value.prompt);
    download("relay-handoff-" + d.export.slice(0, 8) + ".md", value.markdown);
    return;
  }
  if (d.freshness) {
    const fresh = await api("/checkpoints/" + d.freshness + "/freshness");
    modal(
      `<h2>${fresh.current ? "Captured state is current" : "This handoff needs review"}</h2><p>${escape(fresh.reasons.join(" ") || "Captured revisions, recorded files and Git HEAD still match. Capture the native source again to check for uncaptured changes.")}</p><div class="actions"><button data-action="close-modal">Close</button></div>`,
    );
    return;
  }
  if (d.cancel) {
    await api("/handoffs/" + d.cancel + "/cancel", {});
    await refresh();
    return;
  }
  if (d.disconnect) {
    await api("/connections/" + d.disconnect + "/disconnect", {});
    notice("Disconnected. Captured history remains available.");
    await refresh();
    return;
  }
  if (d.command) {
    const command = await api("/connections/" + d.command + "/command");
    modal(
      `<h2>MCP stdio command</h2><p class="help">Command and argument array. For Goose, configure this as a stdio extension; its project manifest is also available on disk.</p><pre>${escape(JSON.stringify(command, null, 2))}</pre><div class="actions"><button data-action="close-modal">Close</button></div>`,
    );
    return;
  }
  if (d.discover) {
    const sessions = await api("/connections/" + d.discover + "/discover");
    modal(
      `<h2>Choose sessions to capture</h2><p class="help">${sessions.length} sessions match this project. Source data stays in its native location.</p><form id="capture-form" data-connection="${d.discover}">${sessions.map((s) => `<label class="check"><input type="checkbox" name="sessions" value="${escape(s.externalId)}">${escape(s.title)}</label>`).join("") || "<p>No matching sessions found. Check your source and project path.</p>"}<div class="actions"><button type="button" data-action="close-modal">Cancel</button><button type="submit" class="primary" ${sessions.length ? "" : "disabled"}>Capture selected</button></div></form>`,
    );
    return;
  }
  if (d.cancelJob) {
    await api("/jobs/" + d.cancelJob + "/cancel", {});
    notice("Cancellation requested; the active source read will finish first.");
    return;
  }
  switch (d.action) {
    case "close-modal":
      closeModal();
      break;
    case "refresh":
      await refresh();
      notice("Workspace refreshed.");
      break;
    case "theme":
      document.documentElement.dataset.theme =
        document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      localStorage.setItem(
        "relay-theme",
        document.documentElement.dataset.theme,
      );
      break;
    case "lock":
      token = "";
      sessionStorage.removeItem("relay-token");
      state.draft = null;
      login();
      break;
    case "add-project":
      modal(
        '<h2>Add a local project</h2><form id="project-form"><label>Absolute project directory<input name="root" placeholder="/path/to/project" required></label><label>Display name · optional<input name="name"></label><div class="actions"><button type="button" data-action="close-modal">Cancel</button><button type="submit" class="primary">Add project</button></div></form>',
      );
      break;
    case "connect":
      connectionDialog();
      break;
    case "new-draft":
      newDraft();
      break;
    case "more-events":
      state.events.push(
        ...(await api(
          "/sessions/" +
            state.sessionId +
            "/events?offset=" +
            state.events.length +
            "&limit=200",
        )),
      );
      render();
      break;
    case "discard-draft":
      state.draft = null;
      render();
      break;
    case "add-instruction": {
      const draft = state.draft;
      draft.instructions.push({
        key: "instruction-" + (draft.instructions.length + 1),
        text: "",
        sources: [draft.events[0].ref],
      });
      draft.reviewed = false;
      render();
      break;
    }
    case "merge":
      if (state.merge.size < 2)
        throw Error("Select at least two checkpoints to merge.");
      await draftFrom([...state.merge]);
      break;
    case "save-handoff": {
      const draft = state.draft;
      if (!draft.reviewed)
        throw Error("Confirm that you reviewed the handoff first.");
      for (const key of Object.keys(draft.conflicts))
        if (!draft.resolutions[key])
          throw Error("Resolve the conflicting instruction: " + key);
      const { events, conflicts, recipient, reviewed, ...input } = draft;
      const checkpoint = await api("/checkpoints", input);
      try {
        await api("/handoffs", { checkpointId: checkpoint.id, recipient });
        state.draft = null;
        state.view = "handoffs";
        await refresh();
        notice(
          "Checkpoint saved. The destination agent can now inspect and accept the offer.",
        );
      } catch (error) {
        state.draft = null;
        state.view = "checkpoints";
        await refresh();
        throw Error("Checkpoint saved, but the offer failed: " + error.message);
      }
      break;
    }
    case "proposal-preview": {
      const draft = state.draft;
      const preview = await api("/proposals/preview", {
        projectId: state.projectId,
        evidence: draft.evidence,
      });
      modal(
        `<h2>Review what goes to Nebius</h2><p class="help">${escape(preview.warning)}</p><p class="help">${num(preview.bytes)} UTF-8 bytes · model ${escape(preview.model)}</p><pre>${escape(JSON.stringify(preview.events, null, 2))}</pre><label class="check"><input type="checkbox" id="provider-consent">I want to send this selected evidence to Nebius to generate a draft.</label><div class="actions"><button data-action="close-modal">Cancel</button><button class="primary" data-action="generate-proposal">Send & generate draft</button></div>`,
      );
      break;
    }
    case "generate-proposal": {
      if (!$("#provider-consent").checked)
        throw Error("Confirm the evidence transfer before generating.");
      const draft = state.draft;
      const result = await api("/proposals", {
        projectId: state.projectId,
        evidence: draft.evidence,
        consent: true,
      });
      Object.assign(draft, result.proposal, {
        reviewed: false,
        resolutions: {},
      });
      closeModal();
      render();
      notice("Proposal loaded. Review every instruction before saving.");
      break;
    }
  }
}
document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || (button.type === "submit" && button.closest("form"))) return;
  if (button.disabled) return;
  button.disabled = true;
  Promise.resolve(action(button))
    .catch((error) => notice(error.message))
    .finally(() => {
      button.disabled = false;
    });
});
document.addEventListener("input", (event) => {
  const el = event.target;
  if (el.id === "session-search") {
    const start = el.selectionStart;
    state.search = el.value;
    render();
    $("#session-search").focus();
    $("#session-search").setSelectionRange(start, start);
    return;
  }
  const d = state.draft;
  if (!d) return;
  if (el.dataset.draft) {
    d[el.dataset.draft] = ["criteria", "openWork", "files"].includes(
      el.dataset.draft,
    )
      ? lines(el.value)
      : el.value;
    d.reviewed = false;
  }
  if (el.dataset.instruction !== undefined) {
    d.instructions[Number(el.dataset.instruction)][el.dataset.field] = el.value;
    d.reviewed = false;
  }
  if ($("#review-confirm") && el.id !== "review-confirm")
    $("#review-confirm").checked = d.reviewed;
});
document.addEventListener("change", (event) => {
  const el = event.target;
  Promise.resolve()
    .then(async () => {
      if (el.id === "project-select") {
        state.projectId = el.value;
        state.sessionId = "";
        state.draft = null;
        state.merge.clear();
        await refresh();
      }
      if (el.dataset.evidence) {
        if (el.checked) state.selected.add(el.dataset.evidence);
        else state.selected.delete(el.dataset.evidence);
        $("#selected-count").textContent =
          state.selected.size + " selected · source text stays inspectable";
      }
      if (el.dataset.merge) {
        if (el.checked) state.merge.add(el.dataset.merge);
        else state.merge.delete(el.dataset.merge);
        render();
      }
      if (el.dataset.instructionSource !== undefined) {
        state.draft.instructions[Number(el.dataset.instructionSource)].sources =
          [state.draft.events[Number(el.value)].ref];
        state.draft.reviewed = false;
        render();
      }
      if (el.dataset.conflict) {
        const d = state.draft,
          item = d.conflicts[el.dataset.conflict][Number(el.value)];
        d.resolutions[el.dataset.conflict] = item.text;
        d.instructions = d.instructions.filter(
          (i) => i.key !== el.dataset.conflict,
        );
        d.instructions.push(structuredClone(item));
        d.reviewed = false;
        render();
      }
      if (el.id === "review-confirm") state.draft.reviewed = el.checked;
      if (el.id === "import-file") {
        const file = el.files[0];
        if (!file) return;
        if (file.size > 4 * 1024 * 1024)
          throw Error("Import file exceeds 4 MiB.");
        const data = JSON.parse(await file.text());
        if (data.projectId !== state.projectId)
          throw Error("The file projectId must match the selected project.");
        await api("/sessions/import", data);
        await refresh();
        notice("Session imported.");
      }
    })
    .catch((error) => notice(error.message));
});
document.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target,
    data = new FormData(form),
    button = form.querySelector("[type=submit]");
  if (button) button.disabled = true;
  Promise.resolve()
    .then(async () => {
      if (form.id === "login-form") {
        token = String(data.get("token")).trim();
        sessionStorage.setItem("relay-token", token);
        await refresh();
      }
      if (form.id === "project-form") {
        const project = await api("/projects", {
          root: data.get("root"),
          ...(data.get("name") ? { name: data.get("name") } : {}),
        });
        state.projectId = project.id;
        closeModal();
        await refresh();
      }
      if (form.id === "connect-form") {
        await api("/connections", {
          projectId: state.projectId,
          harness: data.get("harness"),
          configureMcp: data.has("configure"),
          ...(data.get("source") ? { source: data.get("source") } : {}),
          ...(data.get("executable")
            ? { executable: data.get("executable") }
            : {}),
        });
        closeModal();
        await refresh();
        notice("Harness connected. Discover sessions when ready.");
      }
      if (form.id === "capture-form") {
        const selected = data.getAll("sessions");
        if (!selected.length) throw Error("Choose at least one session.");
        await api("/connections/" + form.dataset.connection + "/sync", {
          externalIds: selected,
        });
        closeModal();
        await refresh();
        notice("Capture started. Progress appears below.");
      }
    })
    .catch((error) => notice(error.message))
    .finally(() => {
      if (button) button.disabled = false;
    });
});
setInterval(async () => {
  if (!token || !state.jobs.some((j) => j.state === "running")) return;
  try {
    const jobs = await api("/jobs");
    const wasRunning = state.jobs.some((j) => j.state === "running");
    state.jobs = jobs.filter((j) =>
      state.connections.some((c) => c.id === j.connectionId),
    );
    if (wasRunning && !state.jobs.some((j) => j.state === "running")) {
      await refresh();
      notice("Capture finished. Review the job results.");
    } else if (state.view === "connections" && !$("#modal").open) render();
  } catch (error) {
    notice(error.message);
  }
}, 1500);
if (token)
  refresh().catch((error) => {
    notice(error.message);
    if (token)
      $("#app").innerHTML =
        '<main class="welcome"><h1>Workspace unavailable</h1><p>' +
        escape(error.message) +
        '</p><button data-action="refresh">Retry connection</button></main>';
  });
else login();

window.addEventListener('beforeunload',event=>{if(state.draft){event.preventDefault();event.returnValue='';}});
