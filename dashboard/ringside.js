/* Shared browser/native Ringside. All labels and previews come from local API data. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const num = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
  const storage = (key, value) => {
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch (_) {}
    return null;
  };
  const bridge = window.ringsideNative;
  const state = {
    runs: [],
    artifacts: [],
    models: null,
    runId: storage("ringside-run") || "",
    taskKey: "",
    artifactName: storage("ringside-output") || "",
    version: "live",
    view: "runs",
    compact: Boolean(bridge),
    search: "",
    outputSearch: "",
    taskType: "",
    runError: "",
    libraryError: "",
    modelError: "",
    updated: 0,
    logKey: "",
    logBusy: false,
    logGeneration: 0,
    frameKey: "",
    frameContent: "",
    frameGeneration: 0,
    refresh: storage("ringside-auto-refresh") !== "false",
    pending: new Set(),
    hudRunsAvailable: false,
    nativeRuns: null,
  };
  const duration = (seconds) => {
    const s = Math.max(0, Math.floor(num(seconds)));
    return s >= 3600
      ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
      : s >= 60
        ? `${Math.floor(s / 60)}m ${s % 60}s`
        : `${s}s`;
  };
  const age = (date) => {
    const t = typeof date === "number" ? date : Date.parse(date);
    return Number.isFinite(t)
      ? `${duration((Date.now() - t) / 1000)} ago`
      : "time not reported";
  };
  const keyOf = (task, i = 0) =>
    String(task.key || task.task_key || `task-${i + 1}`);
  function kind(task, run) {
    const s = String(task.status || task.state || "").toLowerCase();
    if (s === "retrying" || s === "redoing")
      return run?.state === "died" ? "fail" : "retry";
    if (["running", "working", "verifying"].includes(s))
      return run?.state === "died" ? "fail" : "working";
    if (["pass", "passed"].includes(s) || task.verdict === "PASS")
      return "pass";
    if (
      ["fail", "failed", "error", "timeout", "died"].includes(s) ||
      ["FAIL", "ERROR", "TIMEOUT"].includes(task.verdict)
    )
      return "fail";
    return "waiting";
  }
  const label = (task, run) =>
    run?.state === "died" &&
    ["running", "working", "verifying", "retrying", "redoing"].includes(
      task.status,
    )
      ? "Stopped"
      : {
          pass: "Passed",
          retry: "Retrying",
          working: task.status === "verifying" ? "Checking" : "Working",
          fail: "Failed",
          waiting: "Waiting",
        }[kind(task, run)];
  const attention = (run) =>
    Math.max(
      run.state === "died" ? 1 : 0,
      run.tasks.filter((t) => ["fail", "retry"].includes(kind(t, run))).length,
    );
  const passed = (run) =>
    run.tasks.filter((t) => kind(t, run) === "pass").length;
  const progress = (a, b) =>
    `<div class="progress-track" role="progressbar" aria-label="Passed checks" aria-valuemin="0" aria-valuemax="${Math.max(1, b)}" aria-valuenow="${a}"><span class="progress-fill" style="width:${Math.max(0, Math.min(100, b ? (a / b) * 100 : 0))}%"></span></div>`;
  const badge = (text, cls = "") =>
    `<span class="badge ${cls}">${esc(text)}</span>`;
  const dot = (cls) =>
    `<img class="dot" src="/assets/ringside-${cls === "retry" ? "attention" : cls === "working" ? "live" : "mark"}.svg" alt="">`;
  const runStatus = (run) =>
    run.state === "died"
      ? "Stopped"
      : run.state === "live"
        ? "In progress"
        : attention(run)
          ? "Needs attention"
          : "Completed";
  function replace(el, html) {
    if (el.innerHTML === html) return;
    const focused = el.contains(document.activeElement)
      ? document.activeElement?.dataset.focus
      : null;
    const openDetails = [...el.querySelectorAll("details[open]")].map(
      (d) => d.dataset.detail,
    );
    el.innerHTML = html;
    for (const d of el.querySelectorAll("details"))
      if (openDetails.includes(d.dataset.detail)) d.open = true;
    if (focused)
      [...el.querySelectorAll("[data-focus]")]
        .find((n) => n.dataset.focus === focused)
        ?.focus({ preventScroll: true });
  }
  function normalizeRuns(payload) {
    return (Array.isArray(payload) ? payload : payload?.runs || [])
      .filter((r) => r && typeof r === "object")
      .map((r, i) => ({
        ...r,
        run_id: String(r.run_id || r.id || `run-${i}`),
        run_name: String(r.run_name || "Unnamed run"),
        state: r.state || (r.finished ? "finished" : "live"),
        tasks: Array.isArray(r.tasks) ? r.tasks : [],
      }))
      .sort(
        (a, b) =>
          (a.state === "live" ? 0 : 1) - (b.state === "live" ? 0 : 1) ||
          (num(b.mtime) * 1000 || Date.parse(b.started_at) || 0) -
            (num(a.mtime) * 1000 || Date.parse(a.started_at) || 0),
      );
  }
  const matches = (run, search) =>
    !search ||
    `${run.run_name} ${run.identity || ""} ${run.tasks.map((t) => `${keyOf(t)} ${t.model || ""} ${t.spec_short || ""}`).join(" ")}`
      .toLowerCase()
      .includes(search);
  const visibleRuns = () => state.runs.filter((r) => matches(r, state.search));
  const selectedRun = () =>
    visibleRuns().find((r) => r.run_id === state.runId) || visibleRuns()[0];
  const visibleTasks = (run) =>
    !state.search || run.run_name.toLowerCase().includes(state.search)
      ? run.tasks
      : run.tasks.filter((t, i) =>
          `${keyOf(t, i)} ${t.model || ""} ${t.spec_short || ""}`
            .toLowerCase()
            .includes(state.search),
        );
  const selectedTask = (run) =>
    run &&
    (visibleTasks(run).find(
      (t, i) => keyOf(t, run.tasks.indexOf(t)) === state.taskKey,
    ) ||
      visibleTasks(run).find((t) => ["retry", "fail"].includes(kind(t, run))) ||
      visibleTasks(run)[0]);
  const runStats = (run) =>
    `${passed(run)} / ${run.tasks.length} passed${run.state === "died" ? " · stopped" : ` · ${run.tasks.filter((t) => ["working", "retry"].includes(kind(t, run))).length} working`}`;
  function runButton(run, compact = false) {
    const cls = attention(run)
      ? "retry"
      : run.state === "live"
        ? "working"
        : "pass";
    return `<button class="${compact ? "compact-run" : "run-choice"}" data-run="${esc(run.run_id)}" data-focus="run:${esc(run.run_id)}" aria-pressed="${selectedRun()?.run_id === run.run_id}">${dot(cls)}${esc(run.run_name)}<span class="meta">${esc(runStats(run))}</span>${compact ? "" : progress(passed(run), run.tasks.length)}</button>`;
  }
  function renderConnection() {
    const connected = !state.runError && state.updated;
    const text = connected
      ? "Connected · local machine"
      : state.runError
        ? "Reconnecting · showing last results"
        : "Connecting to local machine…";
    replace(
      $("connection"),
      `${connected ? '<img class="connection-mark" src="/assets/ringside-mark.svg" alt="">' : ""}${esc(text)}`,
    );
    $("compact-connection").textContent = state.refresh
      ? `${state.updated ? `Last update ${age(state.updated)} · ` : ""}${text.toLowerCase()}`
      : "Auto-refresh paused · showing last results";
    $("runs-error").hidden = !state.runError;
    $("runs-error").textContent = state.runError
      ? `Runs unavailable: ${state.runError}. Showing the last received results.`
      : "";
  }
  function renderRuns() {
    renderConnection();
    const live = state.runs.filter((r) => r.state === "live");
    const needs = state.runs.reduce((sum, r) => sum + attention(r), 0);
    replace(
      $("run-summary"),
      badge(`${live.length} running`, "working") +
        badge(`${needs} ${needs === 1 ? "needs" : "need"} attention`, "retry") +
        badge(
          `${state.runs.filter((r) => r.state === "finished" && !attention(r)).length} completed`,
          "pass",
        ),
    );
    for (const [id, runs] of [
      ["running-now", live],
      ["recent-runs", state.runs.filter((r) => r.state !== "live")],
    ]) {
      replace(
        $(id),
        runs
          .map(
            (r) =>
              `<button data-run="${esc(r.run_id)}" data-focus="${id}:${esc(r.run_id)}" aria-pressed="${selectedRun()?.run_id === r.run_id}">${dot(attention(r) ? "retry" : r.state === "live" ? "working" : "pass")}${esc(r.run_name)}</button>`,
          )
          .join("") || '<p class="meta empty-note">None yet</p>',
      );
    }
    replace(
      $("run-picker"),
      visibleRuns()
        .map((r) => runButton(r))
        .join("") ||
        `<p class="empty">${state.search ? "No runs match your search." : "No runs yet. Start a Ringer run to see its progress here."}</p>`,
    );
    const run = selectedRun();
    $("run-name").textContent = run?.run_name || "";
    $("run-meta").textContent = run
      ? `${run.tasks.length} tasks · ${run.identity || "identity not reported"} · ${runStatus(run)} · started ${age(run.started_at)}`
      : "";
    $("updated").textContent = state.updated
      ? `Updated ${age(state.updated)}`
      : "";
    if (!run) {
      replace(
        $("task-table"),
        '<p class="empty">Select a run to inspect its tasks.</p>',
      );
      replace($("task-inspector"), '<p class="meta">No task selected.</p>');
    } else {
      const chosen = selectedTask(run);
      replace(
        $("task-table"),
        `<table class="task-table"><thead><tr><th>Task</th><th>Model / harness</th><th>Status</th><th>Time</th></tr></thead><tbody>${visibleTasks(
          run,
        )
          .map((t) => {
            const i = run.tasks.indexOf(t);
            return `<tr class="${chosen === t ? "selected" : ""}"><td><button class="task-select" data-task="${esc(keyOf(t, i))}" data-focus="task:${esc(keyOf(t, i))}" aria-pressed="${chosen === t}">${esc(keyOf(t, i))}</button></td><td class="meta">${esc(t.model || "Model not reported")} · ${esc(t.engine || "Unknown harness")}</td><td>${badge(label(t, run), kind(t, run))}</td><td class="meta">${kind(t, run) === "waiting" ? "—" : esc(duration(t.elapsed_s))}</td></tr>`;
          })
          .join(
            "",
          )}</tbody></table>${!visibleTasks(run).length ? '<p class="empty">No tasks match this search.</p>' : ""}`,
      );
      renderInspector(run, chosen);
    }
    $("compact-title").textContent =
      `${live.length} ${live.length === 1 ? "run" : "runs"} in progress`;
    $("compact-attention").textContent = needs
      ? `${needs} ${needs === 1 ? "task needs" : "tasks need"} attention`
      : "No tasks need attention";
    const compactRuns = [
      ...live,
      ...state.runs.filter((r) => r.state !== "live" && attention(r)),
    ];
    replace(
      $("compact-runs"),
      (compactRuns.length ? compactRuns : state.runs.slice(0, 3))
        .map((r) => runButton(r, true))
        .join("") || '<p class="empty">Waiting for your next run.</p>',
    );
    if (state.view === "logs") renderLogCheck();
  }
  function checkHTML(task) {
    const failed =
      (task.check_returncode !== null &&
        task.check_returncode !== undefined &&
        num(task.check_returncode) !== 0) ||
      task.check_timed_out;
    const reported =
      (task.check_returncode !== null && task.check_returncode !== undefined) ||
      task.check_timed_out;
    return `<p class="check-label ${failed ? "retry" : "meta"}">${failed ? "Last check failed" : reported ? "Last check passed" : "Check has not reported yet"}</p><pre class="check-output">${esc(task.check_output_tail || task.setup_error || (reported ? `Exit code: ${task.check_returncode}${task.check_timed_out ? " · timed out" : ""}` : "No check output yet."))}</pre>`;
  }
  function renderInspector(run, task) {
    if (!task) {
      replace(
        $("task-inspector"),
        '<p class="empty">No tasks have reported yet.</p>',
      );
      return;
    }
    const k = kind(task, run),
      attempts = num(task.attempts);
    const result = state.artifacts.find(
      (a) => a.current_run_id === run.run_id || a.name === run.run_name,
    );
    replace(
      $("task-inspector"),
      `<p class="section-label">Selected task</p><h3>${esc(keyOf(task, run.tasks.indexOf(task)))}</h3>${badge(`${label(task, run)}${attempts ? ` · attempt ${attempts}${task.max_attempts ? ` of ${task.max_attempts}` : ""}` : ""}`, k)}${checkHTML(task)}${k === "retry" ? '<p class="retry">This task is retrying. Its failed check stays visible.</p>' : ""}<p class="meta">${esc(task.engine || "Unknown harness")} · ${esc(task.model || "Model not reported")} · ${esc(duration(task.elapsed_s))}</p>${task.activity && ["working", "retry"].includes(k) ? `<p class="meta">${esc(task.activity)}</p>` : ""}<div class="inspector-actions"><button data-action="logs" data-focus="logs">View logs</button><button data-action="result" data-focus="result" ${result ? "" : "disabled"}>Open result</button></div><details data-detail="${esc(run.run_id)}:${esc(keyOf(task))}"><summary>Task brief &amp; check</summary><p>${esc(task.spec || task.spec_short || "No brief reported.")}</p><pre class="check-output">${esc(task.check || "No check reported.")}</pre>${task.verified ? `<p class="meta">${esc(task.verified)}</p>` : ""}</details>`,
    );
  }
  function showView(view) {
    if (!["runs", "outputs", "models", "logs"].includes(view)) view = "runs";
    state.view = view;
    for (const [name, id] of [
      ["runs", "runs-panel"],
      ["outputs", "artifacts-panel"],
      ["models", "models-panel"],
      ["logs", "logs-panel"],
    ])
      $(id).hidden = name !== view;
    document.querySelectorAll("[data-view]").forEach((b) => {
      if (b.dataset.view === view) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    $("breadcrumb").textContent =
      `Workspace / ${{ runs: "Live runs", outputs: "Outputs", models: "Models", logs: "Worker log" }[view]}`;
    if (view === "models") fetchModels();
    if (view !== "logs") {
      state.logKey = "";
      state.logGeneration++;
    }
  }
  function chooseRun(id) {
    state.runId = id;
    state.taskKey = "";
    state.search = "";
    $("search").value = "";
    storage("ringside-run", id);
    setCompact(false);
    showView("runs");
    renderRuns();
  }
  async function setCompact(value) {
    state.compact = value;
    document.querySelector(".shell").classList.toggle("compact", value);
    $("compact-panel").hidden = !value;
    if (bridge)
      try {
        await bridge.resize(value ? 400 : 1440, value ? 598 : 960);
      } catch (_) {}
  }
  function normalizeLibrary(payload) {
    const entries =
      payload?.artifacts &&
      typeof payload.artifacts === "object" &&
      !Array.isArray(payload.artifacts)
        ? payload.artifacts
        : {};
    return Object.entries(entries)
      .filter(([, v]) => v && typeof v === "object")
      .map(([name, a]) => ({
        ...a,
        name,
        versions: (Array.isArray(a.versions) ? a.versions : [])
          .map((v, i) => ({ ...v, key: String(v.path || v.run_id || i) }))
          .sort(
            (a, b) =>
              (Date.parse(b.finished_at) || 0) -
              (Date.parse(a.finished_at) || 0),
          ),
      }))
      .sort(
        (a, b) =>
          (a.state === "live" ? 0 : 1) - (b.state === "live" ? 0 : 1) ||
          a.name.localeCompare(b.name),
      );
  }
  const artifact = () =>
    state.artifacts.find((a) => a.name === state.artifactName);
  const sanitize = (name) =>
    String(name)
      .replace(/[^A-Za-z0-9._-]+/g, "-")
      .replace(/^[.-]+|[.-]+$/g, "") || "artifact";
  function artifactHref(a, version) {
    const path = String(version?.path || a.live_path || "").replace(/\\/g, "/");
    const index = path.lastIndexOf("/artifacts/");
    const relative =
      index >= 0 ? path.slice(index + 11) : path.replace(/^\/?artifacts\//, "");
    const pieces = relative.split("/").filter(Boolean);
    if (pieces.includes("..") || pieces.includes(".") || path.includes("\0"))
      return "";
    if (version)
      return pieces.length
        ? "/artifacts/" + pieces.map(encodeURIComponent).join("/")
        : "";
    return `/artifacts/live/${encodeURIComponent(sanitize(a.name))}.html`;
  }
  function renderOutputs() {
    if (!artifact()) {
      state.artifactName = state.artifacts[0]?.name || "";
      state.version = "live";
    }
    const a = artifact();
    if (
      state.version !== "live" &&
      !a?.versions.some((v) => v.key === state.version)
    )
      state.version = "live";
    const items = state.artifacts.filter((a) =>
      a.name.toLowerCase().includes(state.outputSearch),
    );
    replace(
      $("output-items"),
      items
        .map(
          (a) =>
            `<button class="output-item" data-output="${esc(a.name)}" data-focus="output:${esc(a.name)}" aria-pressed="${a.name === state.artifactName}">${esc(a.name)}<span class="meta">${esc(a.identity || "Local output")} · ${esc(a.state || "unknown")} · ${esc(age(a.updated_at))}</span></button>`,
        )
        .join("") || '<p class="empty">No outputs match.</p>',
    );
    $("output-count").textContent =
      `Showing ${items.length} of ${state.artifacts.length} outputs`;
    $("artifact-name").textContent = a?.name || "No output selected";
    $("artifact-status").textContent = a
      ? a.state === "live"
        ? "Live"
        : a.state || "Unknown"
      : "";
    $("artifact-status").className =
      `badge ${a?.state === "live" ? "working" : a?.state === "pass" ? "pass" : a?.state === "fail" || a?.state === "died" ? "fail" : ""}`;
    $("artifact-status").hidden = !a;
    $("artifact-version").disabled = !a;
    replace(
      $("artifact-version"),
      a
        ? `<option value="live">Version: ${a.state === "live" ? "now" : "latest"}</option>${a.versions.map((v) => `<option value="${esc(v.key)}">${esc(v.finished_at ? new Date(v.finished_at).toLocaleString() : v.run_id || "Saved version")} · ${esc(v.outcome || "unknown")}</option>`).join("")}`
        : "",
    );
    $("artifact-version").value = state.version;
    $("open-folder").disabled = !a;
    $("preview-note").textContent = a
      ? state.version === "live" && a.state === "live"
        ? "Live preview · automatic refresh"
        : "Saved output · fixed version"
      : "";
    $("library-error").hidden = !state.libraryError;
    $("library-error").textContent = state.libraryError
      ? `Outputs unavailable: ${state.libraryError}. Showing the last received outputs.`
      : "";
    loadPreview();
  }
  async function loadPreview() {
    const a = artifact();
    if (!a) {
      state.frameGeneration++;
      state.frameKey = "";
      state.frameContent = "";
      replace(
        $("frame-wrap"),
        '<p class="empty">No outputs yet. Results will appear here when a run publishes an artifact.</p>',
      );
      return;
    }
    const v =
      state.version === "live"
        ? null
        : a.versions.find((v) => v.key === state.version);
    const href = artifactHref(a, v),
      key = `${a.name}|${state.version}|${href}`;
    if (key === state.frameKey && (v || a.state !== "live")) return;
    const generation = ++state.frameGeneration;
    try {
      const raw = bridge
        ? await bridge.readArtifact(v?.path || a.live_path)
        : await request(href, "text");
      if (generation !== state.frameGeneration) return;
      // Preserve the existing local-artifact viewer: resolve deliverable links,
      // suppress standalone meta-refresh, and update live content in place.
      const baseHref = bridge
        ? bridge.artifactURL(v?.path || a.live_path)
        : new URL(href, window.location.href).href;
      const base = `<base href="${esc(baseHref)}" target="_blank">`;
      let html = raw.replace(
        /<meta[^>]+http-equiv=["']?refresh["']?[^>]*>/gi,
        "",
      );
      html = /<head\b/i.test(html)
        ? html.replace(/<head([^>]*)>/i, `<head$1>${base}`)
        : `${base}${html}`;
      if (key === state.frameKey && html === state.frameContent) return;
      const current = $("frame-wrap").querySelector("iframe");
      if (key === state.frameKey && current?.contentDocument?.documentElement) {
        try {
          morphPreview(
            current.contentDocument.documentElement,
            new DOMParser().parseFromString(html, "text/html").documentElement,
          );
          state.frameContent = html;
          return;
        } catch (_) {
          /* recover with a fresh frame */
        }
      }
      state.frameKey = key;
      state.frameContent = html;
      const iframe = document.createElement("iframe");
      iframe.title = `${a.name} output preview`;
      iframe.srcdoc = html;
      $("frame-wrap").replaceChildren(iframe);
    } catch (error) {
      if (generation !== state.frameGeneration) return;
      state.frameKey = "";
      replace(
        $("frame-wrap"),
        `<p class="empty">Could not load this output: ${esc(error.message)}. It will retry on the next refresh.</p>`,
      );
    }
  }
  // Same-origin local output documents have always been executable previews.
  // Retain their DOM nodes so scripts, scroll position and disclosures survive polling.
  function morphPreview(have, want) {
    if (have.nodeType === 1) {
      for (const name of have.getAttributeNames())
        if (
          !(name === "open" && have.tagName === "DETAILS") &&
          !want.hasAttribute(name)
        )
          have.removeAttribute(name);
      for (const name of want.getAttributeNames())
        if (
          !(name === "open" && have.tagName === "DETAILS") &&
          have.getAttribute(name) !== want.getAttribute(name)
        )
          have.setAttribute(name, want.getAttribute(name));
    }
    const old = [...have.childNodes],
      keyed = new Map(
        old
          .filter((n) => n.nodeType === 1 && n.getAttribute("data-key"))
          .map((n) => [n.getAttribute("data-key"), n]),
      );
    let position = 0;
    const desired = [...want.childNodes].map((w) => {
      const key = w.nodeType === 1 ? w.getAttribute("data-key") : null;
      let h = key ? keyed.get(key) : null;
      if (!key)
        while (position < old.length) {
          const candidate = old[position++];
          if (candidate.nodeType === 1 && candidate.getAttribute("data-key"))
            continue;
          h = candidate;
          break;
        }
      if (
        !h ||
        h.nodeType !== w.nodeType ||
        (h.nodeType === 1 && h.tagName !== w.tagName)
      )
        return w;
      if (h.nodeType === 3 || h.nodeType === 8) {
        if (h.nodeValue !== w.nodeValue) h.nodeValue = w.nodeValue;
      } else if (h.nodeType === 1) morphPreview(h, w);
      return h;
    });
    desired.forEach((node, i) => {
      if (have.childNodes[i] !== node)
        have.insertBefore(node, have.childNodes[i] || null);
    });
    while (have.childNodes.length > desired.length) have.lastChild.remove();
  }
  function renderModels() {
    const rows = state.taskType
      ? (state.models?.groups || []).filter(
          (r) => r.task_type === state.taskType,
        )
      : state.models?.rollup || [];
    // Keep the API's evidence-based routing order and quarantine misrouted data.
    const sorted = state.taskType
      ? [...rows].sort(
          (a, b) =>
            num(Boolean(a.misrouted || a.unattributed)) -
              num(Boolean(b.misrouted || b.unattributed)) ||
            num(b.first_try_pass_rate) - num(a.first_try_pass_rate) ||
            num(b.tasks) - num(a.tasks),
        )
      : rows;
    $("models-status").textContent = state.modelError
      ? `Models unavailable: ${state.modelError}`
      : state.models
        ? `Updated ${age(state.models.generated_at)}`
        : "Loading models…";
    $("models-status").classList.toggle("error", Boolean(state.modelError));
    const types = [
      ...new Set((state.models?.groups || []).map((r) => r.task_type)),
    ].sort();
    replace(
      $("task-type"),
      '<option value="">All task types</option>' +
        types
          .map((t) => `<option value="${esc(t)}">Task type: ${esc(t)}</option>`)
          .join(""),
    );
    $("task-type").value = state.taskType;
    const modelName = (r) =>
      `${esc(r.model_display || r.model || "Unknown")}${num(r.tasks) < 5 ? '<span class="meta retry">Low sample</span>' : ""}${r.misrouted || r.unattributed ? '<span class="meta retry">Not ranked · quarantined data</span>' : r.unregistered ? '<span class="meta retry">Unregistered</span>' : ""}`;
    const speed = (r) =>
      r.median_duration_ms == null
        ? "—"
        : esc(duration(r.median_duration_ms / 1000));
    replace(
      $("model-rows"),
      sorted
        .map(
          (r) =>
            `<tr class="${num(r.tasks) < 5 ? "low-sample" : ""}"><td>${modelName(r)}</td><td>${esc(r.harness || r.engine || "Unknown")}</td><td><span class="rate">${Math.round(num(r.first_try_pass_rate) * 100)}%</span>${progress(num(r.first_try_pass_rate) * 100, 100)}</td><td>${num(r.tasks)}</td><td>${num(r.attempts)}</td><td>${speed(r)}</td></tr>`,
        )
        .join("") ||
        `<tr><td colspan="6">${state.modelError ? "No model results are available." : "No executed checks recorded for this task type yet."}</td></tr>`,
    );
    replace(
      $("model-signal-rows"),
      sorted
        .map(
          (r) =>
            `<tr><td>${modelName(r)}</td><td>${esc(r.lab || "Unknown")}</td><td>${esc(r.harness || r.engine || "Unknown")}</td><td>${esc(r.access || "Unknown")}</td><td>${r.misrouted || r.unattributed ? "Not ranked" : esc(r.tier || "Unranked")}</td><td>${num(r.tasks)}</td><td>${Math.round(num(r.first_try_pass_rate) * 100)}%</td><td>${Math.round(num(r.pass_rate) * 100)}%</td><td>${r.median_tokens == null ? "—" : num(r.median_tokens).toLocaleString()}</td><td>${speed(r)}</td><td>${esc(age(r.last_seen))}</td><td class="judgment-notes">${esc(r.latest_note || (r.notes || []).join("\n"))}</td></tr>`,
        )
        .join(""),
    );
  }
  async function request(path, type = "json") {
    if (!path || !path.startsWith("/"))
      throw new Error("Invalid local resource");
    const base = bridge ? await bridge.serverURL : "";
    const response = await fetch(base + path, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return type === "text" ? response.text() : response.json();
  }
  async function poll(name, operation) {
    if (state.pending.has(name)) return;
    state.pending.add(name);
    try {
      await operation();
    } finally {
      state.pending.delete(name);
    }
  }
  function applyRuns(payload) {
    state.runs = normalizeRuns(payload);
    state.runError = "";
    state.updated = Date.now();
    renderRuns();
  }
  async function fetchRuns() {
    return poll("runs", async () => {
      try {
        const payload = await request("/api/runs");
        state.hudRunsAvailable = true;
        applyRuns(payload);
        const update = payload.update;
        const k = JSON.stringify(update);
        $("update-banner").hidden = !(
          num(update?.behind) > 0 &&
          update?.reason &&
          storage("ringside-dismissed-update") !== k
        );
        $("update-message").textContent = update
          ? `Ringer update available: ${num(update.behind)} commits behind · ${update.reason}`
          : "";
        $("dismiss-update").onclick = () => {
          storage("ringside-dismissed-update", k);
          $("update-banner").hidden = true;
        };
      } catch (error) {
        state.hudRunsAvailable = false;
        if (bridge && state.nativeRuns !== null) {
          applyRuns(state.nativeRuns);
        } else if (!bridge) {
          state.runError = error.message;
          renderRuns();
        }
      }
    });
  }
  async function fetchLibrary() {
    return poll("library", async () => {
      try {
        const payload = bridge
          ? JSON.parse(await bridge.readLibrary())
          : await request("/api/library");
        state.artifacts = normalizeLibrary(payload);
        state.libraryError = "";
      } catch (error) {
        state.libraryError = error.message;
      }
      renderOutputs();
      renderRuns();
    });
  }
  async function fetchModels() {
    return poll("models", async () => {
      try {
        const payload = await request("/api/models");
        if (payload.error) throw new Error(payload.error);
        state.models = payload;
        state.modelError = "";
      } catch (error) {
        state.modelError = error.message;
      }
      renderModels();
    });
  }
  function renderLogCheck() {
    const r = state.runs.find((r) => r.run_id === state.runId),
      t = r?.tasks.find((t, i) => keyOf(t, i) === state.taskKey);
    if (!t) return;
    $("logs-heading").textContent = keyOf(t, r.tasks.indexOf(t));
    $("log-meta").textContent =
      `${r.run_name} / attempt ${num(t.attempts)} / ${label(t, r)}`;
    replace(
      $("log-check"),
      checkHTML(t) +
        (kind(t, r) === "retry"
          ? '<p class="retry">This task is retrying. Its failed check stays visible.</p>'
          : ""),
    );
  }
  async function fetchLog() {
    if (state.view !== "logs" || state.logBusy) return;
    const key = state.logKey,
      generation = state.logGeneration;
    const r = state.runs.find((r) => r.run_id === state.runId),
      t = r?.tasks.find((t, i) => keyOf(t, i) === state.taskKey);
    if (!t) return;
    state.logBusy = true;
    try {
      const raw = bridge
        ? await bridge.readLog(t.log_path || `${t.taskdir}/worker.log`)
        : await request(
            `/logs/${encodeURIComponent(r.run_id)}/${encodeURIComponent(state.taskKey)}`,
            "text",
          );
      if (key !== state.logKey || generation !== state.logGeneration) return;
      const el = $("worker-log"),
        atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
      const text =
        raw.replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, "") || "No log output yet.";
      if (el.textContent !== text) el.textContent = text;
      if (atBottom) el.scrollTop = el.scrollHeight;
      $("log-error").hidden = true;
    } catch (error) {
      if (key === state.logKey && generation === state.logGeneration) {
        $("log-error").hidden = false;
        $("log-error").textContent =
          `Could not read this worker log: ${error.message}`;
      }
    } finally {
      state.logBusy = false;
    }
  }
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.view) showView(button.dataset.view);
    if (button.dataset.run) chooseRun(button.dataset.run);
    if (button.dataset.task) {
      state.taskKey = button.dataset.task;
      renderRuns();
    }
    if (button.dataset.output) {
      state.artifactName = button.dataset.output;
      state.version = "live";
      storage("ringside-output", state.artifactName);
      renderOutputs();
    }
    if (button.dataset.action === "logs") {
      const r = selectedRun(),
        t = selectedTask(r);
      if (!t) return;
      state.runId = r.run_id;
      state.taskKey = keyOf(t, r.tasks.indexOf(t));
      state.logKey = `${r.run_id}/${state.taskKey}`;
      state.logGeneration++;
      $("worker-log").textContent = "Loading log…";
      $("log-error").hidden = true;
      showView("logs");
      renderLogCheck();
      fetchLog();
      $("back-to-run").focus();
    }
    if (button.dataset.action === "result") {
      const r = selectedRun();
      const a = state.artifacts.find(
        (a) => a.current_run_id === r?.run_id || a.name === r?.run_name,
      );
      if (a) {
        state.artifactName = a.name;
        state.version = "live";
        showView("outputs");
        renderOutputs();
      }
    }
  });
  $("search").addEventListener("input", (e) => {
    state.search = e.target.value.trim().toLowerCase();
    renderRuns();
  });
  $("output-search").addEventListener("input", (e) => {
    state.outputSearch = e.target.value.trim().toLowerCase();
    renderOutputs();
  });
  $("task-type").addEventListener("change", (e) => {
    state.taskType = e.target.value;
    renderModels();
  });
  $("artifact-version").addEventListener("change", (e) => {
    state.version = e.target.value;
    renderOutputs();
  });
  $("refresh-models").onclick = fetchModels;
  $("compact-button").onclick = () => setCompact(true);
  $("expand-button").onclick = () => setCompact(false);
  $("open-live-runs").onclick = () => {
    setCompact(false);
    showView("runs");
  };
  $("back-to-run").onclick = () => {
    showView("runs");
    renderRuns();
    document.querySelector('.task-select[aria-pressed="true"]')?.focus();
  };
  $("settings-button").onclick = () => $("settings").showModal();
  $("auto-refresh").checked = state.refresh;
  $("auto-refresh").onchange = (e) => {
    state.refresh = e.target.checked;
    storage("ringside-auto-refresh", String(state.refresh));
    if (state.refresh) {
      fetchRuns();
      fetchLibrary();
      if (state.view === "models") fetchModels();
    }
    renderConnection();
  };
  $("open-folder").onclick = async () => {
    const a = artifact();
    if (!a) return;
    const v = a.versions.find((v) => v.key === state.version);
    try {
      const base = bridge ? await bridge.serverURL : "";
      const response = await fetch(
        `${base}/api/open-folder?artifact=${encodeURIComponent(a.name)}&run=${encodeURIComponent(v?.run_id || a.current_run_id || "")}`,
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      $("library-error").hidden = true;
    } catch (error) {
      $("library-error").hidden = false;
      $("library-error").textContent =
        `Could not open the output folder: ${error.message}`;
    }
  };
  document.querySelector(".brand").onclick = (e) => {
    e.preventDefault();
    showView("runs");
  };
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || $("settings").open) return;
    if (state.compact) setCompact(false);
    if (state.view === "logs") $("back-to-run").click();
  });
  if (bridge) {
    $("hud-hide").hidden = false;
    $("hud-hide").onclick = bridge.hide;
    bridge.onRuns((payload) => {
      state.nativeRuns = payload;
      // The HUD keeps longer history than the tray poller. Prefer its full
      // snapshot while reachable; use native events only as an offline fallback.
      if (!state.hudRunsAvailable && (state.refresh || !state.updated))
        applyRuns(payload);
    });
  }
  setCompact(state.compact);
  renderRuns();
  renderOutputs();
  renderModels();
  showView("runs");
  fetchRuns();
  fetchLibrary();
  setInterval(() => {
    $("clock").textContent = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    if (state.refresh) {
      fetchRuns();
      if (state.view === "logs") fetchLog();
    }
    renderConnection();
  }, 1000);
  setInterval(() => {
    if (state.refresh) fetchLibrary();
  }, 2000);
  setInterval(() => {
    if (state.refresh && state.view === "models") fetchModels();
  }, 15000);
})();
