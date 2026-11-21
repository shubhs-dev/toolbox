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
  $("problems").replaceChildren(...problemItems(data));
  // A pill click edits the fields by hand, so the segments stop driving them.
  renderPills(data, ADD_FIELDS, () => { form.mode = "fields"; fieldsChanged(); });
  updateAddButton();
}

// "You've downloaded this link before" — a warning, not a block: re-downloading for a
// better copy is a fair thing to do.
const checkSeen = debounce(async () => {
  const url = $("url").value.trim();
  const note = $("url-seen");
  if (!url) { note.hidden = true; return; }
  const data = await api("GET", `/api/downloads/seen?${new URLSearchParams({ url })}`).catch(() => ({ seen: [] }));
  if ($("url").value.trim() !== url) return;
  const last = data.seen[0];
  note.hidden = !last;
  if (!last) return;
  const when = last.added_at ? new Date(last.added_at).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "before";
  const name = document.createElement("code");
  name.textContent = last.name || "the video's own title";
  const where = document.createElement("code");
  where.textContent = last.where;
  const parts = [`Already downloaded on ${when} as `, name];
  if (last.present && last.where) parts.push(" — now at ", where, ".");
  else parts.push(" — it's no longer where it was put, so it may have been removed.");
  if (data.seen.length > 1) parts.push(` (${data.seen.length} times in all.)`);
  note.replaceChildren(...parts);
}, 250);

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

const MERGE_STATE = {
  queued: "waiting", running: "merging", done: "merged", failed: "failed",
  unverified: "not confirmed", stopped: "stopped", discarded: "discarded",
};
let mergesDone = 0;

