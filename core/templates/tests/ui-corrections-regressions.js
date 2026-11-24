"use strict";

var fs = require("fs");
var path = require("path");
var vm = require("vm");
var assert = require("assert");

var rootDir = path.resolve(__dirname, "..", "..", "..");
var dashboardSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "dashboard.js"),
  "utf8"
);
var dashboardCss = fs.readFileSync(
  path.join(rootDir, "static", "assets", "css", "dashboard.css"),
  "utf8"
);
var sidebarSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "sidebar-balance.js"),
  "utf8"
);
var appBaseTemplate = fs.readFileSync(
  path.join(rootDir, "core", "templates", "layouts", "app_base.html"),
  "utf8"
);
var sidebarTemplate = fs.readFileSync(
  path.join(rootDir, "core", "templates", "partials", "app_sidebar.html"),
  "utf8"
);
var settingsCss = fs.readFileSync(
  path.join(rootDir, "static", "assets", "css", "settings.css"),
  "utf8"
);
var settingsTemplate = fs.readFileSync(
  path.join(rootDir, "core", "templates", "settings.html"),
  "utf8"
);

var pendingTests = [];

function test(name, callback) {
  var result = callback();
  if (result && typeof result.then === "function") {
    pendingTests.push(
      result.then(function () {
        console.log("PASS " + name);
      })
    );
    return;
  }
  console.log("PASS " + name);
}

function createClassList() {
  return {
    add: function () {},
    remove: function () {},
    toggle: function () {},
    contains: function () {
      return false;
    }
  };
}

function createElement() {
  return {
    textContent: "",
    innerHTML: "",
    title: "",
    dataset: {},
    children: [],
    classList: createClassList(),
    setAttribute: function () {},
    removeAttribute: function () {},
    addEventListener: function () {},
    remove: function () {},
    querySelector: function () {
      return null;
    },
    querySelectorAll: function () {
      return [];
    }
  };
}

function instrumentDashboard(source) {
  var marker = "  if (document.readyState === \"loading\") {";
  var index = source.lastIndexOf(marker);
  assert.notStrictEqual(index, -1);
  return (
    source.slice(0, index) +
    "  window.__renderShell = renderShell;\n\n" +
    source.slice(index)
  );
}

