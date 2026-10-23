const $ = (id) => document.getElementById(id);
const state = { sessions: [], detail: null, docs: null, tab: "workbench", advanced: false, busy: false, argumentInputs: [], lastAction: null, lastAudit: null, toastTimer: null, factRevision: null, activeRequest: 0 };
const clone = (value) => JSON.parse(JSON.stringify(value));
const pretty = (value) => JSON.stringify(value, null, 2);
const compact = (value) => typeof value === "string" ? value : JSON.stringify(value);
const item = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};
const paths = {
  session: "M5 3h8l3 3v11H5V3Zm8 0v4h3M8 10h5M8 13h5",
  edit: "m12 3 4 4-8 8-5 1 1-5 8-8Zm-7 7 4 4",
};
function icon(name) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("viewBox", "0 0 20 20");
  node.setAttribute("fill", "none");
  node.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(node.namespaceURI, "path");
  path.setAttribute("d", paths[name] || paths.session);
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.3");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("stroke-linecap", "round");
  node.append(path);
  return node;
}
function errorMessage(error) { return error instanceof Error ? error.message : String(error); }
function setError(id, error) { const node = $(id); node.hidden = !error; node.textContent = error ? errorMessage(error) : ""; }
function toast(message) {
  clearTimeout(state.toastTimer);
  $("toast").textContent = message;
  $("toast").hidden = false;
  state.toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4300);
}
async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.headers["X-Relay-Client"] = "workbench";
    options.body = JSON.stringify(body);
  }
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error("Cannot reach the local server. Make sure Context Relay is running, then try again."); }
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`The server returned an unreadable response (${response.status}).`); }
  if (!response.ok || data.error) {
    const error = new Error(data.error?.message || `Request failed (${response.status}).`);
    error.code = data.error?.code;
    error.status = response.status;
    throw error;
  }
  return data;
}
function sessionPath(suffix = "") { return `/api/sessions/${encodeURIComponent(state.detail.session.id)}${suffix}`; }
function setBusy(busy) {
  state.busy = busy;
  document.body.classList.toggle("busy", busy);
  $("workbench-content").setAttribute("aria-busy", String(busy));
  const ids = ["demo-stale", "demo-restore", "demo-safe", "check-action", "simulate-action", "audit-context", "restore-context", "new-session", "new-session-bottom", "add-fact", "add-policy"];
  for (const id of ids) if ($(id)) $(id).disabled = busy;
  document.querySelectorAll("dialog button[type=submit]").forEach((button) => { button.disabled = busy; });
}
async function perform(fn, errorId) {
  if (state.busy) return;
  setBusy(true);
  if (errorId) setError(errorId, null);
  try { return await fn(); }
  catch (error) {
    if (errorId) setError(errorId, error);
    else toast(errorMessage(error));
    return null;
  } finally { setBusy(false); }
}
function dateLabel(value, full = false) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, full ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" } : { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}
function friendlyScenario(scenario) { return ({ refund: "Customer support", deployment: "Deployment", custom: "Custom workflow" })[scenario] || "Session"; }
function eventShort(id) { const value = String(id || ""); return value.length > 15 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value; }
function sourceButton(eventId, label) {
  const button = item("button", "source-link", label || `source ${eventShort(eventId)}`);
  button.type = "button";
  button.title = `View original event ${eventId}`;
  button.addEventListener("click", () => focusEvent(eventId));
  return button;
}
function showTab(name, focus = false) {
  state.tab = name;
  document.querySelectorAll("[data-tab]").forEach((tab) => {
    const selected = tab.dataset.tab === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    $(`panel-${tab.dataset.tab}`).hidden = !selected;
  });
  if (focus) $(`tab-${name}`).focus();
  if (name === "integration" && !state.docs) loadDocs();
}
function focusEvent(id) {
  showTab("workbench");
  const event = [...$("event-timeline").children].find((node) => node.dataset.eventId === String(id));
  if (!event) { toast("This source event is not present in the current session."); return; }
  document.querySelectorAll(".event-item.highlight").forEach((node) => node.classList.remove("highlight"));
  event.classList.add("highlight");
  event.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
  event.focus({ preventScroll: true });
  setTimeout(() => event.classList.remove("highlight"), 3500);
}
function renderSessions() {
  const list = $("session-list");
  list.replaceChildren();
  for (const session of state.sessions) {
    const selected = session.id === state.detail?.session.id;
    const button = item("button", `session-item${selected ? " active" : ""}`);
    button.type = "button";
    if (selected) button.setAttribute("aria-current", "page");
    button.title = session.name;
    const symbol = item("span", "session-icon"); symbol.append(icon("session"));
    const label = item("span");
    label.append(item("span", "session-name", session.name), item("span", "session-meta", `${friendlyScenario(session.scenario)} · rev ${session.revision}`));
    button.append(symbol, label);
    button.addEventListener("click", () => { if (!state.busy && !selected) loadSession(session.id); });
    list.append(button);
  }
  if (!state.sessions.length) list.append(item("div", "sidebar-placeholder", "Your sessions will appear here."));
}
function renderFacts() {
  const facts = state.detail.facts || {};
  const list = $("facts-list"); list.replaceChildren();
  for (const [key, fact] of Object.entries(facts)) {
    const row = item("div", "fact-row");
    const main = item("div", "fact-main");
    const provenance = item("div", "fact-provenance");
    provenance.append(item("span", "", fact.source || "source"), item("span", "", "·"), sourceButton(fact.event_id, `event ${eventShort(fact.event_id)}`));
    main.append(item("div", "fact-key", key), item("div", "fact-value", compact(fact.value)), provenance);
    const edit = item("button", "icon-button edit-fact");
    edit.type = "button"; edit.setAttribute("aria-label", `Edit ${key}`); edit.title = `Edit ${key}`;
    edit.append(icon("edit"));
    edit.addEventListener("click", () => openFact(key));
    row.append(main, edit); list.append(row);
  }
  if (!Object.keys(facts).length) list.append(item("div", "inline-empty", "No facts yet. Add a fact to start recording the current state and its evidence."));
  $("revision-badge").textContent = `rev ${state.detail.session.revision}`;
  renderPolicies();
}
function renderPolicies() {
  const policies = state.detail.policies || [];
  $("policy-count").textContent = String(policies.length);
  const list = $("policies-list"); list.replaceChildren();
  for (const policy of policies) {
    const block = item("div", "policy-item");
    block.append(item("strong", "", policy.title || policy.id), item("code", "policy-tool", policy.tool));
    const requirements = policy.requirements || [];
    for (const requirement of requirements) block.append(item("p", "", `${requirement.key} ${requirement.op} ${JSON.stringify(requirement.value)}`));
    for (const [arg, fact] of Object.entries(policy.bindings || {})) block.append(item("p", "", `${arg} ← ${fact}`));
    if (!requirements.length && !Object.keys(policy.bindings || {}).length) block.append(item("p", "", "No fact requirements or argument bindings."));
    list.append(block);
  }
  if (!policies.length) list.append(item("p", "field-help", "No policies yet. Tool checks fail closed until you add an explicit policy."));
}
function renderEvents() {
  const events = state.detail.events || [];
  const list = $("event-timeline"); list.replaceChildren();
  $("event-count").textContent = `${events.length} ${events.length === 1 ? "event" : "events"}`;
  const seenPolicies = new Set();
  events.forEach((event, index) => {
    const entry = item("li", `event-item${index === events.length - 1 ? " latest" : ""}`);
    entry.dataset.eventId = event.id; entry.tabIndex = -1;
    entry.append(item("span", "event-dot", String(event.sequence ?? index + 1)));
    const heading = item("div", "event-heading");
    let title = event.key || event.tool || "Event";
    let type = (event.kind || "event").replaceAll("_", " ");
    let text = event.text;
    if (event.kind === "policy" && event.policy) {
      title = event.policy.tool;
      type = seenPolicies.has(event.policy.id) ? "policy updated" : "policy registered";
      text = event.policy.title || `An explicit state rule was registered for ${event.policy.tool}.`;
      seenPolicies.add(event.policy.id);
    } else if (event.kind === "tool_result") {
      type = `${event.result?.simulated ? "simulation" : "tool"} ${event.status || "result"}`;
      text = event.error?.message || event.result?.message || (event.status === "failed" ? "Tool execution failed. See the recorded receipt for details." : "Tool execution completed. See the recorded receipt for its output.");
    } else if (!text) {
      text = event.kind === "fact" ? `${event.key} = ${compact(event.value)}` : "An event was recorded. Expand its details to inspect the original data.";
    }
    heading.append(item("strong", "", title), item("span", "event-type", type), item("time", "event-time", dateLabel(event.created_at)));
    entry.append(heading, item("p", "event-text", text), item("div", "event-meta", `${event.source || "system"} · ${event.id}`));
    if (event.kind !== "fact") {
      const raw = item("details", "event-record");
      raw.append(item("summary", "text-button", "View recorded details"), item("pre", "", pretty(event)));
      entry.append(raw);
    }
    list.append(entry);
  });
  if (!events.length) list.append(item("li", "inline-empty", "The evidence timeline is empty. Every fact you save will appear here."));
}
function issueNode(issue, className = "issue-item") {
  const node = item("div", className);
  node.append(item("strong", "", issue.key || (issue.code || "State check").replaceAll("_", " ")));
  node.append(item("p", "", issue.message || "The proposed action does not match the current state."));
  if ("expected" in issue || "actual" in issue) {
    const values = item("div", "issue-values");
    if ("expected" in issue) values.append(item("code", "", `Expected: ${JSON.stringify(issue.expected)}`));
    if ("actual" in issue) values.append(item("code", "", `Received: ${JSON.stringify(issue.actual)}`));
    node.append(values);
  }
  if (issue.event_id) node.append(sourceButton(issue.event_id, `View source · ${eventShort(issue.event_id)}`));
  return node;
}
function renderHistory() {
  const checks = state.detail.checks || [];
  $("history-count").textContent = String(checks.length);
  const list = $("check-history"); list.replaceChildren();
  if (!checks.length) {
    const empty = item("div", "card empty-state");
    empty.append(item("span", "empty-symbol", "↗"), item("h3", "", "A clean slate."), item("p", "", "Run your first action check in the workbench. Its decision and evidence will be saved here."));
    const button = item("button", "button button-secondary", "Open workbench"); button.addEventListener("click", () => showTab("workbench")); button.style.marginTop = "18px"; empty.append(button); list.append(empty);
  }
  for (const record of checks) {
    const check = record.check || record;
    const decision = ["allow", "block", "refresh"].includes(check.decision) ? check.decision : "block";
    const details = item("details", "history-card");
    const summary = item("summary");
    summary.append(item("span", `decision-badge ${decision}`, decision.toUpperCase()), item("span", "history-tool", check.tool || record.action?.tool || "Action check"), item("span", "neutral-badge", `rev ${check.revision ?? "—"}`), item("time", "history-date", dateLabel(check.created_at, true)));
    const body = item("div", "history-detail");
    for (const issue of check.issues || []) body.append(issueNode(issue));
    if (!(check.issues || []).length) body.append(item("p", "field-help", "All configured checks passed for this proposal."));
    const raw = item("details", "raw-details"); raw.append(item("summary", "text-button", "View recorded JSON"), item("pre", "", pretty(record))); body.append(raw);
    details.append(summary, body); list.append(details);
  }
}
function resetVerdict() {
  const verdict = $("verdict"); verdict.className = "verdict-empty"; verdict.replaceChildren();
  const label = item("div"); label.append(item("strong", "", "Ready when you are."), item("p", "", "Check an action to see its decision and supporting evidence."));
  verdict.append(item("span", "verdict-empty-mark", "↗"), label);
}
function renderVerdict(check, simulation) {
  const decision = ["allow", "block", "refresh"].includes(check.decision) ? check.decision : "block";
  const verdict = $("verdict"); verdict.className = `verdict ${decision}`; verdict.replaceChildren();
  verdict.setAttribute("role", "status");
  const head = item("div", "verdict-head");
  const labels = { allow: ["✓", "Action allowed", "The proposal satisfies the configured policies."], block: ["×", "Action blocked", "This proposal conflicts with the configured state or policies."], refresh: ["↻", "Refresh needed", "Restore current context before proposing the action again."] };
  const label = labels[decision];
  const copy = item("div"); copy.append(item("strong", "", label[1]), item("p", "", label[2]));
  head.append(item("span", "verdict-symbol", label[0]), copy);
  if (Number.isFinite(check.duration_ms)) head.append(item("span", "latency", `${check.duration_ms.toFixed(2)} ms`));
  verdict.append(head);
  if ((check.issues || []).length) {
    const issues = item("div", "issue-list"); for (const issue of check.issues) issues.append(issueNode(issue)); verdict.append(issues);
  }
  if (simulation) {
    const text = simulation.executed ? "Local simulation completed. No external action was taken." : "Simulation did not execute. Resolve the check above before continuing.";
    const receipt = item("div", "simulation-receipt", text);
    if (simulation.executed) {
      if (simulation.result?.message) receipt.append(item("p", "receipt-message", simulation.result.message));
      const raw = item("details", "event-record");
      raw.append(item("summary", "text-button", "View simulation receipt"), item("pre", "", pretty(simulation.receipt || simulation.result || {})));
      receipt.append(raw);
    }
    verdict.append(receipt);
  } else if (decision === "allow") verdict.append(item("div", "simulation-receipt", "Check only. No tool has executed."));
}
function policyFor(tool) { return (state.detail.policies || []).find((policy) => policy.tool === tool); }
function initialAction() {
  if (state.detail.demo?.safe_action && state.detail.demo?.proposed_action) return clone(state.detail.demo.proposed_action);
  const policy = state.detail.policies?.[0];
  const args = Object.create(null);
  for (const [name, key] of Object.entries(policy?.bindings || {})) args[name] = state.detail.facts[key]?.value ?? "";
  return { tool: policy?.tool || "", arguments: args, context_revision: state.detail.context.revision };
}
function populateTools(selectedTool) {
  const select = $("action-tool"); select.replaceChildren();
  const tools = new Set((state.detail.policies || []).map((policy) => policy.tool));
  if (selectedTool) tools.add(selectedTool);
  for (const tool of tools) { const option = item("option", "", tool); option.value = tool; select.append(option); }
  if (!tools.size) { const option = item("option", "", "Add a policy or use JSON mode"); option.value = ""; select.append(option); }
  if (selectedTool) select.value = selectedTool;
}
function populateArguments(tool, args = {}) {
  const container = $("action-arguments"); container.replaceChildren(); state.argumentInputs = [];
  const policy = policyFor(tool);
  const names = new Set([...Object.keys(policy?.bindings || {}), ...(policy?.optional_args || []), ...Object.keys(args)]);
  for (const [index, name] of [...names].entries()) {
    const hasArg = Object.prototype.hasOwnProperty.call(args, name);
    const key = policy?.bindings?.[name];
    const optional = (policy?.optional_args || []).includes(name) && !key;
    const fact = key && state.detail.facts[key];
    const value = hasArg ? args[name] : fact?.value ?? "";
    const jsonType = typeof value !== "string";
    const wrap = item("div", "argument-field");
    const label = item("label", "field-label", `${name}${optional ? " (optional)" : ""}${jsonType ? " · JSON" : ""}`); label.htmlFor = `argument-${index}`;
    const input = item("input", "input mono"); input.id = `argument-${index}`; input.type = "text"; input.value = jsonType ? JSON.stringify(value) : value; input.autocomplete = "off";
    if (optional && !hasArg && !fact) input.placeholder = "Leave empty to omit";
    wrap.append(label, input); container.append(wrap);
    state.argumentInputs.push({ name, input, jsonType, optional, key });
  }
  if (!names.size) container.append(item("p", "field-help", policy ? "This tool has no declared arguments." : "Use JSON mode to write a tool proposal. Unknown tools are blocked."));
}
function setAction(action) {
  const normalized = { tool: action.tool || "", arguments: action.arguments || {}, context_revision: action.context_revision ?? state.detail.context.revision };
  state.lastAction = clone(normalized);
  populateTools(normalized.tool);
  populateArguments(normalized.tool, normalized.arguments);
  $("action-revision").value = normalized.context_revision;
  $("action-json").value = pretty(normalized);
  setError("action-form-error", null);
}
function readSimpleAction() {
  const args = Object.create(null);
  for (const field of state.argumentInputs) {
    const raw = field.input.value;
    if (field.optional && raw === "") continue;
    if (field.jsonType) { try { args[field.name] = JSON.parse(raw); } catch { throw new Error(`“${field.name}” must be valid JSON.`); } }
    else args[field.name] = raw;
  }
  const revision = Number($("action-revision").value);
  if ($("action-revision").value.trim() === "" || !Number.isInteger(revision) || revision < 0) throw new Error("Context revision must be a whole number of zero or greater.");
  return { tool: $("action-tool").value, arguments: args, context_revision: revision };
}
function readAction() {
  if (!state.advanced) return readSimpleAction();
  let action;
  try { action = JSON.parse($("action-json").value); } catch { throw new Error("The proposed action is not valid JSON. Check commas and quotation marks."); }
  if (!action || typeof action !== "object" || Array.isArray(action)) throw new Error("The proposed action must be a JSON object.");
  return action;
}
function setAdvanced(advanced) {
  try {
    if (advanced) $("action-json").value = pretty(readSimpleAction());
    else if (state.advanced) setAction(readAction());
    state.advanced = advanced;
    $("action-fields").hidden = advanced;
    $("action-json-wrap").hidden = !advanced;
    $("toggle-action-json").setAttribute("aria-pressed", String(advanced));
    $("toggle-action-json").textContent = advanced ? "≡ Fields" : "{ } JSON";
    setError("action-form-error", null);
  } catch (error) { setError("action-form-error", error); }
}
function setCandidate(candidate) {
  $("context-editor").value = pretty(candidate); updateContextSize(); setError("context-error", null);
  if (state.lastAudit) paintAudit();
}
function updateContextSize() {
  const bytes = new TextEncoder().encode($("context-editor").value).length;
  $("context-size").textContent = `${new Intl.NumberFormat().format(bytes)} bytes in editor`;
}
function renderDetail(detail, reset = true) {
  state.detail = detail;
  const index = state.sessions.findIndex((session) => session.id === detail.session.id);
  if (index >= 0) state.sessions[index] = detail.session;
  else state.sessions.unshift(detail.session);
  renderSessions(); renderFacts(); renderEvents(); renderHistory();
  $("breadcrumb-name").textContent = detail.session.name;
  $("export-session").disabled = false;
  $("session-loading").hidden = true; $("workbench-content").hidden = false;
  const hasDemo = Boolean(detail.demo?.safe_action && detail.demo?.stale_context);
  $("demo-card").hidden = !hasDemo;
  $("load-stale-context").hidden = !hasDemo;
  if (hasDemo) {
    $("demo-description").textContent = detail.demo.description || "Follow a changed decision through a stale proposal, restored context, and a corrected action.";
    $("demo-title").textContent = detail.session.scenario === "deployment" ? "The deployment plan changed. Is the agent up to date?" : "A customer changed their mind. Did the agent catch up?";
  }
  if (reset) {
    setAction(initialAction());
    setCandidate(detail.context);
    resetVerdict();
    $("demo-feedback").hidden = true;
    ["demo-stale", "demo-restore", "demo-safe"].forEach((id) => $(id).classList.remove("done"));
    resetAudit();
  }
  else if (state.lastAudit) paintAudit();
}
async function refreshDetail() { const detail = await api(sessionPath()); renderDetail(detail, false); return detail; }
async function loadSession(id) {
  const request = ++state.activeRequest;
  $("global-error").hidden = true;
  $("session-loading").hidden = false; $("workbench-content").hidden = true;
  try {
    const detail = await api(`/api/sessions/${encodeURIComponent(id)}`);
    if (request !== state.activeRequest) return;
    renderDetail(detail);
    try { localStorage.setItem("context-relay-session", id); } catch { /* Storage can be disabled. */ }
  } catch (error) {
    if (request !== state.activeRequest) return;
    $("global-error-text").textContent = errorMessage(error); $("global-error").hidden = false;
    $("session-loading").hidden = true;
    if (state.detail) $("workbench-content").hidden = false;
  }
}
async function runAction(simulate = false, override) {
  const action = override || readAction();
  const response = await api(sessionPath(simulate ? "/simulate" : "/check"), { method: "POST", body: action });
  const check = simulate ? response.check : response;
  renderVerdict(check, simulate ? response : null);
  await refreshDetail();
  return response;
}
function resetAudit() {
  state.lastAudit = null;
  const target = $("audit-result"); target.className = "empty-state"; target.replaceChildren();
  target.append(item("span", "empty-symbol", "≋"), item("h3", "", "What changed along the way?"), item("p", "", "Run an audit to find stale revisions, missing facts, and mismatched sources."));
}
function paintAudit() {
  if (!state.lastAudit) return;
  const { audit, candidate } = state.lastAudit;
  const target = $("audit-result"); target.className = "audit-result"; target.replaceChildren();
  const status = item("div", `audit-status${audit.ok ? " pass" : ""}`);
  status.append(item("h3", "", audit.ok ? `✓ Last audit matched revision ${audit.revision}` : `Last audit found ${audit.issues?.length || 0} ${audit.issues?.length === 1 ? "difference" : "differences"}`));
  status.append(item("p", "", `Compared candidate revision ${candidate.revision ?? "(missing)"} with stored revision ${audit.revision}.`));
  target.append(status);
  const notes = [];
  let editorMatches = false;
  try { editorMatches = JSON.stringify(JSON.parse($("context-editor").value)) === JSON.stringify(candidate); } catch { /* An invalid editor no longer matches the checked input. */ }
  if (!editorMatches) notes.push("The editor now contains a different snapshot. These findings describe the previous candidate.");
  const latestRevision = state.detail?.context?.revision;
  if (latestRevision > audit.revision) notes.push(`Stored state has since advanced to revision ${latestRevision}.`);
  if (notes.length) target.append(item("p", "audit-history-note", `${notes.join(" ")} Run a new audit to check the editor against current state.`));
  for (const issue of audit.issues || []) target.append(issueNode(issue, "audit-issue"));
}
async function auditCandidate(candidate) {
  const audit = await api(sessionPath("/audit"), { method: "POST", body: { candidate } });
  if (state.detail.context.revision !== audit.revision) await refreshDetail();
  state.lastAudit = { audit, candidate: clone(candidate) };
  paintAudit(); return audit;
}
function readCandidate() {
  let candidate;
  try { candidate = JSON.parse($("context-editor").value); } catch { throw new Error("This context snapshot is not valid JSON. Check commas and quotation marks."); }
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) throw new Error("The context snapshot must be a JSON object.");
  return candidate;
}
function feedback(message) { $("demo-feedback").textContent = message; $("demo-feedback").hidden = false; }
function openSession() {
  $("session-form").reset(); setError("session-form-error", null);
  $("session-name").value = "Customer support handoff";
  $("session-dialog").showModal();
  $("session-name").select();
}
function openFact(key) {
  if (!state.detail) return;
  const fact = key ? state.detail.facts[key] : null;
  $("fact-form").reset(); setError("fact-form-error", null);
  $("fact-dialog-title").textContent = fact ? "Update a fact" : "Add a fact";
  $("fact-key").value = key || ""; $("fact-key").readOnly = Boolean(fact);
  $("fact-value").value = fact ? pretty(fact.value) : "";
  state.factRevision = state.detail.session.revision;
  $("fact-dialog").showModal();
  (fact ? $("fact-value") : $("fact-key")).focus();
}
function openPolicy() {
  const entry = Object.entries(state.detail.facts || {})[0];
  const key = entry?.[0] || "approved";
  const value = entry?.[1]?.value ?? true;
  $("policy-editor").value = pretty({ id: "my-tool-policy", title: "My tool policy", tool: "my_tool", requirements: [{ key, op: "eq", value }], bindings: {}, optional_args: [] });
  setError("policy-form-error", null); $("policy-dialog").showModal();
}
async function copyText(text, success) {
  try { await navigator.clipboard.writeText(text); toast(success || "Copied to clipboard."); }
  catch {
    const scratch = item("textarea"); scratch.value = text; scratch.style.position = "fixed"; scratch.style.opacity = "0"; document.body.append(scratch); scratch.select();
    try { if (!document.execCommand("copy")) throw new Error(); toast(success || "Copied to clipboard."); }
    catch { toast("Clipboard access is unavailable. Select the text and copy it manually."); }
    finally { scratch.remove(); }
  }
}
async function loadDocs() {
  try {
    const docs = await api("/api/docs"); state.docs = docs;
    $("quickstart-code").textContent = typeof docs.quickstart === "string" ? docs.quickstart : pretty(docs.quickstart);
    for (const [id, values] of [["capabilities-list", docs.capabilities], ["limitations-list", docs.limitations]]) {
      $(id).replaceChildren();
      for (const value of Array.isArray(values) ? values : [values].filter(Boolean)) $(id).append(item("li", "", typeof value === "string" ? value : value.description || pretty(value)));
    }
  } catch (error) { $("quickstart-code").textContent = errorMessage(error); toast("Could not load the integration guide. Reopen this tab to retry."); }
}
async function boot() {
  $("global-error").hidden = true;
  try {
    const [health, listing] = await Promise.all([api("/api/health"), api("/api/sessions")]);
    $("version-label").textContent = `v${health.version || "0.11.0"}`;
    state.sessions = listing.sessions || [];
    renderSessions();
    if (!state.sessions.length) {
      const detail = await api("/api/sessions", { method: "POST", body: { name: "Customer support handoff", scenario: "refund" } });
      renderDetail(detail); toast("A local demo session is ready. Try the three-step workflow.");
    } else {
      let saved;
      try { saved = localStorage.getItem("context-relay-session"); } catch { /* Storage can be disabled. */ }
      const selected = state.sessions.find((session) => session.id === saved) || state.sessions.find((session) => session.scenario === "refund") || state.sessions[0];
      await loadSession(selected.id);
    }
  } catch (error) {
    $("global-error-text").textContent = errorMessage(error); $("global-error").hidden = false;
    $("session-loading").hidden = true;
    $("session-list").replaceChildren(item("div", "sidebar-placeholder", "Local server unavailable."));
  }
}

