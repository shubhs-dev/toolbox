// vidlib's downloads page. All naming logic lives on the server (vidlib/naming.py); this
// script only sends what the user typed or clicked and renders what comes back.
"use strict";

const $ = (id) => document.getElementById(id);
const ROLES = [["people", "P"], ["title", "T"], ["collection", "C"], ["skip", "×"]];

// ── API ───────────────────────────────────────────────────────────────

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

// ── the add form ──────────────────────────────────────────────────────

const form = {
  // "segments": the fields follow the segment roles; "fields": the user typed in them.
  mode: "segments",
  roles: null,          // null until the user picks a role: the server guesses
  draft: null,          // the server's last answer
  namingId: null,       // set while naming a bulk-pasted item
  seq: 0,               // drops answers to requests that have since been superseded
};

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function nameFromTitle() {
  const title = $("page-title").value;
  const seq = ++form.seq;
  if (!title.trim()) {
    form.draft = null;
    form.roles = null;
    renderSegments([]);
    if (form.mode === "segments") setFields("", "", "");
    await nameFromFields(seq);
    return;
  }
  const data = await api("POST", "/api/name", { title, roles: form.roles });
  if (seq !== form.seq) return;
  form.mode = "segments";
  form.roles = data.segments.map((s) => s.role);
  renderSegments(data.segments);
  setFields(data.people.map((p) => p.text).join(", "), data.title,
            data.collection ? data.collection.text : "");
  renderDraft(data);
}

async function nameFromFields(seq = ++form.seq) {
  const data = await api("POST", "/api/name/fields", {
    people: $("people").value, title: $("title").value, collection: $("collection").value,
  });
  if (seq !== form.seq) return;
  renderDraft(data);
}

function setFields(people, title, collection) {
  $("people").value = people;
  $("title").value = title;
  $("collection").value = collection;
}

function setRole(index, role) {
  let roles = form.roles.slice();
  if (role === "collection") roles = roles.map((r) => (r === "collection" ? "title" : r));
  roles[index] = role;
  form.roles = roles;
  nameFromTitle().catch(showError);
}

function renderSegments(segments) {
  const list = $("segments");
  list.replaceChildren();
  $("segments-wrap").hidden = segments.length === 0;
  segments.forEach((seg, i) => {
    const li = document.createElement("li");
    li.className = `segment role-${seg.role}`;
    const text = document.createElement("span");
    text.className = "segment-text";
    text.textContent = seg.text;
    if (seg.site) text.title = "After the last | — usually the site's name";
    const roles = document.createElement("span");
    roles.className = "roles";
    for (const [role, key] of ROLES) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `role-${role}`;
      b.textContent = key;
      b.title = role;
      b.setAttribute("aria-label", `${seg.text}: ${role}`);
      b.setAttribute("aria-pressed", String(seg.role === role));
      b.addEventListener("click", () => setRole(i, role));
      roles.append(b);
    }
    li.append(text, roles);
    list.append(li);
  });
}

function chip(part, onAccept) {
  const el = document.createElement("span");
  el.className = `chip ${part.match}`;
  const name = document.createElement("span");
  name.textContent = part.text;
  el.append(name);
  if (part.match === "loose") {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "link small";
    b.textContent = `did you mean ${part.suggestion}?`;
    b.addEventListener("click", () => onAccept(part.suggestion));
    el.append(b);
  } else if (part.match === "new") {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = "new";
    el.append(tag);
  }
  return el;
}

function renderDraft(data) {
  form.draft = data;
  const chips = $("people-chips");
  chips.replaceChildren(...data.people.map((p) => chip(p, (spelling) => {
    const names = data.people.map((q) => (q === p ? spelling : q.text));
    $("people").value = names.join(", ");
    form.mode = "fields";
    nameFromFields().catch(showError);
  })));
  const coll = $("collection-chip");
  coll.replaceChildren(...(data.collection ? [chip(data.collection, (spelling) => {
    $("collection").value = spelling;
    form.mode = "fields";
    nameFromFields().catch(showError);
  })] : []));

  const stem = $("stem");
  if (data.stem) {
    stem.textContent = `${data.stem}.ext`;
    stem.classList.remove("dim");
  } else {
    stem.textContent = "Named after the video's own title";
    stem.classList.add("dim");
  }
  $("problems").replaceChildren(...data.problems.map((p) => {
    const li = document.createElement("li");
    li.textContent = p;
    return li;
  }));
  updateAddButton();
}

function updateAddButton() {
  const hasUrl = $("url").value.trim() !== "" || form.namingId !== null;
  const complete = !!(form.draft && form.draft.complete);
  $("add-btn").disabled = !(hasUrl && complete);
  $("add-anyway").hidden = !(hasUrl && !complete);
}

function resetForm() {
  form.mode = "segments";
  form.roles = null;
  form.draft = null;
  form.namingId = null;
  form.seq++;
  $("url").value = "";
  $("url").disabled = false;
  $("page-title").value = "";
  setFields("", "", "");
  renderSegments([]);
  $("naming-banner").hidden = true;
  nameFromFields().catch(showError);
}

