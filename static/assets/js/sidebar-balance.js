/* Gabsweb CashFlow — Shared sidebar total balance */
(function () {
  "use strict";

  var root = typeof window !== "undefined" ? window : globalThis;
  var data = root.GabswebCashFlowData;
  var balanceElement = document.getElementById("sidebar-total-balance");
  if (!data || !balanceElement) return;

  var noteElement = document.getElementById("sidebar-balance-note");
  var lastStatus = null;
  var timeoutId = null;
  var unsubscribe = null;
  var LOADING_NOTE = "Loading finances…";
  var FAILURE_NOTE = "Balance unavailable";
  var LOAD_TIMEOUT_MS = 12000;
  var sharedNoteText = noteElement
    ? String(noteElement.textContent || "").trim()
    : "";

  function status() {
    if (typeof data.getServerFinancialStatus !== "function") {
      return { status: "ready", error: null };
    }
    return data.getServerFinancialStatus();
  }

  function formatBalance(value) {
    if (typeof data.formatNaira === "function") return data.formatNaira(value);
    if (typeof data.formatCurrency === "function") {
      return data.formatCurrency(value);
    }
    return String(value);
  }

  function ownsNote() {
    return Boolean(
      noteElement &&
        noteElement.dataset &&
        noteElement.dataset.sidebarBalanceOwned === "true"
    );
  }

  function setNote(text, title) {
    if (!noteElement) return;
    noteElement.textContent = text;
    noteElement.title = title || "";
    sharedNoteText = text;
    if (noteElement.dataset) {
      noteElement.dataset.sidebarBalanceOwned = "true";
    }
  }

  function loadingText() {
    if (!noteElement) return "";
    return String(noteElement.textContent || "").trim();
  }

  function clearLoadTimeout() {
    if (timeoutId) {
      root.clearTimeout(timeoutId);
      timeoutId = null;
    }
  }

  function monthlyNote() {
    try {
      var month = data.getMonthKey(data.todayKey());
      var income = data.getTotalIncome({ month: month });
      var expenses = data.getTotalExpenses({ month: month });
      var net = Math.round((income - expenses + Number.EPSILON) * 100) / 100;
      if (income === 0 && expenses === 0) return "No activity this month";
      return (net > 0 ? "+" : "") + formatBalance(net) + " net this month";
    } catch (error) {
      return "Total across your accounts";
    }
  }

  function render() {
    var current = status();
    if (current.status === "ready") {
      clearLoadTimeout();
      balanceElement.textContent = formatBalance(data.getTotalBalance());
      if (!loadingText() || loadingText() === sharedNoteText || ownsNote() && loadingText() === sharedNoteText) {
        setNote(monthlyNote());
      }
    } else if (current.status === "error") {
      clearLoadTimeout();
      balanceElement.textContent = "—";
      setNote(
        FAILURE_NOTE,
        current.error || "We couldn't load your total balance. Please try again."
      );
    } else {
      balanceElement.textContent = "—";
      if (!loadingText() || loadingText() === sharedNoteText) {
        setNote(LOADING_NOTE);
      }
    }
    lastStatus = current.status;
  }

  function showTimeoutFailure() {
    timeoutId = null;
    var current = status();
    if (current.status === "ready" || current.status === "error") return;
    balanceElement.textContent = "—";
    setNote(
      FAILURE_NOTE,
      "Loading your total balance is taking too long. Check your connection and refresh."
    );
  }

  if (typeof data.subscribe === "function") {
    unsubscribe = data.subscribe(render);
  }

  render();
  if (status().status === "idle" && typeof data.syncServerFinancials === "function") {
    data.syncServerFinancials().catch(function () {
      // The status API renders the failure state on the next notification.
    });
  }
  if (status().status === "idle" || status().status === "loading") {
    timeoutId = root.setTimeout(showTimeoutFailure, LOAD_TIMEOUT_MS);
  }

  root.GabswebSidebarBalance = {
    refresh: render,
    destroy: function () {
      clearLoadTimeout();
      if (typeof unsubscribe === "function") unsubscribe();
    }
  };
})();