document.querySelectorAll("[data-tab]").forEach((tab) => {
  tab.addEventListener("click", () => showTab(tab.dataset.tab));
  tab.addEventListener("keydown", (event) => {
    const tabs = [...document.querySelectorAll("[data-tab]")];
    let index = tabs.indexOf(tab);
    if (event.key === "ArrowRight") index = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") index = (index + tabs.length - 1) % tabs.length;
    else if (event.key === "Home") index = 0;
    else if (event.key === "End") index = tabs.length - 1;
    else return;
    event.preventDefault(); showTab(tabs[index].dataset.tab, true);
  });
});
$("new-session").addEventListener("click", openSession);
$("new-session-bottom").addEventListener("click", openSession);
$("retry-load").addEventListener("click", boot);
document.querySelectorAll(".close-dialog").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
$("session-scenario").addEventListener("change", () => {
  const names = { refund: "Customer support handoff", deployment: "Deployment handoff", custom: "My workflow" };
  if (Object.values(names).includes($("session-name").value) || !$("session-name").value) $("session-name").value = names[$("session-scenario").value];
});
$("session-form").addEventListener("submit", (event) => {
  event.preventDefault();
  perform(async () => {
    const name = $("session-name").value.trim(); if (!name) throw new Error("Give this session a name.");
    const detail = await api("/api/sessions", { method: "POST", body: { name, scenario: $("session-scenario").value } });
    renderDetail(detail); showTab("workbench"); $("session-dialog").close(); toast("Session created on your machine.");
    try { localStorage.setItem("context-relay-session", detail.session.id); } catch { /* Storage can be disabled. */ }
  }, "session-form-error");
});
$("add-fact").addEventListener("click", () => openFact());
$("fact-form").addEventListener("submit", (event) => {
  event.preventDefault();
  perform(async () => {
    let value; try { value = JSON.parse($("fact-value").value); } catch { throw new Error('The value must be valid JSON. For text, use quotation marks: "replacement".'); }
    const key = $("fact-key").value.trim(); const text = $("fact-text").value.trim();
    if (!key || !text) throw new Error("Add a fact key and its original evidence or explanation.");
    let detail;
    try { detail = await api(sessionPath("/facts"), { method: "POST", body: { key, value, text, expected_revision: state.factRevision } }); }
    catch (error) {
      if (error.code === "revision_conflict") {
        await refreshDetail();
        throw new Error("This session changed while you were editing. Close this editor, review the current state, then reopen the fact to make your change.");
      }
      throw error;
    }
    renderDetail(detail, false); $("fact-dialog").close(); toast("Fact saved. Previous evidence remains in the timeline.");
  }, "fact-form-error");
});
$("add-policy").addEventListener("click", openPolicy);
$("policy-form").addEventListener("submit", (event) => {
  event.preventDefault();
  perform(async () => {
    let policy; try { policy = JSON.parse($("policy-editor").value); } catch { throw new Error("The policy is not valid JSON. Check commas and quotation marks."); }
    const detail = await api(sessionPath("/policies"), { method: "POST", body: policy });
    renderDetail(detail, false);
    const args = Object.create(null);
    for (const [argument, key] of Object.entries(policy.bindings || {})) args[argument] = detail.facts[key]?.value ?? "";
    setAction({ tool: policy.tool, arguments: args, context_revision: detail.context.revision });
    $("policy-dialog").close(); toast("Policy added. Review its bound arguments before checking an action.");
  }, "policy-form-error");
});
$("toggle-action-json").addEventListener("click", () => setAdvanced(!state.advanced));
$("action-tool").addEventListener("change", () => {
  const tool = $("action-tool").value;
  const demo = state.detail.demo;
  const seed = [demo?.proposed_action, demo?.safe_action].find((action) => action?.tool === tool);
  populateArguments(tool, seed?.arguments || {});
});
$("use-current-revision").addEventListener("click", () => { $("action-revision").value = state.detail.context.revision; toast(`Using revision ${state.detail.context.revision}.`); });
$("action-form").addEventListener("submit", (event) => { event.preventDefault(); perform(() => runAction(), "action-form-error"); });
$("simulate-action").addEventListener("click", () => perform(() => runAction(true), "action-form-error"));
$("demo-stale").addEventListener("click", () => perform(async () => {
  const action = clone(state.detail.demo.proposed_action); setAction(action);
  const response = await runAction(false, action); $("demo-stale").classList.add("done");
  feedback(response.decision === "allow" ? "This proposal passes the current configured policies. Review the session changes and evidence below." : "The old proposal was caught. Review the decision below, then restore the current context.");
}, "action-form-error"));
$("demo-restore").addEventListener("click", () => perform(async () => {
  const audit = await auditCandidate(clone(state.detail.demo.stale_context));
  setCandidate(audit.restored_context); $("action-revision").value = audit.restored_context.revision;
  try { const action = readAction(); action.context_revision = audit.restored_context.revision; setAction(action); } catch { /* Leave an invalid user edit intact. */ }
  $("demo-restore").classList.add("done");
  feedback(`Current context restored at revision ${audit.restored_context.revision}. The context audit tab shows what the old snapshot lost. Now try the corrected plan.`);
  toast("Current context restored. The original evidence is preserved.");
}, "action-form-error"));
$("demo-safe").addEventListener("click", () => perform(async () => {
  const action = clone(state.detail.demo.safe_action); action.context_revision = state.detail.context.revision; setAction(action);
  const response = await runAction(true, action); $("demo-safe").classList.toggle("done", Boolean(response.executed));
  feedback(response.executed ? "The corrected plan passed and ran as a local simulation. Its result is recorded in the original evidence below." : "The corrected proposal was checked against your current session. Review the result below; edited facts or policies may change this outcome.");
}, "action-form-error"));
$("context-editor").addEventListener("input", () => { updateContextSize(); resetAudit(); });
$("load-stale-context").addEventListener("click", () => { setCandidate(state.detail.demo.stale_context); resetAudit(); toast("Old snapshot loaded. Run an audit to compare it with current state."); });
$("audit-context").addEventListener("click", () => perform(() => auditCandidate(readCandidate()), "context-error"));
$("restore-context").addEventListener("click", () => perform(async () => {
  await refreshDetail(); setCandidate(state.detail.context); resetAudit(); toast("Latest context restored. Run an audit to verify this snapshot.");
}, "context-error"));
$("copy-context").addEventListener("click", () => copyText($("context-editor").value, "Context JSON copied."));
$("copy-quickstart").addEventListener("click", () => { if (state.docs) copyText($("quickstart-code").textContent, "Python quickstart copied."); });
$("export-session").addEventListener("click", () => perform(async () => {
  const data = await api(sessionPath("/export"));
  const blob = new Blob([pretty(data) + "\n"], { type: "application/json" });
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = `context-relay-${state.detail.session.id}.json`; document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000); toast("Session evidence exported as JSON.");
}));
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !document.querySelector("dialog[open]") && state.detail && !state.busy) {
    if (state.tab === "workbench") { event.preventDefault(); perform(() => runAction(), "action-form-error"); }
    else if (state.tab === "context") { event.preventDefault(); perform(() => auditCandidate(readCandidate()), "context-error"); }
  }
});
boot();
