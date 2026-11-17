"use strict";

var fs = require("fs");
var path = require("path");
var vm = require("vm");
var assert = require("assert");
var webcrypto = require("crypto").webcrypto;

var rootDir = path.resolve(__dirname, "..", "..", "..");
var dataSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "cashflow-data.js"),
  "utf8"
);
var budgetSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "budgets.js"),
  "utf8"
);
var insightsSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "insights.js"),
  "utf8"
);
var savingsSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "savings.js"),
  "utf8"
);
var settingsSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "settings.js"),
  "utf8"
);
var globalNavigationSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "global-navigation.js"),
  "utf8"
);
var savingsStyles = fs.readFileSync(
  path.join(rootDir, "static", "assets", "css", "savings.css"),
  "utf8"
);
var dashboardSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "dashboard.js"),
  "utf8"
);
var transactionSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "transaction.js"),
  "utf8"
);
var goalsSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "goals.js"),
  "utf8"
);
var goalsTemplate = fs.readFileSync(
  path.join(rootDir, "core", "templates", "goals.html"),
  "utf8"
);
var transactionTemplate = fs.readFileSync(
  path.join(rootDir, "core", "templates", "transaction.html"),
  "utf8"
);
var storageKey = "gabsweb-cashflow-data";

function createStorage() {
  var values = Object.create(null);
  return {
    getItem: function (key) {
      return Object.prototype.hasOwnProperty.call(values, key)
        ? values[key]
        : null;
    },
    setItem: function (key, value) {
      values[key] = String(value);
    },
    removeItem: function (key) {
      delete values[key];
    },
    clear: function () {
      values = Object.create(null);
    }
  };
}