function renderMerges(merges) {
  $("pipe-merges").replaceChildren(...merges.map((m) => {
    const li = document.createElement("li");
    li.className = "row";
    const main = document.createElement("div");
    main.className = "row-main";
    const name = document.createElement("div");
    name.className = "row-name";
    name.textContent = `Merge ${m.paths.length} parts → ${m.name}`;
    const sub = document.createElement("div");
    sub.className = "row-sub dim";
    sub.textContent = m.detail;
    sub.title = m.detail;
    main.append(name, sub);
    const side = document.createElement("div");
    side.className = "row-side";
    const pill = document.createElement("span");
    pill.className = `pill ${m.state}`;
    pill.textContent = MERGE_STATE[m.state] || m.state;
    const acts = document.createElement("span");
    acts.className = "row-actions";
    if (m.state === "unverified") {
      acts.append(action("Discard the join", () => api("POST", `/api/library/merge/${m.id}/discard`, {})));
    }
    if (!["queued", "running"].includes(m.state)) {
      acts.append(action("Dismiss", () => api("POST", `/api/library/merge/${m.id}/forget`, {})));
    }
    side.append(pill, acts);
    li.append(main, side);
    return li;
  }));
  const done = merges.filter((m) => m.state === "done").length;
  if (done !== mergesDone) {
    mergesDone = done;
    if (!$("view-library").hidden) loadLibrary().catch(showLibError);
  }
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

  renderMerges(p.merges || []);

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

const lib = { view: "all", offset: 0, total: 0, loaded: false, seq: 0, lastGroup: null,
              selected: new Map() };   // id → video, picked to merge by hand

function renderSelection() {
  const n = lib.selected.size;
  $("select-bar").hidden = n === 0 || lib.view === "parts";
  $("select-count").textContent = `${n} selected`;
  $("select-merge").disabled = n < 2;
  $("lib-grid").classList.toggle("selecting", n > 0);
}

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

// A light hover preview: after a short pause on a card, step through a filmstrip of frames
// from across the video (one cached JPEG, made on first hover). Only where there's a real
// pointer, and without the stepping when reduced motion is asked for.
const STRIP_FRAMES = 12;   // as vidlib/library.py makes them
const canHover = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function attachPreview(card, wrap, v) {
  if (!canHover || !v.duration) return;
  let strip = null, dots = null, timer = null, delay = null, frame = 0, loaded = false;
  const show = (i) => {
    frame = i % STRIP_FRAMES;
    strip.style.backgroundPosition = `${(frame / (STRIP_FRAMES - 1)) * 100}% 0`;
    [...dots.children].forEach((d, j) => d.classList.toggle("at", j === frame));
  };
  const start = () => {
    if (!strip) {
      strip = document.createElement("span");
      strip.className = "strip";
      strip.style.backgroundSize = `${STRIP_FRAMES * 100}% 100%`;
      dots = document.createElement("span");
      dots.className = "strip-dots";
      dots.append(...Array.from({ length: STRIP_FRAMES }, () => document.createElement("i")));
      wrap.append(strip, dots);
      const probe = new Image();
      probe.onload = () => {
        loaded = true;
        strip.style.backgroundImage = `url("${probe.src}")`;
        if (card.matches(":hover")) begin();
      };
      probe.src = `/api/strip?path=${encodeURIComponent(v.path)}`;
      return;
    }
    if (loaded) begin();
  };
  const begin = () => {
    show(0);
    strip.classList.add("on");
    if (!reduceMotion) timer = setInterval(() => show(frame + 1), 400);
  };
  card.addEventListener("mouseenter", () => { delay = setTimeout(start, 350); });
  card.addEventListener("mouseleave", () => {
    clearTimeout(delay);
    clearInterval(timer);
    if (strip) strip.classList.remove("on");
  });
  // Moving across the thumbnail scrubs instead of waiting for the next step.
  wrap.addEventListener("mousemove", (e) => {
    if (!loaded || !strip.classList.contains("on")) return;
    clearInterval(timer);
    const r = wrap.getBoundingClientRect();
    show(Math.min(STRIP_FRAMES - 1, Math.floor(((e.clientX - r.left) / r.width) * STRIP_FRAMES)));
  });
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
  const wrap = document.createElement("span");
  wrap.className = "thumbwrap";
  wrap.append(img);
  if (v.id != null && lib.view !== "parts") {
    const pick = document.createElement("input");
    pick.type = "checkbox";
    pick.className = "pick";
    pick.checked = lib.selected.has(v.id);
    pick.setAttribute("aria-label", `Select ${v.filename} to merge`);
    pick.addEventListener("click", (e) => e.stopPropagation());
    pick.addEventListener("change", () => {
      if (pick.checked) lib.selected.set(v.id, v); else lib.selected.delete(v.id);
      b.classList.toggle("picked", pick.checked);
      renderSelection();
    });
    if (pick.checked) b.classList.add("picked");
    wrap.append(pick);
  }
  attachPreview(b, wrap, v);
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
  b.append(wrap, body);
  b.addEventListener("click", () => openDetail(v.path));
  li.append(b);
  return li;
}

async function loadLibrary(append = false) {
  const partsView = lib.view === "parts";
  $("lib-grid").hidden = partsView;
  $("lib-parts").hidden = !partsView;
  for (const id of ["lib-q", "lib-person", "lib-collection", "lib-resolution"]) $(id).disabled = partsView;
  renderSelection();
  if (partsView) { $("lib-more").hidden = true; return loadParts(); }
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
  $("lib-empty").textContent = "Nothing matches.";
  const all = data.library;
  $("lib-summary").textContent =
    `${data.total} shown of ${all.videos} videos · ${humanSize(all.size)} in the library`;
}

const reloadLibrary = debounce(() => loadLibrary().catch(showLibError), 200);
function showLibError(err) { $("lib-summary").textContent = err.message || String(err); }

// ── parts of one video ──

function partLabel(m) {
  const n = m.first === m.last ? `Part ${m.first}` : `Parts ${m.first}–${m.last}`;
  return m.total ? `${n} of ${m.total}` : n;
}

function partGroup(g) {
  const li = document.createElement("li");
  li.className = `part-group ${g.sure ? "sure" : "maybe"}`;
  const head = document.createElement("div");
  head.className = "part-head";
  const title = document.createElement("div");
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = g.name.title;
  const who = document.createElement("div");
  who.className = "who";
  const total = g.members.reduce((t, m) => t + (m.duration || 0), 0);
  who.textContent = [g.name.people, g.name.collection, `${g.members.length} parts`,
                     total ? duration(total) : ""].filter(Boolean).join(" · ");
  title.append(name, who);
  const side = document.createElement("span");
  side.className = "row-actions";
  const pill = document.createElement("span");
  pill.className = `pill ${g.sure ? "sure" : "maybe"}`;
  pill.textContent = g.sure ? "same title" : "similar titles";
  pill.title = g.sure ? "The titles match apart from the part numbers"
                      : "Most of the words match — check these really belong together";
  const mergeBtn = action("Merge…", async () => openMerge(g), "small primary");
  if (g.doubled.length) {
    mergeBtn.disabled = true;
    mergeBtn.title = "A part appears twice — sort out the duplicate first";
  }
  side.append(pill, mergeBtn, action("Not parts", async () => {
    await api("POST", "/api/library/parts/dismiss", { ids: g.ids });
    li.remove();
    if (!$("lib-parts").children.length) $("lib-empty").hidden = false;
  }));
  head.append(title, side);

  const notes = [];
  if (g.doubled.length) {
    notes.push([`Part ${g.doubled.join(", ")} appears more than once — sort out the duplicate first.`, "block"]);
  }
  if (g.gaps.length) notes.push([`Part ${g.gaps.join(", ")} is missing.`, ""]);
  const warnings = notes.map(([text, cls]) => {
    const p = document.createElement("p");
    p.className = `part-warn ${cls}`;
    p.textContent = text;
    return p;
  });

  const list = document.createElement("ul");
  list.className = "part-members";
  for (const m of g.members) {
    const item = card(m);
    const label = document.createElement("span");
    label.className = "part-label";
    label.textContent = partLabel(m);
    item.querySelector(".thumbwrap").append(label);
    list.append(item);
  }
  li.append(head, ...warnings, list);
  return li;
}

async function loadParts() {
  const seq = ++lib.seq;
  const data = await api("GET", "/api/library/parts");
  if (seq !== lib.seq) return;
  $("lib-parts").replaceChildren(...data.groups.map(partGroup));
  $("lib-empty").hidden = data.groups.length > 0;
  $("lib-empty").textContent = data.groups.length ? "" : "No videos look like parts of one.";
  const sure = data.groups.filter((g) => g.sure).length;
  $("lib-summary").textContent = data.groups.length
    ? `${data.groups.length} set(s) of parts — ${sure} with matching titles, ${data.groups.length - sure} with similar ones`
    : "";
}

// ── merging ──

const MERGE_FIELDS = {
  people: "mg-people", title: "mg-title", collection: "mg-collection",
  peoplePills: "mg-people-pills", titlePills: "mg-title-pills", collectionPills: "mg-collection-pills",
};
const merge = { items: [], seq: 0, previewSeq: 0, named: false, blocking: false, nameOk: false };

function mergeIds() {
  return merge.items.filter((it) => it.include).map((it) => it.video.id);
}

function mergePartRow(it, index) {
  const li = document.createElement("li");
  li.className = `merge-part${it.include ? "" : " off"}`;
  li.draggable = true;
  const grip = document.createElement("span");
  grip.className = "grip";
  grip.textContent = "⠿";
  grip.title = "Drag to reorder";
  const check = document.createElement("input");
  check.type = "checkbox";
  check.checked = it.include;
  check.setAttribute("aria-label", `Include ${it.video.filename}`);
  check.addEventListener("change", () => { it.include = check.checked; mergeChanged(); });
  const lead = document.createElement("span");
  lead.className = "move";
  lead.append(grip, check);
  const img = document.createElement("img");
  img.alt = "";
  img.loading = "lazy";
  img.src = thumbUrl(it.video.path);
  img.addEventListener("error", () => img.classList.add("none"), { once: true });
  const what = document.createElement("span");
  what.className = "what";
  const b = document.createElement("b");
  b.textContent = it.video.filename;
  b.title = it.video.path;
  const meta = document.createElement("span");
  meta.textContent = [it.video.first ? partLabel(it.video) : "", it.video.resolution,
                      it.video.duration ? duration(it.video.duration) : "",
                      it.video.folder === "." ? "library root" : it.video.folder].filter(Boolean).join(" · ");
  what.append(b, meta);
  const move = document.createElement("span");
  move.className = "move";
  const up = action("↑", async () => moveItem(index, index - 1));
  up.setAttribute("aria-label", `Move ${it.video.filename} up`);
  up.disabled = index === 0;
  const down = action("↓", async () => moveItem(index, index + 1));
  down.setAttribute("aria-label", `Move ${it.video.filename} down`);
  down.disabled = index === merge.items.length - 1;
  move.append(up, down);
  li.append(lead, img, what, move);
  li.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", String(index));
    li.classList.add("dragging");
  });
  li.addEventListener("dragend", () => li.classList.remove("dragging"));
  li.addEventListener("dragover", (e) => { e.preventDefault(); li.classList.add("drop-before"); });
  li.addEventListener("dragleave", () => li.classList.remove("drop-before"));
  li.addEventListener("drop", (e) => {
    e.preventDefault();
    li.classList.remove("drop-before");
    const from = Number(e.dataTransfer.getData("text/plain"));
    if (!Number.isNaN(from) && from !== index) moveItem(from, from < index ? index - 1 : index);
  });
  return li;
}

