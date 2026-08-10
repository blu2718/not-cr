(() => {
    const comboRoot = document.querySelector("[data-model-combo]");
    if (!comboRoot) return;

    const input = comboRoot.querySelector("[role=combobox]");
    const list = comboRoot.querySelector("[role=listbox]");
    const hidden = comboRoot.querySelector("input[name=model]");
    const meta = comboRoot.querySelector("#model-meta");
    const error = comboRoot.querySelector("#model-error");
    const reasoning = document.querySelector("select[name=reasoning]");
    const reasoningNote = document.querySelector("#reasoning-note");
    let models = [];
    let filtered = [];
    let activeIndex = -1;
    let loaded = false;
    let failed = false;

    const formatPrice = (value) => {
        const number = Number(value);
        if (!Number.isFinite(number)) return "";
        if (number === 0) return "gratis";
        return `$${(number * 1000000).toFixed(2)} / M tokens`;
    };

    const formatContext = (value) => {
        const number = Number(value);
        if (!Number.isFinite(number)) return "";
        if (number >= 1000000) return `${(number / 1000000).toFixed(1).replace(/\.0$/, "")}m tokens`;
        if (number >= 1000) return `${(number / 1000).toFixed(1).replace(/\.0$/, "")}k tokens`;
        return `${number} tokens`;
    };

    function selectedModel() {
        return models.find((model) => model.id === hidden.value || model.id === input.value);
    }

    function updateMetadata() {
        const model = selectedModel();
        if (!model) {
            meta.textContent = "";
            reasoning.disabled = false;
            reasoningNote.textContent = "";
            return;
        }

        const details = [];
        if (model.pricing) {
            const prompt = formatPrice(model.pricing.prompt);
            const completion = formatPrice(model.pricing.completion);
            if (prompt) details.push(`${prompt} entrada`);
            if (completion) details.push(`${completion} salida`);
        }
        const context = formatContext(model.context_length);
        if (context) details.push(context);
        if (model.supports_reasoning === true) details.push("razonamiento");
        meta.textContent = details.join(" · ");

        if (model.supports_reasoning === false) {
            reasoning.disabled = true;
            reasoning.value = "";
            reasoningNote.textContent = "Este modelo no admite razonamiento.";
        } else {
            reasoning.disabled = false;
            reasoningNote.textContent = "";
        }
    }

    function closeList() {
        list.hidden = true;
        input.setAttribute("aria-expanded", "false");
        activeIndex = -1;
        input.removeAttribute("aria-activedescendant");
    }

    function openList() {
        if (!failed && filtered.length) {
            list.hidden = false;
            input.setAttribute("aria-expanded", "true");
        }
    }

    function renderOptions() {
        const query = input.value.trim().toLowerCase();
        filtered = models.filter((model) => {
            if (!query) return true;
            return model.id.toLowerCase().includes(query) || model.name.toLowerCase().includes(query);
        });
        list.replaceChildren();
        filtered.forEach((model, index) => {
            const option = document.createElement("li");
            option.id = `model-option-${index}`;
            option.setAttribute("role", "option");
            option.dataset.id = model.id;
            option.setAttribute("aria-selected", String(model.id === hidden.value));
            const id = document.createElement("span");
            id.className = "option-id";
            id.textContent = model.id;
            option.append(id);
            if (model.name && model.name !== model.id) {
                const name = document.createElement("span");
                name.className = "option-name";
                name.textContent = model.name;
                option.append(name);
            }
            option.addEventListener("mousedown", (event) => event.preventDefault());
            option.addEventListener("click", () => choose(index));
            list.append(option);
        });
        activeIndex = -1;
        openList();
    }

    function choose(index) {
        const model = filtered[index];
        if (!model) return;
        input.value = model.id;
        hidden.value = model.id;
        updateMetadata();
        closeList();
    }

    async function loadModels() {
        if (loaded || failed) return;
        loaded = true;
        try {
            const response = await fetch(comboRoot.dataset.modelsUrl);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "No se pudo cargar el catálogo.");
            models = Array.isArray(data) ? data : [];
            renderOptions();
            closeList();
            updateMetadata();
        } catch (fetchError) {
            failed = true;
            error.hidden = false;
            error.textContent = `Catálogo no disponible: ${fetchError.message}. Puedes escribir el id manualmente.`;
            closeList();
        }
    }

    input.addEventListener("focus", () => {
        loadModels();
        renderOptions();
    });
    input.addEventListener("input", () => {
        hidden.value = input.value;
        updateMetadata();
        renderOptions();
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown") {
            event.preventDefault();
            if (!filtered.length) return;
            activeIndex = Math.min(activeIndex + 1, filtered.length - 1);
            openList();
        } else if (event.key === "ArrowUp") {
            event.preventDefault();
            if (!filtered.length) return;
            activeIndex = Math.max(activeIndex - 1, 0);
            openList();
        } else if (event.key === "Enter" && activeIndex >= 0) {
            event.preventDefault();
            choose(activeIndex);
        } else if (event.key === "Escape") {
            closeList();
        }
        [...list.children].forEach((option, index) => option.classList.toggle("is-active", index === activeIndex));
        if (activeIndex >= 0) {
            input.setAttribute("aria-activedescendant", `model-option-${activeIndex}`);
        }
    });
    document.addEventListener("click", (event) => {
        if (!comboRoot.contains(event.target)) closeList();
    });

    hidden.value = input.value;
    updateMetadata();
})();