async function submit() {
  const name = form.draft ? form.draft.stem : "";
  const pageTitle = $("page-title").value;
  try {
    if (form.namingId !== null) {
      await api("POST", `/api/downloads/${form.namingId}/name`, { name, page_title: pageTitle });
    } else {
      await api("POST", "/api/downloads", { url: $("url").value, name, page_title: pageTitle });
    }
    message(`Queued ${name || "the video"}`);
    resetForm();
    $("url").focus();
  } catch (err) {
    showError(err);
  }
}

function message(text, isError = false) {
  const el = $("form-msg");
  el.textContent = text;
  el.classList.toggle("error", isError);
  clearTimeout(message.t);
  message.t = setTimeout(() => { el.textContent = ""; }, isError ? 8000 : 4000);
}
function showError(err) { message(err.message || String(err), true); }

// ── autocomplete for People and Collection ────────────────────────────

const PEOPLE_SEP = /(\s*(?:,|&|\+|\band\b|\bx\b|×)\s*)/i;

function attachSuggest(input, list, kind) {
  let items = [];
  let active = -1;
  const close = () => { list.hidden = true; items = []; active = -1; };

  // For People only the name being typed (after the last separator) is completed.
  const current = () => {
    if (kind !== "people") return { head: "", word: input.value };
    const parts = input.value.split(PEOPLE_SEP);
    const word = parts.pop();
    return { head: parts.join(""), word };
  };
  const choose = (value) => {
    const { head } = current();
    input.value = kind === "people" ? `${head}${value}` : value;
    close();
    form.mode = "fields";
    nameFromFields().catch(showError);
  };
  const render = () => {
    list.replaceChildren(...items.map((s, i) => {
      const li = document.createElement("li");
      li.textContent = s;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", String(i === active));
      li.addEventListener("mousedown", (e) => { e.preventDefault(); choose(s); });
      return li;
    }));
    list.hidden = items.length === 0;
  };
  const lookup = debounce(async () => {
    const { word } = current();
    if (word.trim().length < 1) { close(); return; }
    const data = await api("GET", `/api/suggest?kind=${kind}&q=${encodeURIComponent(word)}`);
    items = data.suggestions.filter((s) => s.toLowerCase() !== word.trim().toLowerCase());
    active = -1;
    render();
  }, 120);

  input.addEventListener("input", () => lookup());
  input.addEventListener("blur", () => setTimeout(close, 100));
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      render();
    } else if ((e.key === "Enter" || e.key === "Tab") && active >= 0) {
      e.preventDefault();
      choose(items[active]);
    } else if (e.key === "Escape") {
      close();
    }
  });
}

// ── the queue ─────────────────────────────────────────────────────────

const STATUS_TEXT = {
  naming: "needs a name", queued: "queued", downloading: "downloading",
  downloaded: "downloaded", imported: "in library", failed: "failed", cancelled: "cancelled",
};

