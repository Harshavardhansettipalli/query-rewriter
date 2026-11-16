"use strict";

/*
 * Query Rewriter front end. Plain JavaScript, no build step.
 * Security: user-provided text is only ever inserted with textContent,
 * never innerHTML, so a query like <img onerror=...> can't run as code.
 */

const $ = (id) => document.getElementById(id);
const STORAGE_KEY = "query-rewriter.selected-user";
const state = { users: [], userId: null, result: null, busy: false, profileMode: "edit" };

const ACTION_WORDS = {
  search: "Searched for", view: "Viewed", click: "Opened", download: "Downloaded",
  bookmark: "Bookmarked", purchase: "Purchased", enroll: "Enrolled in",
};
const STATUS_WORDS = { pending: "Not decided", accepted: "Accepted", edited: "Edited", rejected: "Kept original" };

// ------------------------------------------------------------------ helpers
async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Can't reach the server. Check that it is running.");
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status}).`);
  }
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function showError(id, message) {
  const node = $(id);
  node.textContent = message || "";
  node.hidden = !message;
}

let toastTimer;
function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 2600);
}

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
function timeAgo(iso) {
  const seconds = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  const units = [["year", 31536000], ["month", 2592000], ["day", 86400], ["hour", 3600], ["minute", 60]];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

function savedUserId() {
  try { return Number(localStorage.getItem(STORAGE_KEY)) || null; } catch { return null; }
}
function saveUserId(id) {
  try { id ? localStorage.setItem(STORAGE_KEY, String(id)) : localStorage.removeItem(STORAGE_KEY); } catch { /* private mode */ }
}

// --------------------------------------------------------------------- tabs
const TABS = ["search", "profile", "activity", "history"];
function selectTab(name) {
  for (const tab of TABS) {
    const active = tab === name;
    $(`tab-${tab}`).setAttribute("aria-selected", String(active));
    $(`tab-${tab}`).tabIndex = active ? 0 : -1;
    $(`panel-${tab}`).hidden = !active;
  }
  if (name === "profile") fillProfileForm();
  if (name === "activity") loadActivities();
  if (name === "history") loadHistory();
}
function currentTab() {
  return TABS.find((t) => $(`tab-${t}`).getAttribute("aria-selected") === "true");
}

// ----------------------------------------------------------------- profiles
async function loadUsers(selectId) {
  state.users = await api("/api/users");
  const select = $("profile-select");
  select.replaceChildren();
  if (!state.users.length) {
    select.append(el("option", { value: "", text: "No profiles yet" }));
    select.disabled = true;
    setUser(null);
    return;
  }
  select.disabled = false;
  for (const user of state.users) {
    select.append(el("option", { value: String(user.id), text: user.name, title: user.role || user.name }));
  }
  const wanted = selectId ?? state.userId ?? savedUserId();
  const exists = state.users.some((u) => u.id === wanted);
  setUser(exists ? wanted : state.users[0].id);
}

function setUser(id) {
  const changed = id !== state.userId;
  state.userId = id;
  saveUserId(id);
  if (id) $("profile-select").value = String(id);
  $("no-profile-hint").hidden = Boolean(id);
  $("rewrite-btn").disabled = !id;
  if (changed) resetSearch();
}

function currentUser() {
  return state.users.find((u) => u.id === state.userId) || null;
}

function fillProfileForm(mode) {
  const wanted = mode || (state.userId ? "edit" : "create");
  const user = wanted === "edit" ? currentUser() : null;
  state.profileMode = user ? "edit" : "create"; // never PUT without a selected profile
  const form = $("profile-form");
  form.elements.name.value = user?.name || "";
  form.elements.email.value = user?.email || "";
  form.elements.role.value = user?.role || "";
  form.elements.location.value = user?.location || "";
  form.elements.interests.value = (user?.interests || []).join(", ");
  form.elements.preferences.value = Object.entries(user?.preferences || {}).map(([k, v]) => `${k} = ${v}`).join("\n");
  $("profile-heading").textContent = user ? "Edit profile" : "New profile";
  $("save-profile-btn").textContent = user ? "Save profile" : "Create profile";
  $("delete-profile-btn").hidden = !user;
  showError("profile-error", "");
}

function readProfileForm() {
  const f = $("profile-form").elements;
  const name = f.name.value.trim();
  if (!name) throw new Error("Enter a name for this profile.");
  const preferences = {};
  const lines = f.preferences.value.split("\n").map((l) => l.trim()).filter(Boolean);
  lines.forEach((line, i) => {
    const match = line.match(/^([^=:]+)[=:](.+)$/);
    if (!match) throw new Error(`Preference line ${i + 1} needs the form "key = value".`);
    preferences[match[1].trim()] = match[2].trim();
  });
  return {
    name,
    email: f.email.value.trim() || null,
    role: f.role.value.trim() || null,
    location: f.location.value.trim() || null,
    interests: f.interests.value.split(",").map((s) => s.trim()).filter(Boolean),
    preferences,
  };
}

async function saveProfile(event) {
  event.preventDefault();
  showError("profile-error", "");
  const button = $("save-profile-btn");
  try {
    const body = readProfileForm();
    button.disabled = true;
    const creating = state.profileMode === "create";
    const saved = creating
      ? await api("/api/users", { method: "POST", body })
      : await api(`/api/users/${state.userId}`, { method: "PUT", body });
    await loadUsers(saved.id);
    fillProfileForm("edit");
    toast(creating ? "Profile created" : "Profile saved");
  } catch (err) {
    showError("profile-error", err.message);
  } finally {
    button.disabled = false;
  }
}

async function deleteProfile() {
  const user = currentUser();
  if (!user) return;
  if (!confirm(`Delete ${user.name}'s profile, activity and history? This can't be undone.`)) return;
  try {
    await api(`/api/users/${user.id}`, { method: "DELETE" });
    state.userId = null;
    await loadUsers();
    fillProfileForm();
    toast("Profile deleted");
  } catch (err) {
    showError("profile-error", err.message);
  }
}

// ------------------------------------------------------------------- search
function resetSearch() {
  state.result = null;
  $("result").hidden = true;
  $("final").hidden = true;
  showError("search-error", "");
}

async function runRewrite(event) {
  event.preventDefault();
  if (state.busy) return; // double-submit guard
  const query = $("query-input").value.trim();
  showError("search-error", "");
  if (!state.userId) { selectTab("profile"); return; }
  if (!query) { showError("search-error", "Enter a search query."); return; }

  state.busy = true;
  $("rewrite-btn").disabled = true;
  $("result").hidden = true;
  $("final").hidden = true;
  $("loading").hidden = false;
  try {
    state.result = await api("/api/rewrite", { method: "POST", body: { user_id: state.userId, query } });
    renderResult(state.result);
  } catch (err) {
    showError("search-error", err.message);
  } finally {
    state.busy = false;
    $("rewrite-btn").disabled = !state.userId;
    $("loading").hidden = true;
  }
}

/* Word-level diff (longest common subsequence) so the user sees exactly what changed. */
function diffWords(before, after) {
  const a = before.split(/\s+/).filter(Boolean);
  const b = after.split(/\s+/).filter(Boolean);
  const key = (w) => w.toLowerCase().replace(/[.,!?;:]+$/, "");
  const dp = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      dp[i][j] = key(a[i]) === key(b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0, j = 0;
  const push = (type, word) => {
    const last = ops[ops.length - 1];
    if (last && last.type === type) last.words.push(word);
    else ops.push({ type, words: [word] });
  };
  while (i < a.length && j < b.length) {
    if (key(a[i]) === key(b[j])) { push("same", b[j]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { push("del", a[i]); i++; }
    else { push("ins", b[j]); j++; }
  }
  while (i < a.length) push("del", a[i++]);
  while (j < b.length) push("ins", b[j++]);
  return ops;
}

function renderDiff(container, before, after, changed) {
  container.replaceChildren();
  if (!changed) { container.textContent = after; return; }
  diffWords(before, after).forEach((op, index) => {
    if (index > 0) container.append(" ");
    const text = op.words.join(" ");
    if (op.type === "same") container.append(text);
    else container.append(el(op.type, { text }));
  });
}

function addFact(list, term, value) {
  if (!value) return;
  list.append(el("dt", { text: term }), el("dd", { text: value }));
}

function renderResult(r) {
  $("result-status").textContent = r.changed ? "Suggested query" : "Your query looks good";
  $("notice").textContent = r.notice || "";
  $("notice").hidden = !r.notice;

  renderDiff($("diff"), r.original_query, r.suggested_query, r.changed);
  $("explanation").textContent = r.explanation;

  const facts = $("facts");
  facts.replaceChildren();
  addFact(facts, "Intent", r.intent_description || r.intent);
  if (r.ambiguity && r.ambiguity !== "none") {
    addFact(facts, "Ambiguity", r.ambiguity_reason ? `${r.ambiguity}: ${r.ambiguity_reason}` : r.ambiguity);
  }
  addFact(facts, "Spelling", r.spelling_fixes.map((f) => `${f.original} → ${f.corrected}`).join(", "));
  addFact(facts, "Context added", r.context_used.join("; "));
  facts.hidden = !facts.children.length;

  const steps = $("steps");
  steps.replaceChildren();
  const engineName = { python: "Python", llm: "AI", user: "You" };
  const statusName = { done: "Done", skipped: "Skipped", fallback: "Fallback used", pending: "Waiting" };
  for (const step of r.steps) {
    const timing = step.duration_ms ? `, ${step.duration_ms} ms` : "";
    steps.append(el("li", { class: step.status },
      el("div", { class: "step-head" },
        el("span", { text: step.title }),
        el("span", { class: `tag ${step.engine}`, text: engineName[step.engine] || step.engine }),
        el("span", { class: `status ${step.status}`, text: statusName[step.status] + timing })),
      el("div", { class: "step-summary", text: step.summary })));
  }

  $("accept-btn").textContent = r.changed ? "Accept" : "Search with this query";
  $("reject-btn").hidden = !r.changed;
  setEditing(false);
  showError("decision-error", "");
  $("result").hidden = false;
  $("accept-btn").focus({ preventScroll: true });
}

function setEditing(on) {
  $("edit-box").hidden = !on;
  $("diff").hidden = on;
  $("actions").hidden = on;
  $("edit-actions").hidden = !on;
  if (on) {
    const input = $("edit-input");
    input.value = state.result.suggested_query;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

async function decide(action) {
  if (!state.result || state.busy) return;
  showError("decision-error", "");
  const body = { action };
  if (action === "edit") {
    body.edited_query = $("edit-input").value.trim();
    if (!body.edited_query) { showError("decision-error", "Enter the edited query."); return; }
  }
  state.busy = true;
  document.querySelectorAll("#result .btn").forEach((b) => { b.disabled = true; });
  try {
    const res = await api(`/api/queries/${state.result.query_id}/decision`, { method: "POST", body });
    $("result").hidden = true;
    $("final-label").textContent = {
      accepted: "Searching for", edited: "Searching for your edit", rejected: "Searching for your original query",
    }[res.status];
    $("final-query").textContent = res.final_query;
    $("final").hidden = false;
    $("new-search-btn").focus({ preventScroll: true });
  } catch (err) {
    showError("decision-error", err.message);
  } finally {
    state.busy = false;
    document.querySelectorAll("#result .btn").forEach((b) => { b.disabled = false; });
  }
}

// ----------------------------------------------------------------- activity
async function loadActivities() {
  const list = $("activity-list");
  list.replaceChildren();
  showError("activity-error", "");
  if (!state.userId) { list.append(el("li", { class: "empty", text: "Create a profile to record activity." })); return; }
  try {
    const items = await api(`/api/users/${state.userId}/activities?limit=50`);
    if (!items.length) { list.append(el("li", { class: "empty", text: "No activity yet. Add something this person viewed or searched." })); return; }
    for (const a of items) {
      const remove = el("button", { class: "icon-btn", type: "button", text: "Remove", "aria-label": `Remove ${a.content}` });
      remove.addEventListener("click", () => removeActivity(a.id));
      list.append(el("li", {},
        el("div", {},
          el("div", { class: "primary-text", text: `${ACTION_WORDS[a.event_type] || a.event_type} ${a.content}` }),
          el("div", { class: "meta", text: timeAgo(a.created_at) })),
        remove));
    }
  } catch (err) {
    showError("activity-error", err.message);
  }
}

async function addActivity(event) {
  event.preventDefault();
  showError("activity-error", "");
  if (!state.userId) { showError("activity-error", "Create a profile first."); return; }
  const content = $("activity-content").value.trim();
  if (!content) { showError("activity-error", "Describe the activity."); return; }
  try {
    await api(`/api/users/${state.userId}/activities`, {
      method: "POST", body: { event_type: $("activity-type").value, content },
    });
    $("activity-content").value = "";
    await loadActivities();
  } catch (err) {
    showError("activity-error", err.message);
  }
}

async function removeActivity(id) {
  try {
    await api(`/api/users/${state.userId}/activities/${id}`, { method: "DELETE" });
    await loadActivities();
  } catch (err) {
    showError("activity-error", err.message);
  }
}

// ------------------------------------------------------------------ history
async function loadHistory() {
  const list = $("history-list");
  list.replaceChildren();
  if (!state.userId) { list.append(el("li", { class: "empty", text: "Create a profile to see search history." })); return; }
  try {
    const items = await api(`/api/users/${state.userId}/queries?limit=50`);
    if (!items.length) { list.append(el("li", { class: "empty", text: "No searches yet. Run one from the Search tab." })); return; }
    for (const q of items) {
      const shown = q.final_query || q.suggested_query;
      const typed = q.original_query !== shown ? el("div", { class: "meta", text: `You typed: ${q.original_query}` }) : null;
      list.append(el("li", {},
        el("div", {},
          el("div", { class: "primary-text", text: shown }),
          typed,
          el("div", { class: "meta", text: timeAgo(q.created_at) })),
        el("span", { class: `badge ${q.status}`, text: STATUS_WORDS[q.status] || q.status })));
    }
  } catch (err) {
    list.append(el("li", { class: "empty", text: err.message }));
  }
}

// --------------------------------------------------------------------- init
async function checkHealth() {
  try {
    const health = await api("/api/health");
    const banner = $("ai-banner");
    banner.hidden = health.ai_enabled;
    banner.textContent = "AI is off, so only dictionary spelling fixes are available. Add OPENAI_API_KEY to the .env file and restart the server.";
  } catch { /* the first real request will show the error */ }
}

function bindEvents() {
  for (const tab of TABS) $(`tab-${tab}`).addEventListener("click", () => selectTab(tab));
  document.querySelector(".tabs").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const index = TABS.indexOf(currentTab());
    const next = TABS[(index + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
    selectTab(next);
    $(`tab-${next}`).focus();
  });

  $("profile-select").addEventListener("change", (e) => {
    setUser(Number(e.target.value) || null);
    const tab = currentTab();
    if (tab !== "search") selectTab(tab);
  });

  $("search-form").addEventListener("submit", runRewrite);
  $("accept-btn").addEventListener("click", () => decide("accept"));
  $("reject-btn").addEventListener("click", () => decide("reject"));
  $("edit-btn").addEventListener("click", () => setEditing(true));
  $("cancel-edit-btn").addEventListener("click", () => setEditing(false));
  $("save-edit-btn").addEventListener("click", () => decide("edit"));
  $("edit-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); decide("edit"); }
  });
  $("new-search-btn").addEventListener("click", () => {
    resetSearch();
    $("query-input").select();
    $("query-input").focus();
  });

  $("profile-form").addEventListener("submit", saveProfile);
  $("new-profile-btn").addEventListener("click", () => fillProfileForm("create"));
  $("delete-profile-btn").addEventListener("click", deleteProfile);
  $("activity-form").addEventListener("submit", addActivity);
}

async function init() {
  bindEvents();
  checkHealth();
  try {
    await loadUsers();
    if (!state.userId) selectTab("profile");
  } catch (err) {
    showError("search-error", err.message);
  }
}

document.addEventListener("DOMContentLoaded", init);