function moveItem(from, to) {
  if (to < 0 || to >= merge.items.length) return;
  const [it] = merge.items.splice(from, 1);
  merge.items.splice(to, 0, it);
  mergeChanged();
}

function renderMergeParts() {
  $("mg-parts").replaceChildren(...merge.items.map(mergePartRow));
}

function mergeMsg(text, isError = false) {
  $("mg-msg").textContent = text;
  $("mg-msg").classList.toggle("error", isError);
}

// The plan: what concatvid would have to do for the ticked parts, in this order. The
// first answer also fills the name fields; later ones leave your edits alone.
async function loadMergePlan() {
  const seq = ++merge.seq;
  const ids = mergeIds();
  if (ids.length < 2) {
    $("mg-notes").replaceChildren();
    merge.blocking = true;
    updateMergeButton();
    return;
  }
  const plan = await api("POST", "/api/library/merge/plan", { ids });
  if (seq !== merge.seq) return;
  if (!merge.named) {
    $("mg-people").value = plan.name.people;
    $("mg-title").value = plan.name.title;
    $("mg-collection").value = plan.name.collection;
    merge.named = true;
    await mergePreview();
  }
  merge.blocking = plan.blocking;
  $("mg-notes").replaceChildren(...plan.notes.map((n) => {
    const li = document.createElement("li");
    li.className = n.level;
    li.textContent = n.text;
    return li;
  }));
  updateMergeButton();
}

