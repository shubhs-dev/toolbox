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
  // An empty form has nothing wrong with it yet.
  $("problems").replaceChildren(...(data.stem ? data.problems : []).map((p) => {
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

function attachSuggest(input, list, kind, onChoose) {
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
    onChoose();
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
  renderPipeline(state.pipeline);
  const count = $("review-count");
  count.hidden = !state.review_count;
  count.textContent = String(state.review_count || "");
  if (state.review_count !== lastReviewCount) {
    if (lastReviewCount !== null && !$("view-review").hidden) loadReview().catch(showReviewError);
    lastReviewCount = state.review_count;
  }
}
let lastReviewCount = null;

// ── the pipeline ──────────────────────────────────────────────────────

const PIPE_STATE = {
  starting: "starting", scanning: "scanning", encoding: "encoding", idle: "idle",
  paused: "paused", outside_hours: "outside hours", high_load: "high load",
  disk_missing: "disk missing",
};
const RESULT_TEXT = {
  sorted: "sorted", review: "to review", awaiting_trip: "no collection", skipped: "skipped",
  failed: "failed", stopped: "stopped", "dry-run": "dry run",
};

function duration(seconds) {
  if (seconds == null) return "";
  seconds = Math.max(0, Math.round(seconds));
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

function clock(epoch) {
  return new Date(epoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function renderPipeline(p) {
  $("pipeline").hidden = !p;
  if (!p) return;
  const pill = $("pipe-state");
  pill.textContent = PIPE_STATE[p.state] || p.state;
  pill.className = `pill ${p.state}`;
  if (p.paused && p.state === "encoding") pill.textContent = "pausing after this file";
  $("pipe-pause").hidden = p.paused;
  $("pipe-stop").hidden = p.paused && !p.current;
  $("pipe-resume").hidden = !p.paused;

  $("pipe-disk").hidden = p.state !== "disk_missing";
  $("pipe-disk").textContent = `${p.detail} isn't there — is the library's disk connected? ` +
    "Nothing is synced or encoded until it's back.";

  const cur = p.current;
  $("pipe-current").hidden = !cur;
  if (cur) {
    $("pipe-file").textContent = cur.file;
    $("pipe-meta").textContent = cur.step === "encoding"
      ? `${cur.preset} · ${cur.encoder}` : "checking the file…";
    $("pipe-bar").style.width = `${cur.percent}%`;
    const stats = [];
    if (cur.step === "encoding") stats.push(`${cur.percent.toFixed(1)}%`);
    if (cur.eta != null) stats.push(`${duration(cur.eta)} left`);
    if (p.batch_total > 1) stats.push(`file ${p.batch_done + 1} of ${p.batch_total}`);
    $("pipe-stats").textContent = stats.join(" · ");
  }

  let line = "";
  if (p.waiting.length) {
    line = `Next: ${p.waiting.slice(0, 3).join(", ")}${p.waiting.length > 3 ? ` and ${p.waiting.length - 3} more` : ""}`;
  } else if (p.state === "idle" && p.next_poll) {
    line = `Watching the library root — next look at ${clock(p.next_poll)}.`;
  } else if (p.state === "outside_hours") {
    line = `Encodes only start inside ${p.detail}.`;
  } else if (p.state === "high_load") {
    line = `Waiting for the load average to drop below ${p.detail}.`;
  } else if (p.state === "paused") {
    line = "Paused. New files wait in the library root until you resume.";
  }
  $("pipe-line").textContent = line;
  $("pipe-line").hidden = !line;

  $("pipe-recent").replaceChildren(...p.recent.slice(0, 8).map((r) => {
    const li = document.createElement("li");
    const pillEl = document.createElement("span");
    pillEl.className = `pill ${r.status}`;
    pillEl.textContent = RESULT_TEXT[r.status] || r.status;
    const what = document.createElement("span");
    what.className = "what";
    what.textContent = r.path || r.file;
    what.title = [r.file, r.reason].filter(Boolean).join(" — ");
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = clock(r.at);
    li.append(pillEl, what, when);
    return li;
  }));

  const st = p.settings || {};
  const bits = [`every ${st.interval}s`, `encoder ${st.gpu}`];
  if (st.hours) bits.push(`hours ${st.hours}`);
  if (st.max_load != null) bits.push(`max load ${st.max_load}`);
  $("pipe-settings").textContent = bits.join(" · ");
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

// ── the library ───────────────────────────────────────────────────────

const lib = { view: "all", offset: 0, total: 0, loaded: false, seq: 0, lastGroup: null };

function humanSize(n) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

function libQuery(offset) {
  const params = new URLSearchParams({
    view: lib.view, q: $("lib-q").value, person: $("lib-person").value,
    collection: $("lib-collection").value, resolution: $("lib-resolution").value,
    offset: String(offset),
  });
  return `/api/library?${params}`;
}

function fillSelect(select, items, allLabel) {
  const keep = select.value;
  select.replaceChildren(new Option(allLabel, ""), ...items.map((it) => new Option(`${it.name} (${it.count})`, it.name)));
  select.value = keep;
}

function thumbUrl(path) {
  return `/api/thumb?path=${encodeURIComponent(path)}`;
}

function card(v) {
  const li = document.createElement("li");
  const b = document.createElement("button");
  b.type = "button";
  b.className = "card";
  const img = document.createElement("img");
  img.className = "thumb";
  img.loading = "lazy";
  img.alt = "";
  img.src = thumbUrl(v.path);
  img.addEventListener("error", () => img.classList.add("none"), { once: true });
  const body = document.createElement("span");
  body.className = "body";
  const t = document.createElement("span");
  t.className = "t";
  t.textContent = v.complete ? v.title : v.filename;
  t.title = v.filename;
  const p = document.createElement("span");
  p.className = "p";
  p.textContent = v.people.join(", ");
  const m = document.createElement("span");
  m.className = "m";
  const bits = [];
  if (v.collection) {
    const c = document.createElement("span");
    c.className = "c";
    c.textContent = v.collection;
    bits.push(c);
  }
  for (const text of [v.resolution, v.bit_depth > 8 ? `${v.bit_depth}-bit` : "",
                      v.duration ? duration(v.duration) : "", v.size ? humanSize(v.size) : ""]) {
    if (!text) continue;
    const s = document.createElement("span");
    s.textContent = text;
    bits.push(s);
  }
  m.append(...bits);
  if (v.status === "review") {
    const pill = document.createElement("span");
    pill.className = "pill review";
    pill.textContent = "in review";
    m.append(pill);
  }
  body.append(t, p, m);
  b.append(img, body);
  b.addEventListener("click", () => openDetail(v.path));
  li.append(b);
  return li;
}

async function loadLibrary(append = false) {
  const seq = ++lib.seq;
  const offset = append ? lib.offset : 0;
  const data = await api("GET", libQuery(offset));
  if (seq !== lib.seq) return;
  lib.loaded = true;
  fillSelect($("lib-person"), data.people, "Everyone");
  fillSelect($("lib-collection"), data.collections, "All collections");
  const grid = $("lib-grid");
  if (!append) { grid.replaceChildren(); lib.lastGroup = null; }
  for (const v of data.videos) {
    if (v.group !== undefined && v.group !== lib.lastGroup) {
      const sep = document.createElement("li");
      sep.className = "group-break";
      sep.textContent = v.complete ? `${v.people.join(", ")} — ${v.title}` : v.title;
      grid.append(sep);
      lib.lastGroup = v.group;
    }
    grid.append(card(v));
  }
  lib.offset = offset + data.videos.length;
  lib.total = data.total;
  $("lib-more").hidden = lib.offset >= data.total;
  $("lib-empty").hidden = data.total > 0;
  const all = data.library;
  $("lib-summary").textContent =
    `${data.total} shown of ${all.videos} videos · ${humanSize(all.size)} in the library`;
}

const reloadLibrary = debounce(() => loadLibrary().catch(showLibError), 200);
function showLibError(err) { $("lib-summary").textContent = err.message || String(err); }

// ── one video ──

const detail = { path: null, video: null, seq: 0 };

function fact(dl, label, value) {
  if (value === "" || value == null) return;
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dl.append(dt, dd);
}

function detailMsg(text, isError = false) {
  const el = $("detail-msg");
  el.textContent = text;
  el.classList.toggle("error", isError);
}

async function openDetail(path) {
  const v = await api("GET", `/api/library/video?path=${encodeURIComponent(path)}`).catch((err) => {
    showLibError(err);
    return null;
  });
  if (!v) return;
  detail.path = v.path;
  detail.video = v;
  $("detail-title").textContent = v.filename;
  const thumb = $("detail-thumb");
  thumb.classList.remove("none");
  thumb.onerror = () => thumb.classList.add("none");
  thumb.src = thumbUrl(v.path);
  const facts = $("detail-facts");
  facts.replaceChildren();
  fact(facts, "Folder", v.folder === "." ? "library root" : v.folder);
  fact(facts, "People", v.people.join(", "));
  fact(facts, "Title", v.title);
  fact(facts, "Collection", v.collection);
  fact(facts, "Tags", v.tags.join(" "));
  fact(facts, "Video", [v.resolution, v.bit_depth > 8 ? `${v.bit_depth}-bit` : "", v.has_sub ? "subtitles" : ""].filter(Boolean).join(" · "));
  fact(facts, "Length", v.duration ? duration(v.duration) : "");
  fact(facts, "Size", v.size ? humanSize(v.size) : "");
  if (v.status === "review") fact(facts, "In review", `${v.reason} — compared with ${v.compared_with}`);
  $("detail-confirm").hidden = true;
  $("rn-people").value = v.people.join(", ");
  $("rn-title").value = v.title;
  $("rn-collection").value = v.collection;
  detailMsg("");
  await renamePreview();
  const dlg = $("detail");
  if (!dlg.open) dlg.showModal();
}

async function renamePreview() {
  const seq = ++detail.seq;
  const data = await api("POST", "/api/name/fields", {
    people: $("rn-people").value, title: $("rn-title").value, collection: $("rn-collection").value,
  });
  if (seq !== detail.seq) return;
  const tags = detail.video && detail.video.tags.length ? ` [${detail.video.tags.join(" ")}]` : "";
  $("rn-stem").textContent = data.stem ? `${data.stem}${tags}` : "";
  $("rn-problems").replaceChildren(...data.problems.map((p) => {
    const li = document.createElement("li");
    li.textContent = p;
    return li;
  }));
  const unchanged = detail.video && `${data.stem}${tags}` === detail.video.filename.replace(/\.[^.]+$/, "");
  $("rn-save").disabled = !data.complete || unchanged;
}

async function detailAction(action) {
  try {
    await api("POST", `/api/library/${action}`, { path: detail.path });
  } catch (err) { detailMsg(err.message, true); }
}

// ── the review queue ──

const VERDICT = {
  ambiguous: "Same video, and neither copy is clearly better",
  downgrade: "Lower resolution than the library's copy",
  upgrade: "Better than the library's copy", more_complete: "Longer than the library's copy",
};

function sideCard(label, v, other) {
  const box = document.createElement("div");
  box.className = "side-card";
  const lab = document.createElement("span");
  lab.className = "label";
  lab.textContent = label;
  box.append(lab);
  if (!v) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "Not in the library any more.";
    box.append(p);
    return box;
  }
  const img = document.createElement("img");
  img.alt = "";
  img.loading = "lazy";
  img.src = thumbUrl(v.path);
  img.addEventListener("error", () => img.classList.add("none"), { once: true });
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = v.filename;
  const dl = document.createElement("dl");
  const row = (term, value, better) => {
    if (!value) return;
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = value;
    if (better) dd.classList.add("better");
    dl.append(dt, dd);
  };
  const o = other || {};
  row("Resolution", v.resolution || "unknown", other && v.height > o.height);
  row("Bit depth", `${v.bit_depth}-bit`, other && v.bit_depth > o.bit_depth);
  row("Length", v.duration ? duration(v.duration) : "", other && v.duration > o.duration + 1);
  row("Size", v.size ? humanSize(v.size) : "", other && v.size && o.size && v.size < o.size);
  row("Subtitles", v.has_sub ? "yes" : "no", other && v.has_sub && !o.has_sub);
  row("Tags", v.tags.join(" "), false);
  row("Folder", v.folder, false);
  box.append(img, name, dl);
  return box;
}

function pairItem(item) {
  const li = document.createElement("li");
  li.className = "pair";
  const head = document.createElement("div");
  head.className = "pair-head";
  const h = document.createElement("strong");
  h.textContent = item.complete ? `${item.people.join(", ")} — ${item.title}` : item.title;
  const why = document.createElement("span");
  why.className = "hint";
  why.textContent = VERDICT[item.reason] || item.reason;
  head.append(h, why);

  const sides = document.createElement("div");
  sides.className = "pair-sides";
  sides.append(sideCard("Waiting in review", item, item.library_copy),
               sideCard("In the library", item.library_copy, item));

  const msg = document.createElement("span");
  msg.className = "form-msg";
  const decide = async (keep, extra = {}) => {
    try {
      await api("POST", "/api/library/decide", { path: item.path, keep, ...extra });
      loadReview().catch(showReviewError);
    } catch (err) { msg.textContent = err.message; msg.classList.add("error"); }
  };
  const actions = document.createElement("div");
  actions.className = "actions";
  const keepNew = action(item.library_copy ? "Keep this one, trash the library's" : "Put it in the library",
                         () => decide("new"), "small primary");
  const keepOld = action("Keep the library's, trash this one", () => decide("old"));
  const bothBtn = action("Keep both…", async () => { both.hidden = !both.hidden; });
  if (!item.library_copy) { keepOld.hidden = true; bothBtn.hidden = true; }
  actions.append(keepNew, keepOld, bothBtn, msg);

  const both = document.createElement("form");
  both.className = "both";
  both.hidden = true;
  const field = (label, value) => {
    const wrap = document.createElement("label");
    wrap.className = "field";
    const span = document.createElement("span");
    span.className = "label";
    span.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.value = value;
    wrap.append(span, input);
    both.append(wrap);
    return input;
  };
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = "Give this copy a different title (or people) so it's a video of its own.";
  both.append(hint);
  const fp = field("People", item.people.join(", "));
  const ft = field("Title", item.title);
  const fc = field("Collection", item.collection);
  const save = document.createElement("button");
  save.type = "submit";
  save.className = "primary";
  save.textContent = "Keep both";
  both.append(save);
  both.addEventListener("submit", (e) => {
    e.preventDefault();
    decide("both", { people: fp.value, title: ft.value, collection: fc.value });
  });

  li.append(head, sides, actions, both);
  return li;
}

async function loadReview() {
  const data = await api("GET", "/api/library/review");
  $("review-list").replaceChildren(...data.items.map(pairItem));
  $("review-empty").hidden = data.items.length > 0;
}
function showReviewError(err) { $("review-empty").hidden = false; $("review-empty").textContent = err.message; }

// ── routing ──

const PAGES = ["downloads", "library", "review"];

function route() {
  const asked = location.hash.slice(1);
  const page = PAGES.includes(asked) ? asked : "downloads";
  for (const p of PAGES) {
    $(`view-${p}`).hidden = p !== page;
    if (p === page) $(`tab-${p}`).setAttribute("aria-current", "page");
    else $(`tab-${p}`).removeAttribute("aria-current");
  }
  if (page === "library") loadLibrary().catch(showLibError);
  if (page === "review") loadReview().catch(showReviewError);
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
for (const act of ["pause", "stop", "resume"]) {
  $(`pipe-${act}`).addEventListener("click", () => api("POST", `/api/pipeline/${act}`, {}).catch(showError));
}
const fromFields = () => { form.mode = "fields"; nameFromFields().catch(showError); };
attachSuggest($("people"), $("people-suggest"), "people", fromFields);
attachSuggest($("collection"), $("collection-suggest"), "collections", fromFields);

// Library page
$("lib-q").addEventListener("input", reloadLibrary);
for (const id of ["lib-person", "lib-collection", "lib-resolution"]) {
  $(id).addEventListener("change", () => loadLibrary().catch(showLibError));
}
for (const b of document.querySelectorAll(".seg button")) {
  b.addEventListener("click", () => {
    lib.view = b.dataset.view;
    for (const other of document.querySelectorAll(".seg button")) {
      other.setAttribute("aria-pressed", String(other === b));
    }
    loadLibrary().catch(showLibError);
  });
}
$("lib-more").addEventListener("click", () => loadLibrary(true).catch(showLibError));
$("detail-close").addEventListener("click", () => $("detail").close());
$("detail-play").addEventListener("click", () => detailAction("open"));
$("detail-reveal").addEventListener("click", () => detailAction("reveal"));
$("detail-trash").addEventListener("click", () => {
  $("detail-confirm-text").textContent = `Move ${detail.video.filename} to the Trash?`;
  $("detail-confirm").hidden = false;
});
$("detail-confirm-no").addEventListener("click", () => { $("detail-confirm").hidden = true; });
$("detail-confirm-yes").addEventListener("click", async () => {
  try {
    await api("POST", "/api/library/trash", { path: detail.path });
    $("detail").close();
    loadLibrary().catch(showLibError);
  } catch (err) { detailMsg(err.message, true); }
});
const renameChanged = debounce(() => renamePreview().catch((e) => detailMsg(e.message, true)), 150);
for (const id of ["rn-people", "rn-title", "rn-collection"]) $(id).addEventListener("input", renameChanged);
attachSuggest($("rn-people"), $("rn-people-suggest"), "people", renameChanged);
attachSuggest($("rn-collection"), $("rn-collection-suggest"), "collections", renameChanged);
$("rename-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const moved = await api("POST", "/api/library/rename", {
      path: detail.path, people: $("rn-people").value, title: $("rn-title").value,
      collection: $("rn-collection").value,
    });
    await openDetail(moved.path);
    detailMsg(`Renamed — now in ${moved.folder}`);
    loadLibrary().catch(showLibError);
  } catch (err) { detailMsg(err.message, true); }
});
window.addEventListener("hashchange", route);
route();

api("GET", "/api/state").then(renderState).catch(showError);
nameFromFields().catch(showError);
connect();
