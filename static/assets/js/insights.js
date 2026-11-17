/* Gabsweb CashFlow — Reports & Insights */
(function () {
  "use strict";

  var data = window.GabswebCashFlowData;
  if (!data) return;

  var MONTHS_SHORT = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec"
  ];
  var DAY_MS = 86400000;
  var CHART_COLORS = [
    "#6366f1",
    "#10b981",
    "#f59e0b",
    "#ef4444",
    "#8b5cf6",
    "#3b82f6",
    "#14b8a6",
    "#f97316",
    "#ec4899",
    "#64748b"
  ];

  var charts = Object.create(null);
  var state = {
    mode: "month",
    period: null,
    categoryFocus: "all",
    expandedTables: {}
  };
  var INITIAL_REPORT_ROWS = 8;
  var REPORT_TABLE_IDS = [
    "ri-income-category-table",
    "ri-expense-category-table",
    "ri-allocation-table",
    "ri-transfer-table",
    "ri-goals-table",
    "ri-goals-activity",
    "ri-savings-activity",
    "ri-category-changes"
  ];
  var lastReport = null;
  var renderTimer = null;
  var reportLoading = false;
  var reportError = false;
  var REPORT_API = "/insights-api/";
  var PDF_URL = "/insights/pdf/";

  /* ---------- Basic helpers ---------- */

  function byId(id) {
    return document.getElementById(id);
  }

  function roundMoney(value) {
    var number = Number(value);
    if (!Number.isFinite(number)) return 0;
    return Math.round((number + Number.EPSILON) * 100) / 100;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[char];
    });
  }

  function currencyCode() {
    try {
      return data.getSettings().currency || data.DEFAULT_CURRENCY;
    } catch (error) {
      return data.DEFAULT_CURRENCY;
    }
  }

  function formatMoney(amount) {
    return data.formatCurrency(Number(amount) || 0, { currency: currencyCode() });
  }

  function formatSignedMoney(amount) {
    var number = roundMoney(amount);
    if (number === 0) return formatMoney(0);
    return (number > 0 ? "+" : "−") + formatMoney(Math.abs(number));
  }

  function formatAxisMoney(amount) {
    try {
      return data.formatCurrency(Number(amount) || 0, {
        currency: currencyCode(),
        notation: "compact",
        maximumFractionDigits: 1
      });
    } catch (error) {
      return formatMoney(amount);
    }
  }

  function formatPercent(value, digits) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return "—";
    }
    return Number(value).toFixed(digits === undefined ? 1 : digits) + "%";
  }

  function pad2(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function parseDateKey(key) {
    if (typeof key !== "string") return null;
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key);
    if (!match) return null;
    return new Date(
      Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
    );
  }

  function keyFromDate(date) {
    return (
      date.getUTCFullYear() +
      "-" +
      pad2(date.getUTCMonth() + 1) +
      "-" +
      pad2(date.getUTCDate())
    );
  }

  function addDaysKey(key, days) {
    var date = parseDateKey(key);
    if (!date) return null;
    date.setUTCDate(date.getUTCDate() + days);
    return keyFromDate(date);
  }

  function shiftMonthKey(key, months) {
    var date = parseDateKey(key);
    if (!date) return null;
    var day = date.getUTCDate();
    date.setUTCDate(1);
    date.setUTCMonth(date.getUTCMonth() + months);
    var lastDay = new Date(
      Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)
    ).getUTCDate();
    date.setUTCDate(Math.min(day, lastDay));
    return keyFromDate(date);
  }

  function shiftYearKey(key, years) {
    var date = parseDateKey(key);
    if (!date) return null;
    var month = date.getUTCMonth();
    var day = date.getUTCDate();
    date.setUTCDate(1);
    date.setUTCFullYear(date.getUTCFullYear() + years);
    var lastDay = new Date(Date.UTC(date.getUTCFullYear(), month + 1, 0)).getUTCDate();
    date.setUTCMonth(month);
    date.setUTCDate(Math.min(day, lastDay));
    return keyFromDate(date);
  }

  function monthStart(key) {
    return key.slice(0, 7) + "-01";
  }

  function monthEnd(key) {
    var date = parseDateKey(monthStart(key));
    if (!date) return null;
    date.setUTCMonth(date.getUTCMonth() + 1);
    date.setUTCDate(0);
    return keyFromDate(date);
  }

  function diffDays(start, end) {
    var startDate = parseDateKey(start);
    var endDate = parseDateKey(end);
    if (!startDate || !endDate) return 0;
    return Math.round((endDate.getTime() - startDate.getTime()) / DAY_MS);
  }

  function formatShortDate(key) {
    var date = parseDateKey(key);
    if (!date) return "";
    return MONTHS_SHORT[date.getUTCMonth()] + " " + date.getUTCDate();
  }

  function formatLongDate(key) {
    var date = parseDateKey(key);
    if (!date) return "";
    return (
      MONTHS_SHORT[date.getUTCMonth()] +
      " " +
      date.getUTCDate() +
      ", " +
      date.getUTCFullYear()
    );
  }

  function formatRangeLabel(start, end) {
    var startDate = parseDateKey(start);
    var endDate = parseDateKey(end);
    if (!startDate || !endDate) return "—";
    var sameYear = startDate.getUTCFullYear() === endDate.getUTCFullYear();
    var left =
      MONTHS_SHORT[startDate.getUTCMonth()] +
      " " +
      startDate.getUTCDate() +
      (sameYear ? "" : ", " + startDate.getUTCFullYear());
    var right =
      MONTHS_SHORT[endDate.getUTCMonth()] +
      " " +
      endDate.getUTCDate() +
      ", " +
      endDate.getUTCFullYear();
    return left + " – " + right;
  }

  function monthLabel(key) {
    var date = parseDateKey(key);
    if (!date) return key;
    return (
      MONTHS_SHORT[date.getUTCMonth()] +
      " " +
      String(date.getUTCFullYear()).slice(2)
    );
  }

  /* ---------- Server report loading ---------- */

  function defaultPeriod(mode) {
    var today = data.todayKey();
    return mode === "year" ? today.slice(0, 4) : today.slice(0, 7);
  }

  function validPeriod(mode, value) {
    if (mode === "year") {
      return /^\d{4}$/.test(String(value || ""));
    }
    return /^\d{4}-\d{2}$/.test(String(value || ""));
  }

  function readUrlState() {
    var params = new URLSearchParams(window.location.search);
    var mode = params.get("mode") === "year" ? "year" : "month";
    var period = params.get("period") || defaultPeriod(mode);
    if (!validPeriod(mode, period)) period = defaultPeriod(mode);
    state.mode = mode;
    state.period = period;
  }

  function updateUrl() {
    if (!window.history || !window.history.replaceState) return;
    var params = new URLSearchParams(window.location.search);
    params.set("mode", state.mode);
    params.set("period", state.period);
    window.history.replaceState(null, "", window.location.pathname + "?" + params.toString());
  }

  function syncPeriodInputs() {
    var monthInput = byId("ri-period-month");
    var yearInput = byId("ri-period-year");
    var monthField = byId("ri-month-field");
    var yearField = byId("ri-year-field");
    var isYear = state.mode === "year";

    if (monthInput) monthInput.value = isYear ? "" : state.period;
    if (yearInput) yearInput.value = isYear ? state.period : "";
    if (monthField) monthField.hidden = isYear;
    if (yearField) yearField.hidden = !isYear;

    document.querySelectorAll("#ri-mode-group [data-mode]").forEach(function (button) {
      var active = button.getAttribute("data-mode") === state.mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function showLoading() {
    reportLoading = true;
    reportError = false;
    var grid = byId("ri-overview-grid");
    if (grid) {
      var labels = [
        "Opening balance",
        "Total income",
        "Total expenses",
        "Net cash flow",
        "Closing balance"
      ];
      grid.innerHTML = labels
        .map(function (label) {
          return (
            '<article class="ri-stat-card is-loading">' +
            '<div class="ri-stat-top"><span class="ri-stat-icon"><i data-lucide="loader-circle" class="w-5 h-5"></i></span></div>' +
            '<p class="ri-stat-label">' +
            escapeHtml(label) +
            "</p><strong class=\"ri-stat-value\">—</strong>" +
            '<p class="ri-stat-note">Loading report…</p></article>'
          );
        })
        .join("");
    }
    ["ri-allocation-stats", "ri-savings-stats", "ri-goals-stats", "ri-transfer-stats"].forEach(function (id) {
      var el = byId(id);
      if (el) el.innerHTML = '<div class="ri-mini-stat"><span>Loading</span><strong>—</strong><small>Fetching saved records…</small></div>';
    });
    ["ri-allocation-table", "ri-savings-history", "ri-savings-activity", "ri-goals-table", "ri-goals-activity", "ri-transfer-table"].forEach(function (id) {
      var el = byId(id);
      if (el) el.innerHTML = '<div class="ri-table-empty"><strong>Loading report…</strong><p>Fetching your saved financial records.</p></div>';
    });
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function showError() {
    reportLoading = false;
    reportError = true;
    lastReport = null;
    var grid = byId("ri-overview-grid");
    if (grid) {
      grid.innerHTML =
        '<article class="ri-stat-card"><div class="ri-stat-top"><span class="ri-stat-icon is-expense"><i data-lucide="alert-triangle" class="w-5 h-5"></i></span></div>' +
        '<p class="ri-stat-label">Report unavailable</p><strong class="ri-stat-value">—</strong>' +
        '<p class="ri-stat-note">We could not load this report. Refresh the page or try again.</p></article>';
    }
    ["ri-allocation-stats", "ri-savings-stats", "ri-goals-stats", "ri-transfer-stats"].forEach(function (id) {
      var el = byId(id);
      if (el) el.innerHTML = '<div class="ri-mini-stat"><span>Unavailable</span><strong>—</strong><small>No figures are shown to avoid misleading totals.</small></div>';
    });
    ["ri-allocation-table", "ri-savings-history", "ri-savings-activity", "ri-goals-table", "ri-goals-activity", "ri-transfer-table"].forEach(function (id) {
      var el = byId(id);
      if (el) el.innerHTML = '<div class="ri-table-empty"><strong>Could not load this report</strong><p>Refresh the page or try again shortly.</p></div>';
    });
    var paymentButton = byId("ri-pdf-btn");
    if (paymentButton) paymentButton.disabled = true;
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function loadReport() {
    showLoading();
    var url =
      REPORT_API +
      "?mode=" +
      encodeURIComponent(state.mode) +
      "&period=" +
      encodeURIComponent(state.period);
    fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
      cache: "no-store"
    })
      .then(function (response) {
        var contentType = response.headers.get("content-type") || "";
        if (!response.ok || contentType.indexOf("application/json") === -1) {
          throw new Error("Report request failed.");
        }
        return response.json();
      })
      .then(function (payload) {
        if (!payload || !payload.ok || !payload.report) {
          throw new Error("Invalid report response.");
        }
        reportLoading = false;
        reportError = false;
        updateUrl();
        renderAll(payload.report);
      })
      .catch(function () {
        showError();
      });
  }

  function downloadPdf() {
    if (reportError || reportLoading || !lastReport) return;
    window.location.href =
      PDF_URL +
      "?mode=" +
      encodeURIComponent(state.mode) +
      "&period=" +
      encodeURIComponent(state.period);
  }

  /* ---------- Period resolution ---------- */

  function resolveRange() {
    var today = data.todayKey();
    var range = null;

    if (state.preset === "last-month") {
      var lastStart = monthStart(shiftMonthKey(today, -1));
      var lastEnd = monthEnd(lastStart);
      var beforeStart = monthStart(shiftMonthKey(lastStart, -1));
      range = {
        start: lastStart,
        end: lastEnd,
        prevStart: beforeStart,
        prevEnd: monthEnd(beforeStart),
        comparison: "Compared with the month before"
      };
    } else if (state.preset === "last-3-months") {
      var threeStart = monthStart(shiftMonthKey(today, -2));
      var length = diffDays(threeStart, today) + 1;
      var prevEnd = addDaysKey(threeStart, -1);
      range = {
        start: threeStart,
        end: today,
        prevStart: addDaysKey(prevEnd, -(length - 1)),
        prevEnd: prevEnd,
        comparison: "Compared with the previous three months"
      };
    } else if (state.preset === "this-year") {
      var yearStart = today.slice(0, 4) + "-01-01";
      range = {
        start: yearStart,
        end: today,
        prevStart: shiftYearKey(yearStart, -1),
        prevEnd: shiftYearKey(today, -1),
        comparison: "Compared with the same period last year"
      };
    } else if (
      state.preset === "custom" &&
      state.customStart &&
      state.customEnd &&
      data.isDateOnly(state.customStart) &&
      data.isDateOnly(state.customEnd)
    ) {
      var customStart = state.customStart;
      var customEnd = state.customEnd;
      if (customStart > customEnd) {
        customStart = state.customEnd;
        customEnd = state.customStart;
      }
      var span = diffDays(customStart, customEnd) + 1;
      var customPrevEnd = addDaysKey(customStart, -1);
      range = {
        start: customStart,
        end: customEnd,
        prevStart: addDaysKey(customPrevEnd, -(span - 1)),
        prevEnd: customPrevEnd,
        comparison: "Compared with the previous period of equal length"
      };
    } else {
      var monthFirst = monthStart(today);
      var monthLength = diffDays(monthFirst, today) + 1;
      var prevMonthStart = monthStart(shiftMonthKey(monthFirst, -1));
      var prevMonthEnd = addDaysKey(prevMonthStart, monthLength - 1);
      var prevMonthLimit = monthEnd(prevMonthStart);
      if (prevMonthEnd > prevMonthLimit) prevMonthEnd = prevMonthLimit;
      range = {
        start: monthFirst,
        end: today,
        prevStart: prevMonthStart,
        prevEnd: prevMonthEnd,
        comparison: "Compared with the same days last month"
      };
    }

    range.preset = state.preset;
    range.label = formatRangeLabel(range.start, range.end);
    return range;
  }

  /* ---------- Data preparation ---------- */

  function categoryName(transaction) {
    var value = transaction.category ? String(transaction.category).trim() : "";
    return value || "Uncategorised";
  }

  function sumTransactions(transactions, type) {
    return roundMoney(
      transactions.reduce(function (total, transaction) {
        if (transaction.type !== type) return total;
        return total + Number(transaction.amount || 0);
      }, 0)
    );
  }

  function analyseCategories(currentTransactions, previousTransactions, type) {
    var currentMap = Object.create(null);
    var previousMap = Object.create(null);

    currentTransactions.forEach(function (transaction) {
      if (transaction.type !== type) return;
      var name = categoryName(transaction);
      if (!currentMap[name]) currentMap[name] = { amount: 0, count: 0 };
      currentMap[name].amount += Number(transaction.amount || 0);
      currentMap[name].count += 1;
    });

    previousTransactions.forEach(function (transaction) {
      if (transaction.type !== type) return;
      var name = categoryName(transaction);
      if (!previousMap[name]) previousMap[name] = { amount: 0, count: 0 };
      previousMap[name].amount += Number(transaction.amount || 0);
      previousMap[name].count += 1;
    });

    var total = 0;
    Object.keys(currentMap).forEach(function (name) {
      total += currentMap[name].amount;
    });
    total = roundMoney(total);

    var list = Object.keys(currentMap)
      .map(function (name) {
        var amount = roundMoney(currentMap[name].amount);
        var previousAmount = roundMoney((previousMap[name] || {}).amount || 0);
        return {
          category: name,
          amount: amount,
          count: currentMap[name].count,
          previousAmount: previousAmount,
          change: roundMoney(amount - previousAmount),
          changePercent:
            previousAmount > 0
              ? roundMoney(((amount - previousAmount) / previousAmount) * 100)
              : null,
          percentage: total > 0 ? roundMoney((amount / total) * 100) : null
        };
      })
      .sort(function (a, b) {
        return b.amount - a.amount;
      });

    return { total: total, list: list, previousTotal: 0 };
  }

  function buildSeries(start, end, transactions) {
    var days = diffDays(start, end) + 1;
    var mode = days <= 45 ? "day" : days <= 186 ? "week" : "month";
    var buckets = [];
    var cursor;

    if (mode === "day") {
      for (cursor = start; cursor <= end; cursor = addDaysKey(cursor, 1)) {
        buckets.push({
          start: cursor,
          end: cursor,
          label: formatShortDate(cursor),
          income: 0,
          expenses: 0,
          net: 0
        });
      }
    } else if (mode === "week") {
      cursor = start;
      while (cursor <= end) {
        var weekEnd = addDaysKey(cursor, 6);
        if (weekEnd > end) weekEnd = end;
        buckets.push({
          start: cursor,
          end: weekEnd,
          label: formatShortDate(cursor),
          income: 0,
          expenses: 0,
          net: 0
        });
        cursor = addDaysKey(weekEnd, 1);
      }
    } else {
      cursor = monthStart(start);
      while (cursor <= end) {
        var monthFinish = monthEnd(cursor);
        var bucketStart = cursor < start ? start : cursor;
        var bucketEnd = monthFinish > end ? end : monthFinish;
        buckets.push({
          start: bucketStart,
          end: bucketEnd,
          label: monthLabel(cursor),
          income: 0,
          expenses: 0,
          net: 0
        });
        cursor = addDaysKey(monthFinish, 1);
      }
    }

    transactions.forEach(function (transaction) {
      if (transaction.type !== "income" && transaction.type !== "expense") return;
      for (var index = 0; index < buckets.length; index += 1) {
        var bucket = buckets[index];
        if (transaction.date < bucket.start) continue;
        if (transaction.date > bucket.end) continue;
        if (transaction.type === "income") {
          bucket.income += Number(transaction.amount || 0);
        } else {
          bucket.expenses += Number(transaction.amount || 0);
        }
        break;
      }
    });

    buckets.forEach(function (bucket) {
      bucket.income = roundMoney(bucket.income);
      bucket.expenses = roundMoney(bucket.expenses);
      bucket.net = roundMoney(bucket.income - bucket.expenses);
    });

    return { mode: mode, buckets: buckets };
  }

  function buildAllocations(range, transactions) {
    var startMonth = range.start.slice(0, 7);
    var endMonth = range.end.slice(0, 7);
    var plans = ((serverBudget && serverBudget.allocations) || [])
      .filter(function (allocation) {
        return (
          allocation.financialMonth >= startMonth &&
          allocation.financialMonth <= endMonth &&
          allocation.status !== "cancelled"
        );
      })
      .sort(function (a, b) {
        if (a.financialMonth !== b.financialMonth) {
          return a.financialMonth < b.financialMonth ? -1 : 1;
        }
        return String(a.name).localeCompare(String(b.name));
      });

    var actualByAllocation = Object.create(null);
    var linkedTotal = 0;

    transactions.forEach(function (transaction) {
      if (transaction.type !== "expense") return;
      if (!transaction.allocationId) return;
      linkedTotal += Number(transaction.amount || 0);
      if (!actualByAllocation[transaction.allocationId]) {
        actualByAllocation[transaction.allocationId] = 0;
      }
      actualByAllocation[transaction.allocationId] += Number(
        transaction.amount || 0
      );
    });

    var totalPlanned = 0;
    var totalActual = 0;
    var rows = plans.map(function (allocation) {
      var planned = roundMoney(Number(allocation.plannedAmount || 0));
      var actual = roundMoney(actualByAllocation[allocation.id] || 0);
      totalPlanned += planned;
      totalActual += actual;
      return {
        id: allocation.id,
        name: allocation.name,
        financialMonth: allocation.financialMonth,
        planned: planned,
        actual: actual,
        remaining: roundMoney(planned - actual),
        usage:
          planned > 0 ? roundMoney((actual / planned) * 100) : null,
        status: allocation.status
      };
    });

    totalPlanned = roundMoney(totalPlanned);
    totalActual = roundMoney(totalActual);

    return {
      available: Boolean(serverBudget),
      error: budgetError,
      rows: rows,
      totalPlanned: totalPlanned,
      totalActual: totalActual,
      totalRemaining: roundMoney(totalPlanned - totalActual),
      linkedTotal: roundMoney(linkedTotal),
      unlinkedTotal: roundMoney(
        sumTransactions(transactions, "expense") - linkedTotal
      ),
      monthCount: rows.reduce(function (months, row) {
        if (months.indexOf(row.financialMonth) === -1) {
          months.push(row.financialMonth);
        }
        return months;
      }, []).length
    };
  }

  function monthKeyOf(key) {
    return String(key || "").slice(0, 7);
  }

  function savingsMonthsInRange(start, end) {
    var rows = (serverSavings && serverSavings.months) || [];
    var from = monthKeyOf(start);
    var to = monthKeyOf(end);
    return rows.filter(function (row) {
      return row.month >= from && row.month <= to;
    });
  }

  function savingsWithdrawalsInRange(start, end) {
    var rows = savingsMonthsInRange(start, end);
    var withdrawals = [];
    rows.forEach(function (row) {
      (row.withdrawals || []).forEach(function (withdrawal) {
        if (withdrawal.date >= start && withdrawal.date <= end) {
          withdrawals.push(withdrawal);
        }
      });
    });
    return withdrawals;
  }

  function buildSavings(range) {
    var months = savingsMonthsInRange(range.start, range.end);
    var withdrawals = savingsWithdrawalsInRange(range.start, range.end);
    var contributions = 0;
    var monthsWithSavings = 0;

    months.forEach(function (row) {
      var saved = roundMoney(Number(row.saved) || 0);
      contributions = roundMoney(contributions + saved);
      if (saved > 0) monthsWithSavings += 1;
    });

    var totalWithdrawals = withdrawals.reduce(function (total, withdrawal) {
      return roundMoney(total + (Number(withdrawal.amount) || 0));
    }, 0);

    var balance =
      serverSavings && serverSavings.summary
        ? roundMoney(Number(serverSavings.summary.currentBalance) || 0)
        : 0;

    return {
      available: Boolean(serverSavings),
      error: savingsError,
      totalBalance: balance,
      contributions: contributions,
      withdrawalTotal: totalWithdrawals,
      contributionCount: monthsWithSavings,
      withdrawalCount: withdrawals.length,
      months: months.slice().sort(function (a, b) {
        return a.month < b.month ? 1 : a.month > b.month ? -1 : 0;
      }),
      withdrawals: withdrawals.slice().sort(function (a, b) {
        return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
      })
    };
  }

  function loadSavings() {
    fetch(SAVINGS_API, {
      headers: { Accept: "application/json" },
      credentials: "same-origin"
    })
      .then(function (response) {
        var contentType = response.headers.get("content-type") || "";
        if (!response.ok || contentType.indexOf("application/json") === -1) {
          throw new Error("Savings request failed.");
        }
        return response.json();
      })
      .then(function (payload) {
        serverSavings = payload || null;
        savingsError = false;
      })
      .catch(function () {
        serverSavings = null;
        savingsError = true;
      })
      .then(function () {
        if (lastReport) renderAll();
      });
  }

  function loadBudget() {
    fetch(BUDGET_API, {
      headers: { Accept: "application/json" },
      credentials: "same-origin"
    })
      .then(function (response) {
        var contentType = response.headers.get("content-type") || "";
        if (!response.ok || contentType.indexOf("application/json") === -1) {
          throw new Error("Budget request failed.");
        }
        return response.json();
      })
      .then(function (payload) {
        serverBudget = payload || null;
        budgetError = false;
      })
      .catch(function () {
        serverBudget = null;
        budgetError = true;
      })
      .then(function () {
        if (lastReport) renderAll();
      });
  }

  function buildComparison(current, previous) {
    var items = [
      {
        label: "Income",
        current: current.income,
        previous: previous.income,
        favourableWhenUp: true
      },
      {
        label: "Expenses",
        current: current.expenses,
        previous: previous.expenses,
        favourableWhenUp: false
      },
      {
        label: "Net income",
        current: current.net,
        previous: previous.net,
        favourableWhenUp: true
      },
      {
        label: "Savings set aside",
        current: current.savingsContributions,
        previous: previous.savingsContributions,
        favourableWhenUp: true
      }
    ];

    return {
      items: items,
      hasActivity:
        items.some(function (item) {
          return item.current !== 0 || item.previous !== 0;
        })
    };
  }

  function buildReport() {
    var range = resolveRange();
    var currentTransactions = data.getTransactions({
      dateFrom: range.start,
      dateTo: range.end
    });
    var previousTransactions = data.getTransactions({
      dateFrom: range.prevStart,
      dateTo: range.prevEnd
    });
    var currentSavings = buildSavings(range);
    var previousSavings = buildSavings({
      start: range.prevStart,
      end: range.prevEnd
    });

    var current = {
      transactions: currentTransactions,
      income: sumTransactions(currentTransactions, "income"),
      expenses: sumTransactions(currentTransactions, "expense"),
      savingsContributions: currentSavings.contributions,
      savingsWithdrawals: currentSavings.withdrawalTotal
    };
    current.net = roundMoney(current.income - current.expenses);

    var previous = {
      transactions: previousTransactions,
      income: sumTransactions(previousTransactions, "income"),
      expenses: sumTransactions(previousTransactions, "expense"),
      savingsContributions: previousSavings.contributions,
      savingsWithdrawals: previousSavings.withdrawalTotal
    };
    previous.net = roundMoney(previous.income - previous.expenses);

    var expenseCategories = analyseCategories(
      currentTransactions,
      previousTransactions,
      "expense"
    );
    expenseCategories.previousTotal = previous.expenses;

    var incomeCategories = analyseCategories(
      currentTransactions,
      previousTransactions,
      "income"
    );
    incomeCategories.previousTotal = previous.income;

    var report = {
      range: range,
      current: current,
      previous: previous,
      series: buildSeries(range.start, range.end, currentTransactions),
      categories: {
        expense: expenseCategories,
        income: incomeCategories
      },
      allocations: buildAllocations(range, currentTransactions),
      savings: currentSavings,
      comparison: buildComparison(current, previous)
    };

    report.insights = buildInsights(report);
    return report;
  }

  /* ---------- Insight generation ---------- */

  function buildInsights(report) {
    var insights = [];
    var topExpense = report.categories.expense.list[0];
    var topIncome = report.categories.income.list[0];

    if (topExpense && topExpense.amount > 0) {
      insights.push({
        icon: "trending-down",
        tone: "expense",
        title: "Highest spending category",
        text:
          "<strong>" +
          escapeHtml(topExpense.category) +
          "</strong> accounts for " +
          formatPercent(topExpense.percentage) +
          " of expenses (" +
          formatMoney(topExpense.amount) +
          ") across " +
          topExpense.count +
          " expense" +
          (topExpense.count === 1 ? "" : "s") +
          "."
      });
    }

    if (topIncome && topIncome.amount > 0) {
      insights.push({
        icon: "trending-up",
        tone: "income",
        title: "Largest income source",
        text:
          "<strong>" +
          escapeHtml(topIncome.category) +
          "</strong> provided " +
          formatPercent(topIncome.percentage) +
          " of income (" +
          formatMoney(topIncome.amount) +
          ") across " +
          topIncome.count +
          " income record" +
          (topIncome.count === 1 ? "" : "s") +
          "."
      });
    }

    var allExpenses = data.getTransactions({ type: "expense" });
    var byMonth = Object.create(null);
    allExpenses.forEach(function (transaction) {
      if (!transaction.date) return;
      var key = transaction.date.slice(0, 7);
      if (!byMonth[key]) byMonth[key] = 0;
      byMonth[key] += Number(transaction.amount || 0);
    });
    var monthKeys = Object.keys(byMonth);
    if (monthKeys.length) {
      monthKeys.sort(function (a, b) {
        return byMonth[b] - byMonth[a];
      });
      insights.push({
        icon: "calendar",
        tone: "",
        title: "Highest expense month on record",
        text:
          "<strong>" +
          escapeHtml(monthLabel(monthKeys[0] + "-01")) +
          "</strong> has the highest recorded expenses at " +
          formatMoney(byMonth[monthKeys[0]]) +
          "."
      });
    }

    var allocations = report.allocations;
    if (allocations.totalPlanned > 0) {
      var overRows = allocations.rows.filter(function (row) {
        return row.actual > row.planned;
      });
      if (overRows.length) {
        overRows.sort(function (a, b) {
          return b.actual - b.planned - (a.actual - a.planned);
        });
        insights.push({
          icon: "alert-triangle",
          tone: "warning",
          title: "Allocation over plan",
          text:
            "<strong>" +
            escapeHtml(overRows[0].name) +
            "</strong> is over its planned amount by " +
            formatMoney(overRows[0].actual - overRows[0].planned) +
            (overRows.length > 1
              ? ". " + overRows.length + " allocations are above plan."
              : ".")
        });
      } else {
        var usage =
          allocations.totalPlanned > 0
            ? (allocations.totalActual / allocations.totalPlanned) * 100
            : 0;
        insights.push({
          icon: "target",
          tone: "",
          title: "Allocation use",
          text:
            "Linked expenses use <strong>" +
            formatPercent(usage) +
            "</strong> of the " +
            formatMoney(allocations.totalPlanned) +
            " planned for this period."
        });
      }
    }

    if (report.savings.contributions > 0) {
      insights.push({
        icon: "coins",
        tone: "savings",
        title: "Savings set aside",
        text:
          "<strong>" +
          formatMoney(report.savings.contributions) +
          "</strong> was set aside through Budget Savings in this period."
      });
    }

    if (report.savings.withdrawalTotal > 0) {
      insights.push({
        icon: "arrow-up-right",
        tone: "warning",
        title: "Savings withdrawals",
        text:
          "<strong>" +
          formatMoney(report.savings.withdrawalTotal) +
          "</strong> was withdrawn from savings in this period."
      });
    }

    if (
      report.previous.expenses > 0 &&
      (report.current.expenses > 0 || report.previous.expenses > 0)
    ) {
      var expenseChange =
        ((report.current.expenses - report.previous.expenses) /
          report.previous.expenses) *
        100;
      insights.push({
        icon: expenseChange > 0 ? "trending-up" : "trending-down",
        tone: expenseChange > 0 ? "warning" : "income",
        title: "Spending change",
        text:
          "Overall expenses " +
          (expenseChange > 0 ? "increased" : "decreased") +
          " by <strong>" +
          formatPercent(Math.abs(expenseChange)) +
          "</strong> compared with the previous period."
      });
    }

    return insights.slice(0, 6);
  }

  /* ---------- Rendering: shell & controls ---------- */

  function changeBadge(current, previous, favourableWhenUp) {
    var change = roundMoney(current - previous);
    if (previous <= 0) {
      if (current <= 0) return { text: "No activity", className: "" };
      return { text: "No prior data", className: "" };
    }
    if (change === 0) return { text: "No change", className: "" };
    var percent = (change / previous) * 100;
    var favourable = favourableWhenUp ? change > 0 : change < 0;
    return {
      text: (change > 0 ? "+" : "−") + formatPercent(Math.abs(percent)),
      className: favourable ? "is-positive" : "is-negative"
    };
  }

  function renderShell(report) {
    var balanceEl = byId("sidebar-total-balance");
    var noteEl = byId("sidebar-balance-note");
    var nameEl = byId("sidebar-user-name");
    var subtitleEl = byId("ri-header-subtitle");

    if (balanceEl) {
      balanceEl.textContent = formatMoney(data.getTotalBalance());
    }
    if (noteEl) {
      noteEl.textContent =
        report.current.net === 0
          ? "No net movement this period"
          : (report.current.net > 0 ? "+" : "") +
            formatMoney(report.current.net) +
            " net this period";
    }

    try {
      var settings = data.getSettings();
      var serverTwoPart = String(document.body.dataset.userTwoPartName || "").trim();
      var serverFirst = String(document.body.dataset.userFirstName || "").trim();
      var localName = String(settings.name || "").trim();
      var twoPartName =
        serverTwoPart || localName.split(/\s+/).slice(0, 2).join(" ");
      var firstName =
        serverFirst || (localName ? localName.split(/\s+/)[0] : "");
      if (nameEl) nameEl.textContent = twoPartName || "Your profile";
      if (subtitleEl) {
        subtitleEl.textContent = firstName
          ? "Financial reporting for " + firstName
          : "Financial reporting workspace";
      }
    } catch (error) {
      /* keep shell defaults */
    }

    var chip = byId("ri-storage-chip");
    var label = byId("ri-storage-label");
    if (!chip || !label) return;
    var info = data.getLoadInfo ? data.getLoadInfo() : null;
    var needsAttention =
      info &&
      (info.status === "corrupt" ||
        info.status === "incompatible" ||
        info.status === "recovered");
    var sessionOnly = info && info.storageAvailable === false;
    chip.classList.toggle("is-warning", Boolean(needsAttention || sessionOnly));
    if (needsAttention) {
      label.textContent = "Saved data needs attention";
      chip.title = info.warning || "Review your saved financial information.";
    } else if (sessionOnly) {
      label.textContent = "Changes may not be saved";
      chip.title = "Changes may not be saved in this session.";
    } else {
      label.textContent = "Insights up to date";
      chip.title = "Your report information is saved to your account.";
    }
  }

  function syncControls(report) {
    var rangeLabel = byId("ri-range-label");
    var compareLabel = byId("ri-compare-label");

    if (rangeLabel) rangeLabel.textContent = report.range.label;
    if (compareLabel) {
      compareLabel.textContent =
        report.range.comparison +
        " · " +
        formatRangeLabel(report.range.prevStart, report.range.prevEnd);
    }
    state.mode = report.mode === "year" ? "year" : "month";
    state.period = report.key;
    syncPeriodInputs();
  }

  /* ---------- Rendering: overview ---------- */

  function renderOverview(report) {
    var grid = byId("ri-overview-grid");
    if (!grid) return;

    var incomeBadge = changeBadge(
      report.current.income,
      report.previous.income,
      true
    );
    var expenseBadge = changeBadge(
      report.current.expenses,
      report.previous.expenses,
      false
    );
    var netBadge = changeBadge(
      report.current.net,
      report.previous.net,
      true
    );
    var savingsBadge = changeBadge(
      report.current.savingsContributions,
      report.previous.savingsContributions,
      true
    );

    var allocationUse =
      report.allocations.totalPlanned > 0
        ? (report.allocations.totalActual / report.allocations.totalPlanned) *
          100
        : null;
    var allocationBadge =
      allocationUse === null
        ? { text: "No plans", className: "" }
        : {
            text: formatPercent(allocationUse, 0) + " used",
            className:
              allocationUse > 100
                ? "is-negative"
                : allocationUse >= 80
                  ? ""
                  : "is-positive"
          };

    var cards = [
      {
        label: "Opening balance",
        value: formatMoney(report.balances.opening),
        icon: "wallet",
        tone: "is-net",
        note: "Actual account balance at the start of this period",
        badge: { text: report.accounts.count + " accounts", className: "" }
      },
      {
        label: "Total income",
        value: formatMoney(report.current.income),
        icon: "trending-up",
        tone: "is-income",
        note: "vs " + formatMoney(report.previous.income) + " previous period",
        badge: incomeBadge
      },
      {
        label: "Total expenses",
        value: formatMoney(report.current.expenses),
        icon: "trending-down",
        tone: "is-expense",
        note: "vs " + formatMoney(report.previous.expenses) + " previous period",
        badge: expenseBadge
      },
      {
        label: "Net cash flow",
        value: formatMoney(report.current.net),
        valueClass:
          report.current.net < 0
            ? "is-negative"
            : report.current.net > 0
              ? "is-positive"
              : "",
        icon: "scale",
        tone: "is-net",
        note: "Income minus actual expenses",
        badge: netBadge
      },
      {
        label: "Closing balance",
        value: formatMoney(report.balances.closing),
        icon: "landmark",
        tone: "is-net",
        note: "Actual account balance at the end of this period",
        badge: { text: formatMoney(report.balances.movement) + " movement", className: "" }
      },
      {
        label: "Savings set aside",
        value: formatMoney(report.current.savingsContributions),
        icon: "coins",
        tone: "is-savings",
        note:
          report.savings.contributionCount +
          " month" +
          (report.savings.contributionCount === 1 ? "" : "s") +
          " with Budget savings",
        badge: savingsBadge
      },
      {
        label: "Savings withdrawals",
        value: formatMoney(report.current.savingsWithdrawals),
        icon: "arrow-down-left",
        tone: "is-warning",
        note:
          report.savings.withdrawalCount +
          " withdrawal" +
          (report.savings.withdrawalCount === 1 ? "" : "s") +
          " this period",
        badge: {
          text:
            report.savings.withdrawalCount +
            (report.savings.withdrawalCount === 1 ? " withdrawal" : " withdrawals"),
          className: ""
        }
      },
      {
        label: "Spending against allocations",
        value: formatMoney(report.allocations.totalActual),
        icon: "target",
        tone: "is-warning",
        note:
          report.allocations.totalPlanned > 0
            ? "of " +
              formatMoney(report.allocations.totalPlanned) +
              " planned · " +
              formatMoney(report.allocations.totalRemaining) +
              " remaining"
            : "No allocation plans in this period",
        badge: allocationBadge
      }
    ];

    grid.innerHTML = cards
      .map(function (card) {
        return (
          '<article class="ri-stat-card">' +
          '<div class="ri-stat-top">' +
          '<span class="ri-stat-icon ' +
          card.tone +
          '"><i data-lucide="' +
          card.icon +
          '" class="w-5 h-5"></i></span>' +
          '<span class="ri-stat-badge ' +
          card.badge.className +
          '">' +
          escapeHtml(card.badge.text) +
          "</span>" +
          "</div>" +
          '<p class="ri-stat-label">' +
          escapeHtml(card.label) +
          "</p>" +
          '<strong class="ri-stat-value ' +
          (card.valueClass || "") +
          '">' +
          escapeHtml(card.value) +
          "</strong>" +
          '<p class="ri-stat-note">' +
          escapeHtml(card.note) +
          "</p>" +
          "</article>"
        );
      })
      .join("");
  }

  /* ---------- Rendering: charts ---------- */

  function themeColors() {
    var isDark = document.documentElement.classList.contains("dark");
    return {
      isDark: isDark,
      text: isDark ? "#cbd5e1" : "#475569",
      strong: isDark ? "#f1f5f9" : "#0f172a",
      grid: isDark ? "rgba(148, 163, 184, 0.16)" : "rgba(15, 23, 42, 0.07)",
      tooltipBg: isDark ? "rgba(15, 23, 42, 0.96)" : "rgba(255, 255, 255, 0.98)",
      tooltipBorder: isDark
        ? "rgba(148, 163, 184, 0.28)"
        : "rgba(15, 23, 42, 0.1)"
    };
  }

  function destroyChart(key) {
    if (charts[key]) {
      try {
        charts[key].destroy();
      } catch (error) {
        /* noop */
      }
      delete charts[key];
    }
  }

  function createChart(key, canvasId, config) {
    if (typeof Chart === "undefined") return null;
    destroyChart(key);
    var canvas = byId(canvasId);
    if (!canvas) return null;
    if (typeof Chart.getChart === "function") {
      var existing = Chart.getChart(canvas);
      if (existing) existing.destroy();
    }
    canvas.style.display = "block";
    try {
      charts[key] = new Chart(canvas.getContext("2d"), config);
      return charts[key];
    } catch (error) {
      if (window.console && console.error) {
        console.error("Unable to render report chart", error);
      }
      return null;
    }
  }

  function renderAnalysis(report) {
    var empty = byId("ri-cashflow-empty");
    var canvas = byId("ri-cashflow-chart");
    var caption = byId("ri-analysis-caption");
    var note = byId("ri-analysis-note");
    if (!canvas) return;

    var buckets = report.series.buckets;
    var hasActivity = buckets.some(function (bucket) {
      return bucket.income !== 0 || bucket.expenses !== 0;
    });
    var modeLabel =
      report.series.mode === "day"
        ? "daily"
        : report.series.mode === "week"
          ? "weekly"
          : "monthly";

    if (caption) {
      caption.textContent =
        buckets.length + " " + modeLabel + " bucket" + (buckets.length === 1 ? "" : "s");
    }
    if (note) {
      note.textContent =
        "Income and expenses are grouped by " +
        modeLabel +
        " bucket across " +
        report.range.label +
        ".";
    }

    if (!hasActivity) {
      destroyChart("cashflow");
      canvas.style.display = "none";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    var colors = themeColors();
    createChart("cashflow", "ri-cashflow-chart", {
      type: "bar",
      data: {
        labels: buckets.map(function (bucket) {
          return bucket.label;
        }),
        datasets: [
          {
            type: "bar",
            label: "Income",
            data: buckets.map(function (bucket) {
              return bucket.income;
            }),
            backgroundColor: "#10b981",
            borderRadius: 6,
            maxBarThickness: 28
          },
          {
            type: "bar",
            label: "Expenses",
            data: buckets.map(function (bucket) {
              return bucket.expenses;
            }),
            backgroundColor: "#ef4444",
            borderRadius: 6,
            maxBarThickness: 28
          },
          {
            type: "line",
            label: "Net income",
            data: buckets.map(function (bucket) {
              return bucket.net;
            }),
            borderColor: "#6366f1",
            backgroundColor: "rgba(99, 102, 241, 0.08)",
            borderWidth: 2.5,
            fill: true,
            tension: 0.35,
            pointRadius: 3,
            pointHoverRadius: 5,
            pointBackgroundColor: "#6366f1",
            pointBorderColor: colors.isDark ? "#0c0d11" : "#ffffff",
            pointBorderWidth: 1.5
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: colors.tooltipBg,
            titleColor: colors.strong,
            bodyColor: colors.text,
            borderColor: colors.tooltipBorder,
            borderWidth: 1,
            padding: 11,
            callbacks: {
              label: function (context) {
                return (
                  context.dataset.label + ": " + formatMoney(context.parsed.y)
                );
              }
            }
          }
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: colors.text,
              font: { size: 10, weight: "600" },
              maxRotation: 0,
              autoSkip: true
            }
          },
          y: {
            grid: { color: colors.grid },
            border: { display: false },
            ticks: {
              color: colors.text,
              font: { size: 10, weight: "600" },
              callback: function (value) {
                return formatAxisMoney(value);
              }
            }
          }
        }
      }
    });
  }

  function expenseColorMap(list) {
    var map = Object.create(null);
    list.forEach(function (item, index) {
      map[item.category] = CHART_COLORS[index % CHART_COLORS.length];
    });
    return map;
  }

  function renderCategories(report) {
    var focusSelect = byId("ri-category-focus");
    var expenseList = report.categories.expense.list;

    if (focusSelect) {
      var desired = state.categoryFocus;
      var options = ['<option value="all">All categories</option>'];
      expenseList.forEach(function (item) {
        options.push(
          '<option value="' +
            escapeHtml(item.category) +
            '">' +
            escapeHtml(item.category) +
            "</option>"
        );
      });
      focusSelect.innerHTML = options.join("");
      var exists = expenseList.some(function (item) {
        return item.category === desired;
      });
      if (desired !== "all" && !exists) {
        state.categoryFocus = "all";
        desired = "all";
      }
      focusSelect.value = desired;
      focusSelect.disabled = expenseList.length === 0;
    }

    renderExpenseCategoryView(report);

    renderCategoryTable(
      "ri-income-category-table",
      report.categories.income.list,
      "income",
      null,
      "No income categories in this period",
      "Record income in My Finances or choose another reporting period."
    );
  }

  function renderExpenseCategoryView(report) {
    var expenseList = report.categories.expense.list;

    renderCategoryTable(
      "ri-expense-category-table",
      expenseList.filter(function (item) {
        return (
          state.categoryFocus === "all" ||
          item.category === state.categoryFocus
        );
      }),
      "expense",
      expenseColorMap(expenseList),
      "No expense categories in this period",
      "Record expenses in My Finances or choose another reporting period."
    );

    renderExpenseDoughnut(report);
  }

  function renderCategoryTable(
    containerId,
    list,
    type,
    colorMap,
    emptyTitle,
    emptyMessage
  ) {
    var container = byId(containerId);
    if (!container) return;

    if (!list.length) {
      container.innerHTML =
        '<div class="ri-table-empty"><strong>' +
        escapeHtml(emptyTitle) +
        "</strong><p>" +
        escapeHtml(emptyMessage) +
        "</p></div>";
      return;
    }

    var rows = list
      .map(function (item) {
        var dot =
          type === "expense" && colorMap
            ? '<i class="ri-cat-dot" style="background:' +
              colorMap[item.category] +
              '"></i>'
            : type === "income"
              ? '<i class="ri-cat-dot" style="background:#10b981"></i>'
              : "";
        var change;
        if (item.previousAmount <= 0) {
          change =
            item.amount > 0
              ? '<span class="ri-change is-muted">New</span>'
              : '<span class="ri-change is-muted">—</span>';
        } else if (item.change === 0) {
          change = '<span class="ri-change is-muted">No change</span>';
        } else {
          var favourable =
            type === "income" ? item.change > 0 : item.change < 0;
          change =
            '<span class="ri-change ' +
            (favourable ? "is-positive" : "is-negative") +
            '">' +
            escapeHtml(formatSignedMoney(item.change)) +
            "</span>";
        }

        return (
          "<tr>" +
          '<td data-label="Category"><span class="ri-cell-title">' +
          dot +
          escapeHtml(item.category) +
          '</span><span class="ri-cell-sub">' +
          item.count +
          " transaction" +
          (item.count === 1 ? "" : "s") +
          "</span></td>" +
          '<td class="is-right" data-label="Amount"><span class="ri-amount">' +
          escapeHtml(formatMoney(item.amount)) +
          "</span></td>" +
          '<td class="is-right" data-label="Share">' +
          escapeHtml(formatPercent(item.percentage)) +
          "</td>" +
          '<td class="is-right" data-label="vs previous">' +
          change +
          "</td>" +
          "</tr>"
        );
      })
      .join("");

    container.innerHTML =
      '<table class="ri-table"><thead><tr>' +
      "<th>Category</th>" +
      '<th class="is-right">Amount</th>' +
      '<th class="is-right">Share</th>' +
      '<th class="is-right">vs previous</th>' +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table>";
  }

  function renderExpenseDoughnut(report) {
    var empty = byId("ri-expense-category-empty");
    var canvas = byId("ri-expense-category-chart");
    var totalEl = byId("ri-doughnut-total");
    var list = report.categories.expense.list;
    var total = report.categories.expense.total;

    if (totalEl) totalEl.textContent = formatMoney(total);
    if (!canvas) return;

    if (total <= 0 || !list.length) {
      destroyChart("expenseCategory");
      canvas.style.display = "none";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    var colorMap = expenseColorMap(list);
    var labels = [];
    var values = [];
    var colors = [];

    if (state.categoryFocus !== "all") {
      var selected = list.filter(function (item) {
        return item.category === state.categoryFocus;
      })[0];
      if (selected) {
        labels.push(selected.category);
        values.push(selected.amount);
        colors.push(colorMap[selected.category]);
        var remainder = roundMoney(total - selected.amount);
        if (remainder > 0) {
          labels.push("Other categories");
          values.push(remainder);
          colors.push("#94a3b8");
        }
      }
    }

    if (!labels.length) {
      list.forEach(function (item) {
        labels.push(item.category);
        values.push(item.amount);
        colors.push(colorMap[item.category]);
      });
    }

    createChart("expenseCategory", "ri-expense-category-chart", {
      type: "doughnut",
      data: {
        labels: labels,
        datasets: [
          {
            data: values,
            backgroundColor: colors,
            borderWidth: 0,
            hoverOffset: 6
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "70%",
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: themeColors().tooltipBg,
            titleColor: themeColors().strong,
            bodyColor: themeColors().text,
            borderColor: themeColors().tooltipBorder,
            borderWidth: 1,
            padding: 10,
            callbacks: {
              label: function (context) {
                var value = Number(context.parsed) || 0;
                var share = total > 0 ? (value / total) * 100 : null;
                return (
                  context.label +
                  ": " +
                  formatMoney(value) +
                  " (" +
                  formatPercent(share) +
                  ")"
                );
              }
            }
          }
        }
      }
    });
  }

  /* ---------- Rendering: allocations ---------- */

  function renderAllocations(report) {
    var stats = byId("ri-allocation-stats");
    var table = byId("ri-allocation-table");
    var caption = byId("ri-allocation-caption");
    var note = byId("ri-allocation-note");
    var allocations = report.allocations;
    var pendingLabel = allocations.error ? "Unavailable" : "Loading…";

    if (!allocations.available) {
      if (stats) {
        stats.innerHTML = [
          "Planned allocations",
          "Actual linked spending",
          "Remaining allocated",
          "Unlinked expenses"
        ]
          .map(function (label) {
            return (
              '<div class="ri-mini-stat"><span>' +
              escapeHtml(label) +
              '</span><strong>—</strong><small>' +
              escapeHtml(pendingLabel) +
              "</small></div>"
            );
          })
          .join("");
      }
      if (caption) {
        caption.textContent = allocations.error
          ? "Allocation data could not be loaded"
          : "Loading allocation plans…";
      }
      if (note) {
        note.innerHTML =
          '<i data-lucide="info" class="w-4 h-4"></i>' +
          "<span>" +
          (allocations.error
            ? "We could not load your saved allocations. Refresh the page or try again shortly."
            : "Loading your saved allocations from your account…") +
          "</span>";
      }
      if (table) {
        table.innerHTML =
          '<div class="ri-table-empty"><strong>' +
          (allocations.error
            ? "Couldn't load allocations"
            : "Loading allocations…") +
          "</strong><p>" +
          (allocations.error
            ? "Your saved budget allocations are temporarily unavailable. No totals are shown to avoid misleading figures."
            : "Your saved budget allocations will appear here as soon as they finish loading.") +
          "</p></div>";
      }
      return;
    }

    if (stats) {
      stats.innerHTML = [
        {
          label: "Planned allocations",
          value: formatMoney(allocations.totalPlanned),
          note:
            allocations.rows.length +
            " allocation" +
            (allocations.rows.length === 1 ? "" : "s") +
            " in " +
            allocations.monthCount +
            " month" +
            (allocations.monthCount === 1 ? "" : "s"),
          tone: ""
        },
        {
          label: "Actual linked spending",
          value: formatMoney(allocations.totalActual),
          note: "Expenses explicitly linked to allocations",
          tone: ""
        },
        {
          label: "Remaining allocated",
          value: formatMoney(allocations.totalRemaining),
          note:
            allocations.totalRemaining < 0
              ? "Allocations are over their planned amounts"
              : "Planned amount not yet spent",
          tone:
            allocations.totalRemaining < 0
              ? "is-negative"
              : allocations.totalRemaining > 0
                ? "is-positive"
                : ""
        },
        {
          label: "Unlinked expenses",
          value: formatMoney(allocations.unlinkedTotal),
          note: "Included in total expenses, not assigned to an allocation",
          tone: ""
        }
      ]
        .map(function (item) {
          return (
            '<div class="ri-mini-stat"><span>' +
            escapeHtml(item.label) +
            "</span><strong class=\"" +
            item.tone +
            '">' +
            escapeHtml(item.value) +
            "</strong><small>" +
            escapeHtml(item.note) +
            "</small></div>"
          );
        })
        .join("");
    }

    if (caption) {
      caption.textContent = allocations.rows.length
        ? allocations.rows.length +
          " allocation plan" +
          (allocations.rows.length === 1 ? "" : "s") +
          " across " +
          allocations.monthCount +
          " month" +
          (allocations.monthCount === 1 ? "" : "s")
        : "No allocation plans in this reporting period";
    }

    if (note) {
      note.innerHTML =
        '<i data-lucide="info" class="w-4 h-4"></i>' +
        "<span>Actual allocation spending only counts linked expenses dated inside the selected reporting period.</span>";
    }

    if (!table) return;

    if (!allocations.rows.length) {
      table.innerHTML =
        '<div class="ri-table-empty"><strong>No allocations in this period</strong>' +
        "<p>Create allocation plans in Budget &amp; Money Allocation to report on planned versus actual spending.</p></div>";
      return;
    }

    var rows = allocations.rows
      .map(function (row) {
        var progressWidth =
          row.usage === null ? 0 : Math.min(Math.max(row.usage, 0), 100);
        var fillClass =
          row.actual > row.planned
            ? "is-over"
            : row.planned > 0 && row.actual >= row.planned
              ? "is-complete"
              : "";
        var statusText =
          row.usage === null
            ? "No plan amount"
            : row.actual > row.planned
              ? "Over plan"
              : formatPercent(row.usage, 0) + " used";
        var statusClass =
          row.actual > row.planned
            ? "is-negative"
            : row.actual > 0
              ? "is-positive"
              : "";

        return (
          "<tr>" +
          '<td data-label="Allocation"><span class="ri-cell-title">' +
          escapeHtml(row.name) +
          '</span><span class="ri-cell-sub">' +
          escapeHtml(monthLabel(row.financialMonth + "-01")) +
          "</span></td>" +
          '<td class="is-right" data-label="Planned"><span class="ri-amount">' +
          escapeHtml(formatMoney(row.planned)) +
          "</span></td>" +
          '<td class="is-right" data-label="Actual"><span class="ri-amount">' +
          escapeHtml(formatMoney(row.actual)) +
          "</span></td>" +
          '<td class="is-right" data-label="Remaining"><span class="ri-change ' +
          (row.remaining < 0 ? "is-negative" : "is-muted") +
          '">' +
          escapeHtml(formatMoney(row.remaining)) +
          "</span></td>" +
          '<td data-label="Use">' +
          '<div class="ri-progress"><div class="ri-progress-fill ' +
          fillClass +
          '" style="width:' +
          progressWidth +
          '%"></div></div>' +
          '<span class="ri-cell-sub ' +
          statusClass +
          '">' +
          escapeHtml(statusText) +
          "</span></td>" +
          "</tr>"
        );
      })
      .join("");

    table.innerHTML =
      '<table class="ri-table"><thead><tr>' +
      "<th>Allocation</th>" +
      '<th class="is-right">Planned</th>' +
      '<th class="is-right">Actual</th>' +
      '<th class="is-right">Remaining</th>' +
      "<th>Use</th>" +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table>";
  }

  /* ---------- Rendering: savings ---------- */

  function renderSavings(report) {
    var stats = byId("ri-savings-stats");
    var historyContainer = byId("ri-savings-history");
    var activityContainer = byId("ri-savings-activity");
    var savings = report.savings;

    if (stats) {
      stats.innerHTML = [
        {
          label: "Current savings",
          value: formatMoney(savings.totalBalance),
          note: "Total saved minus recorded withdrawals",
          tone: ""
        },
        {
          label: "Saved this period",
          value: formatMoney(savings.contributions),
          note:
            savings.contributionCount +
            " month" +
            (savings.contributionCount === 1 ? "" : "s") +
            " with Budget savings",
          tone: savings.contributions > 0 ? "is-positive" : ""
        },
        {
          label: "Withdrawn this period",
          value: formatMoney(savings.withdrawalTotal),
          note:
            savings.withdrawalCount +
            " withdrawal" +
            (savings.withdrawalCount === 1 ? "" : "s"),
          tone: savings.withdrawalTotal > 0 ? "is-negative" : ""
        },
        {
          label: "Net change this period",
          value: formatMoney(savings.contributions - savings.withdrawalTotal),
          note: "Budget savings minus withdrawals",
          tone:
            savings.contributions - savings.withdrawalTotal > 0
              ? "is-positive"
              : savings.contributions - savings.withdrawalTotal < 0
                ? "is-negative"
                : ""
        }
      ]
        .map(function (item) {
          return (
            '<div class="ri-mini-stat"><span>' +
            escapeHtml(item.label) +
            "</span><strong class=\"" +
            item.tone +
            '">' +
            escapeHtml(item.value) +
            "</strong><small>" +
            escapeHtml(item.note) +
            "</small></div>"
          );
        })
        .join("");
    }

    if (historyContainer) {
      if (!savings.available) {
        historyContainer.innerHTML =
          '<div class="ri-table-empty"><strong>' +
          (savings.error ? "Savings unavailable" : "Loading savings") +
          "</strong><p>" +
          (savings.error
            ? "We couldn't load your savings. Open Savings to try again."
            : "Loading your latest savings history.") +
          "</p></div>";
      } else if (!savings.months.length) {
        historyContainer.innerHTML =
          '<div class="ri-table-empty"><strong>No savings activity yet</strong>' +
          "<p>Your Budget Savings allocations and withdrawals will appear here automatically.</p></div>";
      } else {
        var monthCards = savings.months
          .map(function (row) {
            var saved = roundMoney(Number(row.saved) || 0);
            var withdrawn = roundMoney(Number(row.withdrawn) || 0);
            var net = roundMoney(Number(row.net) || 0);
            var pillClass = net < 0 ? "is-archived" : saved > 0 ? "is-complete" : "";
            var pillText = net < 0 ? "Net withdrawal" : saved > 0 ? "Set aside" : "No savings";
            return (
              '<div class="ri-plan-card">' +
              '<div class="ri-plan-top"><div><strong>' +
              escapeHtml(monthLabel(row.month + "-01")) +
              "</strong><span>" +
              escapeHtml(formatMoney(saved) + " set aside") +
              '</span></div><span class="ri-plan-pill ' +
              pillClass +
              '">' +
              escapeHtml(pillText) +
              "</span></div>" +
              '<div class="ri-plan-meta"><span>' +
              escapeHtml(
                withdrawn > 0
                  ? formatMoney(withdrawn) + " withdrawn"
                  : "No withdrawals"
              ) +
              "</span><strong>" +
              escapeHtml((net < 0 ? "−" : "+") + formatMoney(Math.abs(net))) +
              "</strong></div>" +
              "</div>"
            );
          })
          .join("");
        historyContainer.innerHTML = monthCards;
      }
    }

    if (!activityContainer) return;

    if (!savings.withdrawals.length) {
      activityContainer.innerHTML =
        '<div class="ri-table-empty"><strong>No withdrawals this period</strong>' +
        "<p>Withdrawals recorded in the selected period will appear here. Budget savings are shown in Savings history.</p></div>";
      return;
    }

    var rows = savings.withdrawals
      .map(function (withdrawal) {
        return (
          "<tr>" +
          '<td data-label="Date"><span class="ri-cell-title">' +
          escapeHtml(formatShortDate(withdrawal.date)) +
          "</span></td>" +
          '<td data-label="Reason"><span class="ri-cell-title">' +
          escapeHtml(withdrawal.reason || "Savings withdrawal") +
          "</span></td>" +
          '<td data-label="Type"><span class="ri-change is-negative">Withdrawal</span></td>' +
          '<td class="is-right" data-label="Amount"><span class="ri-amount">' +
          escapeHtml("−" + formatMoney(withdrawal.amount)) +
          "</span></td>" +
          "</tr>"
        );
      })
      .join("");

    activityContainer.innerHTML =
      '<table class="ri-table"><thead><tr>' +
      "<th>Date</th><th>Reason</th><th>Type</th>" +
      '<th class="is-right">Amount</th>' +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table>";
  }

  /* ---------- Rendering: transfers ---------- */

  function renderTransfers(report) {
    var stats = byId("ri-transfer-stats");
    var table = byId("ri-transfer-table");
    var transfers = report.transfers;

    if (stats) {
      stats.innerHTML = [
        {
          label: "Transfers out",
          value: formatMoney(transfers.out),
          note: "Money moved out of source accounts",
          tone: ""
        },
        {
          label: "Transfers in",
          value: formatMoney(transfers.in),
          note: "Money moved into destination accounts",
          tone: ""
        },
        {
          label: "Transfer records",
          value: String(transfers.count),
          note: "Internal movements in this period",
          tone: ""
        },
        {
          label: "Effect on total balance",
          value: formatMoney(0),
          note: "Internal transfers never change combined wealth",
          tone: ""
        }
      ]
        .map(function (item) {
          return (
            '<div class="ri-mini-stat"><span>' +
            escapeHtml(item.label) +
            "</span><strong class=\"" +
            item.tone +
            '">' +
            escapeHtml(item.value) +
            "</strong><small>" +
            escapeHtml(item.note) +
            "</small></div>"
          );
        })
        .join("");
    }

    if (!table) return;

    if (!transfers.rows.length) {
      table.innerHTML =
        '<div class="ri-table-empty"><strong>No transfers in this period</strong>' +
        "<p>Transfers between your accounts will appear here. They are never counted as income or expenses.</p></div>";
      return;
    }

    var rows = transfers.rows
      .map(function (item) {
        return (
          "<tr>" +
          '<td data-label="Date"><span class="ri-cell-title">' +
          escapeHtml(formatShortDate(item.date)) +
          "</span></td>" +
          '<td data-label="From"><span class="ri-cell-title">' +
          escapeHtml(item.from || "—") +
          "</span></td>" +
          '<td data-label="To"><span class="ri-cell-title">' +
          escapeHtml(item.to || "—") +
          "</span></td>" +
          '<td data-label="Description"><span class="ri-cell-sub">' +
          escapeHtml(item.description || "Internal transfer") +
          "</span></td>" +
          '<td class="is-right" data-label="Amount"><span class="ri-amount">' +
          escapeHtml(formatMoney(item.amount)) +
          "</span></td>" +
          "</tr>"
        );
      })
      .join("");

    table.innerHTML =
      '<table class="ri-table"><thead><tr>' +
      "<th>Date</th><th>From</th><th>To</th><th>Description</th>" +
      '<th class="is-right">Amount</th>' +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table>";
  }

  /* ---------- Rendering: goals ---------- */

  function renderGoals(report) {
    var stats = byId("ri-goals-stats");
    var table = byId("ri-goals-table");
    var activity = byId("ri-goals-activity");
    var caption = byId("ri-goals-caption");
    var goals = report.goals;

    if (caption) {
      caption.textContent =
        goals.goal_count +
        " goal" +
        (goals.goal_count === 1 ? "" : "s") +
        " · " +
        formatMoney(goals.funding_total) +
        " cumulative funding";
    }

    if (stats) {
      stats.innerHTML = [
        {
          label: "Cumulative funding",
          value: formatMoney(goals.funding_total),
          note: "Budget Goals allocations to date",
          tone: ""
        },
        {
          label: "Active assigned funding",
          value: formatMoney(goals.reserved_total),
          note: "Reserved by active, unreleased assignments",
          tone: ""
        },
        {
          label: "Consumed by purchases",
          value: formatMoney(goals.consumed_total),
          note: "Assigned funding used by recorded purchases",
          tone: ""
        },
        {
          label: "Available funding",
          value: formatMoney(goals.available),
          note: "Funding not assigned or consumed",
          tone: goals.available > 0 ? "is-positive" : ""
        }
      ]
        .map(function (item) {
          return (
            '<div class="ri-mini-stat"><span>' +
            escapeHtml(item.label) +
            "</span><strong class=\"" +
            item.tone +
            '">' +
            escapeHtml(item.value) +
            "</strong><small>" +
            escapeHtml(item.note) +
            "</small></div>"
          );
        })
        .join("");
    }

    if (table) {
      if (!goals.rows.length) {
        table.innerHTML =
          '<div class="ri-table-empty"><strong>No Goals recorded yet</strong>' +
          "<p>Create Goals and fund them through Budget Goals allocations to see progress here.</p></div>";
      } else {
        var rows = goals.rows
          .map(function (row) {
            var progress = Math.min(Math.max(Number(row.progress) || 0, 0), 100);
            var pillClass =
              row.status === "purchased"
                ? "is-complete"
                : row.status === "fully funded"
                  ? "is-complete"
                  : "";
            return (
              "<tr>" +
              '<td data-label="Goal"><span class="ri-cell-title">' +
              escapeHtml(row.name) +
              (row.target_date
                ? '<span class="ri-cell-sub">Target ' +
                  escapeHtml(formatShortDate(row.target_date)) +
                  "</span>"
                : "") +
              "</span></td>" +
              '<td class="is-right" data-label="Target"><span class="ri-amount">' +
              escapeHtml(formatMoney(row.target)) +
              "</span></td>" +
              '<td class="is-right" data-label="Assigned"><span class="ri-amount">' +
              escapeHtml(formatMoney(row.assigned)) +
              "</span></td>" +
              '<td data-label="Progress">' +
              '<div class="ri-progress"><div class="ri-progress-fill ' +
              (row.status === "purchased" ? "is-complete" : "") +
              '" style="width:' +
              progress +
              '%"></div></div><span class="ri-cell-sub">' +
              escapeHtml(formatPercent(progress)) +
              "</span></td>" +
              '<td class="is-right" data-label="Consumed">' +
              escapeHtml(formatMoney(row.consumed)) +
              "</td>" +
              '<td data-label="Status"><span class="ri-plan-pill ' +
              pillClass +
              '">' +
              escapeHtml(row.status) +
              "</span></td>" +
              "</tr>"
            );
          })
          .join("");
        table.innerHTML =
          '<table class="ri-table"><thead><tr>' +
          "<th>Goal</th>" +
          '<th class="is-right">Target</th>' +
          '<th class="is-right">Assigned</th>' +
          "<th>Progress</th>" +
          '<th class="is-right">Consumed</th>' +
          "<th>Status</th>" +
          "</tr></thead><tbody>" +
          rows +
          "</tbody></table>";
      }
    }

    if (!activity) return;
    if (!goals.month_purchases.length) {
      activity.innerHTML =
        '<div class="ri-table-empty"><strong>No Goal purchases this period</strong>' +
        "<p>Expenses linked to a fully funded Goal will appear here.</p></div>";
      return;
    }
    var purchaseRows = goals.month_purchases
      .map(function (item) {
        return (
          "<tr>" +
          '<td data-label="Goal"><span class="ri-cell-title">' +
          escapeHtml(item.goal) +
          "</span></td>" +
          '<td data-label="Date"><span class="ri-cell-sub">' +
          escapeHtml(formatShortDate(item.date)) +
          "</span></td>" +
          '<td class="is-right" data-label="Amount"><span class="ri-amount">' +
          escapeHtml(formatMoney(item.amount)) +
          "</span></td>" +
          "</tr>"
        );
      })
      .join("");
    activity.innerHTML =
      '<table class="ri-table"><thead><tr>' +
      "<th>Goal</th><th>Date</th>" +
      '<th class="is-right">Amount</th>' +
      "</tr></thead><tbody>" +
      purchaseRows +
      "</tbody></table>";
  }

  /* ---------- Rendering: comparison ---------- */

  function comparisonChangeText(item) {
    if (item.previous <= 0) {
      return item.current <= 0
        ? "No activity in either period"
        : "No previous-period activity";
    }
    if (item.current === item.previous) {
      return "Previous " + formatMoney(item.previous) + " · no change";
    }
    var percent = ((item.current - item.previous) / item.previous) * 100;
    return (
      "Previous " +
      formatMoney(item.previous) +
      " · " +
      (percent > 0 ? "+" : "−") +
      formatPercent(Math.abs(percent))
    );
  }

  function renderComparison(report) {
    var comparison = report.comparison;
    var canvas = byId("ri-comparison-chart");
    var empty = byId("ri-comparison-empty");
    var caption = byId("ri-comparison-caption");
    var changes = byId("ri-category-changes");

    if (caption) {
      caption.textContent =
        "Current: " +
        formatRangeLabel(report.range.start, report.range.end) +
        " · Previous: " +
        formatRangeLabel(report.range.prevStart, report.range.prevEnd);
    }

    if (changes) {
      var summary = comparison.items
        .map(function (item) {
          var change = roundMoney(item.current - item.previous);
          var tone =
            item.previous <= 0
              ? ""
              : (item.favourableWhenUp ? change > 0 : change < 0)
                ? "is-positive"
                : change < 0 || item.favourableWhenUp === false
                  ? change === 0
                    ? ""
                    : "is-negative"
                  : "";
          return (
            '<div class="ri-mini-stat"><span>' +
            escapeHtml(item.label) +
            "</span><strong class=\"" +
            tone +
            '">' +
            escapeHtml(formatMoney(item.current)) +
            "</strong><small>" +
            escapeHtml(comparisonChangeText(item)) +
            "</small></div>"
          );
        })
        .join("");

      var categoryRows = buildCategoryChangeRows(report);
      changes.innerHTML =
        '<div class="ri-mini-stat-grid">' +
        summary +
        "</div>" +
        '<p class="ri-subhead">Expense category changes</p>' +
        categoryRows;
    }

    if (!canvas) return;

    if (!comparison.hasActivity) {
      destroyChart("comparison");
      canvas.style.display = "none";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    var colors = themeColors();
    createChart("comparison", "ri-comparison-chart", {
      type: "bar",
      data: {
        labels: comparison.items.map(function (item) {
          return item.label;
        }),
        datasets: [
          {
            label: "This period",
            data: comparison.items.map(function (item) {
              return item.current;
            }),
            backgroundColor: "#6366f1",
            borderRadius: 6,
            maxBarThickness: 30
          },
          {
            label: "Previous period",
            data: comparison.items.map(function (item) {
              return item.previous;
            }),
            backgroundColor: colors.isDark
              ? "rgba(148, 163, 184, 0.35)"
              : "rgba(100, 116, 139, 0.28)",
            borderRadius: 6,
            maxBarThickness: 30
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: true,
            position: "bottom",
            labels: {
              color: colors.text,
              boxWidth: 8,
              boxHeight: 8,
              usePointStyle: true,
              font: { size: 10, weight: "600" },
              padding: 14
            }
          },
          tooltip: {
            backgroundColor: colors.tooltipBg,
            titleColor: colors.strong,
            bodyColor: colors.text,
            borderColor: colors.tooltipBorder,
            borderWidth: 1,
            padding: 10,
            callbacks: {
              label: function (context) {
                return (
                  context.dataset.label + ": " + formatMoney(context.parsed.y)
                );
              }
            }
          }
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: colors.text,
              font: { size: 10, weight: "600" },
              maxRotation: 0
            }
          },
          y: {
            grid: { color: colors.grid },
            border: { display: false },
            ticks: {
              color: colors.text,
              font: { size: 10, weight: "600" },
              callback: function (value) {
                return formatAxisMoney(value);
              }
            }
          }
        }
      }
    });
  }

  function buildCategoryChangeRows(report) {
    var rows = (report.comparison && report.comparison.categoryChanges) || [];

    if (!rows.length) {
      return (
        '<div class="ri-table-empty"><strong>No category changes</strong>' +
        "<p>There are no actual expenses in either period to compare.</p></div>"
      );
    }

    var body = rows
      .map(function (row) {
        return (
          "<tr>" +
          '<td data-label="Category"><span class="ri-cell-title">' +
          escapeHtml(row.category) +
          "</span></td>" +
          '<td class="is-right" data-label="Current"><span class="ri-amount">' +
          escapeHtml(formatMoney(row.current)) +
          "</span></td>" +
          '<td class="is-right" data-label="Previous">' +
          escapeHtml(formatMoney(row.previous)) +
          "</td>" +
          '<td class="is-right" data-label="Change"><span class="ri-change ' +
          (row.change > 0 ? "is-negative" : row.change < 0 ? "is-positive" : "is-muted") +
          '">' +
          escapeHtml(formatSignedMoney(row.change)) +
          "</span></td>" +
          "</tr>"
        );
      })
      .join("");

    return (
      '<div class="ri-table-wrap"><table class="ri-table"><thead><tr>' +
      "<th>Category</th>" +
      '<th class="is-right">Current</th>' +
      '<th class="is-right">Previous</th>' +
      '<th class="is-right">Change</th>' +
      "</tr></thead><tbody>" +
      body +
      "</tbody></table></div>"
    );
  }

  /* ---------- Rendering: insights ---------- */

  function renderInsights(report) {
    var grid = byId("ri-insights-grid");
    if (!grid) return;

    if (!report.insights.length) {
      grid.innerHTML =
        '<div class="ri-table-empty ri-insights-empty"><strong>Not enough data for insights yet</strong>' +
        "<p>Insights appear only when stored income, expenses, allocations or savings activity support them. Add records or choose a period with activity.</p></div>";
      return;
    }

    grid.innerHTML = report.insights
      .map(function (insight) {
        return (
          '<article class="ri-insight-card">' +
          '<span class="ri-insight-icon ' +
          (insight.tone || "") +
          '"><i data-lucide="' +
          insight.icon +
          '" class="w-5 h-5"></i></span>' +
          "<h3>" +
          escapeHtml(insight.title) +
          "</h3>" +
          "<p>" +
          insight.text +
          "</p>" +
          "</article>"
        );
      })
      .join("");
  }

  function applyTableLimits() {
    REPORT_TABLE_IDS.forEach(function (id) {
      var container = byId(id);
      if (!container) return;
      var previous = container.querySelector("[data-ri-table-toggle]");
      if (previous) previous.remove();
      var table = container.querySelector("table");
      if (!table) return;
      var rows = table.querySelectorAll("tbody tr");
      if (rows.length <= INITIAL_REPORT_ROWS) return;
      var expanded = Boolean(state.expandedTables[id]);
      Array.prototype.forEach.call(rows, function (row, index) {
        row.style.display = !expanded && index >= INITIAL_REPORT_ROWS ? "none" : "";
      });
      var button = document.createElement("button");
      button.type = "button";
      button.className = "ri-table-toggle";
      button.setAttribute("data-ri-table-toggle", id);
      button.textContent = expanded
        ? "Show fewer rows"
        : "View all " + rows.length + " rows";
      container.appendChild(button);
    });
  }

  /* ---------- Main render ---------- */

  function renderAll(report) {
    if (!document.getElementById("ri-overview-grid")) return;
    report = report || lastReport;
    if (!report) return;
    lastReport = report;

    renderShell(report);
    syncControls(report);
    renderOverview(report);
    renderAnalysis(report);
    renderCategories(report);
    renderAllocations(report);
    renderSavings(report);
    renderTransfers(report);
    renderGoals(report);
    renderComparison(report);
    renderInsights(report);
    applyTableLimits();

    var pdfButton = byId("ri-pdf-btn");
    if (pdfButton) pdfButton.disabled = false;

    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  /* ---------- Events ---------- */

  function markInvalid(input, invalid) {
    if (!input) return;
    input.classList.toggle("is-invalid", Boolean(invalid));
    if (invalid) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  }

  function applyPeriod() {
    var monthInput = byId("ri-period-month");
    var yearInput = byId("ri-period-year");
    var value = state.mode === "year" ? yearInput && yearInput.value : monthInput && monthInput.value;
    if (!validPeriod(state.mode, value)) {
      if (state.mode === "year" && yearInput) markInvalid(yearInput, true);
      if (state.mode === "month" && monthInput) markInvalid(monthInput, true);
      return;
    }
    if (state.mode === "year" && yearInput) markInvalid(yearInput, false);
    if (state.mode === "month" && monthInput) markInvalid(monthInput, false);
    state.period = value;
    state.categoryFocus = "all";
    loadReport();
  }

  function bindEvents() {
    var modeGroup = byId("ri-mode-group");
    if (modeGroup) {
      modeGroup.addEventListener("click", function (event) {
        var button = event.target.closest("[data-mode]");
        if (!button) return;
        var mode = button.getAttribute("data-mode") === "year" ? "year" : "month";
        if (mode === state.mode) return;
        state.mode = mode;
        state.period = defaultPeriod(mode);
        state.categoryFocus = "all";
        syncPeriodInputs();
        loadReport();
      });
    }

    var apply = byId("ri-apply-range");
    if (apply) apply.addEventListener("click", applyPeriod);

    ["ri-period-month", "ri-period-year"].forEach(function (id) {
      var input = byId(id);
      if (!input) return;
      input.addEventListener("input", function () {
        markInvalid(input, false);
      });
      input.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
          event.preventDefault();
          applyPeriod();
        }
      });
    });

    var reset = byId("ri-reset-btn");
    if (reset) {
      reset.addEventListener("click", function () {
        state.mode = "month";
        state.period = defaultPeriod("month");
        state.categoryFocus = "all";
        syncPeriodInputs();
        markInvalid(byId("ri-period-month"), false);
        markInvalid(byId("ri-period-year"), false);
        loadReport();
      });
    }

    var pdfBtn = byId("ri-pdf-btn");
    if (pdfBtn) pdfBtn.addEventListener("click", downloadPdf);

    document.addEventListener("click", function (event) {
      var toggle = event.target.closest("[data-ri-table-toggle]");
      if (!toggle) return;
      var id = toggle.getAttribute("data-ri-table-toggle");
      state.expandedTables[id] = !state.expandedTables[id];
      applyTableLimits();
    });

    var categoryFocus = byId("ri-category-focus");
    if (categoryFocus) {
      categoryFocus.addEventListener("change", function () {
        state.categoryFocus = categoryFocus.value || "all";
        if (lastReport) {
          renderExpenseCategoryView(lastReport);
          applyTableLimits();
        }
      });
    }

    var lastDark = document.documentElement.classList.contains("dark");
    var observer = new MutationObserver(function () {
      var isDark = document.documentElement.classList.contains("dark");
      if (isDark === lastDark) return;
      lastDark = isDark;
      clearTimeout(renderTimer);
      renderTimer = setTimeout(function () {
        if (lastReport) renderAll(lastReport);
      }, 120);
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"]
    });
  }

  function init() {
    readUrlState();
    syncPeriodInputs();
    bindEvents();
    loadReport();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
