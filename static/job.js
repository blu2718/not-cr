// Pantalla de procesado: consume el SSE del job (replay + en vivo) y pinta
// cronómetro, progreso, tiempo restante, mapa de páginas, texto en vivo,
// registro en árbol, uso/coste y el estado final.
(function () {
  "use strict";

  var root = document.querySelector("[data-job]");
  if (!root) return;
  var $ = function (selector) { return root.querySelector(selector); };

  var els = {
    pill: $("[data-pill]"),
    warnCount: $("[data-warn-count]"),
    percent: $("[data-percent]"),
    step: $("[data-step]"),
    eta: $("[data-eta]"),
    clock: $("[data-clock]"),
    barWrap: $("[data-bar-wrap]"),
    bar: $("[data-bar]"),
    pagemap: $("[data-pagemap]"),
    usage: $("[data-usage]"),
    progressBlock: $("[data-progress-block]"),
    finalOk: $("[data-final-ok]"),
    finalErr: $("[data-final-err]"),
    live: $("[data-live]"),
    log: $("[data-log]"),
    follow: $("[data-follow]")
  };

  var pricing = {};
  try { pricing = JSON.parse(root.dataset.pricing || "{}") || {}; } catch (e) {}

  var state = {
    status: root.dataset.jobStatus,
    started: root.dataset.started ? Number(root.dataset.started) * 1000 : null,
    finished: root.dataset.finished ? Number(root.dataset.finished) * 1000 : null,
    total: 0,
    index: 0,
    pageTimes: [],
    truncated: {},
    usage: { prompt: 0, completion: 0, total: 0, cost: 0, allCost: true, count: 0 }
  };

  function formatClock(ms) {
    var s = Math.max(0, Math.floor(ms / 1000));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = String(s % 60).padStart(2, "0");
    return h ? h + ":" + String(m).padStart(2, "0") + ":" + sec : m + ":" + sec;
  }

  function formatDuration(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    if (seconds < 60) return seconds + " s";
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + " min " + String(seconds % 60).padStart(2, "0") + " s";
    return Math.floor(minutes / 60) + " h " + String(minutes % 60).padStart(2, "0") + " min";
  }

  function thousands(n) {
    return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  }

  function money(value) {
    return "≈ $" + (value < 0.01 ? value.toFixed(4) : value.toFixed(2));
  }

  function decimal(n) {
    return n.toFixed(1).replace(".", ",");
  }

  // ── Auto-scroll con «Seguir» ──
  var programmatic = false;

  function activePanel() {
    return els.live.hidden ? els.log : els.live;
  }

  function scrollToEnd(panel) {
    if (!els.follow.checked || panel.hidden) return;
    programmatic = true;
    panel.scrollTop = panel.scrollHeight;
    requestAnimationFrame(function () { programmatic = false; });
  }

  [els.live, els.log].forEach(function (panel) {
    panel.addEventListener("scroll", function () {
      if (programmatic) return;
      var atEnd = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 24;
      els.follow.checked = atEnd;
    });
  });
  els.follow.addEventListener("change", function () { scrollToEnd(activePanel()); });

  // ── Pestañas ──
  root.querySelectorAll("[data-tab]").forEach(function (tab) {
    tab.addEventListener("click", function () {
      root.querySelectorAll("[data-tab]").forEach(function (other) {
        other.setAttribute("aria-selected", other === tab ? "true" : "false");
      });
      els.live.hidden = tab.dataset.tab !== "live";
      els.log.hidden = tab.dataset.tab !== "log";
      scrollToEnd(activePanel());
    });
  });

  // ── Pintado ──
  function setPill(status) {
    var labels = { queued: "En cola", running: "Procesando", done: "Listo", error: "Error" };
    els.pill.className = "pill" + (status === "queued" ? "" : " pill--" + status);
    els.pill.textContent = labels[status] || status;
  }

  function buildPagemap(total) {
    if (els.pagemap.childElementCount === total) return;
    els.pagemap.innerHTML = "";
    for (var i = 1; i <= total; i++) {
      var cell = document.createElement("span");
      cell.className = "pagemap__cell";
      cell.title = "Página " + i;
      els.pagemap.appendChild(cell);
    }
  }

  function cell(page) {
    return els.pagemap.children[page - 1];
  }

  function paintCells() {
    var done = state.pageTimes.length;
    for (var page = 1; page <= state.total; page++) {
      var c = cell(page);
      if (!c) continue;
      c.classList.toggle("is-done", page <= done && !state.truncated[page]);
      c.classList.toggle("is-truncated", Boolean(state.truncated[page]));
      c.classList.toggle("is-current", state.status === "running" && page === state.index && page > done);
      c.title = "Página " + page + (page <= done ? " · " + decimal(state.pageTimes[page - 1]) + " s" : "") +
        (state.truncated[page] ? " · truncada" : "");
    }
  }

  function paintProgress() {
    var done = state.pageTimes.length;
    var percent = state.status === "done" ? 100 : state.total ? Math.round(100 * Math.min(done, state.total) / state.total) : 0;
    els.percent.textContent = percent + "%";
    var bar = els.bar.querySelector(".progress__bar");
    bar.style.width = percent + "%";
    els.bar.setAttribute("aria-valuenow", percent);
    if (state.index >= 1 || state.status === "done") els.barWrap.hidden = false;

    if (state.status === "running" && done && state.total > done) {
      var average = state.pageTimes.reduce(function (a, b) { return a + b; }, 0) / done;
      var remaining = average * (state.total - done);
      els.eta.textContent = "≈ " + (remaining < 60 ? Math.ceil(remaining) + " s" : Math.round(remaining / 60) + " min") + " restantes";
    } else {
      els.eta.textContent = "";
    }
    paintCells();
  }

  function costValue() {
    var u = state.usage;
    if (!u.count) return null;
    if (u.allCost) return u.cost;
    var p = parseFloat(pricing.prompt);
    var c = parseFloat(pricing.completion);
    if (isNaN(p) || isNaN(c)) return null;
    return u.prompt * p + u.completion * c;
  }

  function paintUsage() {
    var u = state.usage;
    if (!u.count) return;
    var parts = ["Tokens: " + thousands(u.total) + " (entrada " + thousands(u.prompt) + " · salida " + thousands(u.completion) + ")"];
    var cost = costValue();
    if (cost !== null) parts.push("Coste " + money(cost));
    els.usage.textContent = parts.join(" · ");
    els.usage.hidden = false;
  }

  function tickClock() {
    if (!state.started) {
      els.clock.textContent = "0:00";
      return;
    }
    var end = state.finished || Date.now();
    els.clock.textContent = formatClock(end - state.started);
  }

  function appendLog(text, cls) {
    var line = document.createElement("span");
    line.textContent = text + "\n";
    if (cls) line.className = cls;
    else if (/^\[.*\]$/.test(text)) line.className = "head";
    els.log.appendChild(line);
    scrollToEnd(els.log);
  }

  // ── Eventos ──
  var handlers = {
    log: function (text) { appendLog(text); },
    status: function (payload) {
      if (state.status === "queued") {
        state.status = "running";
        setPill("running");
      }
      if (!state.started) state.started = Date.now();
      var step = payload.step;
      if (step === "starting") {
        els.step.textContent = "Preparando…";
      } else if (step === "converting") {
        els.step.textContent = "Convirtiendo PDF a imágenes";
      } else if (step === "page") {
        state.total = payload.total || 0;
        state.index = payload.index || 0;
        buildPagemap(state.total);
        els.step.textContent = state.index ? "Página " + state.index + " de " + state.total : state.total + " páginas";
      } else if (step === "saving") {
        els.step.textContent = "Guardando Markdown";
      }
      paintProgress();
    },
    page_time: function (seconds) {
      state.pageTimes.push(Number(seconds) || 0);
      paintProgress();
    },
    usage: function (usage) {
      var u = state.usage;
      var prompt = usage.prompt_tokens || 0;
      var completion = usage.completion_tokens || 0;
      u.prompt += prompt;
      u.completion += completion;
      u.total += usage.total_tokens || prompt + completion;
      if (typeof usage.cost === "number") u.cost += usage.cost;
      else u.allCost = false;
      u.count += 1;
      paintUsage();
    },
    warning: function (payload) {
      state.truncated[payload.page] = true;
      appendLog(payload.message, "warn");
      var n = Object.keys(state.truncated).length;
      els.warnCount.hidden = false;
      els.warnCount.textContent = "⚠ " + n + " página" + (n === 1 ? "" : "s") + " truncada" + (n === 1 ? "" : "s");
      paintCells();
    },
    chunk: function (piece) {
      els.live.appendChild(document.createTextNode(piece));
      scrollToEnd(els.live);
    },
    done: function (payload) {
      state.status = "done";
      if (!state.finished) state.finished = Date.now();
      setPill("done");
      els.step.textContent = "Listo";
      paintProgress();
      var url = root.dataset.outputBase + encodeURIComponent(payload.output);
      els.finalOk.querySelector("[data-final-open]").href = url;
      var compare = els.finalOk.querySelector("[data-final-compare]");
      if (compare) compare.href = url + "?view=compare";
      var summary = [formatDuration((state.finished - state.started) / 1000)];
      if (state.usage.count) summary.push(thousands(state.usage.total) + " tokens");
      var cost = costValue();
      if (cost !== null) summary.push(money(cost));
      els.finalOk.querySelector("[data-final-summary]").textContent = summary.join(" · ");
      els.progressBlock.hidden = true;
      els.finalOk.hidden = false;
      finish();
    },
    error: function (payload) {
      state.status = "error";
      if (!state.finished) state.finished = Date.now();
      setPill("error");
      paintProgress();
      appendLog("✗ " + payload.message, "err");
      els.finalErr.querySelector("[data-final-error]").textContent = payload.message;
      var partial = els.finalErr.querySelector("[data-final-partial]");
      if (payload.partial && payload.output) {
        partial.href = root.dataset.outputBase + encodeURIComponent(payload.output);
        partial.hidden = false;
      }
      els.finalErr.hidden = false;
      finish();
    }
  };

  var source = null;
  var clockTimer = setInterval(tickClock, 1000);
  var received = 0; // eventos procesados (para no duplicar tras una reconexión)
  var skip = 0;

  function finish() {
    if (source) source.close();
    clearInterval(clockTimer);
    tickClock();
  }

  function connect() {
    source = new EventSource(root.dataset.stream);
    source.addEventListener("open", function () {
      // El servidor repite todos los eventos desde el principio.
      skip = received;
      received = 0;
    });
    Object.keys(handlers).forEach(function (kind) {
      source.addEventListener(kind, function (event) {
        // Los errores nativos de EventSource (reconexión) no traen datos.
        if (kind === "error" && !event.data) return;
        received += 1;
        if (skip > 0) {
          skip -= 1;
          return;
        }
        var payload;
        try { payload = JSON.parse(event.data); } catch (e) { return; }
        handlers[kind](payload);
      });
    });
  }

  if (state.status === "queued") els.step.textContent = "En cola";
  tickClock();
  connect();
})();
