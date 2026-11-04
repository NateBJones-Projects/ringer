// Dependency-free behavior checks. Browser/layout verification is in TESTING.md.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(
  path.join(__dirname, "../dashboard/ringside.js"),
  "utf8",
);

function environment(payload, native = false) {
  class Element {
    constructor() {
      this.innerHTML = "";
      this.textContent = "";
      this.dataset = {};
      this.value = "";
      this.scrollHeight = 0;
      this.scrollTop = 0;
      this.clientHeight = 0;
      this.events = {};
      this.attrs = {};
      this.classList = { toggle() {} };
    }
    contains() {
      return false;
    }
    querySelectorAll() {
      return [];
    }
    querySelector() {
      return this.children?.[0] || null;
    }
    addEventListener(name, fn) {
      this.events[name] = fn;
    }
    setAttribute(k, v) {
      this.attrs[k] = v;
    }
    removeAttribute(k) {
      delete this.attrs[k];
    }
    replaceChildren(...children) {
      this.children = children;
    }
    focus() {}
    showModal() {
      this.open = true;
    }
    click() {
      this.onclick?.();
    }
  }
  const elements = new Map(),
    docEvents = {},
    timers = [],
    calls = [];
  const el = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const views = ["runs", "outputs", "models"].map((name) => {
    const e = new Element();
    e.dataset.view = name;
    return e;
  });
  const document = {
    getElementById: el,
    querySelector: (selector) => el(selector),
    querySelectorAll: () => views,
    addEventListener: (name, fn) => {
      docEvents[name] = fn;
    },
    createElement: () => new Element(),
  };
  let applyNative;
  const window = native
    ? {
        ringsideNative: {
          serverURL: Promise.resolve("http://127.0.0.1:9876"),
          readLibrary: async () => JSON.stringify(payload["/api/library"]),
          readArtifact: async () => "native preview",
          artifactURL: () => "http://asset.localhost/preview",
          readLog: async () => "native log",
          resize: async (w, h) => calls.push([w, h]),
          hide() {},
          onRuns: (fn) => {
            applyNative = fn;
          },
        },
      }
    : { location: { href: "http://127.0.0.1:9876/" } };
  vm.runInNewContext(
    source,
    {
      document,
      window,
      URL,
      localStorage: { getItem: () => null, setItem() {} },
      setInterval: (fn, ms) => timers.push({ fn, ms }),
      fetch: async (url) => {
        calls.push(url);
        const resource = String(url).replace(/^http:\/\/127\.0\.0\.1:9876/, "");
        const value = payload[resource];
        if (value instanceof Error) throw value;
        return {
          ok: value !== undefined,
          status: value === undefined ? 404 : 200,
          json: async () => value,
          text: async () => String(value),
        };
      },
      console,
      Date,
      Map,
      Set,
    },
    { timeout: 1000 },
  );
  return {
    el,
    timers,
    calls,
    click: (dataset) =>
      docEvents.click({ target: { closest: () => ({ dataset }) } }),
    input: (id, value) => el(id).events.input({ target: { value } }),
    change: (id, value) => el(id).events.change({ target: { value } }),
    nativeRuns: (value = payload["/api/runs"]) => applyNative(value),
  };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() {
  for (let i = 0; i < 5; i++) await tick();
}
const now = new Date().toISOString();
const run = {
  run_id: "release",
  run_name: "Release",
  state: "live",
  started_at: now,
  tasks: [
    {
      key: "redirect",
      status: "retrying",
      verdict: "FAIL",
      attempts: 2,
      max_attempts: 3,
      check_returncode: 1,
      check_output_tail: "<script>bad()</script> expected /dashboard",
      model: "Model A",
      engine: "codex",
    },
    {
      key: "auth",
      status: "pass",
      verdict: "PASS",
      attempts: 1,
      check_returncode: 0,
    },
  ],
};
const models = {
  generated_at: now,
  rollup: [
    {
      model_display: "Model A · high",
      show_reasoning_effort: true,
      reasoning_effort: "high",
      harness: "Codex",
      tasks: 42,
      attempts: 49,
      first_try_pass_rate: 0.91,
      pass_rate: 0.95,
      latest_note: "Fresh note <b>escaped</b>",
    },
    {
      model_display: "Model B",
      harness: "OpenCode",
      tasks: 2,
      attempts: 2,
      first_try_pass_rate: 1,
    },
  ],
  groups: [
    {
      task_type: "research",
      model_display: "Model A",
      harness: "Codex",
      tasks: 3,
      attempts: 5,
      first_try_pass_rate: 0.33,
    },
  ],
};
const data = {
  "/api/runs": {
    runs: [
      run,
      {
        run_id: "dead",
        run_name: "Stopped run",
        state: "died",
        started_at: now,
        tasks: [{ key: "zombie", status: "running" }],
      },
    ],
  },
  "/api/library": {
    artifacts: {
      Release: {
        current_run_id: "release",
        live_path: "/tmp/artifacts/live/Release.html",
        state: "live",
        versions: [
          {
            path: "/tmp/artifacts/versions/release-v1.html",
            run_id: "v1",
            finished_at: now,
            outcome: "pass",
          },
        ],
      },
    },
  },
  "/api/models": models,
  "/artifacts/live/Release.html": "live version",
  "/artifacts/versions/release-v1.html": "saved version",
  "/logs/release/redirect": "\x1b[31mraw <worker> log\x1b[0m",
};
(async () => {
  const env = environment(data);
  await settle();
  assert.match(env.el("task-inspector").innerHTML, /Retrying · attempt 2 of 3/);
  assert.match(
    env.el("task-inspector").innerHTML,
    /&lt;script&gt;bad\(\)&lt;\/script&gt;/,
  );
  assert.doesNotMatch(env.el("task-inspector").innerHTML, /<script>/);
  env.input("search", "auth");
  assert.doesNotMatch(env.el("task-table").innerHTML, /data-task="redirect"/);
  assert.match(env.el("task-table").innerHTML, /data-task="auth"/);
  env.input("search", "");
  env.click({ action: "logs" });
  await settle();
  assert.equal(env.el("worker-log").textContent, "raw <worker> log");
  env.click({ view: "models" });
  await settle();
  assert.match(env.el("model-rows").innerHTML, /91%/);
  assert.equal((env.el("model-rows").innerHTML.match(/high/g) || []).length, 1);
  assert.match(env.el("model-rows").innerHTML, /Low sample/);
  assert.match(
    env.el("model-signal-rows").innerHTML,
    /Fresh note &lt;b&gt;escaped&lt;\/b&gt;/,
  );
  env.change("task-type", "research");
  assert.match(env.el("model-rows").innerHTML, /33%/);
  assert.doesNotMatch(env.el("model-rows").innerHTML, /Model B/);
  env.click({ run: "dead" });
  assert.match(env.el("task-table").innerHTML, /Stopped/);
  assert.doesNotMatch(env.el("task-table").innerHTML, />Working</);
  env.click({ output: "Release" });
  env.change("artifact-version", "/tmp/artifacts/versions/release-v1.html");
  await settle();
  assert.match(env.el("frame-wrap").children[0].srcdoc, /saved version/);
  assert.match(
    env.el("frame-wrap").children[0].srcdoc,
    /base href="http:\/\/127\.0\.0\.1:9876\/artifacts\/versions\/release-v1.html" target="_blank"/,
  );
  env.timers.find((t) => t.ms === 2000).fn();
  await settle();
  assert.equal(
    env.el("artifact-version").value,
    "/tmp/artifacts/versions/release-v1.html",
  );
  assert.match(env.el("frame-wrap").children[0].srcdoc, /saved version/);
  data["/api/runs"] = new Error("disconnected");
  env.timers.find((t) => t.ms === 1000).fn();
  await settle();
  assert.match(env.el("runs-error").textContent, /disconnected/);
  assert.match(env.el("task-table").innerHTML, /zombie/);
  const history = {
    ...run,
    run_id: "older",
    run_name: "Completed history",
    state: "finished",
    tasks: [{ key: "old task", status: "pass" }],
  };
  data["/api/runs"] = { runs: [run, history] };
  const native = environment(data, true);
  native.nativeRuns();
  await settle();
  assert.equal(native.el("compact-panel").hidden, false);
  assert.match(native.el("compact-runs").innerHTML, /Release/);
  native.nativeRuns([]);
  assert.match(native.el("compact-runs").innerHTML, /Release/);
  assert.match(native.el("recent-runs").innerHTML, /Completed history/);
  native.nativeRuns([
    { ...run, run_id: "native-only", run_name: "Offline native run" },
  ]);
  assert.doesNotMatch(
    native.el("compact-runs").innerHTML,
    /Offline native run/,
  );
  data["/api/runs"] = new Error("HUD offline");
  native.timers.find((t) => t.ms === 1000).fn();
  await settle();
  assert.match(native.el("compact-runs").innerHTML, /Offline native run/);
  data["/api/runs"] = { runs: [run, history] };
  native.timers.find((t) => t.ms === 1000).fn();
  await settle();
  native.nativeRuns([]);
  assert.match(native.el("compact-runs").innerHTML, /Release/);
  assert.match(native.el("recent-runs").innerHTML, /Completed history/);
  native.el("expand-button").onclick();
  await settle();
  assert.equal(native.el("compact-panel").hidden, true);
  assert.ok(native.calls.some((c) => Array.isArray(c) && c[0] === 1440));
  const empty = environment({
    "/api/runs": { runs: [] },
    "/api/library": { artifacts: {} },
    "/api/models": { rollup: [], groups: [] },
  });
  await settle();
  assert.match(empty.el("run-picker").innerHTML, /No runs yet/);
  assert.equal(empty.el("open-folder").disabled, true);
  console.log(
    "PASS: retry evidence, escaping, task search, raw logs, model filtering, dead workers, saved versions, reconnect, native compact/expand, empty states",
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
