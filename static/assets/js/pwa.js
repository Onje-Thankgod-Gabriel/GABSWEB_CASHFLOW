/* Gabsweb CashFlow — PWA registration, installation and update lifecycle */
(function () {
  "use strict";

  if (window.__gabswebPwaInitialized) return;
  window.__gabswebPwaInitialized = true;

  var STORAGE_KEY = "gabsweb-pwa-install-v1";
  var SESSION_KEY = "gabsweb-pwa-auto-shown";
  var DISMISS_WINDOW_DAYS = 30;
  var MAX_AUTO_DISMISSALS = 3;
  var INSTALL_CHECK_INTERVAL = 60000;
  var UPDATE_CHECK_INTERVAL = 60 * 60 * 1000;
  var SERVICE_WORKER_URL = "/sw.js";

  var deferredPrompt = null;
  var registration = null;
  var waitingWorker = null;
  var reloadRequested = false;
  var autoPromptDone = false;
  var userInteracted = false;
  var initialized = false;
  var toastTimer = null;
  var dialog = null;
  var lastInstallCheck = 0;
  var lastUpdateCheck = 0;

  function byId(id) {
    return document.getElementById(id);
  }

  function renderIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function readState() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      var parsed = raw ? JSON.parse(raw) : null;
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? parsed
        : {};
    } catch (error) {
      return {};
    }
  }

  function writeState(patch) {
    var state = readState();
    Object.keys(patch).forEach(function (key) {
      state[key] = patch[key];
    });
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (error) {
      /* Storage can be unavailable in private modes; the menu still works. */
    }
    return state;
  }

  function isStandalone() {
    return (
      (window.matchMedia &&
        window.matchMedia("(display-mode: standalone)").matches) ||
      window.navigator.standalone === true
    );
  }

  function isInstalled() {
    return isStandalone() || readState().completed === true;
  }

  function userAgent() {
    return window.navigator.userAgent || "";
  }

  function isIos() {
    var ua = userAgent();
    if (/iphone|ipad|ipod/i.test(ua)) return true;
    return /macintosh/i.test(ua) && window.navigator.maxTouchPoints > 1;
  }

  function detectPlatform() {
    var ua = userAgent();
    if (isIos()) return "ios";
    if (/samsungbrowser/i.test(ua)) return "samsung";
    if (/firefox|fxios/i.test(ua)) return "firefox";
    if (/edg\//i.test(ua) || /chrome\//i.test(ua) || /chromium/i.test(ua)) {
      return "chromium";
    }
    if (/safari\//i.test(ua)) return "safari";
    return "unknown";
  }

  function isAppShell() {
    return Boolean(byId("pwa-install-dialog") && byId("sidebar"));
  }

  function markInstalled() {
    writeState({ completed: true, installedAt: Date.now() });
    updateInstallMenu();
    if (dialog && dialog.open) closeDialog(false);
  }

  function hasInstalledRelatedApp() {
    if (typeof navigator.getInstalledRelatedApps !== "function") {
      return Promise.resolve(null);
    }
    return navigator
      .getInstalledRelatedApps()
      .then(function (apps) {
        return Array.isArray(apps) && apps.length > 0;
      })
      .catch(function () {
        return null;
      });
  }

  function refreshInstallState(source) {
    if (isStandalone()) {
      markInstalled();
      return Promise.resolve(true);
    }

    var now = Date.now();
    if (
      source === "visibility" &&
      now - lastInstallCheck < INSTALL_CHECK_INTERVAL
    ) {
      return Promise.resolve(isInstalled());
    }
    lastInstallCheck = now;

    return hasInstalledRelatedApp().then(function (installed) {
      if (installed === true) {
        writeState({ completed: true });
        updateInstallMenu();
        return true;
      }

      // The browser cannot keep reporting an app that was uninstalled. If the
      // previous "completed" flag is stale, clear it so the install journey
      // returns on the next visit. When the platform has no related-app API,
      // a normal browser visit is treated as an opportunity to install again.
      var state = readState();
      if (state.completed === true) {
        writeState({
          completed: false,
          dismissedAt: 0,
          dismissCount: 0
        });
      }
      updateInstallMenu();
      if (!isInstalled()) maybeAutoPrompt(1200);
      return false;
    });
  }

  function recordDismissal() {
    var state = readState();
    writeState({
      dismissedAt: Date.now(),
      dismissCount: (Number(state.dismissCount) || 0) + 1
    });
  }

  function canAutoPrompt() {
    var state = readState();
    if (!isAppShell() || isInstalled()) return false;
    if (state.never || state.completed) return false;
    if ((Number(state.dismissCount) || 0) >= MAX_AUTO_DISMISSALS) return false;
    if (
      state.dismissedAt &&
      Date.now() - Number(state.dismissedAt) <
        DISMISS_WINDOW_DAYS * 24 * 60 * 60 * 1000
    ) {
      return false;
    }
    if (autoPromptDone) return false;
    try {
      if (window.sessionStorage.getItem(SESSION_KEY) === "1") return false;
    } catch (error) {
      /* Ignore unavailable session storage. */
    }
    return true;
  }

  function markAutoShown() {
    autoPromptDone = true;
    try {
      window.sessionStorage.setItem(SESSION_KEY, "1");
    } catch (error) {
      /* Ignore unavailable session storage. */
    }
  }

  function showToast(message) {
    var toast = byId("pwa-toast");
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toast.hidden = true;
      toastTimer = null;
    }, 4500);
  }

  function setSteps(title, steps) {
    var wrapper = byId("pwa-install-steps");
    var heading = byId("pwa-steps-title");
    var list = byId("pwa-steps-list");
    if (!wrapper || !heading || !list) return;
    if (!steps || !steps.length) {
      wrapper.hidden = true;
      list.innerHTML = "";
      return;
    }
    heading.textContent = title;
    list.innerHTML = steps
      .map(function (step) {
        return "<li>" + step + "</li>";
      })
      .join("");
    wrapper.hidden = false;
  }

  function resetDialog(platform) {
    var copy = byId("pwa-install-copy");
    var confirm = byId("pwa-install-confirm");
    var steps = [];
    var stepsTitle = "";

    if (confirm) confirm.hidden = !deferredPrompt;

    if (deferredPrompt) {
      copy.textContent =
        "Add Gabsweb CashFlow to your device for a faster, full-screen way to open your financial workspace.";
    } else if (platform === "ios") {
      copy.textContent =
        "Safari and other browsers on iPhone and iPad add apps through the Share menu. These steps take a moment and keep your data protected.";
      stepsTitle = "Add to Home Screen";
      steps = [
        "Open the <strong>Share</strong> menu in your browser.",
        "Choose <strong>Add to Home Screen</strong>.",
        "Tap <strong>Add</strong> to confirm."
      ];
    } else if (platform === "safari") {
      copy.textContent =
        "Safari on macOS can keep Gabsweb CashFlow in the Dock like an app. Use the File menu to add it.";
      stepsTitle = "Add to the Dock";
      steps = [
        "Open the <strong>File</strong> menu in Safari.",
        "Choose <strong>Add to Dock</strong>.",
        "Confirm to add Gabsweb CashFlow."
      ];
    } else if (platform === "samsung") {
      copy.textContent =
        "Samsung Internet can add Gabsweb CashFlow to your home screen from the browser menu.";
      stepsTitle = "Add to Home screen";
      steps = [
        "Open the browser menu.",
        "Choose <strong>Add page to</strong> then <strong>Home screen</strong>.",
        "Confirm the installation."
      ];
    } else if (platform === "firefox") {
      copy.textContent =
        "This browser does not offer web app installation here. Use Chrome, Edge or Safari to install Gabsweb CashFlow. You can keep using the app in this tab.";
      steps = [];
    } else if (platform === "chromium") {
      copy.textContent =
        "Your browser has not offered an automatic install prompt yet. You can install Gabsweb CashFlow from the browser menu.";
      stepsTitle = "Install from your browser";
      steps = [
        "Open the browser menu.",
        "Choose <strong>Install app</strong> or <strong>Add to Home screen</strong>.",
        "Confirm the installation."
      ];
    } else {
      copy.textContent =
        "Adding Gabsweb CashFlow to your device makes it quicker to open. If your browser supports installation, it appears in the browser menu.";
      stepsTitle = "Install from your browser";
      steps = [
        "Open your browser menu.",
        "Look for <strong>Install app</strong> or <strong>Add to Home screen</strong>.",
        "Confirm the installation."
      ];
    }

    setSteps(stepsTitle, steps);
    renderIcons();
  }

  function openInstallDialog(source) {
    if (!dialog) return;
    if (isInstalled()) {
      writeState({ completed: true });
      updateInstallMenu();
      showToast("Gabsweb CashFlow is already installed on this device.");
      return;
    }
    if (dialog.open) return;
    if (source === "auto") markAutoShown();
    resetDialog(detectPlatform());
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    renderIcons();
  }

  function closeDialog(record) {
    if (!dialog) return;
    if (dialog.open && typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
    if (record) recordDismissal();
  }

  function updateInstallMenu() {
    var row = byId("pwa-install-menu-row");
    if (!row) return;
    row.hidden = isInstalled();
  }

  function maybeAutoPrompt(delay) {
    if (!canAutoPrompt()) return;
    window.setTimeout(function () {
      if (!canAutoPrompt()) return;
      if (!deferredPrompt && !isIos()) return;
      if (userInteracted) return;
      if (document.querySelector("dialog[open]")) return;
      openInstallDialog("auto");
    }, delay);
  }

  function onBeforeInstallPrompt(event) {
    if (event && typeof event.preventDefault === "function") {
      event.preventDefault();
    }
    deferredPrompt = event;
    // Browsers only offer this prompt while the app is not installed, so a
    // stale "completed" flag (for example after the app was uninstalled) must
    // not keep hiding the installation guidance from the user menu.
    if (readState().completed === true) {
      writeState({ completed: false });
    }
    if (initialized) {
      updateInstallMenu();
      maybeAutoPrompt(1200);
    }
  }

  function onAppInstalled() {
    deferredPrompt = null;
    markInstalled();
    showToast("Gabsweb CashFlow is installed.");
  }

  function setupInstallUI() {
    dialog = byId("pwa-install-dialog");
    if (!dialog) return;

    updateInstallMenu();

    dialog.querySelectorAll("[data-pwa-close]").forEach(function (button) {
      button.addEventListener("click", function () {
        closeDialog(true);
      });
    });

    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) closeDialog(true);
    });

    dialog.addEventListener("cancel", function () {
      recordDismissal();
    });

    var confirm = byId("pwa-install-confirm");
    if (confirm) {
      confirm.addEventListener("click", function () {
        if (!deferredPrompt || typeof deferredPrompt.prompt !== "function") {
          resetDialog(detectPlatform());
          return;
        }
        var promptEvent = deferredPrompt;
        deferredPrompt = null;
        if (confirm) confirm.hidden = true;
        promptEvent.prompt();
        promptEvent.userChoice
          .then(function (choice) {
            if (choice && choice.outcome === "accepted") {
              showToast(
                "Installation started. Gabsweb CashFlow will appear on your device shortly."
              );
              closeDialog(false);
            } else {
              closeDialog(true);
            }
            updateInstallMenu();
          })
          .catch(function () {
            closeDialog(true);
            updateInstallMenu();
          });
      });
    }

    var dontAsk = byId("pwa-dont-ask");
    if (dontAsk) {
      dontAsk.addEventListener("click", function () {
        writeState({ never: true, dismissedAt: Date.now() });
        closeDialog(false);
      });
    }

    var menuItem = byId("pwa-install-menu-item");
    if (menuItem) {
      menuItem.addEventListener("click", function () {
        openInstallDialog("menu");
      });
    }

    if (window.matchMedia) {
      var displayMode = window.matchMedia("(display-mode: standalone)");
      var onDisplayModeChange = function (event) {
        if (event.matches) markInstalled();
      };
      if (typeof displayMode.addEventListener === "function") {
        displayMode.addEventListener("change", onDisplayModeChange);
      }
    }

    if (isInstalled()) {
      markInstalled();
      refreshInstallState("init");
    } else {
      maybeAutoPrompt(2800);
      refreshInstallState("init");
    }
  }

  function setupInteractionDetection() {
    ["keydown", "input", "pointerdown"].forEach(function (eventName) {
      document.addEventListener(
        eventName,
        function () {
          userInteracted = true;
        },
        { passive: true }
      );
    });
  }

  function showUpdate(reg) {
    registration = reg || registration;
    waitingWorker = (registration && registration.waiting) || waitingWorker;
    var banner = byId("pwa-update-banner");
    if (!banner || !waitingWorker) return;
    banner.hidden = false;
    renderIcons();
  }

  function checkWaitingWorker() {
    if (!("serviceWorker" in navigator)) return;
    if (typeof navigator.serviceWorker.getRegistration !== "function") return;
    navigator.serviceWorker
      .getRegistration("/")
      .then(function (reg) {
        if (!reg || !reg.waiting || !navigator.serviceWorker.controller) {
          return;
        }
        showUpdate(reg);
      })
      .catch(function () {
        /* Registration lookup failures must never interrupt the page. */
      });
  }

  function checkForUpdate(force) {
    if (!registration || typeof registration.update !== "function") return;
    var now = Date.now();
    if (!force && now - lastUpdateCheck < UPDATE_CHECK_INTERVAL) return;
    lastUpdateCheck = now;
    // The browser compares /sw.js byte-for-byte. A new deployment therefore
    // installs a waiting worker without any user action; activation still
    // waits for the explicit Refresh button.
    registration.update().catch(function () {
      /* A failed background check must never interrupt the page. */
    });
  }

  function setupUpdateUI() {
    var banner = byId("pwa-update-banner");
    var action = byId("pwa-update-action");
    var dismiss = byId("pwa-update-dismiss");
    if (!banner) return;

    if (action) {
      action.addEventListener("click", function () {
        var worker =
          waitingWorker ||
          (registration && registration.waiting) ||
          null;
        if (!worker) {
          banner.hidden = true;
          return;
        }
        reloadRequested = true;
        banner.hidden = true;
        worker.postMessage({ type: "SKIP_WAITING" });
      });
    }

    if (dismiss) {
      dismiss.addEventListener("click", function () {
        banner.hidden = true;
      });
    }

    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState !== "visible") return;
      checkWaitingWorker();
      checkForUpdate(false);
      refreshInstallState("visibility");
    });

    // Installed PWAs can stay open for days. Keep checking periodically so a
    // deployment is noticed without the user closing and reopening the app.
    window.setInterval(function () {
      checkForUpdate(false);
    }, UPDATE_CHECK_INTERVAL);
  }

  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    if (!window.isSecureContext) return;

    navigator.serviceWorker.addEventListener("controllerchange", function () {
      if (!reloadRequested) return;
      reloadRequested = false;
      window.location.reload();
    });

    window.addEventListener(
      "load",
      function () {
        navigator.serviceWorker
          .register(SERVICE_WORKER_URL, { scope: "/" })
          .then(function (reg) {
            registration = reg;
            lastUpdateCheck = Date.now();

            if (reg.waiting && navigator.serviceWorker.controller) {
              showUpdate(reg);
            }

            // Check once shortly after registration as well as on the hourly
            // schedule, so a long-lived installed app still sees deployments.
            checkForUpdate(true);

            reg.addEventListener("updatefound", function () {
              var installing = reg.installing;
              if (!installing) return;
              installing.addEventListener("statechange", function () {
                if (
                  installing.state === "installed" &&
                  navigator.serviceWorker.controller
                ) {
                  showUpdate(reg);
                }
              });
            });
          })
          .catch(function () {
            /* Registration failures must never block the application. */
          });
      },
      { once: true }
    );
  }

  function init() {
    initialized = true;
    setupInteractionDetection();
    setupInstallUI();
    setupUpdateUI();
    registerServiceWorker();
    if (deferredPrompt && isAppShell() && !isInstalled()) {
      maybeAutoPrompt(1200);
    }
  }

  /* Capture the prompt as early as possible so a fast browser event is not
     missed while the document is still loading. */
  window.addEventListener("beforeinstallprompt", onBeforeInstallPrompt);
  window.addEventListener("appinstalled", onAppInstalled);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
