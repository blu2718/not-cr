// Portada y página del documento: progreso de trabajos (polling de /api/jobs),
// selección múltiple con barra flotante, diálogos de bulk, subida por XHR con
// arrastrar y soltar, vista cuadrícula/lista y atajos de teclado.
(function () {
  "use strict";

  var $ = function (selector, root) { return (root || document).querySelector(selector); };
  var $$ = function (selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); };

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : (many || one + "s"));
  }

  function formatDuration(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    if (seconds < 60) return seconds + " s";
    var minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + " min";
    return Math.floor(minutes / 60) + " h " + String(minutes % 60).padStart(2, "0") + " min";
  }

  function jobState(job) {
    if (job.status === "queued") return "En cola";
    if (job.status === "done") return "✓ Listo";
    if (job.status === "error") return "✗ Error";
    if (job.step === "page" && job.total) return "Página " + job.index + " de " + job.total;
    if (job.step === "saving") return "Guardando";
    return "Convirtiendo";
  }

  function isActive(job) {
    return job.status === "queued" || job.status === "running";
  }

  // ── Trabajos en curso ──────────────────────────────────────────────────────

  var jobsSection = $("[data-jobs-section]");
  var jobsList = jobsSection && $("[data-jobs-list]", jobsSection);
  var jobsDoc = jobsSection && jobsSection.dataset.jobsDoc;
  var pollTimer = null;
  var knownActive = {};

  function progressHtml(percent, state, attrs) {
    return '<div class="progress progress--mini' + (state ? " progress--" + state : "") +
      '" role="progressbar" aria-label="Progreso" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' +
      percent + '" ' + (attrs || "") + '><div class="progress__bar" style="width: ' + percent + '%"></div></div>';
  }

  function jobCardHtml(job) {
    var state = job.status === "done" ? "done" : job.status === "error" ? "error" : "";
    var eta = job.eta_seconds ? "≈ " + formatDuration(job.eta_seconds) : "";
    var href = job.status === "done" && job.output ? "/output/" + encodeURIComponent(job.output) : job.url;
    return '<a class="job-card" href="' + escapeHtml(href) + '" data-job-card="' + job.id + '">' +
      '<img class="job-card__thumb" src="/docs/' + encodeURIComponent(job.doc) + '/thumb" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">' +
      '<span class="job-card__name" title="' + escapeHtml(job.doc) + '">' + escapeHtml(job.doc) + "</span>" +
      '<span class="job-card__meta"><span class="tag tag--model" style="--h: ' + job.hue + '" title="' + escapeHtml(job.model) + '">' +
      escapeHtml(job.model_short) + "</span>" +
      '<span class="job-card__state' + (state ? " job-card__state--" + state : "") + '">' + jobState(job) + "</span></span>" +
      '<span class="job-card__meta">' + progressHtml(job.percent, state, 'style="flex:1"') +
      '<span class="num">' + eta + "</span></span></a>";
  }

  function updateDocTiles(jobs) {
    var byDoc = {};
    jobs.forEach(function (job) {
      if (isActive(job) && !byDoc[job.doc]) byDoc[job.doc] = job;
    });
    $$("[data-doc]").forEach(function (tile) {
      var job = byDoc[tile.dataset.doc];
      if (job) tile.setAttribute("data-busy", "");
      else tile.removeAttribute("data-busy");
      var box = $("[data-doc-job]", tile);
      if (!box) return;
      box.hidden = !job;
      if (!job) return;
      var state = $("[data-doc-job-state]", box);
      if (state) state.textContent = job.status === "running" && job.total ? job.index + "/" + job.total : jobState(job);
      var eta = $("[data-doc-job-eta]", box);
      if (eta) eta.textContent = job.eta_seconds ? "≈ " + formatDuration(job.eta_seconds) : "";
      var bar = $("[data-doc-job-bar] .progress__bar", box);
      if (bar) bar.style.width = job.percent + "%";
    });
  }

  function updateSidebar(jobs) {
    var active = jobs.filter(isActive);
    $$("[data-nav-live]").forEach(function (el) { el.hidden = !active.length; });
    $$("[data-nav-live-count]").forEach(function (el) { el.textContent = active.length; });
    var percent = active.length
      ? Math.round(active.reduce(function (sum, job) { return sum + job.percent; }, 0) / active.length)
      : 0;
    $$("[data-nav-live-bar] .progress__bar").forEach(function (bar) { bar.style.width = percent + "%"; });
  }

  // Cuando termina un trabajo, se recargan las secciones de resultados.
  function refreshSections() {
    fetch(location.href, { headers: { Accept: "text/html" }, cache: "no-store" })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        var fresh = new DOMParser().parseFromString(html, "text/html");
        $$("[data-refresh]").forEach(function (section) {
          var replacement = fresh.getElementById(section.id);
          if (replacement) section.innerHTML = replacement.innerHTML;
        });
        $$("[data-doc]").forEach(function (tile) {
          var selector = tile.tagName.toLowerCase() + '[data-doc="' + CSS.escape(tile.dataset.doc) + '"]';
          var other = fresh.querySelector(selector);
          if (!other) return;
          tile.dataset.results = other.dataset.results;
          var meta = $(".doc-card__meta", tile);
          var otherMeta = $(".doc-card__meta", other);
          if (meta && otherMeta) meta.textContent = otherMeta.textContent;
        });
        disableNoJs();
        syncSelection();
      })
      .catch(function () {});
  }

  function renderJobs(jobs) {
    if (jobsSection) {
      jobsSection.hidden = !jobs.length;
      jobsList.innerHTML = jobs.map(jobCardHtml).join("");
    }
    updateDocTiles(jobs);
    updateSidebar(jobs);

    var finished = false;
    var nowActive = {};
    jobs.forEach(function (job) {
      if (isActive(job)) nowActive[job.id] = true;
      else if (knownActive[job.id]) finished = true;
    });
    Object.keys(knownActive).forEach(function (id) {
      if (!jobs.some(function (job) { return job.id === id; })) finished = true;
    });
    knownActive = nowActive;
    if (finished) refreshSections();
    return Object.keys(nowActive).length > 0;
  }

  function poll() {
    var url = "/api/jobs" + (jobsDoc ? "?doc=" + encodeURIComponent(jobsDoc) : "");
    fetch(url, { cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (jobs) {
        var keepGoing = renderJobs(jobs);
        pollTimer = keepGoing ? setTimeout(poll, 2000) : null;
      })
      .catch(function () {
        pollTimer = setTimeout(poll, 5000);
      });
  }

  function startPolling() {
    if (!pollTimer) poll();
  }

  // Arranca si la página se pintó con trabajos activos.
  var liveCount = $("[data-nav-live-count]");
  if ($$("[data-job-card]").length || $$("[data-busy]").length || (liveCount && Number(liveCount.textContent) > 0)) {
    startPolling();
  }

  // ── Selección múltiple ─────────────────────────────────────────────────────

  var selected = { docs: {}, results: {} };

  function boxes(kind) {
    return $$('[data-select="' + kind + '"]');
  }

  function syncSelection() {
    ["docs", "results"].forEach(function (kind) {
      var set = selected[kind];
      boxes(kind).forEach(function (box) {
        box.checked = Boolean(set[box.value]);
        var holder = box.closest(".doc-card, tr, .result-row");
        if (holder) holder.classList.toggle("is-selected", box.checked);
      });
      // Quitar de la selección lo que ya no está en la página.
      var present = {};
      boxes(kind).forEach(function (box) { present[box.value] = true; });
      Object.keys(set).forEach(function (value) { if (!present[value]) delete set[value]; });

      var count = Object.keys(set).length;
      var bar = $('[data-selbar="' + kind + '"]');
      if (bar) {
        bar.hidden = count === 0;
        $("[data-selbar-count]", bar).textContent = plural(count, "seleccionado");
      }
      if (kind === "docs") {
        var grid = $(".doc-grid");
        if (grid) grid.classList.toggle("has-selection", count > 0);
        var all = $('[data-select-all="docs"]');
        if (all) {
          var total = Object.keys(present).length;
          all.checked = total > 0 && count === total;
          all.indeterminate = count > 0 && count < total;
        }
      }
    });
    $$("[data-select-group]").forEach(function (groupBox) {
      var inGroup = $$('[data-select="results"]', groupBox.closest("[data-group]"));
      var on = inGroup.filter(function (box) { return box.checked; }).length;
      groupBox.checked = inGroup.length > 0 && on === inGroup.length;
      groupBox.indeterminate = on > 0 && on < inGroup.length;
    });
  }

  function clearSelection(kind) {
    (kind ? [kind] : ["docs", "results"]).forEach(function (k) { selected[k] = {}; });
    syncSelection();
  }

  document.addEventListener("change", function (event) {
    var box = event.target;
    if (box.matches("[data-select]")) {
      if (box.checked) selected[box.dataset.select][box.value] = true;
      else delete selected[box.dataset.select][box.value];
      syncSelection();
    } else if (box.matches('[data-select-all="docs"]')) {
      boxes("docs").forEach(function (b) {
        if (box.checked) selected.docs[b.value] = true;
        else delete selected.docs[b.value];
      });
      syncSelection();
    } else if (box.matches("[data-select-group]")) {
      $$('[data-select="results"]', box.closest("[data-group]")).forEach(function (b) {
        if (box.checked) selected.results[b.value] = true;
        else delete selected.results[b.value];
      });
      syncSelection();
    } else if (box.matches("[data-autosubmit]")) {
      box.form.submit();
    }
  });

  document.addEventListener("click", function (event) {
    var clear = event.target.closest("[data-selbar-clear]");
    if (clear) {
      clearSelection(clear.closest("[data-selbar]").dataset.selbar);
      return;
    }
    var moveOne = event.target.closest("[data-move-one]");
    if (moveOne) {
      selected.results = {};
      selected.results[moveOne.dataset.moveOne] = true;
      syncSelection();
      window.notCr.openDialog("move-dialog");
    }
  });

  // ── Diálogos de bulk ───────────────────────────────────────────────────────

  function selectedDocs() {
    var names = Object.keys(selected.docs);
    return names.map(function (name) {
      var tile = $('[data-doc="' + CSS.escape(name) + '"]');
      return {
        name: name,
        pages: tile ? Number(tile.dataset.pages || 0) : 0,
        results: tile ? Number(tile.dataset.results || 0) : 0,
        busy: tile ? tile.hasAttribute("data-busy") : false
      };
    });
  }

  function onDialog(id, onOpen, onClose) {
    var dialog = document.getElementById(id);
    if (!dialog) return;
    dialog.addEventListener("dialog:open", function () { onOpen(dialog); });
    if (onClose) dialog.addEventListener("close", function () { onClose(dialog); });
  }

  onDialog("bulk-process-dialog", function (dialog) {
    var docs = selectedDocs();
    var pages = docs.reduce(function (sum, d) { return sum + d.pages; }, 0);
    $("[data-bulk-summary]", dialog).textContent =
      plural(docs.length, "documento") + (pages ? " · " + plural(pages, "página") : "");
    $("[data-bulk-process-button]", dialog).lastChild.textContent = " Procesar " + plural(docs.length, "documento");
  });

  function updateDeleteDocsButton(dialog) {
    var docs = selectedDocs().filter(function (d) { return !d.busy; });
    var results = docs.reduce(function (sum, d) { return sum + d.results; }, 0);
    var all = $('[data-delete-choice][value="delete_all"]', dialog).checked;
    var button = $("[data-bulk-delete-button]", dialog);
    button.textContent = "Borrar " + plural(docs.length, "PDF", "PDFs") +
      (all && results ? " y " + plural(results, "resultado") : "");
    button.disabled = docs.length === 0;
  }

  onDialog("bulk-delete-docs-dialog", function (dialog) {
    var docs = selectedDocs();
    var skipped = docs.filter(function (d) { return d.busy; });
    var deletable = docs.filter(function (d) { return !d.busy; });
    var results = deletable.reduce(function (sum, d) { return sum + d.results; }, 0);
    $("[data-bulk-delete-title]", dialog).textContent = "Borrar " + plural(deletable.length, "documento");
    $("[data-text-pdf]", dialog).textContent = results
      ? "Se conservan sus " + plural(results, "resultado") + "."
      : "No tienen resultados.";
    $("[data-text-all]", dialog).textContent = "Se borran " + plural(deletable.length, "PDF", "PDFs") +
      " y " + plural(results, "resultado") + ". No se puede deshacer.";
    var skippedBox = $("[data-skipped]", dialog);
    skippedBox.hidden = !skipped.length;
    $("[data-skipped-list]", dialog).innerHTML = skipped.map(function (d) {
      return "<li>" + escapeHtml(d.name) + "</li>";
    }).join("");
    $$("[data-delete-choice]", dialog).forEach(function (radio) { radio.disabled = false; });
    $("[data-bulk-confirm]", dialog).disabled = false;
    updateDeleteDocsButton(dialog);
  }, function (dialog) {
    $$("[data-delete-choice]", dialog).forEach(function (radio) { radio.disabled = true; });
    $("[data-bulk-confirm]", dialog).disabled = true;
  });

  var deleteDocsDialog = document.getElementById("bulk-delete-docs-dialog");
  if (deleteDocsDialog) {
    deleteDocsDialog.addEventListener("change", function () { updateDeleteDocsButton(deleteDocsDialog); });
  }

  onDialog("bulk-delete-results-dialog", function (dialog) {
    var n = Object.keys(selected.results).length;
    $("[data-results-delete-title]", dialog).textContent = "Borrar " + plural(n, "resultado");
    $("[data-results-delete-button]", dialog).textContent = "Borrar " + plural(n, "resultado");
    $("[data-results-confirm]", dialog).disabled = false;
  }, function (dialog) {
    $("[data-results-confirm]", dialog).disabled = true;
  });

  onDialog("move-dialog", function (dialog) {
    var n = Object.keys(selected.results).length;
    $("[data-move-title]", dialog).textContent = "Mover " + plural(n, "resultado") + " a…";
    setTimeout(function () { $("[data-move-folder]", dialog).focus(); }, 0);
  });

  // ── Vista cuadrícula / lista ───────────────────────────────────────────────

  var VIEW_KEY = "not-cr-docs-view";

  function setView(view) {
    $$("[data-view]").forEach(function (el) { el.hidden = el.dataset.view !== view; });
    $$("[data-view-set]").forEach(function (button) {
      button.setAttribute("aria-pressed", button.dataset.viewSet === view ? "true" : "false");
    });
  }

  if ($("[data-view]")) {
    var savedView = "grid";
    try { savedView = localStorage.getItem(VIEW_KEY) === "list" ? "list" : "grid"; } catch (e) {}
    setView(savedView);
    document.addEventListener("click", function (event) {
      var button = event.target.closest("[data-view-set]");
      if (!button) return;
      setView(button.dataset.viewSet);
      try { localStorage.setItem(VIEW_KEY, button.dataset.viewSet); } catch (e) {}
    });
  }

  // ── Subida por XHR, con arrastrar y soltar ─────────────────────────────────

  var uploadForm = $("#upload-form");
  var fileInput = $("#file-input");
  var uploadsPanel = $("[data-uploads]");
  var uploadsList = $("[data-uploads-list]");
  var veil = $("[data-dropveil]");

  function uploadOne(file) {
    return new Promise(function (resolve) {
      var item = document.createElement("li");
      item.className = "uploads__item";
      item.innerHTML = '<span class="name"></span><span class="state num">0%</span>' + progressHtml(0, "", "");
      $(".name", item).textContent = file.name;
      $(".name", item).title = file.name;
      uploadsList.appendChild(item);
      var state = $(".state", item);
      var bar = $(".progress__bar", item);

      var data = new FormData();
      data.append("files", file, file.name);
      var xhr = new XMLHttpRequest();
      xhr.open("POST", uploadForm.action);
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.onprogress = function (event) {
        if (!event.lengthComputable) return;
        var percent = Math.round(100 * event.loaded / event.total);
        bar.style.width = percent + "%";
        state.textContent = percent + "%";
      };
      function finish(ok, reason) {
        bar.style.width = "100%";
        bar.parentNode.classList.add(ok ? "progress--done" : "progress--error");
        state.textContent = ok ? "✓" : "✗";
        state.className = "state " + (ok ? "ok" : "ko");
        if (!ok) {
          var why = document.createElement("span");
          why.className = "reason";
          why.textContent = reason;
          item.appendChild(why);
        }
        resolve(ok);
      }
      xhr.onload = function () {
        var body = null;
        try { body = JSON.parse(xhr.responseText); } catch (e) {}
        if (body && body.ok) finish(true);
        else finish(false, (body && body.error) || "Error " + xhr.status);
      };
      xhr.onerror = function () { finish(false, "Error de red"); };
      xhr.send(data);
    });
  }

  function uploadFiles(files) {
    files = Array.prototype.slice.call(files || []);
    if (!files.length || !uploadsPanel) return;
    uploadsPanel.hidden = false;
    $("[data-uploads-title]").textContent = "Subiendo " + plural(files.length, "archivo");
    // De dos en dos para no saturar la conexión.
    var queue = files.slice();
    var results = [];
    function next() {
      var file = queue.shift();
      if (!file) return Promise.resolve();
      return uploadOne(file).then(function (ok) { results.push(ok); return next(); });
    }
    Promise.all([next(), next()]).then(function () {
      var ok = results.filter(Boolean).length;
      var failed = results.length - ok;
      $("[data-uploads-title]").textContent = plural(ok, "subido") + (failed ? " · " + plural(failed, "rechazado") : "");
      if (ok) setTimeout(function () { location.reload(); }, failed ? 2500 : 800);
    });
  }

  if (uploadForm && fileInput) {
    fileInput.classList.add("sr-only");
    fileInput.addEventListener("change", function () {
      uploadFiles(fileInput.files);
      fileInput.value = "";
    });
    $("[data-uploads-close]").addEventListener("click", function () {
      uploadsPanel.hidden = true;
      uploadsList.innerHTML = "";
    });
    var label = $('label[for="file-input"]');
    if (label) {
      label.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          fileInput.click();
        }
      });
    }

    var dragDepth = 0;
    function hasFiles(event) {
      return event.dataTransfer && Array.prototype.indexOf.call(event.dataTransfer.types, "Files") >= 0;
    }
    window.addEventListener("dragenter", function (event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      dragDepth++;
      veil.hidden = false;
    });
    window.addEventListener("dragover", function (event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    window.addEventListener("dragleave", function (event) {
      if (!hasFiles(event)) return;
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) veil.hidden = true;
    });
    window.addEventListener("drop", function (event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      dragDepth = 0;
      veil.hidden = true;
      uploadFiles(event.dataTransfer.files);
    });
  }

  // ── Atajos de teclado ──────────────────────────────────────────────────────

  function typing(target) {
    return target.closest("input, textarea, select, [contenteditable]") !== null;
  }

  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (document.querySelector("dialog[open]")) return;
    if (event.key === "/" && !typing(event.target)) {
      var search = $("[data-search]");
      if (search) {
        event.preventDefault();
        search.focus();
        search.select();
      }
    } else if (event.key === "u" && !typing(event.target) && fileInput) {
      event.preventDefault();
      fileInput.click();
    } else if (event.key === "Escape") {
      if (typing(event.target) && event.target.matches("[data-search]")) {
        event.target.blur();
      } else {
        clearSelection();
      }
    }
  });

  // Controles sin JS que app.js ya desactivó al cargar; tras refrescar secciones hay que repetirlo.
  function disableNoJs() {
    $$(".nojs-only").forEach(function (block) {
      if (block.closest("form[data-confirm]")) {
        $$("input[name=confirm]", block).forEach(function (input) { input.required = false; });
        return;
      }
      $$("input, select, textarea, button", block).forEach(function (control) { control.disabled = true; });
    });
  }

  syncSelection();
})();
