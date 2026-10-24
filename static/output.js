// Vista de resultado: título editable en el sitio, nota con autoguardado,
// carpeta, vistas Leer / Fuente / Comparar, panel de detalles y «Copiar Markdown».
(function () {
  "use strict";

  var bar = document.querySelector("[data-output]");
  if (!bar) return;
  var editUrl = bar.dataset.editUrl;
  var toast = function (message, kind) { if (window.notCr) window.notCr.toast(message, kind); };

  function save(fields) {
    var body = new FormData();
    Object.keys(fields).forEach(function (key) { body.append(key, fields[key]); });
    return fetch(editUrl, { method: "POST", body: body, headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok || !data.ok) throw new Error(data.error || "HTTP " + response.status);
          return data;
        });
      });
  }

  // ── Título en el sitio ──
  var display = bar.querySelector("[data-title-display]");
  var titleForm = bar.querySelector("[data-title-form]");
  var titleInput = bar.querySelector("[data-title-input]");
  var fallback = titleInput.getAttribute("placeholder");
  var original = titleInput.value;

  function showDisplay() {
    titleForm.hidden = true;
    display.hidden = false;
  }

  display.addEventListener("click", function () {
    display.hidden = true;
    titleForm.hidden = false;
    titleInput.focus();
    titleInput.select();
  });

  function commitTitle() {
    var value = titleInput.value.trim();
    if (value === original) { showDisplay(); return; }
    save({ title: value }).then(function () {
      original = value;
      display.textContent = value || fallback;
      document.title = (value || fallback) + " · not-cr";
      toast("Título guardado");
    }).catch(function (error) {
      toast("No se pudo guardar el título: " + error.message, "error");
      titleInput.value = original;
    }).finally(showDisplay);
  }

  titleForm.addEventListener("submit", function (event) {
    event.preventDefault();
    commitTitle();
  });
  titleInput.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      event.preventDefault();
      titleInput.value = original;
      showDisplay();
    }
  });
  titleInput.addEventListener("blur", function () {
    if (!titleForm.hidden) commitTitle();
  });

  // ── Carpeta ──
  var folderSelect = bar.querySelector("[data-folder-select]");
  var folderLabel = document.querySelector("[data-folder-label]");
  folderSelect.addEventListener("change", function () {
    var value = folderSelect.value;
    save({ folder: value }).then(function () {
      if (folderLabel) folderLabel.textContent = value || "Sin carpeta";
      toast(value ? "Movido a «" + value + "»" : "Movido a «Sin carpeta»");
    }).catch(function (error) {
      toast("No se pudo mover: " + error.message, "error");
    });
  });

  // ── Nota con autoguardado al salir del campo ──
  var note = document.querySelector("[data-note]");
  var noteState = document.querySelector("[data-note-state]");
  var noteForm = document.querySelector("[data-note-form]");
  var savedNote = note ? note.value : "";
  if (note) {
    noteForm.addEventListener("submit", function (event) { event.preventDefault(); note.blur(); });
    note.addEventListener("input", function () {
      noteState.textContent = note.value === savedNote ? "" : "Sin guardar";
    });
    note.addEventListener("blur", function () {
      if (note.value === savedNote) return;
      noteState.textContent = "Guardando…";
      var value = note.value;
      save({ note: value }).then(function () {
        savedNote = value;
        noteState.textContent = "Guardada";
      }).catch(function (error) {
        noteState.textContent = "Error al guardar: " + error.message;
      });
    });
  }

  // ── Copiar Markdown (la app va por HTTP: navigator.clipboard puede no existir) ──
  var source = document.querySelector("[data-source]").value;
  var copyButton = bar.querySelector("[data-copy]");

  function copyFallback(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) {}
    area.remove();
    return ok;
  }

  copyButton.addEventListener("click", function () {
    var done = function () { toast("Markdown copiado"); };
    var fail = function () { toast("No se pudo copiar", "error"); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(source).then(done, function () {
        if (copyFallback(source)) done(); else fail();
      });
    } else if (copyFallback(source)) {
      done();
    } else {
      fail();
    }
  });

  // ── Leer / Fuente / Comparar ──
  var layout = document.querySelector("[data-reader-layout]");
  var readPanel = document.querySelector('[data-panel="read"]');
  var comparePanel = document.querySelector('[data-panel="compare"]');

  function prepareCompare() {
    if (!comparePanel) return;
    var placeholder = comparePanel.querySelector("[data-pdf-src]");
    if (placeholder) {
      var embed = document.createElement("embed");
      embed.className = "pdf-view";
      embed.type = "application/pdf";
      embed.title = "PDF original";
      embed.src = placeholder.dataset.pdfSrc;
      placeholder.replaceWith(embed);
    }
    var text = comparePanel.querySelector("[data-compare-text]");
    if (!text.childElementCount && !text.textContent.trim()) {
      text.innerHTML = readPanel.innerHTML;
      if (window.MathJax && window.MathJax.typesetPromise) window.MathJax.typesetPromise([text]);
    }
  }

  function setView(view) {
    if (view === "compare" && !comparePanel) view = "read";
    document.querySelectorAll("[data-panel]").forEach(function (panel) {
      panel.hidden = panel.dataset.panel !== view;
    });
    document.querySelectorAll("[data-view-tab]").forEach(function (tab) {
      tab.setAttribute("aria-selected", tab.dataset.viewTab === view ? "true" : "false");
    });
    layout.classList.toggle("is-compare", view === "compare");
    if (view === "compare") prepareCompare();
    var url = new URL(location.href);
    if (view === "read") url.searchParams.delete("view");
    else url.searchParams.set("view", view);
    history.replaceState(null, "", url);
  }

  document.querySelectorAll("[data-view-tab]").forEach(function (tab) {
    tab.addEventListener("click", function (event) {
      event.preventDefault();
      setView(tab.dataset.viewTab);
    });
  });

  // ── Panel de detalles plegable ──
  var DETAILS_KEY = "not-cr-details";
  var toggle = bar.querySelector("[data-details-toggle]");

  function setDetails(open) {
    layout.classList.toggle("details-closed", !open);
    toggle.setAttribute("aria-pressed", open ? "true" : "false");
  }

  try { setDetails(localStorage.getItem(DETAILS_KEY) !== "closed"); } catch (e) { setDetails(true); }
  toggle.addEventListener("click", function () {
    var open = layout.classList.contains("details-closed");
    setDetails(open);
    try { localStorage.setItem(DETAILS_KEY, open ? "open" : "closed"); } catch (e) {}
  });
})();
