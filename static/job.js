(() => {
    const page = document.querySelector(".job-page");
    if (!page) return;

    const indicator = document.querySelector("#job-indicator");
    const check = document.querySelector("#job-check");
    const clock = document.querySelector("#job-clock");
    const step = document.querySelector("#job-step");
    const fill = document.querySelector("#progress-fill");
    const live = document.querySelector("#live-text");
    const log = document.querySelector("#job-log");
    const usageLine = document.querySelector("#usage-line");
    const final = document.querySelector("#job-final");
    const errorBox = document.querySelector("#job-error");
    const pricing = JSON.parse(page.dataset.pricing || "null");
    const usage = [];
    let finished = false;

    function parse(event) {
        try {
            return JSON.parse(event.data);
        } catch (_error) {
            return event.data;
        }
    }

    function elapsed() {
        const start = Number(page.dataset.started) * 1000;
        const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
        const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
        const remainder = (seconds % 60).toString().padStart(2, "0");
        clock.textContent = `${minutes}:${remainder}`;
    }

    function renderUsage() {
        const totals = usage.reduce((result, item) => ({
            prompt: result.prompt + Number(item.prompt_tokens || 0),
            completion: result.completion + Number(item.completion_tokens || 0),
        }), { prompt: 0, completion: 0 });
        if (!totals.prompt && !totals.completion) {
            usageLine.hidden = true;
            return;
        }
        let text = `${totals.prompt + totals.completion} tokens (${totals.prompt} entrada · ${totals.completion} salida)`;
        if (pricing && pricing.prompt != null && pricing.completion != null) {
            const cost = totals.prompt * Number(pricing.prompt) + totals.completion * Number(pricing.completion);
            if (Number.isFinite(cost)) text += ` · coste estimado $${cost.toFixed(4)}`;
        }
        usageLine.textContent = text;
        usageLine.hidden = false;
    }

    function addLog(value) {
        const line = document.createElement("div");
        line.textContent = value;
        log.append(line);
        log.scrollTop = log.scrollHeight;
    }

    function finish() {
        finished = true;
        indicator.hidden = true;
        check.hidden = false;
        elapsed();
    }

    const source = new EventSource(page.dataset.streamUrl);
    source.addEventListener("log", (event) => addLog(parse(event)));
    source.addEventListener("chunk", (event) => { live.textContent += String(parse(event)); });
    source.addEventListener("status", (event) => {
        const value = parse(event);
        if (!value || typeof value !== "object") return;
        if (value.step === "converting") step.textContent = "Convirtiendo PDF a imágenes";
        if (value.step === "batch") {
            if (value.index === 0) step.textContent = `${value.total} lotes preparados`;
            else step.textContent = `Lote ${value.index}/${value.total}`;
            if (value.total) fill.style.width = `${Math.round((value.index / value.total) * 100)}%`;
        }
        if (value.step === "saving") step.textContent = "Guardando a Markdown";
    });
    source.addEventListener("usage", (event) => {
        const value = parse(event);
        if (value && typeof value === "object") usage.push(value);
        renderUsage();
    });
    source.addEventListener("done", (event) => {
        const value = parse(event) || {};
        finish();
        step.textContent = "Listo";
        const output = String(value.output || "");
        const outputUrl = page.dataset.outputBase.replace("__OUTPUT__", encodeURIComponent(output));
        final.innerHTML = `<strong>Procesamiento terminado.</strong> <a class="btn btn--primary" href="${outputUrl}">Ver resultado</a>`;
        final.hidden = false;
        source.close();
    });
    source.addEventListener("error", (event) => {
        const value = parse(event);
        finish();
        indicator.hidden = true;
        check.hidden = true;
        errorBox.textContent = `No se pudo procesar: ${value}`;
        errorBox.hidden = false;
        source.close();
    });

    elapsed();
    const timer = window.setInterval(() => {
        elapsed();
        if (finished) window.clearInterval(timer);
    }, 1000);
})();