function loadData(storage, workplaceAccess) {
  var context = {
    GABSWEB_WORKPLACE_ACCESS: workplaceAccess !== false,
    console: console,
    Date: Date,
    JSON: JSON,
    Math: Math,
    Intl: Intl,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    localStorage: storage,
    crypto: webcrypto
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(dataSource, context, {
    filename: "assets/js/cashflow-data.js"
  });
  return context.GabswebCashFlowData;
}


function loadDataForUser(storage, userId, email) {
  var context = {
    GABSWEB_WORKPLACE_ACCESS: true,
    console: console,
    Date: Date,
    JSON: JSON,
    Math: Math,
    Intl: Intl,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    localStorage: storage,
    crypto: webcrypto,
    document: {
      body: {
        dataset: {
          userId: String(userId || ""),
          userEmail: email || ""
        }
      }
    }
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(dataSource, context, {
    filename: "assets/js/cashflow-data.js"
  });
  return context.GabswebCashFlowData;
}

function loadDataWithFetch(storage, responses) {
  var context = {
    GABSWEB_WORKPLACE_ACCESS: true,
    console: console,
    Date: Date,
    JSON: JSON,
    Math: Math,
    Intl: Intl,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    localStorage: storage,
    crypto: webcrypto
  };
  context.fetch = function (url) {
    var payload = responses[url];
    if (!payload) {
      return Promise.resolve({
        ok: false,
        redirected: false,
        status: 404,
        headers: { get: function () { return "application/json"; } },
        json: function () { return Promise.resolve({}); }
      });
    }
    return Promise.resolve({
      ok: true,
      redirected: false,
      status: 200,
      headers: { get: function () { return "application/json"; } },
      json: function () { return Promise.resolve(payload); }
    });
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(dataSource, context, {
    filename: "assets/js/cashflow-data.js"
  });
  return context.GabswebCashFlowData;
}

function createClassList() {
  var values = Object.create(null);
  return {
    add: function (name) {
      values[name] = true;
    },
    remove: function (name) {
      delete values[name];
    },
    contains: function (name) {
      return Boolean(values[name]);
    }
  };
}

function createElement(value) {
  return {
    value: value === undefined ? "" : value,
    textContent: "",
    innerHTML: "",
    options: undefined,
    classList: createClassList(),
    reset: function () {
      this.value = "";
    },
    appendChild: function (child) {
      if (!this.options) this.options = [];
      this.options.push(child);
    }
  };
}

function createBudgetHarness() {
  var elements = Object.create(null);
  [
    "bd-allocation-form",
    "bd-form-month",
    "bd-form-name",
    "bd-form-amount",
    "bd-form-description",
    "bd-form-purpose",
    "bd-modal-title",
    "bd-modal-subtitle",
    "bd-form-submit",
    "bd-form-error",
    "bd-allocation-modal"
  ].forEach(function (id) {
    elements[id] = createElement("");
  });
  elements["bd-form-status"] = createElement("");
  elements["bd-form-status"].options = [
    { value: "planned", textContent: "Planned" },
    { value: "active", textContent: "Active" },
    { value: "completed", textContent: "Completed" },
    { value: "cancelled", textContent: "Cancelled" }
  ];
  var document = {
    readyState: "loading",
    body: { style: {} },
    getElementById: function (id) {
      return elements[id] || null;
    },
    addEventListener: function () {},
    querySelector: function () {
      return null;
    },
    querySelectorAll: function () {
      return [];
    }
  };
  var marker = "})();";
  var index = budgetSource.lastIndexOf(marker);
  var instrumented =
    budgetSource.slice(0, index) +
    "window.__budgetTest = {" +
    " ensureOption: ensureOption," +
    " openEditModal: openEditModal," +
    " setAllocations: function (rows) { serverState.allocations = rows; }" +
    "};\n" +
    budgetSource.slice(index);
  var context = {
    console: console,
    Date: Date,
    Intl: Intl,
    Math: Math,
    JSON: JSON,
    document: document,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    fetch: function () {
      return Promise.resolve({});
    }
  };
  context.window = context;
  context.globalThis = context;
  context.GabswebCashFlowData = {
    todayKey: function () {
      return "2026-11-07";
    },
    subscribe: function () {},
    STORAGE_KEY: storageKey
  };
  vm.createContext(context);
  vm.runInContext(instrumented, context, {
    filename: "assets/js/budgets.js"
  });
  return { api: context.__budgetTest, byId: document.getElementById };
}

function createAccount(data) {
  return data.createAccount({
    name: "Test Account",
    type: "checking",
    openingBalance: 1000,
    openingBalanceDate: "2026-01-01"
  });
}

function createAllocation(data, month) {
  return data.createAllocation({
    name: month + " allocation",
    financialMonth: month,
    plannedAmount: 500,
    status: "planned",
    category: "Test"
  });
}

function createExpense(data, accountId, date) {
  return data.createExpense({
    amount: 100,
    date: date,
    category: "Test",
    accountId: accountId,
    description: "Test expense"
  });
}


function createTransactionHarness() {
  var pendingDeletes = [];
  var postCount = 0;
  var elements = Object.create(null);
  function element(id) {
    if (!elements[id]) {
      elements[id] = {
        id: id,
        value: id === "mf-csrf-token" ? "test-token" : "",
        textContent: "",
        innerHTML: "",
        classList: createClassList(),
        addEventListener: function () {},
      };
    }
    return elements[id];
  }
  var document = {
    readyState: "loading",
    body: { style: {} },
    getElementById: element,
    addEventListener: function () {},
    querySelector: function () {
      return null;
    },
    querySelectorAll: function () {
      return [];
    },
  };
  function jsonResponse(payload, status) {
    var text = JSON.stringify(payload || {});
    return {
      ok: status >= 200 && status < 300,
      redirected: false,
      status: status,
      headers: {
        get: function () {
          return "application/json";
        },
      },
      text: function () {
        return Promise.resolve(text);
      },
    };
  }
  var context = {
    console: console,
    Date: Date,
    Intl: Intl,
    Math: Math,
    JSON: JSON,
    Promise: Promise,
    document: document,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    fetch: function (url, options) {
      if (options && options.method === "POST") {
        postCount += 1;
        return new Promise(function (resolve) {
          pendingDeletes.push(resolve);
        });
      }
      return Promise.resolve(jsonResponse({}));
    },
  };
  context.window = context;
  context.globalThis = context;
  context.GabswebCashFlowData = {
    STORAGE_KEY: storageKey,
    subscribe: function () {},
    getAccounts: function () {
      return [];
    },
  };
  var marker = "})();";
  var index = transactionSource.lastIndexOf(marker);
  var instrumented =
    transactionSource.slice(0, index) +
    "window.__transactionTest = {" +
    " requestTransactionDelete: requestTransactionDelete," +
    " transactionDeleteFeedback: transactionDeleteFeedback" +
    "};\n" +
    transactionSource.slice(index);
  vm.createContext(context);
  vm.runInContext(instrumented, context, {
    filename: "assets/js/transaction.js",
  });
  return {
    api: context.__transactionTest,
    postCount: function () {
      return postCount;
    },
    resolveDelete: function (payload, status) {
      var resolve = pendingDeletes.shift();
      if (!resolve) throw new Error("No pending delete request.");
      resolve(jsonResponse(payload, status || 200));
    },
    pendingDeletes: function () {
      return pendingDeletes.length;
    },
  };
}


function createSettingsClassList() {
  var values = Object.create(null);
  return {
    add: function () {
      Array.prototype.forEach.call(arguments, function (name) {
        values[name] = true;
      });
    },
    remove: function () {
      Array.prototype.forEach.call(arguments, function (name) {
        delete values[name];
      });
    },
    contains: function (name) {
      return Boolean(values[name]);
    },
    toggle: function (name, force) {
      var enabled = force === undefined ? !values[name] : Boolean(force);
      if (enabled) values[name] = true;
      else delete values[name];
      return enabled;
    }
  };
}

function createSettingsHarness() {
  var elements = Object.create(null);
  function element(id) {
    if (!elements[id]) {
      elements[id] = {
        id: id,
        value: "",
        textContent: "",
        innerHTML: "",
        hidden: false,
        checked: false,
        disabled: false,
        dataset: {},
        style: {},
        classList: createSettingsClassList(),
        addEventListener: function () {},
        removeAttribute: function () {},
        setAttribute: function () {},
        appendChild: function () {},
        focus: function () {},
        querySelector: function () {
          return null;
        },
        querySelectorAll: function () {
          return [];
        }
      };
    }
    return elements[id];
  }
  element("settings-profile").dataset.profileUrl = "/profile-api/";
  element("work-preferences-form").dataset.url = "/work-profile-api/";
  element("supervisor-form").dataset.url = "/supervisors-api/";
  element("personal-shift-form").dataset.url = "/personal-shifts-api/";
  element("profile-form").dataset.updateUrl = "/profile/update/";

  var calls = [];
  var context = {
    console: console,
    Date: Date,
    JSON: JSON,
    Math: Math,
    Intl: Intl,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    localStorage: createStorage(),
    FormData: function () {
      return { append: function () {} };
    },
    CustomEvent: function () {},
    document: {
      readyState: "complete",
      body: {
        dataset: {
          userId: "profile-user",
          userFirstName: "First",
          userTwoPartName: "First Person",
          userFullName: "First Person",
          userEmail: "first@example.com",
          userProfessionalRole: "Accountant",
          userAvatarUrl: ""
        }
      },
      documentElement: {
        classList: createSettingsClassList(),
        style: {}
      },
      getElementById: function (id) {
        return element(id);
      },
      querySelector: function () {
        return null;
      },
      querySelectorAll: function () {
        return [];
      },
      addEventListener: function () {},
      createElement: function () {
        return element("created-" + Math.random());
      }
    }
  };
  context.window = context;
  context.globalThis = context;
  context.fetch = function (url) {
    calls.push(url);
    var payload = null;
    if (url === "/profile-api/") {
      payload = {
        ok: true,
        full_name: "First Person",
        first_name: "First",
        two_part_name: "First Person",
        email: "first@example.com",
        professional_role: "Accountant",
        profile_image_url: "",
        uses_google_profile_image: false
      };
    } else if (url === "/work-profile-api/") {
      payload = {
        ok: true,
        workProfile: {
          workTeam: "",
          joiningDate: "",
          joiningArrangement: "",
          worksShifts: false,
          accountStatus: "GENERAL",
          statusLabel: "General",
          workplaceCompany: "",
          workplaceLabel: "GCTN/Apex Pay",
          hasApprovedAccess: false,
          isPending: false,
          isGeneral: true,
          supervisors: [],
          shifts: []
        }
      };
    }
    return Promise.resolve({
      ok: Boolean(payload),
      json: function () {
        return Promise.resolve(payload || { ok: false });
      }
    });
  };
  vm.createContext(context);
  vm.runInContext(settingsSource, context, {
    filename: "assets/js/settings.js"
  });
  return new Promise(function (resolve, reject) {
    setTimeout(function () {
      try {
        resolve({
          calls: calls,
          elements: elements,
          element: element
        });
      } catch (error) {
        reject(error);
      }
    }, 30);
  });
}

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

test("fresh data initializes work schedule before any page script runs", function () {
  var data = loadData(createStorage());
  assert.strictEqual(data.getWorkScheduleRecords().length, 5);
  assert.strictEqual(data.getWorkScheduleConfig().rotation.configured, true);
  assert.strictEqual(
    data.getSettings().preferences.workScheduleInitialized,
    true
  );
});

test("unapproved users do not receive seeded workplace schedule data", function () {
  var data = loadData(createStorage(), false);
  assert.strictEqual(data.getWorkScheduleRecords().length, 0);
  assert.strictEqual(data.getWorkScheduleConfig().rotation.configured, false);
  assert.strictEqual(data.getRotationSettings().configured, false);
  assert.strictEqual(data.getState().workSchedule.records.length, 0);
  assert.throws(function () {
    data.updateWorkScheduleConfig({
      rotation: { configured: true },
      anchor: { date: "2026-10-31", shiftType: "morning", cyclePosition: 0 }
    });
  }, /access is required/);
});

test("unapproved users keep manual schedule entries and cannot add generated ones", function () {
  var data = loadData(createStorage(), false);
  var manual = data.addWorkScheduleRecord({
    date: "2026-11-10",
    status: "working",
    shiftType: "morning",
    startTime: "07:00",
    endTime: "13:00",
    source: "manual"
  });
  assert.strictEqual(data.getWorkScheduleRecords().length, 1);
  assert.strictEqual(data.getWorkScheduleRecord(manual.id).source, "manual");
  assert.throws(function () {
    data.addWorkScheduleRecord({
      date: "2026-11-11",
      status: "working",
      shiftType: "morning",
      startTime: "07:00",
      endTime: "13:00",
      source: "generated"
    });
  }, /access is required/);
});

test("unapproved users cannot convert manual entries into generated ones", function () {
  var data = loadData(createStorage(), false);
  var manual = data.addWorkScheduleRecord({
    date: "2026-11-11",
    status: "working",
    shiftType: "afternoon",
    startTime: "13:00",
    endTime: "19:00",
    source: "manual"
  });
  assert.throws(function () {
    data.updateWorkScheduleRecord(manual.id, { source: "generated" });
  }, /access is required/);
  assert.strictEqual(data.getWorkScheduleRecord(manual.id).source, "manual");
});

test("unapproved users never receive workplace config in state or exports", function () {
  var storage = createStorage();
  loadData(storage, true);
  var data = loadData(storage, false);
  var snapshot = data.getState();
  assert.strictEqual(snapshot.workSchedule.records.length, 0);
  assert.strictEqual(snapshot.workSchedule.rotation.configured, false);
  assert.strictEqual(
    JSON.parse(data.exportData()).workSchedule.rotation.configured,
    false
  );
});

test("revoking access hides previously seeded workplace records", function () {
  var storage = createStorage();
  loadData(storage, true);
  var data = loadData(storage, false);
  assert.strictEqual(data.getWorkScheduleRecords().length, 0);
  assert.strictEqual(data.getWorkScheduleConfig().rotation.configured, false);
});

test("cross-month expense cannot be linked to an allocation", function () {
  var data = loadData(createStorage());
  var account = createAccount(data);
  var allocation = createAllocation(data, "2026-01");
  var expense = createExpense(data, account.id, "2026-02-10");

  assert.throws(function () {
    data.updateTransaction(expense.id, { allocationId: allocation.id });
  }, /financial month/);
  assert.strictEqual(
    data.getTransaction(expense.id).allocationId,
    undefined
  );
  assert.strictEqual(data.getAllocationActualSpending(allocation.id), 0);
});

test("same-month expense links and counts as actual spending", function () {
  var data = loadData(createStorage());
  var account = createAccount(data);
  var allocation = createAllocation(data, "2026-01");
  var expense = createExpense(data, account.id, "2026-01-10");

  data.updateTransaction(expense.id, { allocationId: allocation.id });
  assert.strictEqual(
    data.getTransaction(expense.id).allocationId,
    allocation.id
  );
  assert.strictEqual(data.getAllocationActualSpending(allocation.id), 100);
});

test("moving a linked expense outside the allocation month is rejected", function () {
  var data = loadData(createStorage());
  var account = createAccount(data);
  var allocation = createAllocation(data, "2026-01");
  var expense = createExpense(data, account.id, "2026-01-10");

  data.updateTransaction(expense.id, { allocationId: allocation.id });
  assert.throws(function () {
    data.updateTransaction(expense.id, { date: "2026-02-10" });
  }, /financial month/);
  assert.strictEqual(data.getTransaction(expense.id).date, "2026-01-10");
  assert.strictEqual(data.getAllocationActualSpending(allocation.id), 100);
});

test("legacy cross-month links stay linked but are excluded from actual spending", function () {
  var storage = createStorage();
  var data = loadData(storage);
  var account = createAccount(data);
  var allocation = createAllocation(data, "2026-01");
  var expense = createExpense(data, account.id, "2026-02-10");
  var raw = JSON.parse(storage.getItem(storageKey));
  raw.transactions[0].allocationId = allocation.id;
  storage.setItem(storageKey, JSON.stringify(raw));

  var reloaded = loadData(storage);
  assert.strictEqual(
    reloaded.getTransaction(expense.id).allocationId,
    allocation.id
  );
  assert.strictEqual(reloaded.getAllocationActualSpending(allocation.id), 0);
});

test("unallocated funds never go negative and over-allocation is separate", function () {
  var data = loadData(createStorage());
  var account = createAccount(data);
  createAllocation(data, "2026-01");
  data.createIncome({
    amount: 300,
    date: "2026-01-05",
    category: "Salary",
    accountId: account.id,
    description: "Income"
  });

  var summary = data.getAllocationSummary("2026-01");
  assert.strictEqual(summary.monthlyIncome, 300);
  assert.strictEqual(summary.totalAllocated, 500);
  assert.strictEqual(summary.unallocatedFunds, 0);
  assert.strictEqual(summary.overAllocatedFunds, 200);
});

test("exactly allocated income reports zero unallocated and zero excess", function () {
  var data = loadData(createStorage());
  var account = createAccount(data);
  createAllocation(data, "2026-01");
  data.createIncome({
    amount: 500,
    date: "2026-01-05",
    category: "Salary",
    accountId: account.id,
    description: "Income"
  });

  var summary = data.getAllocationSummary("2026-01");
  assert.strictEqual(summary.unallocatedFunds, 0);
  assert.strictEqual(summary.overAllocatedFunds, 0);
});

test("partly allocated income reports the remaining unallocated amount", function () {
  var data = loadData(createStorage());
  var account = createAccount(data);
  createAllocation(data, "2026-01");
  data.createIncome({
    amount: 800,
    date: "2026-01-05",
    category: "Salary",
    accountId: account.id,
    description: "Income"
  });

  var summary = data.getAllocationSummary("2026-01");
  assert.strictEqual(summary.unallocatedFunds, 300);
  assert.strictEqual(summary.overAllocatedFunds, 0);
});

test("transfers do not change total wealth", function () {
  var data = loadData(createStorage());
  var first = createAccount(data);
  var second = data.createAccount({
    name: "Second Account",
    type: "savings",
    openingBalance: 500,
    openingBalanceDate: "2026-01-01"
  });
  var before = data.getTotalBalance();
  data.createTransfer({
    amount: 100,
    date: "2026-01-15",
    accountId: first.id,
    toAccountId: second.id,
    description: "Move money"
  });
  assert.strictEqual(data.getTotalBalance(), before);
});

test("server account merge preserves local accounts and enables their use", function () {
  var data = loadData(createStorage());
  var local = createAccount(data);
  data.mergeServerAccounts([
    {
      id: "11111111-1111-4111-8111-111111111111",
      name: "Server Bank",
      type: "bank",
      openingBalance: 250,
      openingBalanceDate: "2026-11-01",
      description: "Server owned",
      status: "active",
      createdAt: "2026-11-01T00:00:00Z",
      updatedAt: "2026-11-01T00:00:00Z"
    }
  ]);
  assert.strictEqual(data.getAccounts().length, 2);
  assert.ok(data.getAccount(local.id));
  var server = data.getAccount("11111111-1111-4111-8111-111111111111");
  assert.strictEqual(server.openingBalance, 250);
  data.createExpense({
    amount: 50,
    date: "2026-11-02",
    accountId: server.id,
    description: "Server account expense"
  });
  assert.strictEqual(
    data.getAccountBalance("11111111-1111-4111-8111-111111111111"),
    200
  );
});

test("server account merge removes only stale server mirrors", function () {
  var data = loadData(createStorage());
  var local = createAccount(data);
  data.mergeServerAccounts([
    {
      id: "22222222-2222-4222-8222-222222222222",
      name: "One",
      type: "cash",
      openingBalance: 10,
      openingBalanceDate: "2026-11-01",
      status: "active"
    }
  ]);
  data.mergeServerAccounts([]);
  assert.ok(data.getAccount(local.id));
  assert.strictEqual(data.getAccount("22222222-2222-4222-8222-222222222222"), null);
});

test("allocation edit opens when purpose is a datalist input", function () {
  var harness = createBudgetHarness();
  var purpose = harness.byId("bd-form-purpose");
  assert.strictEqual(purpose.options, undefined);
  assert.doesNotThrow(function () {
    harness.api.ensureOption(purpose, "Feeding", "Feeding");
  });
  harness.api.setAllocations([
    {
      id: "allocation-1",
      name: "Food plan",
      purpose: "Feeding",
      category: "Feeding",
      financialMonth: "2026-11",
      plannedAmount: 500,
      actualSpending: 100,
      status: "planned",
      description: "Edit me"
    }
  ]);
  harness.api.openEditModal("allocation-1");
  assert.strictEqual(harness.byId("bd-modal-title").textContent, "Edit allocation");
  assert.strictEqual(purpose.value, "Feeding");
  assert.strictEqual(
    harness.byId("bd-allocation-modal").classList.contains("open"),
    true
  );
});

test("server financial sync replaces browser copies with persisted accounts and transactions", function () {
  var storage = createStorage();
  var localData = loadData(storage);
  localData.createAccount({
    name: "Legacy local account",
    type: "cash",
    openingBalance: 999,
    openingBalanceDate: "2026-11-01"
  });

  var responses = {
    "/accounts/": {
      ok: true,
      accounts: [
        {
          id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          name: "Server Bank",
          type: "bank",
          openingBalance: "1000.00",
          openingBalanceDate: "2026-11-01",
          description: "",
          status: "active",
          balance: "1250.00",
          createdAt: "2026-11-01T08:00:00Z",
          updatedAt: "2026-11-03T08:00:00Z"
        },
        {
          id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          name: "Server Cash",
          type: "cash",
          openingBalance: "500.00",
          openingBalanceDate: "2026-11-01",
          description: "",
          status: "active",
          balance: "650.00",
          createdAt: "2026-11-01T08:00:00Z",
          updatedAt: "2026-11-03T08:00:00Z"
        }
      ]
    },
    "/transactions/": {
      ok: true,
      transactions: [
        {
          id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          type: "income",
          amount: "300.00",
          date: "2026-11-02",
          accountId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          toAccountId: null,
          category: "Salary",
          description: "Income",
          paymentMethod: "",
          notes: "",
          allocationId: null,
          createdAt: "2026-11-02T08:00:00Z",
          updatedAt: "2026-11-02T08:00:00Z"
        },
        {
          id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          type: "expense",
          amount: "50.00",
          date: "2026-11-03",
          accountId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          toAccountId: null,
          category: "Food",
          description: "Lunch",
          paymentMethod: "",
          notes: "",
          allocationId: null,
          createdAt: "2026-11-03T08:00:00Z",
          updatedAt: "2026-11-03T08:00:00Z"
        },
        {
          id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
          type: "transfer",
          amount: "150.00",
          date: "2026-11-04",
          accountId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          toAccountId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          category: "",
          description: "Move money",
          paymentMethod: "",
          notes: "",
          allocationId: null,
          createdAt: "2026-11-04T08:00:00Z",
          updatedAt: "2026-11-04T08:00:00Z"
        }
      ]
    }
  };

  var data = loadDataWithFetch(storage, responses);
  return data.syncServerFinancials().then(function (status) {
    assert.strictEqual(status.status, "ready");
    assert.strictEqual(data.getAccounts().length, 2);
    assert.strictEqual(data.getAccount("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").name, "Server Bank");
    assert.strictEqual(data.getAccount("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb").balance, undefined);
    assert.strictEqual(data.getTotalBalance(), 1750);
    assert.strictEqual(
      data.getAccountBalance("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
      1100
    );
    assert.strictEqual(
      data.getAccountBalance("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
      650
    );
    assert.strictEqual(data.getTotalIncome({ month: "2026-11" }), 300);
    assert.strictEqual(data.getTotalExpenses({ month: "2026-11" }), 50);
    assert.strictEqual(data.getTransactions({}).length, 3);
    return data.syncServerFinancials();
  }).then(function () {
    assert.strictEqual(data.getTransactions({}).length, 3);
    assert.strictEqual(data.getAccounts().length, 2);
  });
});

test("failed server financial sync reports an error instead of zero balances", function () {
  var storage = createStorage();
  var data = loadDataWithFetch(storage, {
    "/accounts/": { ok: false },
    "/transactions/": { ok: false }
  });
  return data.syncServerFinancials().then(
    function () {
      throw new Error("Expected the sync to fail.");
    },
    function () {
      var status = data.getServerFinancialStatus();
      assert.strictEqual(status.status, "error");
      assert.ok(/couldn't load/i.test(status.error));
    }
  );
});

test("insights allocation reporting uses the persisted budget API", function () {
  assert.ok(insightsSource.indexOf("BUDGET_API") !== -1);
  assert.ok(insightsSource.indexOf("loadBudget") !== -1);
  assert.strictEqual(insightsSource.indexOf("data.getAllocations"), -1);
  assert.ok(insightsSource.indexOf("serverBudget") !== -1);
});

test("savings supports editing as well as deleting persisted withdrawals", function () {
  assert.ok(savingsSource.indexOf("editingId") !== -1);
  assert.ok(savingsSource.indexOf("edit-withdrawal") !== -1);
  assert.ok(savingsSource.indexOf('API + "withdrawals/" + editing.id + "/"') !== -1);
});

test("editing a withdrawal adds its current amount back only for replacement validation", function () {
  assert.ok(savingsSource.indexOf("function availableForWithdrawal") !== -1);
  assert.ok(
    savingsSource.indexOf("if (Number.isFinite(currentAmount)) available += currentAmount") !== -1
  );
  assert.ok(
    savingsSource.indexOf("var available = availableForWithdrawal(editing)") !== -1
  );
  assert.strictEqual(
    savingsSource.indexOf("money(availableForWithdrawal(withdrawal))"),
    -1
  );
  assert.ok(
    savingsSource.indexOf("setText(\"sv-withdraw-available\", money(roundMoney(informational)))") !== -1
  );
  assert.ok(
    savingsSource.indexOf("Number(state.summary && state.summary.currentBalance)") !== -1
  );
});

test("savings summary icons and badges use the premium gradient scale", function () {
  assert.ok(savingsStyles.indexOf("linear-gradient(135deg, #6366f1, #8b5cf6)") !== -1);
  assert.ok(savingsStyles.indexOf("linear-gradient(135deg, #10b981, #059669)") !== -1);
  assert.ok(savingsStyles.indexOf("linear-gradient(135deg, #ef4444, #dc2626)") !== -1);
  assert.ok(savingsStyles.indexOf("font-size: 0.5rem") !== -1);
  assert.ok(savingsStyles.indexOf(".sv-summary-icon {\n  display: inline-flex;") !== -1);
});

test("profile save disables the first click and reports saving state", function () {
  assert.ok(settingsSource.indexOf("if (profileSaving) return;") !== -1);
  assert.ok(settingsSource.indexOf("Saving Changes...") !== -1);
  assert.ok(settingsSource.indexOf("setProfileSaving(true)") !== -1);
  assert.ok(settingsSource.indexOf("setProfileSaving(false)") !== -1);
});

test("saved profile images propagate through shared avatar events", function () {
  assert.ok(
    settingsSource.indexOf('new CustomEvent("gabsweb:profile-image-updated")') !== -1
  );
  assert.ok(
    globalNavigationSource.indexOf('"gabsweb:profile-image-updated"') !== -1
  );
  assert.ok(globalNavigationSource.indexOf("updateProfileImages") !== -1);
});

test("dashboard hides available after savings until savings load", function () {
  assert.ok(dashboardSource.indexOf("savingsReady") !== -1);
  assert.ok(dashboardSource.indexOf("hasSavings ? total - savings : null") !== -1);
  assert.ok(dashboardSource.indexOf("available == null") !== -1);
});

test("my finances available after savings uses the persisted savings API", function () {
  assert.ok(transactionSource.indexOf("SAVINGS_URL") !== -1);
  assert.ok(transactionSource.indexOf("loadServerSavings") !== -1);
  assert.ok(transactionSource.indexOf("serverSavings.summary") !== -1);
  assert.strictEqual(transactionSource.indexOf("data.getSavingsBalance"), -1);
});

test("my finances can link an expense to a fully funded goal", function () {
  assert.ok(transactionSource.indexOf("GOALS_URL") !== -1);
  assert.ok(transactionSource.indexOf("loadServerGoals") !== -1);
  assert.ok(transactionSource.indexOf("fillGoalSelect") !== -1);
  assert.ok(transactionTemplate.indexOf('id="mf-tx-goal"') !== -1);
  assert.ok(transactionTemplate.indexOf("Goal purchase (optional)") !== -1);
});

test("goals distinguish fully funded plans from recorded purchases", function () {
  assert.ok(goalsSource.indexOf("Fully funded — purchase not recorded") !== -1);
  assert.ok(goalsSource.indexOf("Applied to purchase") !== -1);
  assert.ok(goalsSource.indexOf("assignment.released") !== -1);
  assert.ok(goalsSource.indexOf("consumedAmount") !== -1);
  assert.ok(goalsSource.indexOf("returned to available funding") !== -1);
  assert.ok(goalsSource.indexOf("Funding history") !== -1);
  assert.ok(goalsTemplate.indexOf('id="gl-goal-history-grid"') !== -1);
});


test("one transaction delete action reuses the in-flight request", function () {
  var harness = createTransactionHarness();
  var first = harness.api.requestTransactionDelete("tx-1");
  var second = harness.api.requestTransactionDelete("tx-1");
  assert.strictEqual(first, second);
  assert.strictEqual(harness.postCount(), 1);
  assert.strictEqual(harness.pendingDeletes(), 1);
  harness.resolveDelete({ ok: true, deleted: "tx-1" }, 200);
  return first.then(function () {
    assert.strictEqual(harness.postCount(), 1);
  });
});

test("blocked goal purchase deletion uses readable friendly feedback", function () {
  var harness = createTransactionHarness();
  var feedback = harness.api.transactionDeleteFeedback(
    {
      status: 409,
      payload: { code: "goal_funding_reassigned" },
      message: "Internal funding message",
    },
    "Laptop"
  );
  assert.strictEqual(feedback.title, "Cannot delete this expense yet");
  assert.strictEqual(
    feedback.message,
    "Some of this Goal's money has already been assigned to another Goal. " +
      "Remove that assignment first, then try deleting this expense again."
  );
  assert.strictEqual(feedback.type, "warning");
  assert.ok(feedback.duration >= 6000 && feedback.duration <= 8000);
});

test("network deletion failures use friendly wording instead of Failed to fetch", function () {
  var harness = createTransactionHarness();
  var feedback = harness.api.transactionDeleteFeedback(
    { message: "Failed to fetch" },
    "Laptop"
  );
  assert.strictEqual(feedback.title, "Could not delete transaction");
  assert.strictEqual(
    feedback.message,
    "Couldn't connect. Check your connection and try again."
  );
  assert.ok(feedback.duration > 0);
});

test("goal over-commitment wording is simple and consistent", function () {
  assert.ok(
    goalsSource.indexOf(
      "This change would use more money than is available for your Goals."
    ) !== -1
  );
  assert.ok(
    goalsSource.indexOf(
      "Reduce the assignment or add more money to your Goals budget before trying again."
    ) !== -1
  );
  assert.ok(transactionSource.indexOf('"goal_funding_reassigned"') !== -1);
  assert.ok(transactionSource.indexOf("7000") !== -1);
  assert.ok(transactionSource.indexOf("transactionsRequestVersion") !== -1);
});


test("cashflow localStorage is namespaced per authenticated user", function () {
  var storage = createStorage();
  var first = loadDataForUser(storage, "user-1", "first@example.com");
  first.updateProfile({ name: "First User" });
  assert.strictEqual(storage.getItem(storageKey + ":user:user-1") !== null, true);
  assert.strictEqual(storage.getItem(storageKey), null);

  var second = loadDataForUser(storage, "user-2", "second@example.com");
  assert.notStrictEqual(second.getProfile().name, "First User");
  second.updateProfile({ name: "Second User" });

  var firstAgain = loadDataForUser(storage, "user-1", "first@example.com");
  assert.strictEqual(firstAgain.getProfile().name, "First User");
  var secondAgain = loadDataForUser(storage, "user-2", "second@example.com");
  assert.strictEqual(secondAgain.getProfile().name, "Second User");
});

test("legacy localStorage is migrated only when its email matches the user", function () {
  var storage = createStorage();
  var owner = loadDataForUser(storage, "user-legacy", "owner@example.com");
  owner.updateProfile({ name: "Legacy Owner", email: "owner@example.com" });
  var legacyRaw = storage.getItem(storageKey + ":user:user-legacy");
  storage.removeItem(storageKey + ":user:user-legacy");
  storage.setItem(storageKey, legacyRaw);

  var stranger = loadDataForUser(storage, "user-stranger", "stranger@example.com");
  assert.notStrictEqual(stranger.getProfile().name, "Legacy Owner");
  assert.notStrictEqual(
    JSON.parse(storage.getItem(storageKey + ":user:user-stranger")).settings.name,
    "Legacy Owner"
  );

  var matching = loadDataForUser(storage, "user-legacy", "owner@example.com");
  assert.strictEqual(matching.getProfile().name, "Legacy Owner");
  assert.strictEqual(storage.getItem(storageKey + ":user:user-legacy") !== null, true);
});


test("settings loads the signed-in profile independently of workplace data", function () {
  return createSettingsHarness().then(function (harness) {
    assert.strictEqual(harness.calls.indexOf("/profile-api/") !== -1, true);
    assert.strictEqual(harness.calls.indexOf("/work-profile-api/") !== -1, true);
    assert.strictEqual(harness.element("profile-name").value, "First Person");
    assert.strictEqual(harness.element("profile-email").value, "first@example.com");
    assert.strictEqual(harness.element("profile-role").value, "Accountant");
    assert.strictEqual(
      harness.element("profile-detail-name").textContent,
      "First Person"
    );
    assert.strictEqual(
      harness.element("profile-detail-email").textContent,
      "first@example.com"
    );
    assert.strictEqual(
      harness.element("profile-detail-role").textContent,
      "Accountant"
    );
    assert.notStrictEqual(
      harness.element("toast-title").textContent,
      "Profile Not Loaded"
    );
  });
});

Promise.all(pendingTests)
  .then(function () {
    console.log("All financial regression tests passed.");
  })
  .catch(function (error) {
    console.error(error && error.stack ? error.stack : error);
    process.exit(1);
  });