function createDashboardHarness(options) {
  options = options || {};
  var elements = Object.create(null);
  function element(id) {
    if (!elements[id]) elements[id] = createElement();
    return elements[id];
  }
  var data = {
    getMonthKey: function () {
      return "2026-11";
    },
    todayKey: function () {
      return "2026-11-10";
    },
    getProfile: function () {
      return { name: options.profileName || "" };
    },
    isStorageAvailable: function () {
      return true;
    }
  };
  var context = {
    console: console,
    Date: Date,
    JSON: JSON,
    Math: Math,
    Intl: Intl,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    GabswebCashFlowData: data,
    document: {
      readyState: "loading",
      body: {
        dataset: {
          userTwoPartName: options.twoPartName || "",
          userFirstName: options.firstName || ""
        }
      },
      documentElement: {
        classList: createClassList(),
        style: {}
      },
      getElementById: function (id) {
        if (id === "db-legacy-templates") return null;
        return element(id);
      },
      querySelector: function () {
        return null;
      },
      querySelectorAll: function () {
        return [];
      },
      addEventListener: function () {}
    }
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(instrumentDashboard(dashboardSource), context, {
    filename: "assets/js/dashboard.js"
  });
  context.__renderShell();
  return {
    greeting: element("db-greeting").textContent,
    header: element("header-welcome").textContent
  };
}

function createSidebarHarness(total) {
  var balanceElement = createElement();
  var noteElement = createElement();
  noteElement.textContent = "Loading finances…";
  var listeners = [];
  var timer = null;
  var currentStatus = { status: "loading", error: null };
  var data = {
    subscribe: function (listener) {
      listeners.push(listener);
      return function () {
        listeners = listeners.filter(function (item) {
          return item !== listener;
        });
      };
    },
    getServerFinancialStatus: function () {
      return currentStatus;
    },
    getTotalBalance: function () {
      return total;
    },
    getTotalIncome: function () {
      return 500;
    },
    getTotalExpenses: function () {
      return 100;
    },
    getMonthKey: function () {
      return "2026-11";
    },
    todayKey: function () {
      return "2026-11-10";
    },
    formatNaira: function (value) {
      return "NGN " + Number(value).toFixed(2);
    },
    syncServerFinancials: function () {
      return Promise.resolve();
    }
  };
  var context = {
    console: console,
    Number: Number,
    String: String,
    GabswebCashFlowData: data,
    document: {
      getElementById: function (id) {
        if (id === "sidebar-total-balance") return balanceElement;
        if (id === "sidebar-balance-note") return noteElement;
        return null;
      }
    },
    setTimeout: function (callback, delay) {
      timer = { callback: callback, delay: delay };
      return 1;
    },
    clearTimeout: function () {
      timer = null;
    }
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(sidebarSource, context, {
    filename: "assets/js/sidebar-balance.js"
  });
  return {
    balance: balanceElement,
    note: noteElement,
    timer: function () {
      return timer;
    },
    setStatus: function (status, error) {
      currentStatus = { status: status, error: error || null };
      listeners.slice().forEach(function (listener) {
        listener();
      });
    }
  };
}

test("dashboard greeting keeps long names complete and wraps in CSS", function () {
  var result = createDashboardHarness({
    twoPartName: "Alexanderthegreat Verylongsurname",
    firstName: "Alexanderthegreat"
  });
  assert.ok(result.greeting.indexOf("Alexanderthegreat Verylongsurname") !== -1);
  assert.ok(dashboardCss.indexOf("overflow-wrap: anywhere") !== -1);
  assert.ok(dashboardCss.indexOf("text-wrap: balance") !== -1);
  assert.ok(dashboardCss.indexOf(".db-hero > div") !== -1);
});

test("dashboard greeting never shows a full email fallback", function () {
  var result = createDashboardHarness({
    twoPartName: "",
    firstName: "",
    profileName: "gabrielonje@gmail.com"
  });
  assert.strictEqual(result.greeting.indexOf("@"), -1);
  assert.ok(result.greeting.indexOf("gabrielonje") !== -1);
  assert.strictEqual(result.header.indexOf("@"), -1);
});

test("shared sidebar loader replaces loading state with the authoritative balance", function () {
  var harness = createSidebarHarness(1234.5);
  assert.strictEqual(harness.balance.textContent, "—");
  assert.strictEqual(harness.note.textContent, "Loading finances…");
  harness.setStatus("ready");
  assert.strictEqual(harness.balance.textContent, "NGN 1234.50");
  assert.strictEqual(harness.note.textContent, "+NGN 400.00 net this month");
});

test("shared sidebar loader shows failure instead of an endless loading state", function () {
  var harness = createSidebarHarness(0);
  harness.setStatus("error", "Your session has expired.");
  assert.strictEqual(harness.balance.textContent, "—");
  assert.strictEqual(harness.note.textContent, "Balance unavailable");
  assert.ok(harness.note.title.indexOf("session") !== -1);
});

test("shared sidebar loader times out safely and recovers when data arrives", function () {
  var harness = createSidebarHarness(900);
  assert.ok(harness.timer());
  assert.strictEqual(harness.timer().delay, 12000);
  harness.timer().callback();
  assert.strictEqual(harness.note.textContent, "Balance unavailable");
  harness.setStatus("ready");
  assert.strictEqual(harness.balance.textContent, "NGN 900.00");
  assert.strictEqual(harness.note.textContent, "+NGN 400.00 net this month");
});

test("shared sidebar loader preserves page-specific notes", function () {
  var harness = createSidebarHarness(2500);
  harness.note.textContent = "NGN 250.00 available";
  harness.setStatus("ready");
  assert.strictEqual(harness.balance.textContent, "NGN 2500.00");
  assert.strictEqual(harness.note.textContent, "NGN 250.00 available");
});

test("shared sidebar loader does not reuse another account's balance", function () {
  var first = createSidebarHarness(1200);
  first.setStatus("ready");
  var second = createSidebarHarness(7800);
  second.setStatus("ready");
  assert.strictEqual(first.balance.textContent, "NGN 1200.00");
  assert.strictEqual(second.balance.textContent, "NGN 7800.00");
});

test("profile hero name wraps without clipping and stays centered on mobile", function () {
  assert.ok(
    settingsCss.indexOf(".gs-profile-hero .gs-profile-identity h3") !== -1
  );
  assert.ok(settingsCss.indexOf("overflow-wrap: anywhere") !== -1);
  assert.ok(settingsCss.indexOf("text-wrap: balance") !== -1);
  assert.ok(settingsCss.indexOf("text-overflow: clip") !== -1);
  assert.ok(
    settingsCss.indexOf(".gs-profile-hero .gs-profile-identity p {") !== -1
  );
  assert.ok(settingsCss.indexOf("text-align: center;") !== -1);
});

test("page status tags are specific and relevant", function () {
  var labels = [
    ["dashboard.js", "Dashboard up to date"],
    ["dashboard.js", "Planner up to date"],
    ["budgets.js", "Budget up to date"],
    ["goals.js", "Goals up to date"],
    ["savings.js", "Savings up to date"],
    ["insights.js", "Insights up to date"],
    ["work-schedule.js", "Schedule up to date"],
    ["transaction.js", "Finances up to date"]
  ];
  labels.forEach(function (entry) {
    var source = fs.readFileSync(
      path.join(rootDir, "static", "assets", "js", entry[0]),
      "utf8"
    );
    assert.ok(source.indexOf(entry[1]) !== -1, entry[0] + " missing " + entry[1]);
  });
  assert.ok(settingsTemplate.indexOf("Settings saved") !== -1);
  assert.strictEqual(
    settingsTemplate.indexOf("Safely saved to your account"),
    -1
  );
  assert.strictEqual(
    settingsTemplate.indexOf("settings-storage-title\">Saved successfully"),
    -1
  );
});

test("shared sidebar loader is wired into every authenticated app page", function () {
  assert.ok(appBaseTemplate.indexOf("assets/js/sidebar-balance.js") !== -1);
  assert.ok(
    sidebarTemplate.indexOf('id="sidebar-total-balance" class="text-xl sm:text-2xl font-bold text-gradient mb-1">—') !== -1
  );
});

test("dashboard greeting alternates every 20 seconds with a quicker fade", function () {
  assert.ok(dashboardSource.indexOf("var GREETING_INTERVAL = 20000;") !== -1);
  assert.ok(dashboardSource.indexOf("var GREETING_FADE_MS = 1100;") !== -1);
  assert.ok(dashboardSource.indexOf("scheduleGreeting(GREETING_INTERVAL)") !== -1);
  assert.ok(dashboardSource.indexOf("window.setTimeout(apply, GREETING_FADE_MS)") !== -1);
  assert.ok(dashboardCss.indexOf("transition: opacity 1100ms") !== -1);
  assert.strictEqual(dashboardCss.indexOf("transition: opacity 1380ms"), -1);
  assert.ok(dashboardSource.indexOf("prefers-reduced-motion: reduce") !== -1);
  assert.ok(dashboardSource.indexOf("window.clearTimeout(state.greeting.timer)") !== -1);
});

test("footer attribution keeps its icon with the adjacent text", function () {
  var indexCss = fs.readFileSync(
    path.join(rootDir, "static", "assets", "css", "index.css"),
    "utf8"
  );
  var insightsCss = fs.readFileSync(
    path.join(rootDir, "static", "assets", "css", "insights.css"),
    "utf8"
  );
  var dbFootnote = dashboardCss.match(/\.db-footnote\s*\{([^}]*)\}/);
  var riFootnote = insightsCss.match(/\.ri-footnote\s*\{([^}]*)\}/);
  assert.ok(dbFootnote && /align-items:\s*flex-start;/.test(dbFootnote[1]));
  assert.ok(riFootnote && /align-items:\s*flex-start;/.test(riFootnote[1]));
  assert.ok(
    /\.db-footnote i,\s*\.db-footnote svg\s*\{[^}]*flex:\s*0 0 auto;/.test(
      dashboardCss
    ),
    "The dashboard footnote icon must not shrink beside the text"
  );
  assert.ok(
    /\.ri-footnote i,\s*\.ri-footnote svg\s*\{[^}]*flex:\s*0 0 auto;/.test(
      insightsCss
    ),
    "The insights footnote icon must not shrink beside the text"
  );
  assert.ok(
    /\.db-footnote > span\s*\{[^}]*min-width:\s*0;/.test(dashboardCss),
    "The dashboard footnote text must be allowed to wrap"
  );
  assert.ok(
    /\.ri-footnote > span\s*\{[^}]*min-width:\s*0;/.test(insightsCss),
    "The insights footnote text must be allowed to wrap"
  );
  assert.ok(indexCss.indexOf(".app-portfolio-footer") !== -1);
  assert.ok(indexCss.indexOf(".auth-portfolio-footer") !== -1);
});

