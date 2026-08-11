(() => {
    document.querySelectorAll("[data-model-combo]").forEach((comboRoot) => {
        const input = comboRoot.querySelector("[data-model-input]");
        const list = comboRoot.querySelector("[role=listbox]");
        const hidden = comboRoot.querySelector("[data-model-value]");
        const meta = comboRoot.querySelector("[data-model-meta]");
        const error = comboRoot.querySelector("[data-model-error]");
        const reasoning = comboRoot.dataset.reasoningId
            ? document.getElementById(comboRoot.dataset.reasoningId)
            : null;
        const reasoningNote = comboRoot.dataset.reasoningNoteId
            ? document.getElementById(comboRoot.dataset.reasoningNoteId)
            : null;
        let models = [];
        let filtered = [];
        let activeIndex = -1;
        let loaded = false;
        let failed = false;
        let focused = false;
        let selectedReasoningModel = null;
        const fallbackReasoningEfforts = ["low", "medium", "high"];

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

        function modelId(model) {
            return String(model.id || "");
        }

        function modelName(model) {
            return String(model.name || modelId(model));
        }

        function selectedModel() {
            return models.find((model) => modelId(model) === hidden.value || modelId(model) === input.value);
        }

        function appendReasoningOption(value, label) {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = label;
            reasoning.append(option);
        }

        function updateReasoningOptions(model, reset) {
            if (!reasoning) return;

            const previous = reasoning.value;
            const efforts = Array.isArray(model.reasoning_efforts)
                ? model.reasoning_efforts
                    .map((effort) => String(effort).toLowerCase())
                    .filter((effort, index, values) => effort && effort !== "none" && values.indexOf(effort) === index)
                : fallbackReasoningEfforts;
            const mandatory = model.reasoning_mandatory === true;
            const defaultEffort = String(model.reasoning_default_effort || "").toLowerCase();

            reasoning.replaceChildren();
            appendReasoningOption(
                "",
                defaultEffort === "none"
                    ? "Por defecto del proveedor (no enviar)"
                    : `Por defecto del proveedor${defaultEffort ? ` (${defaultEffort})` : ""}`
            );
            efforts.forEach((effort) => appendReasoningOption(effort, effort));
            if (!mandatory) appendReasoningOption("off", "No enviar");

            const validValues = new Set(["", ...efforts]);
            if (!mandatory) validValues.add("off");
            reasoning.value = !reset && validValues.has(previous) ? previous : "";
        }

        function updateMetadata() {
            if (!meta) return;
            const model = selectedModel();
            if (!model) {
                meta.textContent = "";
                if (reasoning) reasoning.disabled = false;
                if (reasoningNote) reasoningNote.textContent = "";
                selectedReasoningModel = null;
                return;
            }

            const modelChanged = selectedReasoningModel !== null
                && selectedReasoningModel !== modelId(model);
            if (selectedReasoningModel !== modelId(model)) {
                updateReasoningOptions(model, modelChanged);
                selectedReasoningModel = modelId(model);
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

            if (!reasoning) return;
            if (model.supports_reasoning === false) {
                reasoning.disabled = true;
                reasoning.value = "";
                if (reasoningNote) reasoningNote.textContent = "Este modelo no admite razonamiento.";
            } else {
                reasoning.disabled = false;
                if (reasoningNote) {
                    reasoningNote.textContent = model.reasoning_mandatory === true
                        ? "Este modelo exige razonamiento."
                        : "";
                }
            }
        }

        function closeList() {
            list.hidden = true;
            input.setAttribute("aria-expanded", "false");
            activeIndex = -1;
            input.removeAttribute("aria-activedescendant");
        }

        function openList() {
            if (focused && !failed && filtered.length) {
                list.hidden = false;
                input.setAttribute("aria-expanded", "true");
            }
        }

        function highlightActiveOption() {
            [...list.children].forEach((option, index) => {
                option.classList.toggle("is-active", index === activeIndex);
            });
            if (activeIndex >= 0) {
                const active = list.children[activeIndex];
                input.setAttribute("aria-activedescendant", active.id);
                active.scrollIntoView({ block: "nearest" });
            } else {
                input.removeAttribute("aria-activedescendant");
            }
        }

        function renderOptions() {
            const query = input.value.trim().toLowerCase();
            filtered = models.filter((model) => {
                if (!query) return true;
                return modelId(model).toLowerCase().includes(query)
                    || modelName(model).toLowerCase().includes(query);
            });
            list.replaceChildren();
            filtered.forEach((model, index) => {
                const option = document.createElement("li");
                option.id = `${input.id}-option-${index}`;
                option.setAttribute("role", "option");
                option.dataset.id = modelId(model);
                option.setAttribute("aria-selected", String(modelId(model) === hidden.value));
                const id = document.createElement("span");
                id.className = "option-id";
                id.textContent = modelId(model);
                option.append(id);
                if (modelName(model) !== modelId(model)) {
                    const name = document.createElement("span");
                    name.className = "option-name";
                    name.textContent = modelName(model);
                    option.append(name);
                }
                option.addEventListener("mousedown", (event) => event.preventDefault());
                option.addEventListener("click", () => choose(index));
                list.append(option);
            });
            activeIndex = -1;
            highlightActiveOption();
            openList();
        }

        function choose(index) {
            const model = filtered[index];
            if (!model) return;
            input.value = modelId(model);
            hidden.value = modelId(model);
            updateMetadata();
            closeList();
        }

        async function loadModels() {
            if (loaded || failed) return;
            loaded = true;
            try {
                const response = await fetch(comboRoot.dataset.modelsUrl, { cache: "no-store" });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || "No se pudo cargar el catálogo.");
                models = Array.isArray(data) ? data : [];
                renderOptions();
                updateMetadata();
            } catch (fetchError) {
                failed = true;
                error.hidden = false;
                error.textContent = `Catálogo no disponible: ${fetchError.message}. Puedes escribir el id manualmente.`;
                closeList();
            }
        }

        input.addEventListener("focus", () => {
            focused = true;
            renderOptions();
            openList();
            loadModels();
        });
        input.addEventListener("blur", () => {
            window.setTimeout(() => {
                if (!comboRoot.contains(document.activeElement)) {
                    focused = false;
                    closeList();
                }
            }, 0);
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
            highlightActiveOption();
        });
        document.addEventListener("click", (event) => {
            if (!comboRoot.contains(event.target)) {
                focused = false;
                closeList();
            }
        });

        hidden.value = input.value;
        updateMetadata();
    });
})();
