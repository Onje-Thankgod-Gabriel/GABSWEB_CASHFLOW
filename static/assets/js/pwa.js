/* Gabsweb CashFlow — PWA registration, installation and update lifecycle */
(function () {
  "use strict";

  if (window.__gabswebPwaInitialized) return;
  window.__gabswebPwaInitialized = true;

  var STORAGE_KEY = "gabsweb-pwa-install-v1";
  var INSTALL_CHECK_INTERVAL = 60000;
  var UPDATE_CHECK_INTERVAL = 60 * 60 * 1000;
  var SERVICE_WORKER_URL = "/sw.js";

  var registration = null;
  var waitingWorker = null;
  var reloadRequested = false;
  var autoPromptDone = false;
  var initialized = false;
  var toastTimer = null;
  var dialog = null;
  var promptInFlight = false;
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

  function isLoginPrompt() {
    var body = document.body;
    return Boolean(
      body && body.getAttribute("data-pwa-login-prompt") === "true"
    );
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

  /*
   * The inline capture script in base.html owns the single authoritative
   * beforeinstallprompt reference at window.__gabswebDeferredInstallPrompt.
   * This module never keeps a second copy, so the event cannot be lost,
   * overwritten or cleared in two different places.
   */
  function getDeferredPrompt() {
    var event = window && window.__gabswebDeferredInstallPrompt;
    return event && typeof event.prompt === "function" ? event : null;
  }

  function setDeferredPrompt(event) {
    if (!window) return;
    window.__gabswebDeferredInstallPrompt =
      event && typeof event.prompt === "function" ? event : null;
  }

  function clearDeferredPrompt() {
    setDeferredPrompt(null);
  }

  function hasUsablePrompt() {
    return Boolean(getDeferredPrompt());
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
    var completedAtStart = readState().completed === true;

    return hasInstalledRelatedApp().then(function (installed) {
      if (installed === true) {
        writeState({ completed: true });
        updateInstallMenu();
        return true;
      }

      // Only an explicit "not installed" answer may clear a previous
      // completion. Browsers without the related-app API return null; keep the
      // completed flag there so a genuinely installed app does not prompt
      // again merely because it was opened in a normal browser tab. The
      // beforeinstallprompt event remains the authoritative uninstall signal
      // on those browsers.
      if (
        installed === false &&
        completedAtStart &&
        readState().completed === true
      ) {
        writeState({
          completed: false,
          dismissedAt: 0,
          dismissCount: 0
        });
      }
      updateInstallMenu();
      if (installed === null) return isInstalled();
      return false;
    });
  }

  function recordDismissal() {
    // Dismissal only applies to the current authenticated session. The next
    // successful login sets a fresh server-side prompt flag.
    autoPromptDone = true;
  }

  function canAutoPrompt() {
    if (!isAppShell() || isInstalled()) return false;
    if (autoPromptDone) return false;
    return isLoginPrompt();
  }

  function markAutoShown() {
    autoPromptDone = true;
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

  function setInstallStatus(message) {
    var status = byId("pwa-install-status");
    if (!status) return;
    if (message) {
      status.textContent = message;
      status.hidden = false;
    } else {
      status.textContent = "";
      status.hidden = true;
    }
  }

  function platformGuidance(platform) {
    if (platform === "ios") {
      return {
        copy:
          "Safari and other browsers on iPhone and iPad add apps through the Share menu. Tap Install app to continue.",
        stepsTitle: "Add to Home Screen",
        steps: [
          "Open the <strong>Share</strong> menu in your browser.",
          "Choose <strong>Add to Home Screen</strong>.",
          "Tap <strong>Add</strong> to confirm."
        ]
      };
    }
    if (platform === "safari") {
      return {
        copy:
          "Safari on macOS can keep Gabsweb CashFlow in the Dock like an app. Tap Install app to continue.",
        stepsTitle: "Add to the Dock",
        steps: [
          "Open the <strong>File</strong> menu in Safari.",
          "Choose <strong>Add to Dock</strong>.",
          "Confirm to add Gabsweb CashFlow."
        ]
      };
    }
    if (platform === "samsung") {
      return {
        copy:
          "Samsung Internet can add Gabsweb CashFlow to your home screen from the browser menu. Tap Install app to continue.",
        stepsTitle: "Add to Home screen",
        steps: [
          "Open the browser menu.",
          "Choose <strong>Add page to</strong> then <strong>Home screen</strong>.",
          "Confirm the installation."
        ]
      };
    }
    if (platform === "firefox") {
      return {
        copy:
          "Firefox does not offer web app installation here. Use Chrome, Edge or Safari to install Gabsweb CashFlow. You can keep using the app in this tab.",
        stepsTitle: "",
        steps: []
      };
    }
    if (platform === "chromium") {
      return {
        copy:
          "You can install Gabsweb CashFlow from your browser menu. Tap Install app to continue.",
        stepsTitle: "Install from your browser",
        steps: [
          "Open the browser menu.",
          "Choose <strong>Install app</strong> or <strong>Add to Home screen</strong>.",
          "Confirm the installation."
        ]
      };
    }
    return {
      copy:
        "Adding Gabsweb CashFlow to your device makes it quicker to open. Tap Install app to continue.",
      stepsTitle: "Install from your browser",
      steps: [
        "Open your browser menu.",
        "Look for <strong>Install app</strong> or <strong>Add to Home screen</strong>.",
        "Confirm the installation."
      ]
    };
  }

  function resetDialog(platform) {
    var copy = byId("pwa-install-copy");
    var confirm = byId("pwa-install-confirm");

    if (confirm) {
      // The single installation modal always presents a working Install app
      // button. When direct installation is unavailable, the button reveals
      // the browser-specific guidance inside this same modal.
      confirm.hidden = false;
      confirm.disabled = promptInFlight;
      confirm.textContent = "Install app";
    }
    setInstallStatus("");

    if (hasUsablePrompt()) {
      if (copy) {
        copy.textContent =
          "Add Gabsweb CashFlow to your device for a faster, full-screen way to open your financial workspace.";
      }
      setSteps("", []);
    } else {
      var guidance = platformGuidance(platform);
      if (copy) copy.textContent = guidance.copy;
      // Instructions stay inside this modal and are revealed by the Install
      // app button, so the button is useful in every browser.
      setSteps("", []);
    }

    renderIcons();
  }

  function showInstallGuidance(message) {
    var platform = detectPlatform();
    var guidance = platformGuidance(platform);
    var copy = byId("pwa-install-copy");
    var confirm = byId("pwa-install-confirm");

    if (copy) copy.textContent = guidance.copy;
    if (confirm) {
      confirm.hidden = false;
      confirm.disabled = promptInFlight;
    }
    setSteps(guidance.stepsTitle, guidance.steps);
    setInstallStatus(message || "");

    var steps = byId("pwa-install-steps");
    if (steps && !steps.hidden && typeof steps.scrollIntoView === "function") {
      steps.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    renderIcons();
  }

  function openInstallDialog(source) {
    if (!dialog) return;
    if (isInstalled()) {
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
      if (document.querySelector("dialog[open]")) return;
      openInstallDialog("auto");
    }, delay);
  }

  function onBeforeInstallPrompt(event) {
    if (!event || typeof event.prompt !== "function") return;
    setDeferredPrompt(event);
    // Browsers only offer this prompt while the app is not installed, so a
    // stale "completed" flag (for example after the app was uninstalled) must
    // not keep hiding the installation guidance from the user menu.
    if (readState().completed === true) {
      writeState({ completed: false });
    }
    if (!initialized) return;
    updateInstallMenu();
    if (dialog && dialog.open) resetDialog(detectPlatform());
    if (canAutoPrompt()) maybeAutoPrompt(300);
  }

  function onAppInstalled() {
    clearDeferredPrompt();
    markInstalled();
    showToast("Gabsweb CashFlow is installed.");
  }

  function handleInstallClick(event) {
    if (event && typeof event.preventDefault === "function") {
      event.preventDefault();
    }
    if (promptInFlight) return;

    var confirm = byId("pwa-install-confirm");
    var promptEvent = getDeferredPrompt();
    if (!promptEvent) {
      // No direct browser prompt is available. Keep the single modal open and
      // reveal its browser-specific guidance. This is not an installation.
      showInstallGuidance(
        "This browser hasn't offered a direct install prompt. You can install Gabsweb CashFlow from the browser menu instead."
      );
      return;
    }

    /*
     * Consume the event synchronously inside the user's click. Nothing
     * asynchronous may run before prompt(), otherwise the browser no longer
     * treats the call as a user gesture.
     */
    promptInFlight = true;
    clearDeferredPrompt();
    if (confirm) confirm.disabled = true;
    setInstallStatus("Opening your browser's install prompt…");

    var promptResult;
    try {
      promptResult = promptEvent.prompt();
    } catch (error) {
      promptResult = Promise.reject(error);
    }

    Promise.resolve(promptResult)
      .then(function (result) {
        // Current Chromium resolves prompt() with the user's choice. Older
        // versions resolve with undefined, so fall back to userChoice then.
        if (result && result.outcome) return result;
        return promptEvent.userChoice;
      })
      .then(function (choice) {
        if (choice && choice.outcome === "accepted") {
          // Accepted means the native install flow has started. appinstalled
          // confirms completion; marking it now prevents a duplicate prompt.
          markInstalled();
          showToast(
            "Installation started. Gabsweb CashFlow will appear on your device shortly."
          );
        } else {
          // Dismissing the browser prompt is not a close action. Keep the same
          // modal open and show honest fallback guidance.
          showInstallGuidance(
            "Installation was cancelled. You can try again, or install Gabsweb CashFlow from the browser menu."
          );
        }
      })
      .catch(function () {
        // The prompt was unavailable or already consumed. Never close the
        // modal as if the close button had been pressed.
        showInstallGuidance(
          "Your browser didn't open the install prompt. You can install Gabsweb CashFlow from the browser menu instead."
        );
      })
      .then(function () {
        promptInFlight = false;
        if (confirm) confirm.disabled = false;
        updateInstallMenu();
      });
  }

  function setupInstallUI() {
    dialog = byId("pwa-install-dialog");
    if (!dialog) return;

    updateInstallMenu();

    // Close controls only ever close. The install control is a separate
    // element with its own handler, so a tap can never run both actions.
    dialog.querySelectorAll("[data-pwa-close]").forEach(function (button) {
      button.addEventListener("click", function (event) {
        event.preventDefault();
        closeDialog(true);
      });
    });

    // Only a click on the backdrop itself closes the dialog. Clicks on the
    // card, the Install app button or any other control are ignored here.
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) closeDialog(true);
    });

    dialog.addEventListener("cancel", function () {
      recordDismissal();
    });

    var confirm = byId("pwa-install-confirm");
    if (confirm) {
      confirm.addEventListener("click", handleInstallClick);
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

    // Resolve the installed state before showing the login prompt. A browser
    // that has no beforeinstallprompt event is not proof of installation.
    refreshInstallState("init").then(function (installed) {
      updateInstallMenu();
      if (!installed && canAutoPrompt()) maybeAutoPrompt(1200);
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
    if (getDeferredPrompt() && readState().completed === true) {
      writeState({ completed: false });
    }
    setupInstallUI();
    setupUpdateUI();
    registerServiceWorker();
  }

  /* The inline capture script in base.html is the only beforeinstallprompt
     listener. This callback lets it wake the already-loaded UI when a late
     event arrives. appinstalled has its own single listener here. */
  window.__gabswebHandleInstallPrompt = onBeforeInstallPrompt;
  window.addEventListener("appinstalled", onAppInstalled);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
