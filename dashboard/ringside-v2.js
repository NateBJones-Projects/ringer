/* ============================================================================
 * Ringside v2 — Dashboard logic for monitoring AI agent swarms
 * Self-contained IIFE. Calls init() on DOMContentLoaded.
 * ========================================================================= */
(function () {
  'use strict';

  // ── State ────────────────────────────────────────────────────────────────
  const state = {
    runs: [],
    active: {},
    artifacts: [],
    theme: localStorage.getItem('ringside-theme') || 'dark',
    currentPage: 'dashboard',
    expandedWorkers: new Map(),   // key → true
    selectedRun: '',
    selectedArtifact: '',
    artifactVersion: 'live',
    modelsData: null,
    apiBase: '',
    kirbyActive: false,
    kirbyShiftStart: 0,
    kirbyClickCount: 0,
    kirbyDanceActive: false,
  };

  let matrixAnimId = null;
  let matrixCanvas = null;
  let clockInterval = null;

  // ── Kirby Data ───────────────────────────────────────────────────────────
  const KIRBYS = [
    "(b'.' )b", "p(-_-p)", "t('.'t)", "(>'.')>", "<('.'<)",
    "\\(^o^)/", "(b'O' )b", "q('.'q)", "(>'-')>", "d('.'d)",
    "\\('o')/", "(b*_* )b", "t(>.<t)", "m('.'m)", "(づ'.' )づ",
  ];
  const KIRBY_COLORS = [
    '#ff69b4', '#ff1493', '#00ff41', '#35d0ff', '#ff5f6b',
    '#ffd700', '#ff6347', '#7b68ee', '#00fa9a', '#ff4500',
    '#da70d6', '#32cd32', '#ff8c00', '#1e90ff', '#ff00ff',
  ];

  // ── Helpers ──────────────────────────────────────────────────────────────

  /** Coerce to number or return 0. */
  function numberOrZero(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }

  /** Parse a time value (epoch-ms, ISO string, or seconds) into epoch-ms. */
  function parseTime(v) {
    if (!v) return 0;
    if (typeof v === 'number') return v > 1e12 ? v : v * 1000;
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? 0 : d.getTime();
  }

  /** Format seconds into human-readable duration (e.g. "3m 12s"). */
  function formatDuration(s) {
    s = Math.round(numberOrZero(s));
    if (s < 0) s = 0;
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return h + 'h ' + m + 'm';
  }

  /** Format token count compactly (e.g. 86600 → "86.6k"). */
  function formatTokens(n) {
    n = numberOrZero(n);
    if (n < 1000) return String(n);
    if (n < 1e6) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
    return (n / 1e6).toFixed(2).replace(/\.?0+$/, '') + 'M';
  }

  /** Relative time-ago string from epoch-ms or ISO string. */
  function relativeAgo(v) {
    const ms = parseTime(v);
    if (!ms) return '';
    const diff = Math.max(0, Math.floor((Date.now() - ms) / 1000));
    if (diff < 5) return 'just now';
    if (diff < 60) return diff + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  /** Stable run identifier. */
  function runId(run, i) {
    return run.runId || run.id || 'run-' + i;
  }

  /** Stable task key. */
  function taskKey(task, i) {
    return task.key || task.name || 'task-' + i;
  }

  /** Classify task state into a rendering kind. */
  function taskKind(task) {
    if (!task) return 'waiting';
    const s = (task.state || task.status || '').toLowerCase();
    if (s === 'pass' || s === 'passed' || s === 'done') return 'pass';
    if (s === 'working' || s === 'running' || s === 'active') return 'working';
    if (s === 'retry' || s === 'retrying') return 'retry';
    if (s === 'fail' || s === 'failed' || s === 'error') return 'fail';
    return 'waiting';
  }

  /** Human label for task kind. */
  function taskStateText(kind) {
    const map = { pass: 'Passed', working: 'Working', retry: 'Retrying', fail: 'Failed', waiting: 'Waiting' };
    return map[kind] || 'Waiting';
  }

  /** Brief activity text for a task. */
  function taskActivity(task) {
    if (!task) return '';
    if (task.activity) return task.activity;
    const k = taskKind(task);
    if (k === 'working') return task.step || 'running...';
    if (k === 'retry') return 'attempt ' + (numberOrZero(task.attempt) + 1);
    return '';
  }

  /** Strip ANSI escape codes from worker log text. */
  function stripAnsi(s) {
    return s
      .replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '')
      .replace(/\x1b\[[0-9;]{1,6}m/g, '');
  }

  // ── Data Normalization ───────────────────────────────────────────────────

  /**
   * Normalize the /api/runs payload into a sorted array of runs,
   * each with computed pass/fail/done counts and total tokens.
   */
  function normalizeRuns(payload) {
    if (!payload) return;
    const raw = Array.isArray(payload.runs) ? payload.runs : [];
    state.active = payload.active || {};

    state.runs = raw.map(function (run, ri) {
      const id = runId(run, ri);
      const tasks = Array.isArray(run.tasks) ? run.tasks : [];
      let pass = 0, fail = 0, done = 0, tokens = 0;

      tasks.forEach(function (t) {
        const k = taskKind(t);
        if (k === 'pass') { pass++; done++; }
        else if (k === 'fail') { fail++; done++; }
        else if (k === 'working' || k === 'retry') { /* in progress */ }
        else { /* waiting */ }
        tokens += numberOrZero(t.tokens || t.totalTokens);
      });

      return {
        id: id,
        label: run.label || run.name || id,
        tasks: tasks,
        pass: pass,
        fail: fail,
        done: done,
        total: tasks.length,
        tokens: tokens,
        startedAt: parseTime(run.startedAt || run.started),
        finishedAt: parseTime(run.finishedAt || run.finished),
        isLive: !!state.active[id],
        raw: run,
      };
    });

    // Sort: live runs first, then most recent start
    state.runs.sort(function (a, b) {
      if (a.isLive !== b.isLive) return a.isLive ? -1 : 1;
      return (b.startedAt || 0) - (a.startedAt || 0);
    });
  }

  /** Normalize the /api/library payload into an artifacts array. */
  function normalizeLibrary(payload) {
    if (!payload || !payload.artifacts) return;
    const arts = payload.artifacts;
    state.artifacts = Object.keys(arts).map(function (name) {
      const a = arts[name];
      return {
        name: name,
        state: a.state || 'unknown',
        versions: Array.isArray(a.versions) ? a.versions : [],
        raw: a,
      };
    });
  }

  // ── Fetch ────────────────────────────────────────────────────────────────

  function fetchRuns() {
    fetch(apiUrl('/api/runs'), { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        normalizeRuns(data);
        render();
        fetchExpandedWorkerLogs();
      })
      .catch(function () { /* swallow — don't crash rendering */ });
  }

  function fetchLibrary() {
    fetch(apiUrl('/api/library'), { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        normalizeLibrary(data);
        render();
      })
      .catch(function () {});
  }

  function fetchModels() {
    fetch(apiUrl('/api/models'), { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.modelsData = data;
        render();
        setTimeout(buildAnalytics, 100);
      })
      .catch(function () {});
  }


  function apiUrl(path) { return state.apiBase + path; }

  /** Fetch log for each expanded, still-running worker. */
  function fetchExpandedWorkerLogs() {
    state.expandedWorkers.forEach(function (_v, compositeKey) {
      var parts = compositeKey.split('::');
      var rid = parts[0];
      var tkey = parts[1];

      // Only fetch if the task is still running
      var run = state.runs.find(function (r) { return r.id === rid; });
      if (!run) return;
      var task = run.tasks.find(function (t, ti) { return taskKey(t, ti) === tkey; });
      if (!task) return;
      var kind = taskKind(task);
      if (kind !== 'working' && kind !== 'retry') return;

      fetch(apiUrl('/logs/' + encodeURIComponent(rid) + '/' + encodeURIComponent(tkey)), { cache: 'no-store' })
        .then(function (r) { return r.text(); })
        .then(function (text) {
          updateWorkerLog(compositeKey, stripAnsi(text));
        })
        .catch(function () {});
    });
  }

  /** Update a worker's log <pre> element with scroll-pinning. */
  function updateWorkerLog(compositeKey, text) {
    var el = document.querySelector('[data-log-key="' + compositeKey + '"]');
    if (!el) return;

    // Pin to bottom if user hasn't scrolled up
    var pinned = (el.scrollTop + el.clientHeight >= el.scrollHeight - 20);
    el.textContent = text;
    if (pinned) {
      el.scrollTop = el.scrollHeight;
    }
  }

  // ── Theme System ─────────────────────────────────────────────────────────

  /** Apply theme by name: 'dark' | 'light' | 'h4'. */
  function setTheme(name) {
    state.theme = name;
    document.documentElement.setAttribute('data-theme', name);
    localStorage.setItem('ringside-theme', name);

    if (name === 'h4') {
      startMatrixRain();
    } else {
      stopMatrixRain();
    }
  }

  /** Cycle through themes: dark → light → h4 → dark. */
  function cycleTheme() {
    var order = ['dark', 'light', 'h4'];
    var idx = order.indexOf(state.theme);
    setTheme(order[(idx + 1) % order.length]);
    render();
    // Rebuild charts with theme-appropriate colors
    if (state.currentPage === 'analytics' || state.currentPage === 'models') {
      setTimeout(buildAnalytics, 100);
    }
  }

  /** Start the full-screen matrix rain canvas effect. */
  function startMatrixRain() {
    if (matrixAnimId) return;

    // Use the existing canvas from HTML; show the overlay
    matrixCanvas = document.getElementById('matrix-canvas');
    var overlay = document.querySelector('.matrix-overlay');
    if (!matrixCanvas || !overlay) return;
    overlay.style.display = 'block';

    var ctx = matrixCanvas.getContext('2d');
    var chars = 'アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン0123456789@#$%^&*';
    var fontSize = 14;
    var columns;
    var drops;

    function resize() {
      if (!matrixCanvas) return;
      matrixCanvas.width = window.innerWidth;
      matrixCanvas.height = window.innerHeight;
      columns = Math.floor(matrixCanvas.width / fontSize);
      drops = new Array(columns);
      for (var i = 0; i < columns; i++) {
        drops[i] = Math.random() * -100;
      }
    }

    resize();
    window.addEventListener('resize', resize);

    function draw() {
      ctx.fillStyle = 'rgba(0, 0, 0, 0.05)';
      ctx.fillRect(0, 0, matrixCanvas.width, matrixCanvas.height);
      ctx.fillStyle = '#00ff41';
      ctx.font = fontSize + 'px monospace';

      for (var i = 0; i < columns; i++) {
        var ch = chars.charAt(Math.floor(Math.random() * chars.length));
        var x = i * fontSize;
        var y = drops[i] * fontSize;
        ctx.fillText(ch, x, y);

        if (y > matrixCanvas.height && Math.random() > 0.975) {
          drops[i] = 0;
        }
        drops[i]++;
      }

      matrixAnimId = requestAnimationFrame(draw);
    }

    matrixAnimId = requestAnimationFrame(draw);
  }

  /** Stop and hide matrix rain. */
  function stopMatrixRain() {
    if (matrixAnimId) {
      cancelAnimationFrame(matrixAnimId);
      matrixAnimId = null;
    }
    var overlay = document.querySelector('.matrix-overlay');
    if (overlay) overlay.style.display = 'none';
    matrixCanvas = null;
  }

  // ── DOM Helpers ──────────────────────────────────────────────────────────

  /** Create element with optional class, attributes, and children. */
  function el(tag, opts) {
    var node = document.createElement(tag);
    if (!opts) return node;
    if (opts.className) node.className = opts.className;
    if (opts.text) node.textContent = opts.text;
    if (opts.attrs) {
      Object.keys(opts.attrs).forEach(function (k) {
        node.setAttribute(k, opts.attrs[k]);
      });
    }
    if (opts.style) node.style.cssText = opts.style;
    if (opts.onclick) node.addEventListener('click', opts.onclick);
    if (opts.children) {
      opts.children.forEach(function (c) { if (c) node.appendChild(c); });
    }
    return node;
  }

  /** Create a text node. */
  function txt(s) {
    return document.createTextNode(s || '');
  }

  // ── Morph (flicker-free DOM reconciliation) ──────────────────────────────

  /** Sync attributes from `want` onto `have`. */
  function syncAttrs(have, want) {
    // Remove attributes not in want
    var haveAttrs = have.attributes;
    for (var i = haveAttrs.length - 1; i >= 0; i--) {
      var name = haveAttrs[i].name;
      if (!want.hasAttribute(name)) {
        have.removeAttribute(name);
      }
    }
    // Set attributes from want
    var wantAttrs = want.attributes;
    for (var j = 0; j < wantAttrs.length; j++) {
      var attr = wantAttrs[j];
      if (have.getAttribute(attr.name) !== attr.value) {
        have.setAttribute(attr.name, attr.value);
      }
    }
  }

  /**
   * Morph an existing DOM node to match a desired node,
   * minimizing DOM mutations for flicker-free updates.
   */
  function morphNode(have, want) {
    if (!have || !want) return want || have;

    // Different node types or tags — full replace
    if (have.nodeType !== want.nodeType) {
      have.replaceWith(want);
      return want;
    }

    // Text nodes
    if (have.nodeType === 3) {
      if (have.textContent !== want.textContent) {
        have.textContent = want.textContent;
      }
      return have;
    }

    // Element nodes
    if (have.nodeType === 1) {
      if (have.tagName !== want.tagName) {
        have.replaceWith(want);
        return want;
      }
      syncAttrs(have, want);
      morphChildren(have, want);
    }

    return have;
  }

  /** Reconcile child lists using data-key for stable matching. */
  function morphChildren(have, want) {
    var haveKids = Array.from(have.childNodes);
    var wantKids = Array.from(want.childNodes);

    // Build key maps for element children
    var haveByKey = {};
    haveKids.forEach(function (c) {
      if (c.nodeType === 1 && c.dataset && c.dataset.key) {
        haveByKey[c.dataset.key] = c;
      }
    });

    var usedKeys = {};
    var hi = 0;

    for (var wi = 0; wi < wantKids.length; wi++) {
      var wantChild = wantKids[wi];
      var key = (wantChild.nodeType === 1 && wantChild.dataset) ? wantChild.dataset.key : null;
      var matched = null;

      if (key && haveByKey[key]) {
        matched = haveByKey[key];
        usedKeys[key] = true;
        // Move into position if needed
        if (have.childNodes[wi] !== matched) {
          have.insertBefore(matched, have.childNodes[wi] || null);
        }
        morphNode(matched, wantChild);
      } else if (!key && hi < haveKids.length && haveKids[hi] && !haveKids[hi].dataset?.key) {
        // Positional fallback for non-keyed nodes
        matched = haveKids[hi];
        morphNode(matched, wantChild);
        hi++;
      } else {
        // Insert new node
        have.insertBefore(wantChild, have.childNodes[wi] || null);
      }
    }

    // Remove excess children
    while (have.childNodes.length > wantKids.length) {
      have.removeChild(have.lastChild);
    }
  }

  // ── Rendering ────────────────────────────────────────────────────────────

  /** Master render — re-renders all visible sections. */
  function render() {
    updateTopbar();
    updateKPIs();

    if (state.currentPage === 'dashboard') {
      morphInto('#runs-container', renderRuns());
      updateArtifactPanel();
      morphInto('#activity-feed', renderActivityFeed());
    }

    morphInto('#bottom-bar', renderBottomBar());
  }

  /** Morph content into a container selected by CSS selector. */
  function morphInto(selector, content) {
    var container = document.querySelector(selector);
    if (!container || !content) return;
    morphChildren(container, content);
  }

  // ── Topbar ───────────────────────────────────────────────────────────────

  /** Update topbar elements in-place (no morphing — preserves static HTML structure). */
  function updateTopbar() {
    // Live badge
    var liveCount = state.runs.filter(function (r) { return r.isLive; }).length;
    var badge = document.getElementById('live-badge');
    if (badge) {
      badge.hidden = false;
      badge.classList.toggle('active', liveCount > 0);
      var countEl = document.getElementById('live-count');
      if (countEl) countEl.textContent = liveCount > 0 ? liveCount + ' SWARMS LIVE' : 'IDLE';
    }

    // Theme label
    var themeLabel = document.getElementById('theme-label');
    if (themeLabel) {
      var icons = { dark: '☽ Dark', light: '☀ Light', h4: '◉ h4' };
      themeLabel.textContent = icons[state.theme] || state.theme;
    }
  }

  /** Format clock for topbar display. */
  function formatClock() {
    var d = new Date();
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  /** Tick the clock element. */
  function tickClock() {
    var clockEl = document.getElementById('clock');
    if (clockEl) clockEl.textContent = formatClock();
  }

  // ── KPI Strip ────────────────────────────────────────────────────────────

  /** Update KPI values in-place by ID (no morphing — preserves static HTML structure). */
  function updateKPIs() {
    var activeWorkers = 0, passed = 0, failed = 0, totalTokens = 0;
    var earliest = Infinity, totalDone = 0, totalTasks = 0;

    state.runs.forEach(function (run) {
      totalTasks += run.total;
      passed += run.pass;
      failed += run.fail;
      totalDone += run.done;
      totalTokens += run.tokens;
      if (run.startedAt && run.startedAt < earliest) earliest = run.startedAt;

      run.tasks.forEach(function (t) {
        var k = taskKind(t);
        if (k === 'working' || k === 'retry') activeWorkers++;
      });
    });

    var elapsed = earliest < Infinity ? (Date.now() - earliest) / 1000 : 0;
    var throughput = elapsed > 0 ? (totalDone / (elapsed / 60)).toFixed(1) : '0';

    function setKPI(id, value, sub) {
      var el = document.getElementById(id);
      if (el) el.textContent = value;
      if (sub) {
        var subEl = document.getElementById(id + '-sub');
        if (subEl) subEl.textContent = sub;
      }
    }

    setKPI('kpi-active', String(activeWorkers), activeWorkers > 0 ? 'working' : 'idle');
    setKPI('kpi-passed', String(passed), totalTasks > 0 ? ((passed / totalTasks * 100).toFixed(0) + '% pass rate') : '—');
    setKPI('kpi-failed', String(failed));
    setKPI('kpi-tokens', formatTokens(totalTokens));
    setKPI('kpi-elapsed', formatDuration(elapsed));
    setKPI('kpi-throughput', throughput, 'tasks/min');
  }

  // ── Run Panels ───────────────────────────────────────────────────────────

  function renderRuns() {
    var wrapper = el('div');

    if (state.runs.length === 0) {
      wrapper.appendChild(el('div', {
        className: 'empty-state',
        text: 'No runs yet. Waiting for data...',
        attrs: { 'data-key': 'empty' },
      }));
      return wrapper;
    }

    // Filter to selected run if set, otherwise show all
    var visible = state.selectedRun
      ? state.runs.filter(function (r) { return r.id === state.selectedRun; })
      : state.runs;

    visible.forEach(function (run) {
      wrapper.appendChild(renderRunPanel(run));
    });

    return wrapper;
  }

  /** Render a single run as a glass panel with header, progress, and worker grid. */
  function renderRunPanel(run) {
    var panel = el('div', {
      className: 'glass run-panel' + (run.isLive ? ' live' : ''),
      attrs: { 'data-key': 'run-' + run.id },
    });

    // Header
    var header = el('div', { className: 'run-header' });
    header.appendChild(el('span', { className: 'run-label', text: run.label }));
    header.appendChild(el('span', {
      className: 'run-status',
      text: run.isLive ? 'LIVE' : 'DONE',
    }));
    header.appendChild(el('span', {
      className: 'run-progress-text',
      text: run.done + '/' + run.total,
    }));
    if (run.startedAt) {
      header.appendChild(el('span', {
        className: 'run-age',
        text: relativeAgo(run.startedAt),
      }));
    }
    panel.appendChild(header);

    // Progress bar
    var pct = run.total > 0 ? (run.done / run.total * 100) : 0;
    var progressOuter = el('div', { className: 'progress-bar' });
    var progressInner = el('div', {
      className: 'progress-fill' + (run.fail > 0 ? ' has-fail' : ''),
      style: 'width:' + pct.toFixed(1) + '%',
    });
    progressOuter.appendChild(progressInner);
    panel.appendChild(progressOuter);

    // Worker grid (2-column layout)
    var grid = el('div', { className: 'worker-grid' });
    run.tasks.forEach(function (task, ti) {
      var key = taskKey(task, ti);
      var compositeKey = run.id + '::' + key;
      var kind = taskKind(task);
      var expanded = state.expandedWorkers.has(compositeKey);

      var card = el('div', {
        className: 'worker-card ' + kind + (expanded ? ' expanded' : ''),
        attrs: { 'data-key': 'worker-' + key },
      });

      // Card header (clickable)
      var cardHeader = el('div', {
        className: 'worker-header',
        onclick: function () { toggleWorker(compositeKey); },
      });
      cardHeader.appendChild(el('span', { className: 'worker-icon', text: kindIcon(kind) }));
      cardHeader.appendChild(el('span', { className: 'worker-name', text: task.name || key }));
      cardHeader.appendChild(el('span', { className: 'worker-state', text: taskStateText(kind) }));

      var activity = taskActivity(task);
      if (activity) {
        cardHeader.appendChild(el('span', { className: 'worker-activity', text: activity }));
      }
      card.appendChild(cardHeader);

      // Expanded detail
      if (expanded) {
        card.appendChild(renderWorkerDetail(run, task, compositeKey));
      }

      grid.appendChild(card);
    });
    panel.appendChild(grid);

    return panel;
  }

  /** Icon character for task kind. */
  function kindIcon(kind) {
    var icons = { pass: '✓', working: '▶', retry: '↻', fail: '✗', waiting: '○' };
    return icons[kind] || '○';
  }

  /** Render expanded worker detail: spec, log, verdict. */
  function renderWorkerDetail(run, task, compositeKey) {
    var detail = el('div', { className: 'worker-detail' });

    // Spec brief (collapsible)
    if (task.spec || task.brief || task.description) {
      var specText = task.spec || task.brief || task.description;
      var specSection = el('details', { className: 'worker-spec' });
      specSection.appendChild(el('summary', { text: 'Spec' }));
      specSection.appendChild(el('p', { text: specText }));
      detail.appendChild(specSection);
    }

    // Live log stream
    var logPre = el('pre', {
      className: 'worker-log',
      attrs: { 'data-log-key': compositeKey },
      text: 'Loading log...',
    });
    detail.appendChild(logPre);

    // Verification verdict
    if (task.verdict || task.result) {
      var verdict = task.verdict || task.result;
      var kind = taskKind(task);
      detail.appendChild(el('div', {
        className: 'worker-verdict ' + kind,
        text: typeof verdict === 'string' ? verdict : JSON.stringify(verdict),
      }));
    }

    return detail;
  }

  /** Toggle worker expansion. */
  function toggleWorker(compositeKey) {
    if (state.expandedWorkers.has(compositeKey)) {
      state.expandedWorkers.delete(compositeKey);
    } else {
      state.expandedWorkers.set(compositeKey, true);
    }
    render();
  }

  // ── Artifact Panel ───────────────────────────────────────────────────────

  /** Update artifact panel elements in-place (preserves glass panel structure). */
  function updateArtifactPanel() {
    var picker = document.getElementById('artifact-picker');
    var versionPicker = document.getElementById('artifact-version');
    var preview = document.getElementById('artifact-preview');
    var status = document.getElementById('artifact-status');
    if (!picker) return;

    // Populate artifact picker
    var currentVal = picker.value;
    picker.innerHTML = '';
    var defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = state.artifacts.length ? '-- Select --' : 'No artifacts';
    picker.appendChild(defaultOpt);

    state.artifacts.forEach(function (art) {
      var opt = document.createElement('option');
      opt.value = art.name;
      opt.textContent = art.name + ' (' + art.state + ')';
      if (art.name === state.selectedArtifact) opt.selected = true;
      picker.appendChild(opt);
    });

    // Restore selection
    if (state.selectedArtifact) picker.value = state.selectedArtifact;

    if (status) {
      status.textContent = state.artifacts.length + ' artifact' + (state.artifacts.length !== 1 ? 's' : '');
    }

    // Update preview
    var selected = state.artifacts.find(function (a) { return a.name === state.selectedArtifact; });
    if (preview) {
      if (selected && selected.versions && selected.versions.length > 0) {
        var ver = selected.versions[selected.versions.length - 1];
        if (ver && ver.url) {
          preview.innerHTML = '<iframe src="' + ver.url + '" sandbox="allow-scripts allow-same-origin" style="width:100%;height:200px;border:none;border-radius:var(--radius-sm);"></iframe>';
        } else {
          preview.innerHTML = '<span style="color:var(--muted);">No preview available</span>';
        }
      } else {
        preview.innerHTML = '<span style="color:var(--muted);">Select an artifact to preview</span>';
      }
    }

    // Wire up change listener (only once)
    if (!picker._wired) {
      picker._wired = true;
      picker.addEventListener('change', function (e) {
        state.selectedArtifact = e.target.value;
        render();
      });
    }
  }

  // ── Activity Feed ────────────────────────────────────────────────────────

  function renderActivityFeed() {
    var wrapper = el('div');
    var events = [];

    // Aggregate events from all runs
    state.runs.forEach(function (run) {
      run.tasks.forEach(function (task, ti) {
        var kind = taskKind(task);
        if (kind === 'waiting') return; // skip unstarted tasks

        events.push({
          time: parseTime(task.updatedAt || task.startedAt || run.startedAt),
          run: run.label,
          task: task.name || taskKey(task, ti),
          kind: kind,
          text: taskStateText(kind),
          activity: taskActivity(task),
        });
      });
    });

    // Sort newest first
    events.sort(function (a, b) { return b.time - a.time; });

    // Limit to 50 recent events
    events = events.slice(0, 50);

    if (events.length === 0) {
      wrapper.appendChild(el('div', {
        className: 'empty-state',
        text: 'No activity yet.',
        attrs: { 'data-key': 'empty-feed' },
      }));
      return wrapper;
    }

    events.forEach(function (evt, i) {
      var row = el('div', {
        className: 'feed-item ' + evt.kind,
        attrs: { 'data-key': 'feed-' + i },
      });
      row.appendChild(el('span', { className: 'feed-time', text: relativeAgo(evt.time) }));
      row.appendChild(el('span', { className: 'feed-icon', text: kindIcon(evt.kind) }));
      row.appendChild(el('span', { className: 'feed-run', text: evt.run }));
      row.appendChild(el('span', { className: 'feed-task', text: evt.task }));
      row.appendChild(el('span', { className: 'feed-text', text: evt.text }));
      if (evt.activity) {
        row.appendChild(el('span', { className: 'feed-activity', text: evt.activity }));
      }
      wrapper.appendChild(row);
    });

    return wrapper;
  }

  // ── Bottom Bar ───────────────────────────────────────────────────────────

  function renderBottomBar() {
    var wrapper = el('div');

    // "All" pill
    var allPill = el('button', {
      className: 'run-pill' + (state.selectedRun === '' ? ' active' : ''),
      text: 'All',
      attrs: { 'data-key': 'pill-all' },
      onclick: function () { state.selectedRun = ''; render(); },
    });
    wrapper.appendChild(allPill);

    // One pill per run
    state.runs.forEach(function (run) {
      var pill = el('button', {
        className: 'run-pill' + (state.selectedRun === run.id ? ' active' : '') +
                   (run.isLive ? ' live' : ''),
        text: run.label,
        attrs: { 'data-key': 'pill-' + run.id },
        onclick: function () {
          state.selectedRun = (state.selectedRun === run.id) ? '' : run.id;
          render();
        },
      });
      wrapper.appendChild(pill);
    });

    return wrapper;
  }

  // ── Analytics ────────────────────────────────────────────────────────────

  var analyticsCharts = {};  // track Chart.js instances for cleanup

  function getModelsArray() {
    if (!state.modelsData) return [];
    var source = Array.isArray(state.modelsData) ? state.modelsData : (state.modelsData.models || state.modelsData.scoreboard || state.modelsData.rollup || []);
    var normalized = [];
    source.forEach(function (row) {
      var breakdowns = Array.isArray(row.task_types) ? row.task_types : [row];
      breakdowns.forEach(function (item) {
        normalized.push({
          model: row.model || row.model_id || row.name || 'unknown',
          task_class: item.task_type || row.task_type || 'all tasks',
          quality: numberOrZero(item.pass_rate || row.pass_rate),
          first_try_pass: numberOrZero(item.first_try_pass_rate || row.first_try_pass_rate),
          latency: numberOrZero(item.median_duration_ms || row.median_duration_ms),
          requests: numberOrZero(item.tasks || row.tasks),
          tokens: numberOrZero(row.median_tokens)
        });
      });
    });
    return normalized;
  }

  /** Get theme-aware chart colors. */
  function chartColors() {
    var t = state.theme;
    return {
      text: t === 'h4' ? '#00ff41' : t === 'light' ? '#333' : '#c5ccd6',
      grid: t === 'h4' ? 'rgba(0,255,65,0.08)' : t === 'light' ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)',
      accent: t === 'h4' ? '#00ff41' : '#35d0ff',
      pass: '#2ee89a',
      fail: '#ff4f6e',
      working: '#f5b731',
      palette: t === 'h4'
        ? ['#00ff41', '#00cc33', '#33ff66', '#66ff99', '#00ff88', '#00e650']
        : ['#35d0ff', '#2ee89a', '#f5b731', '#ff4f6e', '#a78bfa', '#f472b6'],
    };
  }

  /** Destroy all existing Chart.js instances. */
  function destroyCharts() {
    Object.keys(analyticsCharts).forEach(function (k) {
      if (analyticsCharts[k]) { analyticsCharts[k].destroy(); delete analyticsCharts[k]; }
    });
  }

  /** Build all analytics charts and summary KPIs. */
  function buildAnalytics() {
    var models = getModelsArray();
    var statusEl = document.getElementById('analytics-data-status');
    if (statusEl) statusEl.textContent = models.length ? 'Live local scoreboard' : 'No local scoreboard data yet';
    if (models.length === 0) return;

    var C = chartColors();

    // --- Aggregate data ---
    var uniqueModels = [];
    var modelMap = {};  // model -> { quality[], pass[], latency[], requests }
    var taskClassCounts = {};
    var totalRequests = 0;

    models.forEach(function (m) {
      var name = m.model || m.model_id || m.name || 'unknown';
      var tc = m.task_class || 'unknown';
      if (!modelMap[name]) {
        modelMap[name] = { quality: [], pass: [], latency: [], requests: 0 };
        uniqueModels.push(name);
      }
      modelMap[name].quality.push(numberOrZero(m.quality));
      modelMap[name].pass.push(numberOrZero(m.first_try_pass));
      modelMap[name].latency.push(numberOrZero(m.latency));
      modelMap[name].requests += numberOrZero(m.requests);
      totalRequests += numberOrZero(m.requests);
      taskClassCounts[tc] = (taskClassCounts[tc] || 0) + numberOrZero(m.requests);
    });

    // Compute averages per model
    var avgQuality = [], avgPass = [], avgLatency = [], reqCounts = [];
    uniqueModels.forEach(function (name) {
      var d = modelMap[name];
      var avg = function (arr) { return arr.reduce(function (a, b) { return a + b; }, 0) / arr.length; };
      avgQuality.push(+(avg(d.quality) * 100).toFixed(1));
      avgPass.push(+(avg(d.pass) * 100).toFixed(1));
      avgLatency.push(+avg(d.latency).toFixed(0));
      reqCounts.push(d.requests);
    });

    // Best model by quality
    var bestQualIdx = avgQuality.indexOf(Math.max.apply(null, avgQuality));
    var bestLatIdx = avgLatency.indexOf(Math.min.apply(null, avgLatency));
    var bestPassIdx = avgPass.indexOf(Math.max.apply(null, avgPass));

    // --- Summary KPIs ---
    var summaryEl = document.getElementById('analytics-summary');
    if (summaryEl) {
      summaryEl.innerHTML = '';
      var kpis = [
        { label: 'Total Models', value: String(uniqueModels.length), sub: models.length + ' model-task combos' },
        { label: 'Total Requests', value: formatTokens(totalRequests), sub: Object.keys(taskClassCounts).length + ' task classes' },
        { label: 'Best Quality', value: avgQuality[bestQualIdx] + '%', sub: uniqueModels[bestQualIdx] },
        { label: 'Best Pass Rate', value: avgPass[bestPassIdx] + '%', sub: uniqueModels[bestPassIdx] },
      ];
      kpis.forEach(function (k) {
        var card = document.createElement('div');
        card.className = 'analytics-kpi';
        card.innerHTML = '<div class="ak-value">' + k.value + '</div>' +
          '<div class="ak-label">' + k.label + '</div>' +
          '<div class="ak-sub">' + k.sub + '</div>';
        summaryEl.appendChild(card);
      });
    }

    // --- Chart defaults ---
    var chartDefaults = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: C.text, font: { size: 11 } } },
      },
      scales: {
        x: { ticks: { color: C.text, font: { size: 10 } }, grid: { color: C.grid } },
        y: { ticks: { color: C.text, font: { size: 10 } }, grid: { color: C.grid } },
      },
    };

    destroyCharts();

    // --- Chart 1: Quality Score Bar ---
    var ctx1 = document.getElementById('chart-quality');
    if (ctx1) {
      analyticsCharts.quality = new Chart(ctx1, {
        type: 'bar',
        data: {
          labels: uniqueModels,
          datasets: [{
            label: 'Avg Quality %',
            data: avgQuality,
            backgroundColor: C.palette.slice(0, uniqueModels.length),
            borderRadius: 4,
            borderSkipped: false,
          }],
        },
        options: Object.assign({}, chartDefaults, {
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: C.text, font: { size: 10 }, maxRotation: 30 }, grid: { display: false } },
            y: { ticks: { color: C.text }, grid: { color: C.grid }, min: 0, max: 100,
              title: { display: true, text: 'Quality %', color: C.text } },
          },
        }),
      });
    }

    // --- Chart 2: First-Try Pass Rate Bar ---
    var ctx2 = document.getElementById('chart-pass-rate');
    if (ctx2) {
      analyticsCharts.passRate = new Chart(ctx2, {
        type: 'bar',
        data: {
          labels: uniqueModels,
          datasets: [{
            label: 'First-Try Pass %',
            data: avgPass,
            backgroundColor: avgPass.map(function (v) {
              return v >= 80 ? C.pass : v >= 60 ? C.working : C.fail;
            }),
            borderRadius: 4,
            borderSkipped: false,
          }],
        },
        options: Object.assign({}, chartDefaults, {
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: C.text, font: { size: 10 }, maxRotation: 30 }, grid: { display: false } },
            y: { ticks: { color: C.text }, grid: { color: C.grid }, min: 0, max: 100,
              title: { display: true, text: 'Pass Rate %', color: C.text } },
          },
        }),
      });
    }

    // --- Chart 3: Latency Bar (horizontal) ---
    var ctx3 = document.getElementById('chart-latency');
    if (ctx3) {
      analyticsCharts.latency = new Chart(ctx3, {
        type: 'bar',
        data: {
          labels: uniqueModels,
          datasets: [{
            label: 'Avg Latency (ms)',
            data: avgLatency,
            backgroundColor: avgLatency.map(function (v) {
              return v <= 2000 ? C.pass : v <= 3500 ? C.working : C.fail;
            }),
            borderRadius: 4,
            borderSkipped: false,
          }],
        },
        options: Object.assign({}, chartDefaults, {
          indexAxis: 'y',
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: C.text }, grid: { color: C.grid },
              title: { display: true, text: 'Latency (ms)', color: C.text } },
            y: { ticks: { color: C.text, font: { size: 10 } }, grid: { display: false } },
          },
        }),
      });
    }

    // --- Chart 4: Request Volume Bar ---
    var ctx4 = document.getElementById('chart-requests');
    if (ctx4) {
      analyticsCharts.requests = new Chart(ctx4, {
        type: 'bar',
        data: {
          labels: uniqueModels,
          datasets: [{
            label: 'Requests',
            data: reqCounts,
            backgroundColor: C.accent,
            borderRadius: 4,
            borderSkipped: false,
          }],
        },
        options: Object.assign({}, chartDefaults, {
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: C.text, font: { size: 10 }, maxRotation: 30 }, grid: { display: false } },
            y: { ticks: { color: C.text }, grid: { color: C.grid },
              title: { display: true, text: 'Requests', color: C.text } },
          },
        }),
      });
    }

    // --- Chart 5: Task Class Doughnut ---
    var ctx5 = document.getElementById('chart-task-dist');
    if (ctx5) {
      var tcLabels = Object.keys(taskClassCounts);
      var tcValues = tcLabels.map(function (k) { return taskClassCounts[k]; });
      analyticsCharts.taskDist = new Chart(ctx5, {
        type: 'doughnut',
        data: {
          labels: tcLabels,
          datasets: [{
            data: tcValues,
            backgroundColor: C.palette.concat(['#6b7a99', '#e2e5ea']).slice(0, tcLabels.length),
            borderWidth: 0,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { position: 'right', labels: { color: C.text, font: { size: 11 }, padding: 12 } },
          },
        },
      });
    }

    // --- Chart 6: Radar Comparison ---
    var ctx6 = document.getElementById('chart-radar');
    if (ctx6) {
      var radarDatasets = uniqueModels.slice(0, 4).map(function (name, idx) {
        var d = modelMap[name];
        var avg = function (arr) { return arr.reduce(function (a, b) { return a + b; }, 0) / arr.length; };
        return {
          label: name,
          data: [
            +(avg(d.quality) * 100).toFixed(0),
            +(avg(d.pass) * 100).toFixed(0),
            +((5000 - avg(d.latency)) / 50).toFixed(0),  // invert latency: lower = better
            +(d.requests / (totalRequests / uniqueModels.length) * 50).toFixed(0),  // normalized volume
          ],
          borderColor: C.palette[idx],
          backgroundColor: C.palette[idx].replace(')', ',0.1)').replace('rgb', 'rgba'),
          borderWidth: 2,
          pointRadius: 3,
        };
      });
      analyticsCharts.radar = new Chart(ctx6, {
        type: 'radar',
        data: {
          labels: ['Quality', 'Pass Rate', 'Speed', 'Volume'],
          datasets: radarDatasets,
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: {
            r: {
              angleLines: { color: C.grid },
              grid: { color: C.grid },
              pointLabels: { color: C.text, font: { size: 11 } },
              ticks: { display: false },
              suggestedMin: 0,
              suggestedMax: 100,
            },
          },
          plugins: {
            legend: { position: 'bottom', labels: { color: C.text, font: { size: 10 }, boxWidth: 12 } },
          },
        },
      });
    }

    // --- Chart 7: Quality vs Latency Scatter ---
    var ctx7 = document.getElementById('chart-scatter');
    if (ctx7) {
      var scatterDatasets = uniqueModels.map(function (name, idx) {
        return {
          label: name,
          data: models.filter(function (m) { return (m.model || m.model_id) === name; }).map(function (m) {
            return { x: numberOrZero(m.latency), y: numberOrZero(m.quality) * 100, r: Math.sqrt(numberOrZero(m.requests)) * 2 };
          }),
          backgroundColor: C.palette[idx % C.palette.length],
          borderColor: C.palette[idx % C.palette.length],
        };
      });
      analyticsCharts.scatter = new Chart(ctx7, {
        type: 'bubble',
        data: { datasets: scatterDatasets },
        options: Object.assign({}, chartDefaults, {
          plugins: {
            legend: { position: 'bottom', labels: { color: C.text, font: { size: 10 }, boxWidth: 12 } },
            tooltip: { callbacks: {
              label: function (ctx) {
                return ctx.dataset.label + ': ' + ctx.raw.y.toFixed(0) + '% quality, ' + ctx.raw.x + 'ms';
              }
            }},
          },
          scales: {
            x: { ticks: { color: C.text }, grid: { color: C.grid },
              title: { display: true, text: 'Latency (ms)', color: C.text } },
            y: { ticks: { color: C.text }, grid: { color: C.grid }, min: 50, max: 100,
              title: { display: true, text: 'Quality %', color: C.text } },
          },
        }),
      });
    }

    // --- Scoreboard Table ---
    var tableEl = document.getElementById('analytics-table');
    if (tableEl) {
      var html = '<table class="models-table"><thead><tr>' +
        '<th>Model</th><th>Task Class</th><th>Quality</th><th>Latency</th>' +
        '<th>Requests</th><th>First-Try Pass</th><th>Efficiency</th></tr></thead><tbody>';
      models.forEach(function (m) {
        var q = numberOrZero(m.quality);
        var p = numberOrZero(m.first_try_pass);
        var lat = numberOrZero(m.latency);
        var eff = lat > 0 ? (q * 1000 / lat).toFixed(2) : '-';
        var passClass = p >= 0.8 ? 'pass' : p >= 0.6 ? 'working' : 'fail';
        html += '<tr><td><strong>' + (m.model || m.model_id || '-') + '</strong></td>' +
          '<td>' + (m.task_class || '-') + '</td>' +
          '<td>' + (q * 100).toFixed(0) + '%</td>' +
          '<td>' + lat.toFixed(0) + 'ms</td>' +
          '<td>' + (m.requests || 0) + '</td>' +
          '<td class="text-' + passClass + '" style="font-weight:600;">' + (p * 100).toFixed(0) + '%</td>' +
          '<td>' + eff + '</td></tr>';
      });
      html += '</tbody></table>';
      tableEl.innerHTML = html;
    }
  }

  // ── Navigation ───────────────────────────────────────────────────────────

  /** Navigate to a page: 'dashboard' | 'analytics' | 'models'. */
  function navigateTo(page) {
    state.currentPage = page;

    // Toggle nav icon active states
    document.querySelectorAll('.nav-icon').forEach(function (icon) {
      icon.classList.toggle('active', icon.dataset.page === page);
    });

    // Show/hide page panels (remove hidden attr, use display)
    document.querySelectorAll('.page-panel').forEach(function (panel) {
      panel.removeAttribute('hidden');
      panel.style.display = panel.dataset.page === page ? 'block' : 'none';
    });

    // Fetch models data on analytics/models visit, then build charts
    if (page === 'analytics' || page === 'models') {
      if (!state.modelsData) {
        fetchModels();
      } else {
        // Re-build charts (they need fresh canvas contexts after DOM show/hide)
        setTimeout(buildAnalytics, 50);
      }
    }

    render();
  }

  /** Wire up navigation icon click listeners. */
  function setupNavigation() {
    document.querySelectorAll('.nav-icon').forEach(function (icon) {
      icon.addEventListener('click', function () {
        navigateTo(icon.dataset.page || 'dashboard');
      });
    });
  }

  // ── Theme Toggle Setup ───────────────────────────────────────────────────

  function setupThemeToggle() {
    // Wire up both theme buttons (sidebar + topbar)
    var sidebarBtn = document.getElementById('theme-toggle-btn');
    if (sidebarBtn) sidebarBtn.addEventListener('click', cycleTheme);
    var topbarBtn = document.getElementById('theme-label');
    if (topbarBtn) topbarBtn.addEventListener('click', cycleTheme);

    // Keyboard shortcut
    document.addEventListener('keydown', function (e) {
      if (e.key === 'T' && e.ctrlKey && e.shiftKey) {
        e.preventDefault();
        cycleTheme();
      }
    });
  }

  function hexToRgba(hex, alpha) {
    var value = String(hex || '#06090f').replace('#', '');
    if (value.length === 3) value = value.split('').map(function (c) { return c + c; }).join('');
    var n = parseInt(value, 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
  }

  function loadDesigner() {
    try { return JSON.parse(localStorage.getItem('ringside-custom-theme') || '{}'); } catch (_) { return {}; }
  }

  function applyDesigner(settings) {
    var root = document.documentElement;
    if (!settings || !settings.background) return;
    root.style.setProperty('--ground', settings.background);
    root.style.setProperty('--surface', settings.base === 'white' ? '#ffffff' : settings.base === 'grey' ? '#242830' : '#070707');
    root.style.setProperty('--accent', settings.accent);
    root.style.setProperty('--accent-glow', hexToRgba(settings.accent, .32));
    root.style.setProperty('--glass-bg', hexToRgba(settings.background, Number(settings.overlay || 55) / 100));
    root.style.setProperty('--surface-glass', hexToRgba(settings.background, .78));
    root.style.setProperty('--custom-background-image', settings.image ? 'linear-gradient(' + hexToRgba(settings.background, .40) + ',' + hexToRgba(settings.background, .70) + '), url("' + settings.image + '")' : 'none');
  }

  function setupThemeDesigner() {
    var base = document.getElementById('designer-base'), accent = document.getElementById('designer-accent'), background = document.getElementById('designer-background'), overlay = document.getElementById('designer-overlay'), value = document.getElementById('designer-overlay-value');
    if (!base || !accent || !background || !overlay) return;
    var settings = loadDesigner();
    ['base', 'accent', 'background', 'overlay'].forEach(function (key) { if (settings[key]) ({ base: base, accent: accent, background: background, overlay: overlay })[key].value = settings[key]; });
    applyDesigner(settings);
    function save() { settings = { base: base.value, accent: accent.value, background: background.value, overlay: overlay.value, image: settings.image || '' }; value.textContent = overlay.value + '%'; localStorage.setItem('ringside-custom-theme', JSON.stringify(settings)); applyDesigner(settings); }
    [base, accent, background, overlay].forEach(function (el) { el.addEventListener('input', save); el.addEventListener('change', save); });
    document.getElementById('designer-upload').addEventListener('change', function (e) { var file = e.target.files && e.target.files[0]; if (!file) return; var reader = new FileReader(); reader.onload = function () { settings.image = reader.result; save(); }; reader.readAsDataURL(file); });
    document.getElementById('designer-clear-image').addEventListener('click', function () { settings.image = ''; save(); });
    document.getElementById('theme-reset').addEventListener('click', function () { localStorage.removeItem('ringside-custom-theme'); document.documentElement.removeAttribute('style'); base.value = 'dark'; accent.value = '#35d0ff'; background.value = '#06090f'; overlay.value = 55; value.textContent = '55%'; });
  }

  // ── Kirby Easter Egg ─────────────────────────────────────────────────────

  var kirbyTimer = null;

  function setupKirbyListeners() {
    // Shift hold detection: 5 seconds continuous → activate
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Shift') return;
      if (state.kirbyActive || kirbyTimer) return;

      state.kirbyShiftStart = Date.now();
      kirbyTimer = setTimeout(function () {
        state.kirbyActive = true;
        state.kirbyClickCount = 0;
        updateKirbyCursor();
      }, 5000);
    });

    document.addEventListener('keyup', function (e) {
      if (e.key !== 'Shift') return;
      if (kirbyTimer) {
        clearTimeout(kirbyTimer);
        kirbyTimer = null;
      }
      if (state.kirbyActive) {
        state.kirbyActive = false;
        state.kirbyClickCount = 0;
        document.body.style.cursor = '';
      }
    });

    // Click while kirby active → increment, update cursor, maybe dance party
    document.addEventListener('click', function () {
      if (!state.kirbyActive) return;
      state.kirbyClickCount++;
      updateKirbyCursor();

      if (state.kirbyClickCount >= 30 && !state.kirbyDanceActive) {
        startKirbyDanceParty();
      }
    });
  }

  /** Update the cursor to an SVG of the current kirby + color. */
  function updateKirbyCursor() {
    var idx = state.kirbyClickCount % KIRBYS.length;
    var kirby = KIRBYS[idx];
    var color = KIRBY_COLORS[idx];

    // Encode kirby as SVG data URI for cursor
    var svg =
      '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="24">' +
      '<text x="2" y="18" font-family="monospace" font-size="14" fill="' + color + '">' +
      escapeXml(kirby) +
      '</text></svg>';

    var dataUri = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    document.body.style.cursor = 'url("' + dataUri + '") 0 12, auto';
  }

  /** Escape XML special characters. */
  function escapeXml(s) {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;');
  }

  /** Launch the full-screen KIRBY DANCE PARTY overlay. */
  function startKirbyDanceParty() {
    state.kirbyDanceActive = true;

    var overlay = document.createElement('div');
    overlay.className = 'kirby-dance-overlay';
    overlay.style.cssText =
      'position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:99999;' +
      'background:rgba(0,0,0,0.95);display:flex;flex-direction:column;' +
      'align-items:center;justify-content:center;cursor:default;';

    // Blinking header
    var header = document.createElement('div');
    header.style.cssText =
      'font-size:3rem;font-weight:bold;font-family:monospace;margin-bottom:2rem;';
    header.textContent = 'KIRBY DANCE PARTY';
    overlay.appendChild(header);

    // Giant bouncing kirby
    var kirbyEl = document.createElement('div');
    kirbyEl.style.cssText =
      'font-size:5rem;font-family:monospace;transition:transform 0.3s;';
    overlay.appendChild(kirbyEl);

    document.body.appendChild(overlay);

    // Animate header color (alternate pink/cyan)
    var blinkState = false;
    var blinkInterval = setInterval(function () {
      blinkState = !blinkState;
      header.style.color = blinkState ? '#ff69b4' : '#00ffff';
    }, 400);

    // Cycle kirby character every 300ms
    var kirbyIdx = 0;
    var cycleInterval = setInterval(function () {
      kirbyIdx = (kirbyIdx + 1) % KIRBYS.length;
      kirbyEl.textContent = KIRBYS[kirbyIdx];
      kirbyEl.style.color = KIRBY_COLORS[kirbyIdx];
      // Bounce effect
      var scale = 1 + Math.sin(kirbyIdx * 0.5) * 0.2;
      kirbyEl.style.transform = 'scale(' + scale + ') rotate(' + (kirbyIdx * 15 - 100) + 'deg)';
    }, 300);

    // Auto-dismiss after 10 seconds
    setTimeout(function () {
      clearInterval(blinkInterval);
      clearInterval(cycleInterval);
      overlay.remove();
      state.kirbyDanceActive = false;
      state.kirbyActive = false;
      state.kirbyClickCount = 0;
      document.body.style.cursor = '';
    }, 10000);
  }

  // ── Init ─────────────────────────────────────────────────────────────────

  function init() {
    setTheme(state.theme);
    applyDesigner(loadDesigner());
    tickClock();
    setInterval(tickClock, 1000);
    setInterval(fetchRuns, 1000);
    setInterval(fetchLibrary, 2000);
    if (window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke) {
      window.__TAURI__.core.invoke('hud_endpoint').then(function (endpoint) { state.apiBase = endpoint; fetchRuns(); fetchLibrary(); }).catch(function () { fetchRuns(); fetchLibrary(); });
    } else { fetchRuns(); fetchLibrary(); }
    setupNavigation();
    setupKirbyListeners();
    setupThemeToggle();
    setupThemeDesigner();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