async function mergePreview() {
  const seq = ++merge.previewSeq;
  const data = await api("POST", "/api/name/fields", {
    people: $("mg-people").value, title: $("mg-title").value,
    collection: $("mg-collection").value, fill_unknown: true,
  });
  if (seq !== merge.previewSeq) return;
  $("mg-stem").textContent = data.stem;
  $("mg-problems").replaceChildren(...problemItems(data));
  renderPills(data, MERGE_FIELDS, mergeNameChanged);
  merge.nameOk = data.complete;
  updateMergeButton();
}

function updateMergeButton() {
  const gapsOk = $("mg-gap").hidden || $("mg-gap-ok").checked;
  $("mg-go").disabled = merge.blocking || !merge.nameOk || mergeIds().length < 2 || !gapsOk;
}

const mergeNameChanged = debounce(() => mergePreview().catch((e) => mergeMsg(e.message, true)), 150);

function mergeChanged() {
  renderMergeParts();
  loadMergePlan().catch((e) => mergeMsg(e.message, true));
}

async function openMerge(group) {
  merge.items = group.members.map((m) => ({ video: m, include: true }));
  merge.named = false;
  merge.nameOk = false;
  merge.blocking = false;
  mergeMsg("");
  $("mg-search").value = "";
  $("mg-results").replaceChildren();
  const gaps = group.gaps || [];
  $("mg-gap").hidden = gaps.length === 0;
  $("mg-gap-ok").checked = false;
  $("mg-gap-text").textContent = `Merge anyway — Part ${gaps.join(", ")} is missing`;
  renderMergeParts();
  $("merge").showModal();
  await loadMergePlan();
}

