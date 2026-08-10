(() => {
    const root = document.documentElement;
    const button = document.querySelector("#theme-toggle");
    if (!button) return;

    function updateButton() {
        const dark = root.dataset.theme === "dark";
        button.setAttribute("aria-pressed", String(dark));
        button.setAttribute("aria-label", dark ? "Activar modo claro" : "Activar modo oscuro");
        button.textContent = dark ? "Modo claro" : "Modo oscuro";
    }

    button.addEventListener("click", () => {
        const dark = root.dataset.theme !== "dark";
        if (dark) root.dataset.theme = "dark";
        else delete root.dataset.theme;
        try {
            localStorage.setItem("not-cr-theme", dark ? "dark" : "light");
        } catch (_error) {}
        updateButton();
    });

    updateButton();
})();
