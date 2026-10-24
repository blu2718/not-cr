// Combobox buscador de modelos (5.4): vacío lista todos; al escribir filtra por
// subcadena en id y nombre. Actualiza la ficha del modelo y el selector de
// razonamiento. Si el catálogo falla, funciona como texto libre.
(function () {
  "use strict";

  var catalogPromise = null;

  function loadCatalog() {
    if (!catalogPromise) {
      catalogPromise = fetch("/api/models", { cache: "no-store", headers: { Accept: "application/json" } })
        .then(function (response) {
          return response.json().then(function (body) {
            if (!response.ok) throw new Error(body && body.error ? body.error : "HTTP " + response.status);
            return body;
          });
        });
    }
    return catalogPromise;
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function highlight(text, query) {
    if (!query) return escapeHtml(text);
    var index = text.toLowerCase().indexOf(query);
    if (index < 0) return escapeHtml(text);
    return escapeHtml(text.slice(0, index)) + "<mark>" +
      escapeHtml(text.slice(index, index + query.length)) + "</mark>" +
      escapeHtml(text.slice(index + query.length));
  }

  function shortPrice(perToken) {
    var value = parseFloat(perToken);
    if (isNaN(value) || value < 0) return "";
    if (value === 0) return "gratis";
    return "$" + (value * 1e6).toFixed(2) + "/M";
  }

  function shortContext(n) {
    if (!n) return "";
    if (n >= 1e6) return Math.round(n / 1e5) / 10 + "M";
    return Math.round(n / 1000) + "k";
  }

  function init(root) {
    var input = root.querySelector("[data-combo-input]");
    var hidden = root.querySelector("[data-combo-value]");
    var list = root.querySelector("[data-combo-list]");
    var sheet = root.querySelector("[data-model-sheet]");
    var note = root.querySelector("[data-combo-note]");
    var scope = root.closest("dialog") || root.closest("form") || root.parentElement;
    var reasoning = scope.querySelector("[data-reasoning]");
    var reasoningNote = scope.querySelector("[data-reasoning-note]");
    var submits = scope.querySelectorAll("[data-needs-vision]");
    var models = null;
    var filtered = [];
    var active = -1;
    var failed = false;
    var MAX_OPTIONS = 600;

    function find(id) {
      if (!models) return null;
      for (var i = 0; i < models.length; i++) if (models[i].id === id) return models[i];
      return null;
    }

    function setActive(index) {
      var options = list.querySelectorAll("[role=option]");
      if (!options.length) { active = -1; return; }
      active = Math.max(0, Math.min(index, options.length - 1));
      options.forEach(function (option, i) {
        option.setAttribute("aria-selected", i === active ? "true" : "false");
      });
      var current = options[active];
      input.setAttribute("aria-activedescendant", current.id);
      current.scrollIntoView({ block: "nearest" });
    }

    function render() {
      if (models === null) {
        list.innerHTML = new Array(6).join('<li class="skeleton" aria-hidden="true"></li>');
        return;
      }
      var query = input.value.trim().toLowerCase();
      // Si el input muestra exactamente el modelo elegido, enseñar todo el catálogo.
      if (query && find(input.value.trim()) && !input.dataset.typed) query = "";
      filtered = !query ? models : models.filter(function (m) {
        return m.id.toLowerCase().indexOf(query) >= 0 || m.name.toLowerCase().indexOf(query) >= 0;
      });
      if (!filtered.length) {
        list.innerHTML = '<li class="combo__empty">Ningún modelo coincide. Se usará el id tal cual.</li>';
        active = -1;
        return;
      }
      var html = [];
      filtered.slice(0, MAX_OPTIONS).forEach(function (m, i) {
        var badges = [];
        if (m.supports_images === true) badges.push('<span class="tag tag--ok">visión</span>');
        if (m.supports_images === false) badges.push('<span class="tag tag--err">sin visión</span>');
        if (m.supports_reasoning === true) badges.push('<span class="tag tag--accent">razonamiento</span>');
        var price = [shortPrice(m.pricing && m.pricing.prompt), shortContext(m.context_length)].filter(Boolean).join(" · ");
        html.push(
          '<li class="combo__opt' + (m.supports_images === false ? " is-novision" : "") + '" role="option" id="' +
          list.id + "-opt-" + i + '" data-index="' + i + '" aria-selected="false">' +
          '<span class="combo__name">' + highlight(m.name, query) + "</span>" +
          '<span class="combo__price">' + escapeHtml(price) + "</span>" +
          '<span class="combo__id">' + highlight(m.id, query) + "</span>" +
          '<span class="combo__badges">' + badges.join("") + "</span></li>"
        );
      });
      if (filtered.length > MAX_OPTIONS) {
        html.push('<li class="combo__empty">… y ' + (filtered.length - MAX_OPTIONS) + " más: sigue escribiendo para filtrar.</li>");
      }
      list.innerHTML = html.join("");
      active = -1;
      if (query) setActive(0);
    }

    function open() {
      if (!list.hidden || failed) return;
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      render();
    }

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }

    function choose(model) {
      input.value = model.id;
      hidden.value = model.id;
      delete input.dataset.typed;
      close();
      update();
    }

    function setSubmitsEnabled(enabled) {
      submits.forEach(function (button) {
        button.disabled = !enabled;
      });
    }

    function rebuildReasoning(model) {
      if (!reasoning) return;
      var configMode = reasoning.hasAttribute("data-config-mode");
      var current = reasoning.value;
      if (model && model.supports_reasoning === false) {
        reasoning.disabled = true;
        if (reasoningNote) reasoningNote.hidden = false;
        return;
      }
      reasoning.disabled = false;
      if (reasoningNote) reasoningNote.hidden = true;
      var efforts = model && model.reasoning_efforts && model.reasoning_efforts.length
        ? model.reasoning_efforts.slice()
        : ["low", "medium", "high"];
      var configDefault = reasoning.dataset.configDefault || "";
      if (configMode && configDefault && efforts.indexOf(configDefault) < 0 && !model) efforts.push(configDefault);

      var options = [];
      if (configMode) {
        options.push(["", "No enviar"]);
      } else {
        var label = "Por defecto (config: " + (configDefault || "no enviar") + ")";
        if (configDefault && model && model.reasoning_efforts && model.reasoning_efforts.length &&
            model.reasoning_efforts.indexOf(configDefault) < 0) {
          label += " · no disponible";
        }
        options.push(["", label]);
      }
      efforts.forEach(function (effort) { options.push([effort, effort]); });
      if (!configMode) options.push(["off", "No enviar"]);

      reasoning.innerHTML = "";
      options.forEach(function (pair) {
        var option = document.createElement("option");
        option.value = pair[0];
        option.textContent = pair[1];
        reasoning.appendChild(option);
      });
      var values = options.map(function (pair) { return pair[0]; });
      reasoning.value = values.indexOf(current) >= 0 ? current : "";
    }

    function update() {
      var id = hidden.value.trim();
      var model = find(id);
      sheet.innerHTML = "";
      function add(html) {
        var span = document.createElement("span");
        span.innerHTML = html;
        sheet.appendChild(span);
      }
      if (model) {
        if (model.price_prompt) add("Entrada " + escapeHtml(model.price_prompt));
        if (model.price_completion) add("Salida " + escapeHtml(model.price_completion));
        if (model.context) add(escapeHtml(model.context));
        if (model.supports_reasoning === true) add('<span class="tag tag--accent">razonamiento</span>');
        if (model.supports_images === false) {
          add('<span class="warn">Este modelo no acepta imágenes; no sirve para OCR</span>');
        }
      } else if (models && models.length && id) {
        add("No está en el catálogo: se enviará el id tal cual.");
      }
      setSubmitsEnabled(!(model && model.supports_images === false) && Boolean(id));
      rebuildReasoning(model);
    }

    input.addEventListener("focus", function () {
      loadCatalog();
      open();
    });
    input.addEventListener("click", open);
    input.addEventListener("input", function () {
      input.dataset.typed = "1";
      hidden.value = input.value.trim();
      list.hidden = true;
      open();
      update();
    });
    input.addEventListener("blur", function () {
      setTimeout(close, 120);
      if (!input.value.trim()) {
        // Vacío al salir → vuelve al modelo por defecto.
        input.value = root.dataset.defaultModel || "";
        hidden.value = input.value;
        update();
      }
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (list.hidden) open();
        setActive(active + 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive(active - 1);
      } else if (event.key === "Enter") {
        if (!list.hidden && active >= 0 && filtered[active]) {
          event.preventDefault();
          choose(filtered[active]);
        } else if (!list.hidden) {
          event.preventDefault();
          close();
        }
      } else if (event.key === "Escape") {
        if (!list.hidden) {
          event.preventDefault();
          event.stopPropagation();
          close();
        }
      }
    });
    list.addEventListener("mousedown", function (event) { event.preventDefault(); });
    list.addEventListener("click", function (event) {
      var option = event.target.closest("[role=option]");
      if (option) choose(filtered[Number(option.dataset.index)]);
    });

    loadCatalog().then(function (data) {
      models = data;
      if (!list.hidden) render();
      update();
    }).catch(function (error) {
      models = [];
      failed = true;
      note.hidden = false;
      note.textContent = "No se pudo cargar el catálogo (" + error.message + "). Escribe el id del modelo a mano.";
      if (!list.hidden) close();
      update();
    });
  }

  document.querySelectorAll("[data-combobox]").forEach(init);
})();
