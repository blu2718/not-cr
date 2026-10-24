// Selector de tema Sistema / Claro / Oscuro. La elección se guarda en localStorage;
// el script inline de <head> la aplica antes de pintar.
(function () {
  "use strict";
  var KEY = "not-cr-theme";
  var root = document.documentElement;

  function stored() {
    try {
      var value = localStorage.getItem(KEY);
      return value === "light" || value === "dark" ? value : "system";
    } catch (e) {
      return "system";
    }
  }

  function apply(theme) {
    if (theme === "light" || theme === "dark") root.dataset.theme = theme;
    else delete root.dataset.theme;
    document.querySelectorAll("[data-theme-set]").forEach(function (button) {
      button.setAttribute("aria-pressed", button.dataset.themeSet === theme ? "true" : "false");
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-theme-set]");
    if (!button) return;
    var theme = button.dataset.themeSet;
    try {
      if (theme === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch (e) {}
    apply(theme);
  });

  apply(stored());
})();
