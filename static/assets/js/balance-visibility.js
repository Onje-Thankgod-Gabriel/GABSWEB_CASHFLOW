(function () {
  "use strict";

  var root = window;
  var STORAGE_PREFIX = "gabsweb-balance-hidden:";
  var TOGGLE_SELECTOR = "[data-balance-visibility-toggle], #db-balance-toggle";
  var hidden = false;

  function metaUserId() {
    var meta = document.querySelector('meta[name="gabsweb-user-id"]');
    return meta ? String(meta.getAttribute("content") || "").trim() : "";
  }

  function userId() {
    var body = document.body;
    var bodyId =
      body && body.dataset ? String(body.dataset.userId || "").trim() : "";
    return bodyId || metaUserId() || "guest";
  }

  function storageKey() {
    return STORAGE_PREFIX + userId();
  }

  function readPreference() {
    try {
      return root.localStorage.getItem(storageKey()) === "1";
    } catch (error) {
      return false;
    }
  }

  function writePreference(value) {
    try {
      root.localStorage.setItem(storageKey(), value ? "1" : "0");
    } catch (error) {
      return;
    }
  }

  function icon(name) {
    return '<i data-lucide="' + name + '" class="w-4 h-4"></i>';
  }

  function renderIcons() {
    if (root.lucide && typeof root.lucide.createIcons === "function") {
      root.lucide.createIcons();
    }
  }

  function updateToggle(toggle) {
    if (!toggle) return;
    toggle.setAttribute("aria-pressed", hidden ? "true" : "false");
    toggle.setAttribute("aria-label", hidden ? "Show balances" : "Hide balances");
    toggle.setAttribute("title", hidden ? "Show balances" : "Hide balances");
    toggle.innerHTML = icon(hidden ? "eye-off" : "eye");
  }

  function updateToggles() {
    document.querySelectorAll(TOGGLE_SELECTOR).forEach(updateToggle);
    renderIcons();
  }

  function applyHidden(value, persist) {
    hidden = Boolean(value);
    document.documentElement.classList.toggle("db-balance-hidden", hidden);
    if (document.body) {
      document.body.classList.toggle("db-balance-hidden", hidden);
    }
    updateToggles();
    if (persist) writePreference(hidden);
    try {
      root.dispatchEvent(
        new CustomEvent("gabsweb:balance-visibility", {
          detail: { hidden: hidden }
        })
      );
    } catch (error) {
      return;
    }
  }

  function bindToggle(toggle) {
    if (!toggle || toggle.dataset.balanceVisibilityBound === "true") return;
    toggle.dataset.balanceVisibilityBound = "true";
    toggle.addEventListener("click", function () {
      applyHidden(!hidden, true);
    });
  }

  function bindToggles() {
    document.querySelectorAll(TOGGLE_SELECTOR).forEach(bindToggle);
    updateToggles();
  }

  function toggle() {
    applyHidden(!hidden, true);
  }

  hidden = readPreference();
  document.documentElement.classList.toggle("db-balance-hidden", hidden);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      hidden = readPreference();
      applyHidden(hidden, false);
      bindToggles();
    });
  } else {
    applyHidden(hidden, false);
    bindToggles();
  }

  root.addEventListener("storage", function (event) {
    if (event.key !== storageKey()) return;
    applyHidden(event.newValue === "1", false);
  });

  root.addEventListener("pageshow", function () {
    applyHidden(readPreference(), false);
    bindToggles();
  });

  root.GabswebBalanceVisibility = {
    isHidden: function () {
      return hidden;
    },
    setHidden: function (value) {
      applyHidden(value, true);
    },
    toggle: toggle,
    refresh: function () {
      applyHidden(readPreference(), false);
      bindToggles();
    }
  };
})();
