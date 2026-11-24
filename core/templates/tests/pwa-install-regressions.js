"use strict";

var fs = require("fs");
var path = require("path");
var vm = require("vm");
var assert = require("assert");

var rootDir = path.resolve(__dirname, "..", "..", "..");
var pwaSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "pwa.js"),
  "utf8"
);

function createElement(id, attributes) {
  var element = {
    id: id,
    hidden: false,
    disabled: false,
    open: false,
    textContent: "",
    innerHTML: "",
    parentNode: null,
    children: [],
    _listeners: {},
    _attributes: attributes || {},
    addEventListener: function (type, handler) {
      if (!this._listeners[type]) this._listeners[type] = [];
      this._listeners[type].push(handler);
    },
    removeEventListener: function (type, handler) {
      var list = this._listeners[type] || [];
      var index = list.indexOf(handler);
      if (index !== -1) list.splice(index, 1);
    },
    appendChild: function (child) {
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    removeChild: function (child) {
      var index = this.children.indexOf(child);
      if (index !== -1) {
        this.children.splice(index, 1);
        child.parentNode = null;
      }
      return child;
    },
    contains: function (node) {
      var current = node;
      while (current) {
        if (current === this) return true;
        current = current.parentNode;
      }
      return false;
    },
    closest: function (selector) {
      var current = this;
      while (current) {
        if (current.matches && current.matches(selector)) return current;
        current = current.parentNode;
      }
      return null;
    },
    matches: function (selector) {
      if (selector.charAt(0) === "#") {
        return this.id === selector.slice(1);
      }
      if (selector === "[data-pwa-close]") {
        return Object.prototype.hasOwnProperty.call(
          this._attributes,
          "data-pwa-close"
        );
      }
      return false;
    },
    querySelectorAll: function (selector) {
      var matches = [];
      (function walk(node) {
        node.children.forEach(function (child) {
          if (child.matches(selector)) matches.push(child);
          walk(child);
        });
      })(this);
      return matches;
    },
    getAttribute: function (name) {
      if (Object.prototype.hasOwnProperty.call(this._attributes, name)) {
        return this._attributes[name];
      }
      return null;
    },
    setAttribute: function (name, value) {
      this._attributes[name] = String(value);
    },
    removeAttribute: function (name) {
      delete this._attributes[name];
    },
    showModal: function () {
      this.open = true;
    },
    close: function () {
      this.open = false;
    },
    scrollIntoView: function () {}
  };
  return element;
}

/*
 * Fire a real bubbling click. The Install app handler, the close handlers and
 * the dialog backdrop handler are exercised exactly as a browser dispatches
 * them, so a close cannot be hidden behind a per-element shortcut.
 */
function fire(element, type, extra) {
  var event = {
    type: type,
    target: element,
    currentTarget: null,
    defaultPrevented: false,
    _propagationStopped: false,
    _immediateStopped: false,
    preventDefault: function () {
      this.defaultPrevented = true;
    },
    stopPropagation: function () {
      this._propagationStopped = true;
    },
    stopImmediatePropagation: function () {
      this._propagationStopped = true;
      this._immediateStopped = true;
    }
  };
  if (extra) {
    Object.keys(extra).forEach(function (key) {
      event[key] = extra[key];
    });
  }

  var node = element;
  while (node) {
    event.currentTarget = node;
    var listeners = (node._listeners[type] || []).slice();
    for (var i = 0; i < listeners.length; i += 1) {
      listeners[i](event);
      if (event._immediateStopped) return event;
    }
    if (event._propagationStopped) return event;
    node = node.parentNode;
  }
  return event;
}

function createHarness(options) {
  options = options || {};

  var body = createElement("body");
  var sidebar = createElement("sidebar");
  var dialog = createElement("pwa-install-dialog");
  var card = createElement("pwa-card");
  var dismiss = createElement("pwa-install-dismiss", { "data-pwa-close": "" });
  var later = createElement("pwa-install-later", { "data-pwa-close": "" });
  var confirm = createElement("pwa-install-confirm", { "data-pwa-install": "" });
  var copy = createElement("pwa-install-copy");
  var steps = createElement("pwa-install-steps");
  var stepsTitle = createElement("pwa-steps-title");
  var stepsList = createElement("pwa-steps-list");
  var status = createElement("pwa-install-status");
  var menuRow = createElement("pwa-install-menu-row");
  var menuItem = createElement("pwa-install-menu-item");
  var toast = createElement("pwa-toast");

  steps.hidden = true;
  status.hidden = true;
  menuRow.hidden = true;

  body.appendChild(sidebar);
  body.appendChild(dialog);
  dialog.appendChild(card);
  card.appendChild(dismiss);
  card.appendChild(copy);
  card.appendChild(steps);
  steps.appendChild(stepsTitle);
  steps.appendChild(stepsList);
  card.appendChild(status);
  card.appendChild(confirm);
  card.appendChild(later);
  body.appendChild(menuRow);
  menuRow.appendChild(menuItem);
  body.appendChild(toast);

  var elements = {};
  [
    sidebar,
    dialog,
    card,
    dismiss,
    later,
    confirm,
    copy,
    steps,
    stepsTitle,
    stepsList,
    status,
    menuRow,
    menuItem,
    toast
  ].forEach(function (element) {
    elements[element.id] = element;
  });

  body.getAttribute = function (name) {
    if (name === "data-pwa-login-prompt") {
      return options.loginPrompt === false ? "false" : "true";
    }
    return null;
  };

  var windowListeners = {};
  var timers = [];
  var storage = {};

  var document = {
    readyState: "complete",
    body: body,
    getElementById: function (id) {
      return elements[id] || null;
    },
    querySelector: function (selector) {
      if (selector === "dialog[open]") {
        return dialog.open ? dialog : null;
      }
      if (selector === "#pwa-install-dialog") {
        return dialog;
      }
      return null;
    },
    querySelectorAll: function (selector) {
      if (selector === "#pwa-install-dialog") return [dialog];
      return [];
    },
    addEventListener: function () {}
  };

  var navigator = {
    userAgent:
      options.userAgent ||
      "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36",
    standalone: false
  };

  var localStorage = {
    getItem: function (key) {
      return Object.prototype.hasOwnProperty.call(storage, key)
        ? storage[key]
        : null;
    },
    setItem: function (key, value) {
      storage[key] = String(value);
    }
  };

  var sandbox = {
    console: console,
    Promise: Promise,
    Date: Date,
    JSON: JSON,
    Object: Object,
    Array: Array,
    Boolean: Boolean,
    Number: Number,
    String: String,
    Error: Error,
    document: document,
    navigator: navigator,
    localStorage: localStorage,
    setTimeout: function (handler) {
      timers.push(handler);
      return timers.length;
    },
    clearTimeout: function () {},
    setInterval: function () {
      return 0;
    },
    clearInterval: function () {},
    matchMedia: function () {
      return {
        matches: false,
        addEventListener: function () {}
      };
    },
    addEventListener: function (type, handler) {
      if (!windowListeners[type]) windowListeners[type] = [];
      windowListeners[type].push(handler);
    }
  };
  sandbox.window = sandbox;
  sandbox.__gabswebDeferredInstallPrompt = options.earlyPrompt || null;

  vm.runInNewContext(pwaSource, sandbox, { filename: "pwa.js" });

  function dispatchWindow(type, event) {
    if (type === "beforeinstallprompt") {
      // Emulate the single early capture listener in base.html.
      if (event && typeof event.preventDefault === "function") {
        event.preventDefault();
      }
      sandbox.__gabswebDeferredInstallPrompt = event;
      if (typeof sandbox.__gabswebHandleInstallPrompt === "function") {
        sandbox.__gabswebHandleInstallPrompt(event);
      }
      return;
    }
    (windowListeners[type] || []).forEach(function (handler) {
      handler(event || {});
    });
  }

  return {
    sandbox: sandbox,
    element: function (id) {
      return elements[id];
    },
    click: function (id) {
      return fire(elements[id], "click");
    },
    readState: function () {
      try {
        return JSON.parse(storage["gabsweb-pwa-install-v1"] || "{}");
      } catch (error) {
        return {};
      }
    },
    runTimers: function () {
      var guard = 0;
      while (timers.length && guard < 50) {
        var pending = timers;
        timers = [];
        pending.forEach(function (handler) {
          handler();
        });
        guard += 1;
      }
    },
    runTimerOnce: function () {
      var handler = timers.shift();
      if (handler) handler();
    },
    dispatchWindow: dispatchWindow,
    dispatchInstallPrompt: function (event) {
      dispatchWindow("beforeinstallprompt", event);
    },
    flush: function () {
      var chain = Promise.resolve();
      for (var i = 0; i < 10; i += 1) {
        chain = chain.then(function () {});
      }
      return chain;
    },
    dialog: dialog
  };
}

function promptEvent(outcome, options) {
  options = options || {};
  var event = {
    defaultPrevented: false,
    promptCalls: 0,
    preventDefault: function () {
      this.defaultPrevented = true;
    },
    prompt: function () {
      this.promptCalls += 1;
      if (options.reject) {
        return Promise.reject(new Error("prompt unavailable"));
      }
      return Promise.resolve(options.promptResult);
    },
    userChoice: Promise.resolve({ outcome: outcome })
  };
  return event;
}

async function run() {
  // 1. Without a browser event the modal still opens on login, the Install app
  // button is visible, and clicking it reveals honest browser guidance.
  var guided = createHarness();
  await guided.flush();
  guided.runTimers();
  await guided.flush();
  assert.strictEqual(guided.dialog.open, true);
  assert.strictEqual(guided.element("pwa-install-confirm").hidden, false);
  assert.strictEqual(
    guided.element("pwa-install-steps").hidden,
    true,
    "Guidance is revealed by the Install app button, not by faking a prompt"
  );

  var guidedClick = guided.click("pwa-install-confirm");
  assert.strictEqual(guidedClick.defaultPrevented, true);
  assert.strictEqual(guided.dialog.open, true, "Install click must never close");
  assert.strictEqual(guided.element("pwa-install-steps").hidden, false);
  assert.strictEqual(guided.element("pwa-install-confirm").disabled, false);
  assert.notStrictEqual(guided.readState().completed, true);

  // The close control is independent and must still close the same modal.
  guided.click("pwa-install-dismiss");
  assert.strictEqual(guided.dialog.open, false, "Close control must close");

  // 2. A captured browser event is retained until the Install app button is
  // clicked, then consumed exactly once.
  var direct = createHarness();
  await direct.flush();
  direct.runTimers();
  await direct.flush();
  assert.strictEqual(direct.dialog.open, true);

  var accepted = promptEvent("accepted");
  direct.dispatchInstallPrompt(accepted);
  await direct.flush();
  assert.strictEqual(accepted.defaultPrevented, true);
  assert.strictEqual(
    direct.sandbox.__gabswebDeferredInstallPrompt,
    accepted,
    "One authoritative reference retains the event"
  );
  assert.strictEqual(direct.element("pwa-install-confirm").hidden, false);
  assert.strictEqual(direct.element("pwa-install-steps").hidden, true);

  direct.click("pwa-install-confirm");
  assert.strictEqual(accepted.promptCalls, 1, "Click invokes prompt() once");
  assert.strictEqual(
    direct.sandbox.__gabswebDeferredInstallPrompt,
    null,
    "The event is consumed immediately in the click"
  );
  await direct.flush();
  assert.strictEqual(direct.readState().completed, true);
  assert.strictEqual(direct.dialog.open, false);

  // A second click cannot prompt again after the event was consumed.
  direct.click("pwa-install-confirm");
  assert.strictEqual(accepted.promptCalls, 1);

  // 3. A dismissed native prompt keeps the same modal open. It is not a close
  // action and must not claim the app is installed.
  var declined = createHarness({ loginPrompt: false });
  declined.click("pwa-install-menu-item");
  await declined.flush();
  assert.strictEqual(declined.dialog.open, true);

  var dismissed = promptEvent("dismissed");
  declined.dispatchInstallPrompt(dismissed);
  await declined.flush();
  declined.click("pwa-install-confirm");
  assert.strictEqual(dismissed.promptCalls, 1);
  await declined.flush();
  assert.strictEqual(declined.dialog.open, true);
  assert.strictEqual(declined.element("pwa-install-steps").hidden, false);
  assert.ok(
    declined.element("pwa-install-status").textContent.indexOf("cancelled") !==
      -1
  );
  assert.notStrictEqual(declined.readState().completed, true);
  assert.strictEqual(declined.sandbox.__gabswebDeferredInstallPrompt, null);

  // A subsequent click has no event left and shows guidance, without closing.
  declined.click("pwa-install-confirm");
  assert.strictEqual(dismissed.promptCalls, 1);
  assert.strictEqual(declined.dialog.open, true);

  // Independent close controls continue to work after an install attempt.
  declined.click("pwa-install-later");
  assert.strictEqual(declined.dialog.open, false);
  assert.notStrictEqual(declined.readState().completed, true);

  // 4. A prompt that throws is reported as unavailable, never as success.
  var failing = createHarness({ loginPrompt: false });
  failing.click("pwa-install-menu-item");
  await failing.flush();
  var failingEvent = promptEvent("accepted", { reject: true });
  failing.dispatchInstallPrompt(failingEvent);
  await failing.flush();
  failing.click("pwa-install-confirm");
  await failing.flush();
  assert.strictEqual(failingEvent.promptCalls, 1);
  assert.strictEqual(failing.dialog.open, true);
  assert.strictEqual(failing.element("pwa-install-steps").hidden, false);
  assert.notStrictEqual(failing.readState().completed, true);

  // 5. appinstalled confirms installation, clears the event and hides the menu.
  var installed = createHarness({ loginPrompt: false });
  installed.click("pwa-install-menu-item");
  await installed.flush();
  installed.dispatchWindow("appinstalled", {});
  await installed.flush();
  assert.strictEqual(installed.readState().completed, true);
  assert.strictEqual(installed.dialog.open, false);
  assert.strictEqual(installed.element("pwa-install-menu-row").hidden, true);
  assert.strictEqual(installed.sandbox.__gabswebDeferredInstallPrompt, null);

  // 6. An event captured before pwa.js loaded is still usable.
  var earlyEvent = promptEvent("accepted");
  var early = createHarness({ earlyPrompt: earlyEvent });
  await early.flush();
  early.runTimers();
  await early.flush();
  assert.strictEqual(early.dialog.open, true);
  assert.strictEqual(early.element("pwa-install-confirm").hidden, false);
  early.click("pwa-install-confirm");
  assert.strictEqual(earlyEvent.promptCalls, 1);
  await early.flush();
  assert.strictEqual(early.readState().completed, true);

  // 7. A late event upgrades the already-open modal without replacing it or
  // hiding the Install app button.
  var late = createHarness({ loginPrompt: false });
  late.click("pwa-install-menu-item");
  await late.flush();
  assert.strictEqual(late.dialog.open, true);
  assert.strictEqual(late.element("pwa-install-steps").hidden, true);
  var lateEvent = promptEvent("dismissed");
  late.dispatchInstallPrompt(lateEvent);
  await late.flush();
  assert.strictEqual(late.element("pwa-install-confirm").hidden, false);
  assert.strictEqual(late.element("pwa-install-steps").hidden, true);
  late.click("pwa-install-confirm");
  assert.strictEqual(lateEvent.promptCalls, 1);

  // 8. Platform-specific fallback instructions live in the same modal.
  var ios = createHarness({
    loginPrompt: false,
    userAgent:
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
  });
  ios.click("pwa-install-menu-item");
  await ios.flush();
  ios.click("pwa-install-confirm");
  assert.strictEqual(ios.dialog.open, true);
  assert.ok(ios.element("pwa-install-copy").textContent.indexOf("Share") !== -1);
  assert.strictEqual(
    ios.element("pwa-steps-title").textContent,
    "Add to Home Screen"
  );
  assert.notStrictEqual(ios.readState().completed, true);

  var firefox = createHarness({
    loginPrompt: false,
    userAgent: "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
  });
  firefox.click("pwa-install-menu-item");
  await firefox.flush();
  firefox.click("pwa-install-confirm");
  assert.ok(
    firefox
      .element("pwa-install-copy")
      .textContent.indexOf("does not offer web app installation") !== -1
  );
  assert.strictEqual(firefox.element("pwa-install-steps").hidden, true);

  // 9. Login scope: the modal appears once for a login prompt and not at all
  // on a normal refresh/navigation.
  var login = createHarness();
  await login.flush();
  login.runTimers();
  await login.flush();
  assert.strictEqual(login.dialog.open, true);
  login.click("pwa-install-later");
  login.runTimers();
  await login.flush();
  assert.strictEqual(
    login.dialog.open,
    false,
    "Dismissal must not reopen during the same page load"
  );

  var refresh = createHarness({ loginPrompt: false });
  await refresh.flush();
  refresh.runTimers();
  await refresh.flush();
  assert.strictEqual(
    refresh.dialog.open,
    false,
    "Refresh/navigation without a fresh login flag must not prompt"
  );

  // 10. pwa.js must not register a competing beforeinstallprompt listener.
  assert.strictEqual(
    pwaSource.indexOf('window.addEventListener("beforeinstallprompt"'),
    -1,
    "base.html owns the single beforeinstallprompt listener"
  );
  assert.ok(pwaSource.indexOf("__gabswebHandleInstallPrompt") !== -1);

  console.log("PWA install regressions passed");
}

run().then(
  function () {
    process.exit(0);
  },
  function (error) {
    console.error(error && error.stack ? error.stack : error);
    process.exit(1);
  }
);
