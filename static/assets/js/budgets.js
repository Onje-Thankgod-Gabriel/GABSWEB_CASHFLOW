/* Gabsweb CashFlow — Budget & Money Allocation */
(function () {
  "use strict";

  var data = window.GabswebCashFlowData;
  if (!data) return;

  var STATUS_ORDER = ["planned", "active", "completed", "cancelled"];
  var STATUS_META = {
    planned: { label: "Planned", icon: "clock", tone: "is-planned" },
    active: { label: "Active", icon: "activity", tone: "is-active" },
    completed: { label: "Completed", icon: "check", tone: "is-completed" },
    cancelled: { label: "Cancelled", icon: "ban", tone: "is-cancelled" },
  };
  var PURPOSE_OPTIONS = [
    "Feeding",
    "Transportation",
    "Rent",
    "Family support",
    "Personal expenses",
    "Utilities",
    "Emergency fund",
    "Savings",
    "Education",
    "Health",
    "Goals",
    "Other",
  ];
  var PURPOSE_ICONS = {
    feeding: { icon: "utensils", tone: "is-income" },
    food: { icon: "utensils", tone: "is-income" },
    transportation: { icon: "car", tone: "is-transfer" },
    transport: { icon: "car", tone: "is-transfer" },
    rent: { icon: "home", tone: "is-warning" },
    housing: { icon: "home", tone: "is-warning" },
    family: { icon: "users", tone: "is-purple" },
    "family support": { icon: "users", tone: "is-purple" },
    personal: { icon: "user", tone: "is-transfer" },
    "personal expenses": { icon: "user", tone: "is-transfer" },
    utilities: { icon: "zap", tone: "is-warning" },
    emergency: { icon: "shield-check", tone: "is-income" },
    "emergency fund": { icon: "shield-check", tone: "is-income" },
    savings: { icon: "coins", tone: "is-income" },
    goals: { icon: "target", tone: "is-purple" },
    education: { icon: "book-open", tone: "is-transfer" },
    health: { icon: "heart", tone: "is-expense" },
    other: { icon: "target", tone: "is-neutral" },
  };
  var TOAST_ICONS = {
    success: "check",
    error: "alert-circle",
    info: "info",
    warning: "alert-triangle",
  };

  var currentMonthKey = getTodayKey().slice(0, 7);
  var INITIAL_LIST_SIZE = 6;
  var state = {
    selectedMonth: currentMonthKey,
    status: "all",
    search: "",
    editingId: null,
    detailId: null,
    editingCategoryId: null,
    allocationsExpanded: false,
    historyExpanded: false,
  };
  var confirmHandler = null;
  var toastTimer = null;
  var accountMap = Object.create(null);
  var BUDGET_API = "/budget-api/";
  var serverState = {
    loaded: false,
    loading: true,
    error: "",
    allocations: [],
    categories: [],
    months: [],
    transactions: [],
    accounts: [],
  };

  function csrfToken() {
    var token = byId("bd-csrf-token");
    return token ? token.value : "";
  }

  function apiRequest(url, options) {
    var config = options || {};
    return window.GabswebApi.request(url, {
      method: config.method || "GET",
      body: config.body,
      csrfToken: csrfToken(),
    });
  }

  function normalizeTransaction(entry) {
    return {
      id: String(entry.id),
      type: entry.type,
      amount: Number(entry.amount || 0),
      date: entry.date,
      accountId: String(entry.accountId),
      toAccountId: entry.toAccountId ? String(entry.toAccountId) : undefined,
      category: entry.category || "",
      description: entry.description || "",
      paymentMethod: entry.paymentMethod || "",
      notes: entry.notes || "",
      allocationId: entry.allocationId ? String(entry.allocationId) : null,
      allocationName: entry.allocationName || "",
      createdAt: entry.createdAt || "",
      updatedAt: entry.updatedAt || "",
    };
  }

  function normalizeAllocation(allocation) {
    return {
      id: String(allocation.id),
      name: allocation.name || "",
      purpose: allocation.purpose || allocation.category || "",
      category: allocation.category || allocation.purpose || "",
      categoryId: allocation.categoryId ? String(allocation.categoryId) : "",
      financialMonth: allocation.financialMonth,
      plannedAmount: Number(allocation.plannedAmount || 0),
      actualSpending: Number(allocation.actualSpending || 0),
      remainingAmount: Number(allocation.remainingAmount || 0),
      status: allocation.status || "planned",
      description: allocation.description || "",
      notes: allocation.notes || "",
      createdAt: allocation.createdAt || "",
      updatedAt: allocation.updatedAt || "",
    };
  }

  function normalizeMonth(summary) {
    return {
      month: summary.month,
      monthlyIncome: Number(summary.monthlyIncome || 0),
      totalAllocated: Number(summary.totalAllocated || 0),
      unallocatedFunds: Math.max(0, Number(summary.unallocatedFunds || 0)),
      overAllocatedFunds: Math.max(
        0,
        Number(summary.overAllocatedFunds || 0)
      ),
      totalActualSpending: Number(summary.totalActualSpending || 0),
      totalRemaining: Number(summary.totalRemaining || 0),
      allocationCount: Number(summary.allocationCount || 0),
    };
  }

  function errorText(error, fallback) {
    var fields = window.GabswebApi.fieldErrors(error && error.payload);
    var parts = [];
    Object.keys(fields).forEach(function (field) {
      parts = parts.concat(fields[field]);
    });
    if (parts.length) return parts.join(" ");
    return window.GabswebApi.errorMessage(error, fallback);
  }

  function setFieldError(field, message) {
    var element = document.querySelector(
      '[data-bd-field-error="' + field + '"]'
    );
    if (!element) return;
    if (message) {
      element.textContent = message;
      element.classList.add("is-visible");
    } else {
      element.textContent = "";
      element.classList.remove("is-visible");
    }
  }

  function clearFieldErrors() {
    document.querySelectorAll("[data-bd-field-error]").forEach(function (element) {
      element.textContent = "";
      element.classList.remove("is-visible");
    });
  }

  function showFieldErrors(error) {
    var mapping = {
      planned_amount: "amount",
      category: "purpose",
    };
    var fields = window.GabswebApi.fieldErrors(error && error.payload);
    var shown = false;
    Object.keys(fields).forEach(function (field) {
      if (field === "__all__" || !fields[field].length) return;
      setFieldError(mapping[field] || field, fields[field][0]);
      shown = true;
    });
    return shown;
  }

  function loadBudgetData() {
    serverState.loading = true;
    serverState.error = "";
    return Promise.all([
      apiRequest(BUDGET_API + "?month=" + encodeURIComponent(state.selectedMonth)),
      apiRequest("/transactions/"),
      apiRequest("/accounts/"),
    ])
      .then(function (results) {
        var budgetPayload = results[0] || {};
        var transactionPayload = results[1] || {};
        var accountPayload = results[2] || {};
        serverState.allocations = (
          budgetPayload.allocations || []
        ).map(normalizeAllocation);
        serverState.categories = budgetPayload.categories || [];
        serverState.months = (budgetPayload.months || []).map(normalizeMonth);
        serverState.selected = budgetPayload.selected
          ? normalizeMonth(budgetPayload.selected)
          : null;
        serverState.transactions = (
          transactionPayload.transactions || []
        ).map(normalizeTransaction);
        serverState.accounts = (accountPayload.accounts || []).map(function (
          account
        ) {
          return {
            id: String(account.id),
            name: account.name || "",
            balance: Number(account.balance || 0),
          };
        });
        serverState.loaded = true;
      })
      .catch(function (error) {
        serverState.error = window.GabswebApi.errorMessage(
          error,
          "We couldn't load your budget. Please try again."
        );
        throw error;
      })
      .then(
        function () {
          serverState.loading = false;
          renderAll();
        },
        function (error) {
          serverState.loading = false;
          renderAll();
          throw error;
        }
      );
  }

  /* ---------- Small helpers ---------- */

  function byId(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    var replacements = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return String(value === null || value === undefined ? "" : value).replace(
      /[&<>"']/g,
      function (character) {
        return replacements[character];
      }
    );
  }

  function iconMarkup(name, className) {
    return (
      '<i data-lucide="' +
      escapeHtml(name) +
      '" class="' +
      escapeHtml(className || "w-4 h-4") +
      '"></i>'
    );
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function pad2(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function getTodayKey() {
    if (typeof data.todayKey === "function") return data.todayKey();
    var now = new Date();
    return (
      now.getFullYear() +
      "-" +
      pad2(now.getMonth() + 1) +
      "-" +
      pad2(now.getDate())
    );
  }

  function getMonthKey(value) {
    return value ? String(value).slice(0, 7) : "";
  }

  function isMonthKey(value) {
    return /^\d{4}-(0[1-9]|1[0-2])$/.test(String(value || ""));
  }

  function parseDateOnly(value) {
    if (!value) return null;
    var parts = String(value).split("-");
    if (parts.length !== 3) return null;
    var parsed = new Date(
      Number(parts[0]),
      Number(parts[1]) - 1,
      Number(parts[2]),
      12,
      0,
      0
    );
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function formatMonth(monthKey) {
    var parsed = parseDateOnly(String(monthKey) + "-01");
    if (!parsed) return monthKey;
    try {
      return new Intl.DateTimeFormat("en-NG", {
        month: "long",
        year: "numeric",
      }).format(parsed);
    } catch (error) {
      return monthKey;
    }
  }

  function formatDate(value) {
    var parsed = parseDateOnly(value);
    if (!parsed) return "—";
    try {
      return new Intl.DateTimeFormat("en-NG", {
        month: "short",
        day: "numeric",
        year: "numeric",
      }).format(parsed);
    } catch (error) {
      return value;
    }
  }

  function getCurrency() {
    try {
      var settings = data.getSettings();
      return settings && settings.currency ? settings.currency : "NGN";
    } catch (error) {
      return "NGN";
    }
  }

  function formatMoney(amount) {
    var value = Number(amount);
    if (!Number.isFinite(value)) value = 0;
    try {
      return new Intl.NumberFormat("en-NG", {
        style: "currency",
        currency: getCurrency(),
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(value);
    } catch (error) {
      return "NGN " + value.toFixed(2);
    }
  }

  function roundMoney(value) {
    var number = Number(value);
    if (!Number.isFinite(number)) return 0;
    return Math.round((number + Number.EPSILON) * 100) / 100;
  }

  function pluralize(count, singular, plural) {
    return count === 1 ? singular : plural || singular + "s";
  }

  function statusDetails(status) {
    var key = String(status || "").toLowerCase();
    if (STATUS_META[key]) return STATUS_META[key];
    return {
      label: status ? String(status) : "Planned",
      icon: "circle",
      tone: "is-planned",
    };
  }

  function purposeDetails(allocation) {
    var purpose = String(
      (allocation && allocation.purpose) || (allocation && allocation.category) || ""
    )
      .trim()
      .toLowerCase();
    if (PURPOSE_ICONS[purpose]) return PURPOSE_ICONS[purpose];
    var keys = Object.keys(PURPOSE_ICONS);
    for (var index = 0; index < keys.length; index += 1) {
      if (purpose && purpose.indexOf(keys[index]) !== -1) {
        return PURPOSE_ICONS[keys[index]];
      }
    }
    return { icon: "target", tone: "is-neutral" };
  }

  function accountName(accountId) {
    var account = accountMap[accountId];
    return account ? account.name : "Unknown account";
  }

  function allocationProgress(allocation, actual) {
    var planned = Number(allocation && allocation.plannedAmount) || 0;
    if (planned <= 0) return null;
    return roundMoney((actual / planned) * 100);
  }

  function progressTone(percentage) {
    if (percentage === null) return "";
    if (percentage > 100) return "is-danger";
    if (percentage >= 100) return "is-complete";
    if (percentage >= 80) return "is-warning";
    return "";
  }

  /* ---------- Data helpers ---------- */

  function allAllocations() {
    return serverState.allocations.slice();
  }

  function allocationsForMonth(month) {
    return serverState.allocations
      .filter(function (allocation) {
        return allocation.financialMonth === month;
      })
      .sort(function (a, b) {
        return String(a.name || "").localeCompare(String(b.name || ""));
      });
  }

  function getAllocation(allocationId) {
    for (var index = 0; index < serverState.allocations.length; index += 1) {
      if (serverState.allocations[index].id === allocationId) {
        return serverState.allocations[index];
      }
    }
    return null;
  }

  function getTransactions(filters) {
    var options = filters || {};
    return serverState.transactions.filter(function (transaction) {
      if (options.type && transaction.type !== options.type) return false;
      if (
        options.month &&
        getMonthKey(transaction.date) !== options.month
      ) {
        return false;
      }
      if (
        options.allocationId &&
        transaction.allocationId !== options.allocationId
      ) {
        return false;
      }
      return true;
    });
  }

  function linkedExpenses(allocationId) {
    return getTransactions({ allocationId: allocationId, type: "expense" })
      .slice()
      .sort(function (a, b) {
        if (a.date !== b.date) return a.date < b.date ? 1 : -1;
        return String(b.createdAt || "").localeCompare(String(a.createdAt || ""));
      });
  }

  function unlinkedExpenses(month) {
    var filters = { type: "expense" };
    if (month) filters.month = month;
    return getTransactions(filters)
      .filter(function (transaction) {
        return !transaction.allocationId;
      })
      .sort(function (a, b) {
        if (a.date !== b.date) return a.date < b.date ? 1 : -1;
        return String(b.createdAt || "").localeCompare(String(a.createdAt || ""));
      });
  }

  function isExpenseInAllocationPeriod(transaction, allocation) {
    return Boolean(
      transaction &&
        allocation &&
        getMonthKey(transaction.date) === allocation.financialMonth
    );
  }

  function getAllocationActualSpending(allocationId) {
    var allocation = getAllocation(allocationId);
    return allocation ? allocation.actualSpending : 0;
  }

  function getAllocationSummary(month) {
    for (var index = 0; index < serverState.months.length; index += 1) {
      if (serverState.months[index].month === month) {
        return serverState.months[index];
      }
    }
    if (serverState.selected && serverState.selected.month === month) {
      return serverState.selected;
    }
    var allocations = allocationsForMonth(month).filter(function (allocation) {
      return allocation.status !== "cancelled";
    });
    var totalAllocated = roundMoney(
      allocations.reduce(function (sum, allocation) {
        return sum + allocation.plannedAmount;
      }, 0)
    );
    var totalActual = roundMoney(
      allocations.reduce(function (sum, allocation) {
        return sum + allocation.actualSpending;
      }, 0)
    );
    var monthlyIncome = roundMoney(
      getTransactions({ type: "income", month: month }).reduce(function (
        sum,
        transaction
      ) {
        return sum + transaction.amount;
      },
      0)
    );
    var difference = roundMoney(monthlyIncome - totalAllocated);
    return {
      month: month,
      monthlyIncome: monthlyIncome,
      totalAllocated: totalAllocated,
      unallocatedFunds: Math.max(0, difference),
      overAllocatedFunds: Math.max(0, roundMoney(-difference)),
      totalActualSpending: totalActual,
      totalRemaining: roundMoney(totalAllocated - totalActual),
      allocationCount: allocations.length,
    };
  }

  /* ---------- Server-backed mutations ---------- */

  function allocationRequestBody(payload) {
    return JSON.stringify({
      month: payload.financialMonth,
      category: payload.purpose,
      name: payload.name,
      planned_amount: String(roundMoney(payload.plannedAmount)),
      status: payload.status,
      description: payload.description || "",
    });
  }

  function createAllocationOnServer(payload) {
    return apiRequest("/budget-api/allocations/", {
      method: "POST",
      body: allocationRequestBody(payload),
    });
  }

  function updateAllocationOnServer(allocationId, payload) {
    return apiRequest(
      "/budget-api/allocations/" + encodeURIComponent(allocationId) + "/",
      {
        method: "POST",
        body: allocationRequestBody(payload),
      }
    );
  }

  function updateAllocationStatusOnServer(allocationId, status) {
    return apiRequest(
      "/budget-api/allocations/" +
        encodeURIComponent(allocationId) +
        "/status/",
      {
        method: "POST",
        body: JSON.stringify({ status: status }),
      }
    );
  }

  function deleteAllocationOnServer(allocationId) {
    return apiRequest(
      "/budget-api/allocations/" +
        encodeURIComponent(allocationId) +
        "/delete/",
      { method: "POST" }
    );
  }

  function updateTransactionAllocationOnServer(transactionId, allocationId) {
    return apiRequest(
      "/transactions/" + encodeURIComponent(transactionId) + "/allocation/",
      {
        method: "POST",
        body: JSON.stringify({ allocationId: allocationId || null }),
      }
    );
  }

  function createCategoryOnServer(name) {
    return apiRequest("/budget-api/categories/", {
      method: "POST",
      body: JSON.stringify({ name: name }),
    });
  }

  function updateCategoryOnServer(categoryId, name) {
    return apiRequest(
      "/budget-api/categories/" + encodeURIComponent(categoryId) + "/",
      {
        method: "POST",
        body: JSON.stringify({ name: name }),
      }
    );
  }

  function deleteCategoryOnServer(categoryId) {
    return apiRequest(
      "/budget-api/categories/" + encodeURIComponent(categoryId) + "/delete/",
      { method: "POST" }
    );
  }

  /* ---------- Month selector ---------- */

  function buildMonthOptions() {
    var months = Object.create(null);
    months[currentMonthKey] = true;
    months[state.selectedMonth] = true;

    allAllocations().forEach(function (allocation) {
      if (isMonthKey(allocation.financialMonth)) {
        months[allocation.financialMonth] = true;
      }
    });
    serverState.months.forEach(function (summary) {
      if (isMonthKey(summary.month)) months[summary.month] = true;
    });
    getTransactions({ type: "income" }).forEach(function (transaction) {
      var month = getMonthKey(transaction.date);
      if (isMonthKey(month)) months[month] = true;
    });

    var list = Object.keys(months)
      .filter(isMonthKey)
      .sort()
      .reverse();
    if (list.length === 0) list = [currentMonthKey];

    var select = byId("bd-month-select");
    if (!select) return;
    select.innerHTML = list
      .map(function (month) {
        return (
          '<option value="' +
          escapeHtml(month) +
          '">' +
          escapeHtml(formatMonth(month)) +
          "</option>"
        );
      })
      .join("");
    select.value = state.selectedMonth;
  }

  /* ---------- Sidebar & storage ---------- */

  function renderShell() {
    var totalBalance = serverState.accounts.reduce(function (sum, account) {
      return sum + account.balance;
    }, 0);
    var income = getTransactions({
      type: "income",
      month: currentMonthKey,
    }).reduce(function (sum, transaction) {
      return sum + transaction.amount;
    }, 0);
    var expenses = getTransactions({
      type: "expense",
      month: currentMonthKey,
    }).reduce(function (sum, transaction) {
      return sum + transaction.amount;
    }, 0);
    var netFlow = roundMoney(income - expenses);
    var balanceEl = byId("sidebar-total-balance");
    var noteEl = byId("sidebar-balance-note");

    if (balanceEl) balanceEl.textContent = formatMoney(totalBalance);
    if (noteEl) {
      noteEl.textContent = serverState.loading
        ? "Loading finances…"
        : netFlow === 0
          ? "No change this month"
          : (netFlow > 0 ? "+" : "") + formatMoney(netFlow) + " net this month";
    }

    var chip = byId("bd-storage-chip");
    var label = byId("bd-storage-label");
    if (!chip || !label) return;
    chip.classList.toggle("is-warning", Boolean(serverState.error));
    if (serverState.error) {
      label.textContent = "Couldn't load budget";
      chip.title = "We couldn't load your budget. Please try again.";
    } else if (serverState.loading) {
      label.textContent = "Loading…";
      chip.title = "Loading your budget…";
    } else {
      label.textContent = "Budget up to date";
      chip.title = "Your budget is saved to your account.";
    }
  }

  /* ---------- Overview ---------- */

  function renderOverview() {
    var summary = getAllocationSummary(state.selectedMonth);
    var monthAllocations = allocationsForMonth(state.selectedMonth);
    var activeAllocations = monthAllocations.filter(function (allocation) {
      return allocation.status !== "cancelled";
    });
    var activeIds = Object.create(null);
    activeAllocations.forEach(function (allocation) {
      activeIds[allocation.id] = true;
    });
    var monthTransactions = getTransactions({ month: state.selectedMonth });
    var incomeCount = monthTransactions.filter(function (transaction) {
      return transaction.type === "income";
    }).length;
    var linkedCount = monthTransactions.filter(function (transaction) {
      return transaction.type === "expense" && activeIds[transaction.allocationId];
    }).length;

    byId("bd-overview-month-label").textContent =
      formatMonth(state.selectedMonth) +
      " · " +
      pluralize(activeAllocations.length, "active plan", "active plans");

    if (serverState.loading || serverState.error) {
      var pendingText = serverState.error
        ? "Budget data could not be loaded"
        : "Loading your budget…";
      var pendingNote = serverState.error
        ? "We couldn't load your saved budget. Please try again."
        : "Your saved budget is loading.";
      ["bd-total-income", "bd-total-allocated", "bd-unallocated", "bd-actual-spending"].forEach(
        function (id) {
          var element = byId(id);
          if (!element) return;
          element.textContent = "—";
          element.classList.remove("is-positive", "is-negative");
        }
      );
      byId("bd-income-note").textContent = pendingNote;
      byId("bd-allocated-badge").textContent = pendingText;
      byId("bd-allocated-note").textContent = pendingNote;
      byId("bd-unallocated-badge").textContent = pendingText;
      byId("bd-unallocated-note").textContent = pendingNote;
      byId("bd-spending-note").textContent = pendingNote;
      var pendingBanner = byId("bd-overallocation-banner");
      if (pendingBanner) pendingBanner.hidden = true;
      return;
    }

    var incomeEl = byId("bd-total-income");
    incomeEl.textContent = formatMoney(summary.monthlyIncome);
    incomeEl.classList.toggle("is-positive", summary.monthlyIncome > 0);
    byId("bd-income-note").textContent = incomeCount
      ? incomeCount + " " + pluralize(incomeCount, "income entry", "income entries") + " this month"
      : "No income recorded this month";

    byId("bd-total-allocated").textContent = formatMoney(summary.totalAllocated);
    byId("bd-allocated-badge").textContent =
      activeAllocations.length +
      " " +
      pluralize(activeAllocations.length, "allocation", "allocations");
    var allocatedPercent =
      summary.monthlyIncome > 0
        ? Math.round((summary.totalAllocated / summary.monthlyIncome) * 100)
        : null;
    byId("bd-allocated-note").textContent =
      activeAllocations.length === 0
        ? "No allocations planned yet"
        : allocatedPercent === null
          ? "Income not recorded for this month"
          : allocatedPercent + "% of recorded income allocated";

    var unallocatedEl = byId("bd-unallocated");
    unallocatedEl.textContent = formatMoney(summary.unallocatedFunds);
    unallocatedEl.classList.toggle("is-positive", summary.unallocatedFunds > 0);
    var unallocatedBadge = byId("bd-unallocated-badge");
    var unallocatedNote = byId("bd-unallocated-note");
    if (summary.overAllocatedFunds > 0) {
      unallocatedBadge.textContent = "Fully allocated";
      unallocatedNote.textContent =
        "All available income is allocated. Budget is over-allocated by " +
        formatMoney(summary.overAllocatedFunds) +
        ".";
    } else if (summary.monthlyIncome === 0 && summary.totalAllocated === 0) {
      unallocatedBadge.textContent = "To assign";
      unallocatedNote.textContent = "Income minus total allocated";
    } else {
      unallocatedBadge.textContent = "To assign";
      unallocatedNote.textContent =
        "Monthly income minus planned allocations — not your total account balance";
    }

    var overallocationBanner = byId("bd-overallocation-banner");
    var overallocationAmount = byId("bd-overallocated-amount");
    if (overallocationBanner && overallocationAmount) {
      overallocationAmount.textContent = formatMoney(
        summary.overAllocatedFunds
      );
      overallocationBanner.hidden = summary.overAllocatedFunds <= 0;
    }

    byId("bd-actual-spending").textContent = formatMoney(
      summary.totalActualSpending
    );
    byId("bd-spending-note").textContent = linkedCount
      ? linkedCount +
        " linked " +
        pluralize(linkedCount, "expense", "expenses") +
        " against this month's plans"
      : "No linked expenses yet";
  }

  /* ---------- Allocation cards ---------- */

  function matchesFilters(allocation) {
    if (state.status !== "all" && allocation.status !== state.status) {
      return false;
    }
    if (!state.search) return true;
    var haystack = [
      allocation.name,
      allocation.purpose,
      allocation.category,
      allocation.description,
      allocation.notes,
    ]
      .join(" ")
      .toLowerCase();
    return haystack.indexOf(state.search) !== -1;
  }

  function allocationCard(allocation) {
    var actual = getAllocationActualSpending(allocation.id);
    var remaining = roundMoney(Number(allocation.plannedAmount || 0) - actual);
    var percentage = allocationProgress(allocation, actual);
    var status = statusDetails(allocation.status);
    var purpose = purposeDetails(allocation);
    var linked = linkedExpenses(allocation.id);
    var width = percentage === null ? 0 : Math.min(percentage, 100);
    var progressText =
      percentage === null ? "No planned amount" : percentage.toFixed(1) + "% used";
    var remainingLabel =
      remaining < 0
        ? "Over by " + formatMoney(Math.abs(remaining))
        : formatMoney(remaining) + " left";

    return (
      '<article class="bd-allocation-card' +
      (allocation.status === "cancelled" ? " is-cancelled" : "") +
      '">' +
      '<div class="bd-allocation-top">' +
      '<div class="bd-allocation-identity">' +
      '<span class="bd-allocation-icon ' +
      purpose.tone +
      '">' +
      iconMarkup(purpose.icon, "w-5 h-5") +
      "</span>" +
      '<div class="bd-allocation-copy">' +
      '<h3 class="bd-allocation-name" title="' +
      escapeHtml(allocation.name) +
      '">' +
      escapeHtml(allocation.name || "Untitled allocation") +
      "</h3>" +
      '<p class="bd-allocation-purpose">' +
      iconMarkup("tag", "w-3 h-3") +
      "<span>" +
      escapeHtml(allocation.purpose || allocation.category || "General purpose") +
      "</span></p>" +
      "</div></div>" +
      '<span class="bd-status-pill ' +
      status.tone +
      '">' +
      iconMarkup(status.icon, "w-3 h-3") +
      escapeHtml(status.label) +
      "</span>" +
      "</div>" +
      '<div class="bd-allocation-amount"><span>Planned amount</span><strong>' +
      escapeHtml(formatMoney(allocation.plannedAmount)) +
      "</strong></div>" +
      '<div class="bd-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' +
      escapeHtml(String(percentage === null ? 0 : Math.round(Math.min(percentage, 100)))) +
      '"><div class="bd-progress-fill ' +
      progressTone(percentage) +
      '" style="width: ' +
      width +
      '%"></div></div>' +
      '<div class="bd-allocation-meta"><span>' +
      escapeHtml(formatMoney(actual)) +
      " spent · " +
      escapeHtml(progressText) +
      '</span><strong class="' +
      (remaining < 0 ? "is-negative" : "is-positive") +
      '">' +
      escapeHtml(remainingLabel) +
      "</strong></div>" +
      '<div class="bd-allocation-footer">' +
      '<span class="bd-allocation-footnote">' +
      iconMarkup("receipt", "w-3 h-3") +
      escapeHtml(
        linked.length
          ? linked.length + " linked " + pluralize(linked.length, "expense", "expenses")
          : "No linked expenses"
      ) +
      "</span>" +
      '<button type="button" class="bd-btn bd-btn-ghost bd-btn-small" data-bd-details="' +
      escapeHtml(allocation.id) +
      '">View details' +
      iconMarkup("chevron-right", "w-4 h-4") +
      "</button>" +
      "</div>" +
      "</article>"
    );
  }

  function listToggleMarkup(key, total, expanded, singular, plural) {
    if (total <= INITIAL_LIST_SIZE) return "";
    return (
      '<button type="button" class="bd-btn bd-btn-ghost bd-btn-small bd-list-toggle" data-bd-toggle="' +
      escapeHtml(key) +
      '">' +
      iconMarkup(expanded ? "chevron-up" : "chevron-down", "w-4 h-4") +
      "<span>" +
      (expanded
        ? "Show fewer"
        : "View all " +
          total +
          " " +
          pluralize(total, singular || "record", plural || "records")) +
      "</span></button>"
    );
  }

  function renderAllocations() {
    var monthAllocations = allocationsForMonth(state.selectedMonth);
    var visible = monthAllocations.filter(matchesFilters);
    var grid = byId("bd-allocation-grid");
    var summaryLine = byId("bd-allocation-summary-line");
    if (!grid) return;

    if (serverState.loading || serverState.error) {
      summaryLine.textContent = serverState.error
        ? "Budget allocations could not be loaded."
        : "Loading your budget allocations…";
      grid.innerHTML =
        '<div class="bd-empty bd-empty-wide">' +
        '<div class="bd-empty-icon">' +
        iconMarkup(serverState.error ? "alert-circle" : "loader", "w-5 h-5") +
        "</div><h3>" +
        (serverState.error ? "Budget unavailable" : "Loading allocations…") +
        "</h3><p>" +
        (serverState.error
          ? "We couldn't load your saved allocations. Please try again."
          : "Your saved allocations will appear here as soon as they finish loading.") +
        "</p></div>";
      refreshIcons();
      return;
    }

    var active = monthAllocations.filter(function (allocation) {
      return allocation.status !== "cancelled";
    });
    var plannedTotal = active.reduce(function (sum, allocation) {
      return sum + Number(allocation.plannedAmount || 0);
    }, 0);

    if (monthAllocations.length === 0) {
      summaryLine.textContent = "No allocations for this month yet.";
    } else {
      summaryLine.textContent =
        visible.length +
        " of " +
        monthAllocations.length +
        " " +
        pluralize(monthAllocations.length, "allocation", "allocations") +
        " shown · " +
        formatMoney(plannedTotal) +
        " planned";
    }

    if (visible.length === 0) {
      if (monthAllocations.length === 0) {
        grid.innerHTML =
          '<div class="bd-empty bd-empty-wide">' +
          '<div class="bd-empty-icon">' +
          iconMarkup("layers", "w-5 h-5") +
          "</div>" +
          "<h3>No allocations yet</h3>" +
          "<p>Start by dividing this month's income into planned purposes such as feeding, transport or savings.</p>" +
          '<button type="button" class="bd-btn bd-btn-primary" data-bd-empty-add="true">' +
          iconMarkup("plus", "w-4 h-4") +
          "<span>Add your first allocation</span></button>" +
          "</div>";
      } else {
        grid.innerHTML =
          '<div class="bd-empty bd-empty-wide">' +
          '<div class="bd-empty-icon">' +
          iconMarkup("search", "w-5 h-5") +
          "</div>" +
          "<h3>No allocations match</h3>" +
          "<p>Try a different status filter or clear your search to see every allocation for this month.</p>" +
          '<button type="button" class="bd-btn bd-btn-ghost" data-bd-empty-clear="true">Clear filters</button>' +
          "</div>";
      }
    } else {
      var shownAllocations = state.allocationsExpanded
        ? visible
        : visible.slice(0, INITIAL_LIST_SIZE);
      grid.innerHTML =
        shownAllocations.map(allocationCard).join("") +
        listToggleMarkup(
          "allocations",
          visible.length,
          state.allocationsExpanded,
          "allocation",
          "allocations"
        );
    }

    refreshIcons();
  }

  /* ---------- History ---------- */

  function renderHistory() {
    var container = byId("bd-history-list");
    if (!container) return;
    if (serverState.loading || serverState.error) {
      container.innerHTML =
        '<div class="bd-empty is-compact">' +
        '<div class="bd-empty-icon">' +
        iconMarkup(serverState.error ? "alert-circle" : "loader", "w-5 h-5") +
        "</div><h3>" +
        (serverState.error ? "Budget history unavailable" : "Loading history…") +
        "</h3><p>" +
        (serverState.error
          ? "We couldn't load your budget history. Please try again."
          : "Your budget history will appear here when it finishes loading.") +
        "</p></div>";
      refreshIcons();
      return;
    }
    var months = Object.create(null);
    allAllocations().forEach(function (allocation) {
      if (!isMonthKey(allocation.financialMonth)) return;
      if (!months[allocation.financialMonth]) {
        months[allocation.financialMonth] = true;
      }
    });
    var list = Object.keys(months)
      .filter(isMonthKey)
      .sort()
      .reverse();

    if (list.length === 0) {
      container.innerHTML =
        '<div class="bd-empty is-compact">' +
        '<div class="bd-empty-icon">' +
        iconMarkup("history", "w-5 h-5") +
        "</div>" +
        "<h3>No allocation history yet</h3>" +
        "<p>Past months will appear here once you create allocations.</p>" +
        "</div>";
      refreshIcons();
      return;
    }

    var shownMonths = state.historyExpanded
      ? list
      : list.slice(0, INITIAL_LIST_SIZE);
    container.innerHTML =
      shownMonths
        .map(function (month) {
          var summary = getAllocationSummary(month);
          var count = allocationsForMonth(month).length;
          return (
            '<button type="button" class="bd-history-row' +
            (month === state.selectedMonth ? " is-active" : "") +
            '" data-bd-history="' +
            escapeHtml(month) +
            '">' +
            '<span class="bd-history-month"><strong>' +
            escapeHtml(formatMonth(month)) +
            "</strong><small>" +
            escapeHtml(count + " " + pluralize(count, "allocation", "allocations")) +
            "</small></span>" +
            '<span class="bd-history-metric"><span>Planned</span><strong>' +
            escapeHtml(formatMoney(summary.totalAllocated)) +
            "</strong></span>" +
            '<span class="bd-history-metric"><span>Actual</span><strong>' +
            escapeHtml(formatMoney(summary.totalActualSpending)) +
            "</strong></span>" +
            '<span class="bd-history-metric is-remaining"><span>Remaining</span><strong class="' +
            (summary.totalRemaining < 0 ? "is-negative" : "is-positive") +
            '">' +
            escapeHtml(formatMoney(summary.totalRemaining)) +
            "</strong></span>" +
            "</button>"
          );
        })
        .join("") +
      listToggleMarkup("history", list.length, state.historyExpanded, "month", "months");
    refreshIcons();
  }

  /* ---------- Unlinked expenses ---------- */

  function renderUnlinked() {
    var container = byId("bd-unlinked-list");
    var monthLabel = byId("bd-unlinked-month");
    if (!container || !monthLabel) return;
    if (serverState.loading || serverState.error) {
      monthLabel.textContent = serverState.error
        ? formatMonth(state.selectedMonth) + " · budget unavailable"
        : formatMonth(state.selectedMonth) + " · loading…";
      container.innerHTML =
        '<div class="bd-empty is-compact">' +
        '<div class="bd-empty-icon">' +
        iconMarkup(serverState.error ? "alert-circle" : "loader", "w-5 h-5") +
        "</div><h3>" +
        (serverState.error ? "Unlinked expenses unavailable" : "Loading expenses…") +
        "</h3><p>" +
        (serverState.error
          ? "We couldn't load your expenses. Please try again."
          : "Your unlinked expenses will appear here when they finish loading.") +
        "</p></div>";
      refreshIcons();
      return;
    }

    var list = unlinkedExpenses(state.selectedMonth);
    var total = list.reduce(function (sum, transaction) {
      return sum + Number(transaction.amount || 0);
    }, 0);

    monthLabel.textContent = list.length
      ? formatMonth(state.selectedMonth) +
        " · " +
        list.length +
        " unlinked " +
        pluralize(list.length, "expense", "expenses") +
        " · " +
        formatMoney(total)
      : formatMonth(state.selectedMonth) + " · no unlinked expenses";

    if (list.length === 0) {
      container.innerHTML =
        '<div class="bd-empty is-compact is-warning">' +
        '<div class="bd-empty-icon">' +
        iconMarkup("check-circle", "w-5 h-5") +
        "</div>" +
        "<h3>Nothing unassigned</h3>" +
        "<p>Every recorded expense this month is linked to an allocation, or no expenses were recorded.</p>" +
        "</div>";
      refreshIcons();
      return;
    }

    var shown = list.slice(0, 6);
    container.innerHTML =
      shown
        .map(function (transaction) {
          return (
            '<div class="bd-unlinked-row">' +
            '<div class="bd-unlinked-copy"><strong>' +
            escapeHtml(
              transaction.description ||
                transaction.category ||
                "Unlinked expense"
            ) +
            "</strong><small>" +
            escapeHtml(formatDate(transaction.date)) +
            " · " +
            escapeHtml(accountName(transaction.accountId)) +
            "</small></div>" +
            '<span class="bd-unlinked-amount">-' +
            escapeHtml(formatMoney(transaction.amount)) +
            "</span>" +
            "</div>"
          );
        })
        .join("") +
      (list.length > shown.length
        ? '<p class="bd-details-note">' +
          escapeHtml(
            "+" +
              (list.length - shown.length) +
              " more unlinked " +
              pluralize(list.length - shown.length, "expense", "expenses") +
              " this month"
          ) +
          "</p>"
        : "");
    refreshIcons();
  }

  /* ---------- Details ---------- */

  function renderDetails() {
    if (!state.detailId) return;
    var allocation = getAllocation(state.detailId);
    if (!allocation) {
      state.detailId = null;
      hideModal("bd-details-modal");
      return;
    }

    var actual = getAllocationActualSpending(allocation.id);
    var remaining = roundMoney(Number(allocation.plannedAmount || 0) - actual);
    var percentage = allocationProgress(allocation, actual);
    var status = statusDetails(allocation.status);
    var purpose = purposeDetails(allocation);
    var linked = linkedExpenses(allocation.id);
    var eligibleLinked = linked.filter(function (transaction) {
      return isExpenseInAllocationPeriod(transaction, allocation);
    });
    var linkedTotal = eligibleLinked.reduce(function (sum, transaction) {
      return sum + Number(transaction.amount || 0);
    }, 0);
    var incompatibleCount = linked.length - eligibleLinked.length;
    var width = percentage === null ? 0 : Math.min(percentage, 100);
    var availableToLink =
      allocation.status === "cancelled"
        ? []
        : unlinkedExpenses(allocation.financialMonth);

    var description =
      String(allocation.description || "").trim() ||
      String(allocation.notes || "").trim() ||
      "No description added.";

    var linkedMarkup = linked.length
      ? '<div class="bd-expense-list">' +
        linked
          .map(function (transaction) {
            var compatible = isExpenseInAllocationPeriod(
              transaction,
              allocation
            );
            return (
              '<div class="bd-expense-row' +
              (compatible ? "" : " is-outside-period") +
              '">' +
              '<span class="bd-expense-icon">' +
              iconMarkup("arrow-up-right", "w-4 h-4") +
              "</span>" +
              '<div class="bd-expense-copy"><strong>' +
              escapeHtml(
                transaction.description ||
                  transaction.category ||
                  "Linked expense"
              ) +
              "</strong><small>" +
              escapeHtml(formatDate(transaction.date)) +
              " · " +
              escapeHtml(accountName(transaction.accountId)) +
              (compatible
                ? ""
                : " · Outside " +
                  escapeHtml(formatMonth(allocation.financialMonth)) +
                  " — not counted") +
              "</small></div>" +
              '<span class="bd-expense-amount">-' +
              escapeHtml(formatMoney(transaction.amount)) +
              "</span>" +
              '<button type="button" class="bd-icon-button is-danger" data-bd-unlink="' +
              escapeHtml(transaction.id) +
              '" title="Unlink this expense" aria-label="Unlink expense">' +
              iconMarkup("unlink", "w-4 h-4") +
              "</button>" +
              "</div>"
            );
          })
          .join("") +
        "</div>"
      : '<p class="bd-details-note">No expenses are linked yet. Actual spending stays at ' +
        escapeHtml(formatMoney(0)) +
        " until a real expense is linked.</p>";

    var linkMarkup = "";
    if (allocation.status !== "cancelled") {
      if (availableToLink.length) {
        linkMarkup =
          '<div class="bd-link-picker">' +
          '<select aria-label="Select an unlinked expense">' +
          '<option value="">Select an unlinked expense…</option>' +
          availableToLink
            .map(function (transaction) {
              var label =
                formatDate(transaction.date) +
                " · " +
                (transaction.description ||
                  transaction.category ||
                  "Expense") +
                " · " +
                formatMoney(transaction.amount);
              return (
                '<option value="' +
                escapeHtml(transaction.id) +
                '">' +
                escapeHtml(label) +
                "</option>"
              );
            })
            .join("") +
          "</select>" +
          '<button type="button" class="bd-btn bd-btn-soft bd-btn-small" data-bd-link="' +
          escapeHtml(allocation.id) +
          '">Link expense</button>' +
          "</div>" +
          '<p class="bd-details-note is-spaced">Linking does not move money. The expense already affected your account balance when it was recorded in My Finances.</p>';
      } else {
        linkMarkup =
          '<p class="bd-details-note">There are no unlinked expenses available for ' +
          escapeHtml(formatMonth(allocation.financialMonth)) +
          '. Record an expense in that month in My Finances first, then link it here.</p>';
      }
    }

    var deleteMarkup =
      linked.length === 0
        ? '<button type="button" class="bd-btn bd-btn-danger bd-btn-small bd-details-delete" data-bd-delete="' +
          escapeHtml(allocation.id) +
          '">Delete allocation</button>'
        : "";

    var cancelLabel =
      allocation.status === "cancelled"
        ? "Restore allocation"
        : "Cancel allocation";
    var cancelAttr =
      allocation.status === "cancelled"
        ? 'data-bd-restore="' + escapeHtml(allocation.id) + '"'
        : 'data-bd-cancel="' + escapeHtml(allocation.id) + '"';

    byId("bd-details-title").textContent = "Allocation details";
    byId("bd-details-subtitle").textContent =
      "Planned amount, actual spending and linked expenses.";

    byId("bd-details-body").innerHTML =
      '<div class="bd-details-head">' +
      '<span class="bd-allocation-icon ' +
      purpose.tone +
      '">' +
      iconMarkup(purpose.icon, "w-5 h-5") +
      "</span>" +
      '<div class="bd-details-identity"><h4>' +
      escapeHtml(allocation.name || "Untitled allocation") +
      "</h4><p>" +
      escapeHtml(allocation.purpose || allocation.category || "General purpose") +
      " · " +
      escapeHtml(formatMonth(allocation.financialMonth)) +
      "</p></div>" +
      '<span class="bd-status-pill ' +
      status.tone +
      '">' +
      iconMarkup(status.icon, "w-3 h-3") +
      escapeHtml(status.label) +
      "</span>" +
      "</div>" +
      '<div class="bd-details-stats">' +
      '<div class="bd-details-stat"><span>Planned</span><strong>' +
      escapeHtml(formatMoney(allocation.plannedAmount)) +
      "</strong></div>" +
      '<div class="bd-details-stat"><span>Spent</span><strong>' +
      escapeHtml(formatMoney(actual)) +
      "</strong></div>" +
      '<div class="bd-details-stat"><span>Remaining</span><strong class="' +
      (remaining < 0 ? "is-negative" : "is-positive") +
      '">' +
      escapeHtml(formatMoney(remaining)) +
      "</strong></div>" +
      "</div>" +
      '<div class="bd-details-progress"><div class="bd-details-progress-head"><span>Allocation used</span><strong>' +
      escapeHtml(percentage === null ? "—" : percentage.toFixed(1) + "%") +
      '</strong></div><div class="bd-progress"><div class="bd-progress-fill ' +
      progressTone(percentage) +
      '" style="width: ' +
      width +
      '%"></div></div></div>' +
      '<dl class="bd-details-list">' +
      "<div class=\"bd-details-row\"><dt>Financial month</dt><dd>" +
      escapeHtml(formatMonth(allocation.financialMonth)) +
      "</dd></div>" +
      '<div class="bd-details-row"><dt>Purpose</dt><dd>' +
      escapeHtml(allocation.purpose || allocation.category || "—") +
      "</dd></div>" +
      '<div class="bd-details-row"><dt>Status</dt><dd>' +
      escapeHtml(status.label) +
      "</dd></div>" +
      '<div class="bd-details-row"><dt>Description</dt><dd>' +
      escapeHtml(description) +
      "</dd></div>" +
      "</dl>" +
      '<div class="bd-details-section"><div class="bd-details-section-head"><h4>Linked expenses</h4><span>' +
      escapeHtml(
        eligibleLinked.length +
          " " +
          pluralize(eligibleLinked.length, "expense", "expenses") +
          " · " +
          formatMoney(linkedTotal)
      ) +
      "</span></div>" +
      (incompatibleCount
        ? '<p class="bd-period-warning app-message-card app-message-card--warning">' +
          escapeHtml(
            incompatibleCount +
              " linked " +
              pluralize(incompatibleCount, "expense", "expenses") +
              " outside " +
              formatMonth(allocation.financialMonth) +
              " " +
              (incompatibleCount === 1 ? "is" : "are") +
              " not counted. Unlink or move the expense to this allocation's month."
          ) +
          "</p>"
        : "") +
      linkedMarkup +
      linkMarkup +
      "</div>" +
      '<div class="bd-details-actions"><div class="bd-details-actions-group">' +
      '<button type="button" class="bd-btn bd-btn-ghost bd-btn-small" data-bd-edit="' +
      escapeHtml(allocation.id) +
      '">' +
      iconMarkup("edit-3", "w-4 h-4") +
      "<span>Edit allocation</span></button>" +
      '<button type="button" class="bd-btn bd-btn-soft bd-btn-small" ' +
      cancelAttr +
      ">" +
      escapeHtml(cancelLabel) +
      "</button>" +
      deleteMarkup +
      "</div></div>";

    refreshIcons();
  }

  /* ---------- Modals, toast & confirmation ---------- */

  function showModal(id) {
    var modal = byId(id);
    if (!modal) return;
    modal.classList.add("open");
    document.body.style.overflow = "hidden";
    refreshIcons();
  }

  function hideModal(id) {
    var modal = byId(id);
    if (!modal) return;
    modal.classList.remove("open");
    if (!document.querySelector(".modal.open")) {
      document.body.style.overflow = "";
    }
  }

  function closeAllModals() {
    document.querySelectorAll(".modal.open").forEach(function (modal) {
      modal.classList.remove("open");
    });
    document.body.style.overflow = "";
  }

  function showToast(title, message, type) {
    var toast = byId("toast");
    var toastTitle = byId("toast-title");
    var toastMessage = byId("toast-message");
    var toastIcon = byId("toast-icon");
    if (!toast || !toastTitle || !toastMessage) return;
    var toastType = type || "success";
    toastTitle.textContent = title;
    toastMessage.textContent = message;
    toast.className = "toast bd-toast show";
    if (toastIcon) {
      toastIcon.className =
        "bd-toast-icon is-" +
        toastType +
        " flex items-center justify-center flex-shrink-0";
      toastIcon.innerHTML = iconMarkup(
        TOAST_ICONS[toastType] || "check",
        "w-5 h-5"
      );
    }
    refreshIcons();
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toast.classList.remove("show");
    }, 3200);
  }

  function openConfirm(options) {
    var settings = options || {};
    byId("bd-confirm-title").textContent = settings.title || "Confirm action";
    byId("bd-confirm-subtitle").textContent =
      settings.subtitle || "This action needs your confirmation.";
    byId("bd-confirm-copy").textContent = settings.message || "";
    var action = byId("bd-confirm-action");
    action.textContent = settings.confirmLabel || "Confirm";
    action.className =
      "bd-btn " +
      (settings.danger === false ? "bd-btn-primary" : "bd-btn-danger");
    confirmHandler = settings.onConfirm || null;
    showModal("bd-confirm-modal");
  }

  /* ---------- Form ---------- */

  function ensureOption(select, value, label) {
    if (!select || !value || !select.options) return;
    var found = false;
    Array.prototype.forEach.call(select.options, function (option) {
      if (option.value === value) found = true;
    });
    if (!found) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = label || value;
      select.appendChild(option);
    }
  }

  function setFormError(message) {
    var error = byId("bd-form-error");
    if (!error) return;
    if (message) {
      error.textContent = message;
      error.classList.add("is-visible");
    } else {
      error.textContent = "";
      error.classList.remove("is-visible");
    }
  }

  function openAddModal() {
    state.editingId = null;
    var form = byId("bd-allocation-form");
    if (!form) return;
    form.reset();
    setFormError("");
    clearFieldErrors();
    byId("bd-form-month").value = state.selectedMonth;
    byId("bd-form-status").value = "planned";
    byId("bd-form-purpose").value = "Feeding";
    byId("bd-modal-title").textContent = "Add allocation";
    byId("bd-modal-subtitle").textContent =
      "Create a planned use of income for a financial month.";
    byId("bd-form-submit").innerHTML =
      iconMarkup("check", "w-4 h-4") + "<span>Create allocation</span>";
    showModal("bd-allocation-modal");
    var name = byId("bd-form-name");
    window.setTimeout(function () {
      if (name) name.focus();
    }, 80);
  }

  function openEditModal(allocationId) {
    var allocation = getAllocation(allocationId);
    if (!allocation) return;
    state.editingId = allocationId;
    var form = byId("bd-allocation-form");
    if (!form) return;
    form.reset();
    setFormError("");
    clearFieldErrors();
    byId("bd-form-month").value = allocation.financialMonth;
    byId("bd-form-name").value = allocation.name || "";
    byId("bd-form-amount").value = Number(allocation.plannedAmount || 0);
    byId("bd-form-description").value =
      allocation.description || allocation.notes || "";

    var purposeField = byId("bd-form-purpose");
    var purposeValue = allocation.purpose || allocation.category || "Other";
    if (purposeField) purposeField.value = purposeValue;

    var statusSelect = byId("bd-form-status");
    ensureOption(
      statusSelect,
      allocation.status,
      statusDetails(allocation.status).label
    );
    statusSelect.value = allocation.status || "planned";

    byId("bd-modal-title").textContent = "Edit allocation";
    byId("bd-modal-subtitle").textContent =
      "Update the plan. Editing never creates a duplicate allocation.";
    byId("bd-form-submit").innerHTML =
      iconMarkup("check", "w-4 h-4") + "<span>Save changes</span>";
    showModal("bd-allocation-modal");
  }

  function submitAllocation(event) {
    event.preventDefault();
    var month = byId("bd-form-month").value;
    var name = byId("bd-form-name").value.trim();
    var purpose = byId("bd-form-purpose").value;
    var amountValue = byId("bd-form-amount").value;
    var amount = Number(amountValue);
    var status = byId("bd-form-status").value;
    var description = byId("bd-form-description").value.trim();

    clearFieldErrors();
    if (!isMonthKey(month)) {
      setFieldError("month", "Select a valid budget month.");
      setFormError("Please correct the highlighted field.");
      byId("bd-form-month").focus();
      return;
    }
    if (!name) {
      setFieldError("name", "Enter an allocation name.");
      setFormError("Please correct the highlighted field.");
      byId("bd-form-name").focus();
      return;
    }
    if (!purpose) {
      setFieldError("purpose", "Choose or enter a valid allocation category.");
      setFormError("Please correct the highlighted field.");
      byId("bd-form-purpose").focus();
      return;
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      setFieldError("amount", "Enter an allocation amount greater than zero.");
      setFormError("Please correct the highlighted field.");
      byId("bd-form-amount").focus();
      return;
    }
    if (STATUS_ORDER.indexOf(status) === -1) {
      setFieldError("status", "Choose a valid allocation status.");
      setFormError("Please correct the highlighted field.");
      return;
    }

    var payload = {
      financialMonth: month,
      name: name,
      purpose: purpose,
      plannedAmount: roundMoney(amount),
      description: description,
      status: status,
    };

    var wasEditing = Boolean(state.editingId);
    var request = wasEditing
      ? updateAllocationOnServer(state.editingId, payload)
      : createAllocationOnServer(payload);
    var submitButton = byId("bd-form-submit");
    if (submitButton) submitButton.disabled = true;

    request
      .then(function () {
        state.editingId = null;
        hideModal("bd-allocation-modal");
        state.selectedMonth = month;
        return loadBudgetData();
      })
      .then(function () {
        showToast(
          wasEditing ? "Allocation updated" : "Allocation created",
          wasEditing
            ? name + " was updated without creating a duplicate."
            : name + " is now part of your " + formatMonth(month) + " plan.",
          "success"
        );
      })
      .catch(function (error) {
        clearFieldErrors();
        var highlighted = showFieldErrors(error);
        var fields = window.GabswebApi.fieldErrors(error && error.payload);
        var general = fields.__all__ && fields.__all__.length
          ? fields.__all__[0]
          : "";
        setFormError(
          general ||
            (highlighted
              ? "Please correct the highlighted fields."
              : errorText(
                  error,
                  "We couldn't save this allocation. Please try again."
                ))
        );
      })
      .then(function () {
        if (submitButton) submitButton.disabled = false;
      });
  }

  /* ---------- Category management ---------- */

  function setCategoryError(message) {
    var error = byId("bd-category-error");
    if (!error) return;
    if (message) {
      error.textContent = message;
      error.classList.add("is-visible");
    } else {
      error.textContent = "";
      error.classList.remove("is-visible");
    }
  }

  function syncPurposeOptions() {
    var datalist = byId("bd-purpose-options");
    if (!datalist) return;
    var names = PURPOSE_OPTIONS.slice();
    serverState.categories.forEach(function (category) {
      var exists = names.some(function (name) {
        return name.toLowerCase() === String(category.name).toLowerCase();
      });
      if (!exists) names.push(category.name);
    });
    datalist.innerHTML = names
      .map(function (name) {
        return '<option value="' + escapeHtml(name) + '"></option>';
      })
      .join("");
  }

  function renderCategories() {
    var list = byId("bd-category-list");
    if (!list) return;
    if (!serverState.categories.length) {
      list.innerHTML =
        '<div class="bd-empty is-compact">' +
        '<div class="bd-empty-icon">' +
        iconMarkup("tags", "w-5 h-5") +
        "</div>" +
        "<h3>No categories yet</h3>" +
        "<p>Create categories here, or type a new name while adding an allocation.</p>" +
        "</div>";
      refreshIcons();
      return;
    }
    list.innerHTML = serverState.categories
      .map(function (category) {
        return (
          '<div class="bd-category-row">' +
          "<span>" +
          escapeHtml(category.name) +
          "</span>" +
          '<button type="button" class="bd-icon-button" data-bd-edit-category="' +
          escapeHtml(category.id) +
          '" aria-label="Rename category">' +
          iconMarkup("edit-3", "w-4 h-4") +
          "</button>" +
          '<button type="button" class="bd-icon-button is-danger" data-bd-delete-category="' +
          escapeHtml(category.id) +
          '" aria-label="Delete category">' +
          iconMarkup("trash-2", "w-4 h-4") +
          "</button>" +
          "</div>"
        );
      })
      .join("");
    refreshIcons();
  }

  function resetCategoryForm() {
    state.editingCategoryId = null;
    var form = byId("bd-category-form");
    if (form) form.reset();
    setCategoryError("");
    var submit = byId("bd-category-submit");
    if (submit) {
      submit.innerHTML =
        iconMarkup("check", "w-4 h-4") + "<span>Add category</span>";
    }
  }

  function openCategoriesModal() {
    resetCategoryForm();
    renderCategories();
    showModal("bd-categories-modal");
    var input = byId("bd-category-name");
    window.setTimeout(function () {
      if (input) input.focus();
    }, 80);
  }

  function editCategory(categoryId) {
    var category = serverState.categories.find(function (item) {
      return item.id === categoryId;
    });
    if (!category) return;
    state.editingCategoryId = categoryId;
    setCategoryError("");
    byId("bd-category-name").value = category.name;
    var submit = byId("bd-category-submit");
    if (submit) {
      submit.innerHTML =
        iconMarkup("check", "w-4 h-4") + "<span>Save category</span>";
    }
    byId("bd-category-name").focus();
  }

  function submitCategory(event) {
    event.preventDefault();
    var name = byId("bd-category-name").value.trim();
    if (!name) {
      setCategoryError("Enter a category name.");
      byId("bd-category-name").focus();
      return;
    }
    var wasEditing = Boolean(state.editingCategoryId);
    var request = wasEditing
      ? updateCategoryOnServer(state.editingCategoryId, name)
      : createCategoryOnServer(name);
    var submitButton = byId("bd-category-submit");
    if (submitButton) submitButton.disabled = true;
    request
      .then(function () {
        resetCategoryForm();
        return loadBudgetData();
      })
      .then(function () {
        showToast(
          wasEditing ? "Category updated" : "Category added",
          name + " is available for your monthly allocations.",
          "success"
        );
      })
      .catch(function (error) {
        setCategoryError(errorText(error, "Please try again."));
      })
      .then(function () {
        if (submitButton) submitButton.disabled = false;
      });
  }

  function requestDeleteCategory(categoryId) {
    var category = serverState.categories.find(function (item) {
      return item.id === categoryId;
    });
    if (!category) return;
    openConfirm({
      title: "Delete category",
      subtitle: "Categories in use are protected.",
      message:
        'Delete the category "' +
        category.name +
        '"? It can only be deleted when no allocation uses it.',
      confirmLabel: "Delete category",
      danger: true,
      onConfirm: function () {
        deleteCategoryOnServer(categoryId)
          .then(function () {
            if (state.editingCategoryId === categoryId) resetCategoryForm();
            return loadBudgetData();
          })
          .then(function () {
            showToast(
              "Category deleted",
              category.name + " was removed from your categories.",
              "success"
            );
          })
          .catch(function (error) {
            showToast(
              "Could not delete category",
              errorText(error, "Please try again."),
              "error"
            );
          });
      },
    });
  }

  /* ---------- Allocation actions ---------- */

  function openDetails(allocationId) {
    if (!getAllocation(allocationId)) return;
    state.detailId = allocationId;
    renderDetails();
    showModal("bd-details-modal");
  }

  function requestCancel(allocationId) {
    var allocation = getAllocation(allocationId);
    if (!allocation) return;
    openConfirm({
      title: "Cancel allocation",
      subtitle: "The plan will remain in history.",
      message:
        'Cancel "' +
        allocation.name +
        '"? It will be excluded from totals and summary cards, but linked expenses and the record itself will be kept.',
      confirmLabel: "Cancel allocation",
      danger: true,
      onConfirm: function () {
        updateAllocationStatusOnServer(allocationId, "cancelled")
          .then(loadBudgetData)
          .then(function () {
            showToast(
              "Allocation cancelled",
              allocation.name + " was cancelled and kept in history.",
              "info"
            );
          })
          .catch(function (error) {
            showToast(
              "Could not cancel allocation",
              errorText(error, "Please try again."),
              "error"
            );
          });
      },
    });
  }

  function requestRestore(allocationId) {
    var allocation = getAllocation(allocationId);
    if (!allocation) return;
    updateAllocationStatusOnServer(allocationId, "active")
      .then(loadBudgetData)
      .then(function () {
        showToast(
          "Allocation restored",
          allocation.name + " is active again and counted in totals.",
          "success"
        );
      })
      .catch(function (error) {
        showToast(
          "Could not restore allocation",
          errorText(error, "Please try again."),
          "error"
        );
      });
  }

  function requestDelete(allocationId) {
    var allocation = getAllocation(allocationId);
    if (!allocation) return;
    var references = getTransactions({ allocationId: allocationId });
    if (references.length) {
      showToast(
        "Allocation still has linked expenses",
        "Unlink its expenses first, or cancel the allocation to keep its history.",
        "error"
      );
      return;
    }
    openConfirm({
      title: "Delete allocation",
      subtitle: "This cannot be undone.",
      message:
        'Permanently delete "' +
        allocation.name +
        '"? This removes the plan from every month view and from history.',
      confirmLabel: "Delete allocation",
      danger: true,
      onConfirm: function () {
        deleteAllocationOnServer(allocationId)
          .then(function () {
            if (state.detailId === allocationId) {
              state.detailId = null;
              hideModal("bd-details-modal");
            }
            return loadBudgetData();
          })
          .then(function () {
            showToast(
              "Allocation deleted",
              allocation.name + " was permanently removed.",
              "success"
            );
          })
          .catch(function (error) {
            showToast(
              "Could not delete allocation",
              errorText(error, "Please try again."),
              "error"
            );
          });
      },
    });
  }

  function linkExpense(allocationId, transactionId) {
    if (!transactionId) {
      showToast(
        "Select an expense",
        "Choose an unlinked expense before linking it to this allocation.",
        "warning"
      );
      return;
    }
    var allocation = getAllocation(allocationId);
    if (!allocation) return;
    var transaction = getTransactionById(transactionId);
    if (!transaction || transaction.type !== "expense") {
      showToast(
        "Select an expense",
        "Only expense transactions can be linked to an allocation.",
        "error"
      );
      return;
    }
    if (!isExpenseInAllocationPeriod(transaction, allocation)) {
      showToast(
        "Expense is outside this period",
        "An expense dated " +
          formatDate(transaction.date) +
          " cannot fund the " +
          formatMonth(allocation.financialMonth) +
          " allocation.",
        "error"
      );
      return;
    }
    updateTransactionAllocationOnServer(transactionId, allocationId)
      .then(loadBudgetData)
      .then(function () {
        showToast(
          "Expense linked",
          "Actual spending for " + allocation.name + " was recalculated.",
          "success"
        );
      })
      .catch(function (error) {
        showToast(
          "Could not link expense",
          errorText(error, "Please try again."),
          "error"
        );
      });
  }

  function unlinkExpense(transactionId) {
    updateTransactionAllocationOnServer(transactionId, null)
      .then(loadBudgetData)
      .then(function () {
        showToast(
          "Expense unlinked",
          "The expense remains an ordinary expense in My Finances.",
          "success"
        );
      })
      .catch(function (error) {
        showToast(
          "Could not unlink expense",
          errorText(error, "Please try again."),
          "error"
        );
      });
  }

  /* ---------- Rendering orchestration ---------- */

  function renderAll() {
    accountMap = Object.create(null);
    serverState.accounts.forEach(function (account) {
      accountMap[account.id] = account;
    });
    buildMonthOptions();
    syncPurposeOptions();
    renderShell();
    renderOverview();
    renderAllocations();
    renderHistory();
    renderUnlinked();
    if (state.detailId && byId("bd-details-modal").classList.contains("open")) {
      renderDetails();
    }
    if (byId("bd-categories-modal").classList.contains("open")) {
      renderCategories();
    }
  }

  function selectMonth(month) {
    if (!isMonthKey(month)) return;
    state.selectedMonth = month;
    state.allocationsExpanded = false;
    buildMonthOptions();
    renderAll();
  }

  /* ---------- Event handling ---------- */

  function handleDocumentClick(event) {
    var target = event.target;

    var listToggle = target.closest("[data-bd-toggle]");
    if (listToggle) {
      if (listToggle.getAttribute("data-bd-toggle") === "allocations") {
        state.allocationsExpanded = !state.allocationsExpanded;
        renderAllocations();
      } else {
        state.historyExpanded = !state.historyExpanded;
        renderHistory();
      }
      return;
    }

    var detailsButton = target.closest("[data-bd-details]");
    if (detailsButton) {
      openDetails(detailsButton.getAttribute("data-bd-details"));
      return;
    }

    var historyButton = target.closest("[data-bd-history]");
    if (historyButton) {
      selectMonth(historyButton.getAttribute("data-bd-history"));
      return;
    }

    var editButton = target.closest("[data-bd-edit]");
    if (editButton) {
      hideModal("bd-details-modal");
      state.detailId = null;
      openEditModal(editButton.getAttribute("data-bd-edit"));
      return;
    }

    var editCategoryButton = target.closest("[data-bd-edit-category]");
    if (editCategoryButton) {
      editCategory(editCategoryButton.getAttribute("data-bd-edit-category"));
      return;
    }

    var deleteCategoryButton = target.closest("[data-bd-delete-category]");
    if (deleteCategoryButton) {
      requestDeleteCategory(
        deleteCategoryButton.getAttribute("data-bd-delete-category")
      );
      return;
    }

    var deleteButton = target.closest("[data-bd-delete]");
    if (deleteButton) {
      requestDelete(deleteButton.getAttribute("data-bd-delete"));
      return;
    }

    var cancelButton = target.closest("[data-bd-cancel]");
    if (cancelButton) {
      requestCancel(cancelButton.getAttribute("data-bd-cancel"));
      return;
    }

    var restoreButton = target.closest("[data-bd-restore]");
    if (restoreButton) {
      requestRestore(restoreButton.getAttribute("data-bd-restore"));
      return;
    }

    var unlinkButton = target.closest("[data-bd-unlink]");
    if (unlinkButton) {
      unlinkExpense(unlinkButton.getAttribute("data-bd-unlink"));
      return;
    }

    var linkButton = target.closest("[data-bd-link]");
    if (linkButton) {
      var picker = linkButton.closest(".bd-link-picker");
      var select = picker ? picker.querySelector("select") : null;
      linkExpense(
        linkButton.getAttribute("data-bd-link"),
        select ? select.value : ""
      );
      return;
    }

    var closeButton = target.closest("[data-bd-close]");
    if (closeButton) {
      var closeId = closeButton.getAttribute("data-bd-close");
      hideModal(closeId);
      if (closeId === "bd-details-modal") state.detailId = null;
      return;
    }

    if (target.closest("[data-bd-empty-add]")) {
      openAddModal();
      return;
    }

    if (target.closest("[data-bd-empty-clear]")) {
      state.status = "all";
      state.search = "";
      var searchInput = byId("bd-search");
      if (searchInput) searchInput.value = "";
      document.querySelectorAll("[data-bd-status]").forEach(function (chip) {
        chip.classList.toggle(
          "active",
          chip.getAttribute("data-bd-status") === "all"
        );
      });
      renderAllocations();
      return;
    }

    if (
      target.classList &&
      target.classList.contains("modal") &&
      target.classList.contains("bd-overlay-modal")
    ) {
      hideModal(target.id);
      if (target.id === "bd-details-modal") state.detailId = null;
    }
  }

  function bindEvents() {
    byId("bd-add-allocation").addEventListener("click", openAddModal);
    byId("bd-manage-categories").addEventListener("click", openCategoriesModal);
    byId("bd-category-form").addEventListener("submit", submitCategory);
    byId("bd-category-cancel").addEventListener("click", resetCategoryForm);

    byId("bd-month-select").addEventListener("change", function (event) {
      selectMonth(event.target.value);
    });

    byId("bd-search").addEventListener("input", function (event) {
      state.search = event.target.value.trim().toLowerCase();
      state.allocationsExpanded = false;
      renderAllocations();
    });

    document.querySelectorAll("[data-bd-status]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        state.status = chip.getAttribute("data-bd-status");
        state.allocationsExpanded = false;
        document.querySelectorAll("[data-bd-status]").forEach(function (item) {
          item.classList.toggle("active", item === chip);
        });
        renderAllocations();
      });
    });

    byId("bd-allocation-form").addEventListener("submit", submitAllocation);

    byId("bd-confirm-action").addEventListener("click", function () {
      var handler = confirmHandler;
      confirmHandler = null;
      hideModal("bd-confirm-modal");
      if (handler) handler();
    });

    document.addEventListener("click", handleDocumentClick);

    document.querySelectorAll(".bd-overlay-modal").forEach(function (modal) {
      modal.addEventListener("click", function (event) {
        if (event.target === modal) {
          hideModal(modal.id);
          if (modal.id === "bd-details-modal") state.detailId = null;
        }
      });
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeAllModals();
    });

    if (typeof data.subscribe === "function") {
      data.subscribe(function () {
        renderAll();
      });
    }

    window.addEventListener("storage", function (event) {
      if (event.key === data.STORAGE_KEY) window.location.reload();
    });
  }

  function init() {
    document.title = "Gabsweb CashFlow · Budget & Money Allocation";
    var pageTitle = byId("page-title");
    if (pageTitle) pageTitle.textContent = "Budget & Money Allocation";
    state.selectedMonth = currentMonthKey;
    bindEvents();
    renderAll();
    refreshIcons();
    loadBudgetData().catch(function () {
      /* The error state is already rendered by loadBudgetData. */
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