function formatRate(bytes) {
  if (!bytes) return "";
  const units = ["B/s", "KB/s", "MB/s", "GB/s"];
  let i = 0;
  while (bytes >= 1000 && i < units.length - 1) { bytes /= 1000; i++; }
  return `${bytes.toFixed(bytes < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

function action(label, fn, cls = "small") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = cls;
  b.textContent = label;
  b.addEventListener("click", () => fn().catch(showError));
  return b;
}

function renderRow(d) {
  const li = $("row-template").content.firstElementChild.cloneNode(true);
  li.classList.add(d.status);
  li.querySelector(".row-name").textContent =
    d.status === "naming" ? (d.page_title || (d.fetching ? "Fetching title…" : d.url))
      : (d.name || "Named after the video's own title");
  let sub = d.url;
  if (d.status === "imported" && d.library_path) sub = d.library_path;
  else if (d.status === "downloaded" && d.file) sub = d.file;
  li.querySelector(".row-sub").textContent = sub;
  li.querySelector(".row-sub").title = sub;
  li.querySelector(".row-error").textContent = d.error;

  const bar = li.querySelector(".row-bar");
  bar.hidden = !["downloading", "queued"].includes(d.status);
  bar.firstElementChild.style.width = `${d.percent}%`;

  const pill = li.querySelector(".pill");
  pill.textContent = STATUS_TEXT[d.status] || d.status;
  pill.className = `pill ${d.status}`;
  const stats = [];
  if (d.status === "downloading") {
    stats.push(`${d.percent.toFixed(1)}%`);
    if (d.speed) stats.push(d.speed);
    if (d.eta) stats.push(`ETA ${d.eta}`);
  }
  li.querySelector(".row-stats").textContent = stats.join(" · ");

  const acts = li.querySelector(".row-actions");
  if (d.status === "naming") {
    acts.append(action("Name", async () => startNaming(d), "small primary"));
  }
  if (d.status === "queued" || d.status === "downloading") {
    acts.append(action("Cancel", () => api("POST", `/api/downloads/${d.id}/cancel`, {})));
  }
  if (d.status === "failed" || d.status === "cancelled") {
    acts.append(action("Retry", () => api("POST", `/api/downloads/${d.id}/retry`, {})));
  }
  if (!["queued", "downloading"].includes(d.status)) {
    acts.append(action("Remove", () => api("DELETE", `/api/downloads/${d.id}`)));
  }
  return li;
}

function startNaming(d) {
  resetForm();
  form.namingId = d.id;
  $("url").value = d.url;
  $("url").disabled = true;
  $("page-title").value = d.page_title;
  $("naming-banner").hidden = false;
  $("page-title").focus();
  return nameFromTitle();
}

function renderState(state) {
  const mode = $("mode");
  const downloadOnly = state.mode === "download-only";
  mode.textContent = downloadOnly ? "download only" : "library";
  mode.classList.toggle("download-only", downloadOnly);
  mode.title = downloadOnly ? `Saving into ${state.target}; library at ${state.library}`
                            : `Library at ${state.library}`;

  const groups = { naming: [], waiting: [], active: [], failed: [], done: [] };
  for (const d of state.downloads) {
    if (d.status === "naming") groups.naming.push(d);
    else if (d.status === "downloaded") groups.waiting.push(d);
    else if (d.status === "queued" || d.status === "downloading") groups.active.push(d);
    else if (d.status === "failed") groups.failed.push(d);
    else groups.done.push(d);
  }
  groups.active.sort((a, b) => (a.status === "downloading" ? -1 : 1) - (b.status === "downloading" ? -1 : 1) || a.id - b.id);
  groups.done.reverse();
  for (const [key, rows] of Object.entries(groups)) {
    $(`rows-${key}`).replaceChildren(...rows.map(renderRow));
    if (key !== "active") $(`group-${key}`).hidden = rows.length === 0;
  }
  $("empty-active").hidden = groups.active.length > 0;
  $("waiting-hint").textContent = downloadOnly
    ? `Saved in ${state.target}. Move them once the library has room.`
    : "These couldn't be moved into the library root, usually because a file of that name is already there.";
  $("import-btn").textContent = `Move ${groups.waiting.length} into library`;

  const o = state.overall;
  const parts = [];
  if (o.total) parts.push(`${o.done} of ${o.total} done`);
  if (o.running) parts.push(`${o.running} downloading`);
  if (o.queued) parts.push(`${o.queued} queued`);
  if (o.failed) parts.push(`${o.failed} failed`);
  if (o.naming) parts.push(`${o.naming} to name`);
  $("overall-counts").textContent = parts.join(" · ") || "Nothing queued";
  $("overall-speed").textContent = formatRate(o.speed);
  $("overall-bar").style.width = `${o.percent}%`;
  document.title = o.running ? `vidlib · ${Math.round(o.percent)}%` : "vidlib";
}

function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = (e) => {
    $("conn").classList.remove("off");
    renderState(JSON.parse(e.data));
  };
  es.onerror = () => { $("conn").classList.add("off"); $("conn").title = "Reconnecting…"; };
  es.onopen = () => { $("conn").title = "Live updates"; };
}

// ── wiring ────────────────────────────────────────────────────────────

const titleChanged = debounce(() => {
  form.roles = null;   // a new title gets a fresh guess
  nameFromTitle().catch(showError);
}, 150);
const fieldsChanged = debounce(() => {
  form.mode = "fields";
  nameFromFields().catch(showError);
}, 150);

$("page-title").addEventListener("input", titleChanged);
for (const id of ["people", "title", "collection"]) $(id).addEventListener("input", fieldsChanged);
$("url").addEventListener("input", updateAddButton);
$("add-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!$("add-btn").disabled) submit();
});
$("add-anyway").addEventListener("click", submit);
$("form-reset").addEventListener("click", resetForm);
$("naming-cancel").addEventListener("click", resetForm);
$("clear-finished").addEventListener("click", () => api("POST", "/api/downloads/clear", {}).catch(showError));
$("import-btn").addEventListener("click", async () => {
  try {
    const r = await api("POST", "/api/downloads/import", {});
    message(r.problems.length ? `Moved ${r.moved}; ${r.problems.join("; ")}` : `Moved ${r.moved} into the library`,
            r.problems.length > 0);
  } catch (err) { showError(err); }
});
$("bulk-add").addEventListener("click", async () => {
  const urls = $("bulk-urls").value.split(/\s+/).filter((u) => u.trim());
  if (!urls.length) return;
  try {
    const r = await api("POST", "/api/downloads/bulk", { urls });
    $("bulk-urls").value = "";
    message(`${r.added} URL(s) added — fetching their titles`);
  } catch (err) { showError(err); }
});
attachSuggest($("people"), $("people-suggest"), "people");
attachSuggest($("collection"), $("collection-suggest"), "collections");

api("GET", "/api/state").then(renderState).catch(showError);
nameFromFields().catch(showError);
connect();
