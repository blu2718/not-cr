// Comportamiento compartido del shell: toasts, diálogos, confirmación de borrados,
// menús ⋯ y panel lateral en móvil.
(function () {
  "use strict";

  var toastsBox = document.querySelector("[data-toasts]");

  function toast(message, kind) {
    var el = document.createElement("div");
    el.className = "toast toast--" + (kind === "error" ? "error" : "success");
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    var text = document.createElement("div");
    text.className = "toast__text";
    text.textContent = message;
    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn btn--ghost btn--icon";
    close.setAttribute("data-toast-close", "");
    close.setAttribute("aria-label", "Cerrar");
    close.textContent = "×";
    el.appendChild(text);
    el.appendChild(close);
    toastsBox.appendChild(el);
    setupToast(el);
  }

  function setupToast(el) {
    if (el.classList.contains("toast--success")) {
      setTimeout(function () { el.remove(); }, 5000);
    }
  }

  document.querySelectorAll(".toast").forEach(setupToast);

  // ── Diálogos ──
  function openDialog(id) {
    var dialog = document.getElementById(id);
    if (!dialog) return null;
    dialog.dispatchEvent(new CustomEvent("dialog:open", { bubbles: true }));
    if (!dialog.open) dialog.showModal();
    return dialog;
  }

  // ── Formularios con confirmación (data-confirm) ──
  // Sin JS el formulario lleva un checkbox obligatorio «Confirmo el borrado».
  // Con JS se quita el `required` y se confirma en el <dialog> compartido.
  var confirmDialog = document.getElementById("confirm-dialog");
  var confirmSlot = confirmDialog && confirmDialog.querySelector("[data-confirm-slot]");
  var confirmOk = confirmDialog && confirmDialog.querySelector("[data-confirm-ok]");
  var pendingForm = null;
  var movedChoices = null;

  document.querySelectorAll("form[data-confirm] input[name=confirm]").forEach(function (input) {
    input.required = false;
  });

  // Los controles de respaldo sin JS no deben viajar con los formularios.
  document.querySelectorAll(".nojs-only").forEach(function (block) {
    if (block.closest("form[data-confirm]")) return;
    block.querySelectorAll("input, select, textarea, button").forEach(function (control) {
      control.disabled = true;
    });
  });

  function updateConfirmButton() {
    if (!pendingForm) return;
    var label = pendingForm.dataset.confirmButton || "Borrar";
    if (movedChoices) {
      var checked = movedChoices.querySelector("input[type=radio]:checked");
      if (checked && checked.dataset.button) label = checked.dataset.button;
    }
    confirmOk.textContent = label;
  }

  function restoreChoices() {
    if (movedChoices && pendingForm) {
      pendingForm.insertBefore(movedChoices, pendingForm.firstChild);
      movedChoices.classList.add("nojs-only");
    }
    movedChoices = null;
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.matches("form[data-confirm]") || form.dataset.confirmed === "1" || !confirmDialog) return;
    event.preventDefault();
    pendingForm = form;
    confirmDialog.querySelector("[data-confirm-title]").textContent = form.dataset.confirmTitle || "¿Seguro?";
    var text = confirmDialog.querySelector("[data-confirm-text]");
    text.textContent = form.dataset.confirmText || "";
    text.hidden = !form.dataset.confirmText;

    confirmSlot.innerHTML = "";
    var choices = form.querySelector("[data-choices]");
    if (choices) {
      movedChoices = choices;
      choices.classList.remove("nojs-only");
      var confirmBox = choices.querySelector(".confirm-box");
      if (confirmBox) confirmBox.hidden = true;
      confirmSlot.appendChild(choices);
    }
    updateConfirmButton();
    var menu = form.closest("details[open]");
    if (menu) menu.open = false;
    confirmDialog.showModal();
  }, true);

  if (confirmDialog) {
    confirmSlot.addEventListener("change", updateConfirmButton);
    confirmOk.addEventListener("click", function () {
      var form = pendingForm;
      if (!form) return;
      restoreChoices();
      var check = form.querySelector("input[name=confirm]");
      if (check) check.checked = true;
      form.dataset.confirmed = "1";
      confirmDialog.close();
      if (form.requestSubmit) form.requestSubmit();
      else form.submit();
    });
    confirmDialog.addEventListener("close", function () {
      restoreChoices();
      pendingForm = null;
    });
  }

  // ── Clicks delegados ──
  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-dialog-open]");
    if (opener) {
      event.preventDefault();
      var panel = document.getElementById("menu-panel");
      if (panel && panel.open && opener.dataset.dialogOpen !== "menu-panel") panel.close();
      openDialog(opener.dataset.dialogOpen);
      return;
    }
    var closer = event.target.closest("[data-dialog-close]");
    if (closer) {
      var dialog = closer.closest("dialog");
      if (dialog) dialog.close();
      return;
    }
    var toastClose = event.target.closest("[data-toast-close]");
    if (toastClose) {
      toastClose.closest(".toast").remove();
      return;
    }
    // Click en el fondo de un diálogo modal: cerrar.
    if (event.target.tagName === "DIALOG" && event.target.open) {
      var rect = event.target.getBoundingClientRect();
      var inside = event.clientX >= rect.left && event.clientX <= rect.right &&
        event.clientY >= rect.top && event.clientY <= rect.bottom;
      if (!inside) event.target.close();
    }
    // Cerrar menús ⋯ abiertos al hacer click fuera.
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      document.querySelectorAll("details.menu[open]").forEach(function (menu) { menu.open = false; });
    }
  });

  window.notCr = { toast: toast, openDialog: openDialog };
})();