const searchToAdd = debounce(async () => {
  const q = $("mg-search").value.trim();
  if (!q) { $("mg-results").replaceChildren(); return; }
  const data = await api("GET", `/api/library?${new URLSearchParams({ q, view: "all" })}`);
  const have = new Set(merge.items.map((it) => it.video.path));
  $("mg-results").replaceChildren(...data.videos.filter((v) => !have.has(v.path)).slice(0, 8)
    .map((v) => {
      const li = document.createElement("li");
      li.append(action(`+ ${v.filename}`, async () => {
        merge.items.push({ video: v, include: true });
        li.remove();
        mergeChanged();
      }));
      return li;
    }));
}, 200);

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
  hideConfirm();
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
    path: detail.path, fill_unknown: true,
  });
  if (seq !== detail.seq) return;
  const tags = detail.video && detail.video.tags.length ? ` [${detail.video.tags.join(" ")}]` : "";
  $("rn-stem").textContent = data.stem ? `${data.stem}${tags}` : "";
  $("rn-problems").replaceChildren(...problemItems(data));
  renderPills(data, RENAME_FIELDS, renameChanged);
  const unchanged = detail.video && `${data.stem}${tags}` === detail.video.filename.replace(/\.[^.]+$/, "");
  $("rn-save").disabled = !data.complete || unchanged;
}

// Your own fill-ins for a field you can't name yet, beside what the title gives away.
const FILL_INS = { people: ["Unknown"], title: ["Unknown"], collection: ["Unknown", "Clips"] };