test("every layout ends with the shared Gabsweb portfolio footer", function () {
  ["app_base.html", "auth_base.html", "error_base.html"].forEach(function (name) {
    var source = fs.readFileSync(
      path.join(rootDir, "core", "templates", "layouts", name),
      "utf8"
    );
    assert.ok(
      source.indexOf('include "partials/portfolio_footer.html"') !== -1,
      name + " must include the shared portfolio footer"
    );
  });
});

test("login success notices auto-dismiss within three seconds", function () {
  var noticesSource = fs.readFileSync(
    path.join(rootDir, "static", "assets", "js", "app-notices.js"),
    "utf8"
  );
  assert.ok(noticesSource.indexOf("Successfully signed in as") !== -1);
  assert.ok(noticesSource.indexOf("Logged in as") !== -1);

  var timer = noticesSource.match(
    /setTimeout\(function \(\) \{\s*dismissMessage\(element\);\s*\}, (\d+)\)/
  );
  assert.ok(timer, "Login notices must be dismissed on a timer");
  assert.ok(
    Number(timer[1]) <= 3000,
    "Login notices must disappear within three seconds"
  );
  assert.ok(
    noticesSource.indexOf("element.remove();") !== -1,
    "Dismissed notices must leave the layout without a page refresh"
  );
  assert.ok(
    appBaseTemplate.indexOf(
      "{% static 'assets/js/app-notices.js' %}"
    ) !== -1,
    "app-notices.js must load on every authenticated app page"
  );
});