function pillButton(label, cls, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `pill-btn ${cls}`;
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

// Pills under People / Title / Collection: known names the title gives away (clicking one moves
// it out of the title into its field) and your fill-ins. *f* names the form's three inputs
// and pill rows; *changed* re-runs the form's preview.
function renderPills(data, f, changed) {
  const people = $(f.people), title = $(f.title), coll = $(f.collection);
  const rows = [$(f.peoplePills), $(f.titlePills), $(f.collectionPills)];
  // Each "title without X" was worked out from the title as it is now. Once a pill or the
  // keyboard changes it, every pill is stale: clear them until the next answer brings fresh
  // ones, and ignore a click on one that's still on screen.
  const basis = title.value;
  const set = (input, value) => {
    input.value = value;
    for (const row of rows) row.classList.add("stale");   // kept in place until the answer
    changed();
  };
  for (const row of rows) row.classList.remove("stale");
  const fresh = (fn) => () => { if (title.value === basis) fn(); };
  const found = data.found || [];
  const peoplePills = found.filter((x) => x.kind === "people").map((x) =>
    pillButton(x.name, "found-people", fresh(() => {
      const now = people.value.trim();
      people.value = now && now !== "Unknown" ? `${now}, ${x.name}` : x.name;
      set(title, x.title_without);
    })));
  const collPills = found.filter((x) => x.kind === "collection").map((x) =>
    pillButton(x.name, "found-collection", fresh(() => {
      coll.value = x.name;
      set(title, x.title_without);
    })));
  // Fill-ins stay where they are, shown as chosen, so the row never changes shape.
  const fillIns = (input, field) => FILL_INS[field].map((v) => {
    const chosen = input.value.trim().toLowerCase() === v.toLowerCase();
    const b = pillButton(v, "fill-in", () => { if (!chosen) set(input, v); });
    b.setAttribute("aria-pressed", String(chosen));
    return b;
  });
  $(f.peoplePills).replaceChildren(...peoplePills, ...fillIns(people, "people"));
  $(f.titlePills).replaceChildren(...fillIns(title, "title"));
  $(f.collectionPills).replaceChildren(...collPills, ...fillIns(coll, "collection"));
}

const RENAME_FIELDS = {
  people: "rn-people", title: "rn-title", collection: "rn-collection",
  peoplePills: "rn-people-pills", titlePills: "rn-title-pills", collectionPills: "rn-collection-pills",
};
const ADD_FIELDS = {
  people: "people", title: "title", collection: "collection",
  peoplePills: "people-pills", titlePills: "title-pills", collectionPills: "collection-pills",
};

// The draft's problems, plus a warning when the name is one the library already has: that
// makes them the same video to duplicate detection, which keeps only the better copy.
function problemItems(data) {
  const items = (data.stem ? data.problems : []).map((p) => {
    const li = document.createElement("li");
    li.textContent = p;
    return li;
  });
  if (data.same_as && data.same_as.length) {
    const li = document.createElement("li");
    li.className = "same";
    li.textContent = `Already in the library as ${data.same_as.join(", ")} — vidlib will treat ` +
      "them as the same video and keep only the better copy. If they're different videos, " +
      "change the title.";
    items.push(li);
  }
  return items;
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
$("url").addEventListener("input", () => { updateAddButton(); checkSeen(); });
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
$("merge-x").addEventListener("click", () => $("merge").close());
$("select-clear").addEventListener("click", () => {
  lib.selected.clear();
  for (const c of document.querySelectorAll(".card.picked")) c.classList.remove("picked");
  for (const c of document.querySelectorAll(".pick")) c.checked = false;
  renderSelection();
});
$("select-merge").addEventListener("click", () => {
  // In the order picked; the merge screen reorders.
  const members = [...lib.selected.values()];
  openMerge({ members, gaps: [] }).catch((e) => mergeMsg(e.message, true));
});
$("mg-cancel").addEventListener("click", () => $("merge").close());
$("merge").addEventListener("click", (e) => { if (e.target === $("merge")) $("merge").close(); });
$("mg-gap-ok").addEventListener("change", updateMergeButton);
$("mg-search").addEventListener("input", searchToAdd);
for (const id of ["mg-people", "mg-title", "mg-collection"]) $(id).addEventListener("input", mergeNameChanged);
attachSuggest($("mg-people"), $("mg-people-suggest"), "people", mergeNameChanged);
attachSuggest($("mg-collection"), $("mg-collection-suggest"), "collections", mergeNameChanged);
$("merge-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("POST", "/api/library/merge", {
      ids: mergeIds(), people: $("mg-people").value, title: $("mg-title").value,
      collection: $("mg-collection").value,
    });
    $("merge").close();
    lib.selected.clear();
    await loadLibrary().catch(showLibError);
    $("lib-summary").textContent =
      "Merge queued — it runs between encodes. Follow it in the Downloads tab's Encoding panel.";
  } catch (err) { mergeMsg(err.message, true); }
});
$("detail-play").addEventListener("click", () => detailAction("open"));
$("detail-reveal").addEventListener("click", () => detailAction("reveal"));
$("detail-trash").addEventListener("click", () => {
  $("detail-confirm-text").textContent = "Move this video to the Trash?";   // named in the title
  $("detail-file-actions").hidden = true;    // the confirmation takes their place in the bar
  $("detail-confirm").hidden = false;
  $("detail-confirm-yes").focus();
});
$("rn-close").addEventListener("click", () => $("detail").close());
// A click on the dimmed backdrop lands on the dialog element itself (its content is inside).
$("detail").addEventListener("click", (e) => { if (e.target === $("detail")) $("detail").close(); });
function hideConfirm() {
  $("detail-confirm").hidden = true;
  $("detail-file-actions").hidden = false;
}
$("detail-confirm-no").addEventListener("click", hideConfirm);
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