test("pending workplace notice collapses to a compact line after five seconds", function () {
  var noticesSource = fs.readFileSync(
    path.join(rootDir, "static", "assets", "js", "app-notices.js"),
    "utf8"
  );
  var noticesCss = fs.readFileSync(
    path.join(rootDir, "static", "assets", "css", "account-notices.css"),
    "utf8"
  );
  var noticesTemplate = fs.readFileSync(
    path.join(
      rootDir,
      "core",
      "templates",
      "partials",
      "app_account_notices.html"
    ),
    "utf8"
  );

  assert.ok(noticesTemplate.indexOf("data-pending-notice") !== -1);
  assert.ok(noticesTemplate.indexOf("Workplace Access Pending") !== -1);
  assert.ok(
    noticesSource.indexOf(
      '[data-pending-notice][data-pending-expanded="true"]'
    ) !== -1,
    "The collapse timer must target the expanded pending notice"
  );

  var timer = noticesSource.match(
    /setTimeout\(function \(\) \{[\s\S]*?pendingNotice[\s\S]*?\}, (\d+)\)/
  );
  assert.ok(timer, "The pending notice must collapse on a timer");
  assert.strictEqual(Number(timer[1]), 5000);
  assert.ok(noticesSource.indexOf('classList.add("is-compact")') !== -1);
  assert.ok(
    noticesSource.indexOf('pendingNotice.removeAttribute("data-pending-expanded")') !== -1
  );
  assert.ok(
    /\.app-pending-notice\.is-compact \.app-pending-title-full,[\s\S]*?\.app-pending-help\s*\{[^}]*display:\s*none;/.test(
      noticesCss
    ),
    "Compact mode must hide the full explanation"
  );
  assert.ok(
    /\.app-pending-notice\.is-compact \.app-pending-title-compact\s*\{[^}]*display:\s*inline;/.test(
      noticesCss
    ),
    "Compact mode must show the one-line title"
  );
});

test("notification panel Clear button clears persisted notifications", function () {
  var notificationsSource = fs.readFileSync(
    path.join(rootDir, "static", "assets", "js", "notifications.js"),
    "utf8"
  );
  var notificationsTemplate = fs.readFileSync(
    path.join(rootDir, "core", "templates", "partials", "app_header_controls.html"),
    "utf8"
  );
  var notificationsCss = fs.readFileSync(
    path.join(rootDir, "static", "assets", "css", "notifications.css"),
    "utf8"
  );

  assert.ok(notificationsTemplate.indexOf('id="clear-notifications"') !== -1);
  assert.ok(
    notificationsTemplate.indexOf('aria-label="Clear all notifications"') !== -1
  );
  assert.ok(notificationsTemplate.indexOf('data-lucide="trash-2"') !== -1);
  assert.ok(
    notificationsTemplate.indexOf("app-notification-head-actions") !== -1,
    "Clear must sit in its own header actions group"
  );
  assert.ok(
    notificationsTemplate.indexOf("app-notification-mark-all") <
      notificationsTemplate.indexOf('id="clear-notifications"'),
    "Clear must be separate from the mark-all control"
  );

  assert.ok(notificationsSource.indexOf('API_URL + "clear/"') !== -1);
  assert.ok(notificationsSource.indexOf("method: \"POST\"") !== -1);
  assert.ok(notificationsSource.indexOf("function clearNotifications()") !== -1);
  assert.ok(notificationsSource.indexOf("state.clearing") !== -1);
  assert.ok(
    notificationsSource.indexOf("clearAll.disabled = state.clearing") !== -1,
    "Clear must be disabled while a request is in flight"
  );
  assert.ok(
    notificationsSource.indexOf(
      "clearAll.hidden = state.serverItems.length === 0"
    ) !== -1,
    "Clear must not be offered when there is nothing stored to clear"
  );
  assert.ok(
    notificationsSource.indexOf('dropdown.querySelector("#clear-notifications")') !==
      -1
  );

  assert.ok(notificationsCss.indexOf(".app-notification-head-actions") !== -1);
  assert.ok(notificationsCss.indexOf(".app-notification-clear") !== -1);
  assert.ok(notificationsCss.indexOf(".app-notification-clear:focus-visible") !== -1);
});

Promise.all(pendingTests)
  .then(function () {
    console.log("All UI correction regression tests passed.");
    process.exit(0);
  })
  .catch(function (error) {
    console.error(error && error.stack ? error.stack : error);
    process.exit(1);
  });
