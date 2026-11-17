/* Gabsweb CashFlow — Savings */
(function () {
  "use strict";

  var data = window.GabswebCashFlowData;
  if (!data) return;

  var API = "/savings-api/";
  var MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December"
  ];
  var TOAST_ICONS = {
    success: "check",
    error: "alert-circle",
    info: "info",
    warning: "alert-triangle"
  };

  var state = {
    selectedMonth: (data.todayKey ? data.todayKey() : new Date().toISOString().slice(0, 10)).slice(0, 7),
    loading: true,
    error: "",
    summary: {},
    months: [],
    withdrawals: [],
    submitting: false,
    editingId: null,
    clientReference: null,
    confirmHandler: null,
    historyExpanded: false
  };

  var INITIAL_HISTORY_SIZE = 6;

  var toastTimer = null;

  function byId(id) {
    return document.getElementById(id);
  }

  function getTodayKey() {
    return data.todayKey ? data.todayKey() : new Date().toISOString().slice(0, 10);
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
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

  function iconMarkup(name, className) {
    return (
      '<i data-lucide="' +
      escapeHtml(name) +
      '" class="' +
      escapeHtml(className || "w-4 h-4") +
      '"></i>'
    );
  }

  function money(value) {
    return data.formatNaira(Number(value) || 0);
  }

  function roundMoney(value) {
    return Math.round((Number(value) || 0) * 100) / 100;
  }

  function dateText(value) {
    return value ? data.formatDate(value) : "—";
  }

  function monthLabel(monthKey) {
    if (!monthKey) return "—";
    var parts = String(monthKey).split("-");
    var index = Number(parts[1]) - 1;
    if (!MONTH_NAMES[index]) return monthKey;
    return MONTH_NAMES[index] + " " + parts[0];
  }

  function setText(id, value) {
    var element = byId(id);
    if (element) element.textContent = value;
  }

  function setFormError(id, message) {
    var error = byId(id);
    if (!error) return;
    if (message) {
      error.textContent = message;
      error.classList.add("is-visible");
    } else {
      error.textContent = "";
      error.classList.remove("is-visible");
    }
  }

  function csrfToken() {
    var token = byId("sv-csrf-token");
    return token ? token.value : "";
  }

  function apiRequest(url, options) {
    var config = options || {};
    var headers = {
      Accept: "application/json",
      "X-CSRFToken": csrfToken()
    };
    if (config.body) headers["Content-Type"] = "application/json";
    return fetch(url, {
      method: config.method || "GET",
      credentials: "same-origin",
      headers: headers,
      body: config.body
    }).then(function (response) {
      var contentType = response.headers.get("content-type") || "";
      var isJson = contentType.indexOf("application/json") !== -1;
      return response.text().then(function (text) {
        var payload = null;
        if (isJson && text) {
          try {
            payload = JSON.parse(text);
          } catch (error) {
            payload = null;
          }
        }
        if (response.redirected) {
          var sessionError = new Error("Your session has expired.");
          sessionError.status = 401;
          throw sessionError;
        }
        if (!response.ok || !isJson) {
          var requestError = new Error(
            (payload && payload.message) || "The savings request failed."
          );
          requestError.status = response.status;
          requestError.payload = payload;
          throw requestError;
        }
        return payload;
      });
    });
  }

  function errorText(error, fallback) {
    if (error && error.payload && error.payload.errors) {
      var parts = [];
      Object.keys(error.payload.errors).forEach(function (field) {
        var messages = error.payload.errors[field];
        if (!Array.isArray(messages)) messages = [String(messages)];
        parts = parts.concat(messages);
      });
      if (parts.length) return parts.join(" ");
    }
    if (error && error.message) return error.message;
    return fallback;
  }

  function loadSavings() {
    state.loading = true;
    state.error = "";
    renderPageStatus();
    return apiRequest(API + "?month=" + encodeURIComponent(state.selectedMonth))
      .then(function (payload) {
        state.summary = (payload && payload.summary) || {};
        state.months = (payload && payload.months) || [];
        state.withdrawals = (payload && payload.withdrawals) || [];
        state.selectedMonth = (payload && payload.month) || state.selectedMonth;
        state.loading = false;
      })
      .catch(function (error) {
        state.loading = false;
        state.error =
          error && (error.status === 401 || error.status === 403)
            ? "Your session has expired. Sign in again to manage savings."
            : "We couldn't load your savings. Please try again.";
      })
      .then(function () {
        renderAll();
      });
  }

  function renderPageStatus() {
    var container = byId("sv-page-status");
    if (!container) return;
    if (state.loading) {
      container.className = "sv-page-status is-visible";
      container.innerHTML =
        '<span class="sv-page-status-icon">' +
        iconMarkup("loader", "w-4 h-4") +
        "</span><span>Loading your savings…</span>";
      refreshIcons();
      return;
    }
    if (state.error) {
      container.className = "sv-page-status is-visible is-error";
      container.innerHTML =
        '<span class="sv-page-status-icon">' +
        iconMarkup("alert-circle", "w-4 h-4") +
        "</span><span>" +
        escapeHtml(state.error) +
        '</span><button type="button" class="sv-btn sv-btn-ghost sv-btn-small" data-sv-action="retry">Try again</button>';
      refreshIcons();
      return;
    }
    container.className = "sv-page-status";
    container.innerHTML = "";
  }

  function renderStorageStatus() {
    var chip = byId("sv-storage-chip");
    var label = byId("sv-storage-label");
    if (!chip || !label) return;
    chip.classList.toggle("is-warning", Boolean(state.error));
    if (state.loading) {
      label.textContent = "Loading…";
      chip.title = "Loading your savings…";
    } else if (state.error) {
      label.textContent = "Couldn't load savings";
      chip.title = "We couldn't load your savings. Please try again.";
    } else {
      label.textContent = "Savings up to date";
      chip.title = "Your savings are saved to your account.";
    }
  }

  function renderSummary() {
    var summary = state.summary || {};
    var current = Number(summary.currentBalance || 0);
    var totalSaved = Number(summary.totalSaved || 0);
    var totalWithdrawn = Number(summary.totalWithdrawn || 0);
    var currentEl = byId("sv-current-balance");

    if (state.loading || state.error) {
      setText("sv-current-balance", "—");
      setText(
        "sv-current-balance-note",
        state.error ? "Savings could not be loaded" : "Loading your savings…"
      );
      setText("sv-total-saved", "—");
      setText(
        "sv-total-saved-note",
        state.error ? "Please try again" : "Loading your savings…"
      );
      setText("sv-total-withdrawn", "—");
      setText(
        "sv-total-withdrawn-note",
        state.error ? "Please try again" : "Loading your savings…"
      );
      if (currentEl) {
        currentEl.classList.remove("is-positive", "is-negative");
      }
      return;
    }

    setText("sv-current-balance", money(current));
    if (currentEl) {
      currentEl.classList.toggle("is-positive", current > 0);
      currentEl.classList.toggle("is-negative", current < 0);
    }
    setText(
      "sv-current-balance-note",
      totalSaved > 0
        ? money(current) + " available from " + money(totalSaved) + " saved"
        : "Total saved minus withdrawals"
    );
    setText("sv-total-saved", money(totalSaved));
    setText(
      "sv-total-saved-note",
      totalSaved > 0
        ? "Added automatically from your Budget Savings allocations"
        : "Add a Savings allocation in Budget to start saving"
    );
    setText("sv-total-withdrawn", money(totalWithdrawn));
    setText(
      "sv-total-withdrawn-note",
      totalWithdrawn > 0
        ? "Money taken out of your accumulated savings"
        : "No withdrawals recorded yet"
    );
  }

  function renderMonthSelect() {
    var select = byId("sv-month-select");
    if (!select) return;
    var months = {};
    state.months.forEach(function (row) {
      if (row && row.month) months[row.month] = true;
    });
    months[state.selectedMonth] = true;
    var html = Object.keys(months)
      .sort()
      .reverse()
      .map(function (month) {
        return (
          '<option value="' +
          escapeHtml(month) +
          '">' +
          escapeHtml(monthLabel(month)) +
          "</option>"
        );
      })
      .join("");
    select.innerHTML = html;
    select.value = state.selectedMonth;
  }

  function renderMonthly() {
    var summary = state.summary || {};
    var saved = Number(summary.monthlySaved || 0);
    var withdrawn = Number(summary.monthlyWithdrawn || 0);
    var net = Number(summary.monthlyNet || 0);

    if (state.loading || state.error) {
      setText("sv-month-saved", "—");
      setText(
        "sv-month-saved-note",
        state.error ? "Savings could not be loaded" : "Loading your savings…"
      );
      setText("sv-month-withdrawn", "—");
      setText(
        "sv-month-withdrawn-note",
        state.error ? "Savings could not be loaded" : "Loading your savings…"
      );
      setText("sv-month-net", "—");
      setText(
        "sv-month-net-note",
        state.error ? "Savings could not be loaded" : "Loading your savings…"
      );
      var pendingNet = byId("sv-month-net");
      if (pendingNet) pendingNet.classList.remove("is-positive", "is-negative");
      return;
    }

    setText("sv-month-saved", money(saved));
    setText(
      "sv-month-saved-note",
      saved > 0
        ? "Automatically from your Budget Savings allocation"
        : "No Savings allocation for this month"
    );
    setText("sv-month-withdrawn", money(withdrawn));
    setText(
      "sv-month-withdrawn-note",
      withdrawn > 0 ? "Withdrawn during this month" : "No withdrawals this month"
    );
    setText("sv-month-net", money(net));
    var netEl = byId("sv-month-net");
    if (netEl) {
      netEl.classList.toggle("is-positive", net > 0);
      netEl.classList.toggle("is-negative", net < 0);
    }
    setText(
      "sv-month-net-note",
      saved > 0 || withdrawn > 0
        ? money(saved) + " saved · " + money(withdrawn) + " withdrawn"
        : "Saved minus withdrawn this month"
    );
  }

  function withdrawalRow(withdrawal) {
    return (
      '<div class="sv-withdrawal-row">' +
      '<span class="sv-withdrawal-icon">' +
      iconMarkup("arrow-up-right", "w-4 h-4") +
      "</span>" +
      '<div class="sv-withdrawal-copy"><strong>' +
      escapeHtml(withdrawal.reason) +
      "</strong><small>" +
      escapeHtml(dateText(withdrawal.date)) +
      " · Withdrawal</small></div>" +
      '<div class="sv-withdrawal-side"><strong class="is-withdrawn">-' +
      money(withdrawal.amount) +
      "</strong>" +
      '<button type="button" class="sv-icon-button" data-sv-action="edit-withdrawal" data-sv-id="' +
      escapeHtml(withdrawal.id) +
      '" title="Edit withdrawal" aria-label="Edit withdrawal">' +
      iconMarkup("pencil", "w-4 h-4") +
      "</button>" +
      '<button type="button" class="sv-icon-button is-danger" data-sv-action="delete-withdrawal" data-sv-id="' +
      escapeHtml(withdrawal.id) +
      '" title="Delete withdrawal" aria-label="Delete withdrawal">' +
      iconMarkup("trash-2", "w-4 h-4") +
      "</button></div></div>"
    );
  }

  function historyMonth(row) {
    var saved = Number(row.saved || 0);
    var withdrawn = Number(row.withdrawn || 0);
    var net = Number(row.net || 0);
    var withdrawals = row.withdrawals || [];
    var selected = row.month === state.selectedMonth;
    return (
      '<article class="sv-history-month' +
      (selected ? " is-selected" : "") +
      '">' +
      '<div class="sv-history-month-head">' +
      '<div><h3>' +
      escapeHtml(monthLabel(row.month)) +
      "</h3><p>" +
      (saved > 0
        ? "Savings allocation from Budget"
        : "No Savings allocation recorded") +
      "</p></div>" +
      '<div class="sv-history-month-values">' +
      '<div><span>Saved</span><strong class="is-saved">' +
      money(saved) +
      "</strong></div>" +
      '<div><span>Withdrawn</span><strong class="is-withdrawn">' +
      money(withdrawn) +
      "</strong></div>" +
      '<div><span>Net</span><strong class="' +
      (net < 0 ? "is-negative" : net > 0 ? "is-positive" : "") +
      '">' +
      money(net) +
      "</strong></div></div></div>" +
      (withdrawals.length
        ? '<div class="sv-withdrawal-list">' +
          withdrawals.map(withdrawalRow).join("") +
          "</div>"
        : '<p class="sv-history-empty-line">No withdrawals recorded in this month.</p>') +
      "</article>"
    );
  }

  function renderHistory() {
    var list = byId("sv-history-list");
    if (!list) return;
    if (state.loading || state.error) {
      list.innerHTML =
        '<div class="sv-empty">' +
        '<span class="sv-empty-icon">' +
        iconMarkup(state.error ? "alert-circle" : "loader", "w-5 h-5") +
        "</span>" +
        "<h3>" +
        (state.error ? "Savings unavailable" : "Loading savings…") +
        "</h3><p>" +
        (state.error
          ? "We couldn't load your savings history. Please try again."
          : "Your savings history will appear here when it finishes loading.") +
        "</p></div>";
      refreshIcons();
      return;
    }
    var rows = state.months || [];
    var hasActivity = rows.some(function (row) {
      return Number(row.saved || 0) > 0 || Number(row.withdrawn || 0) > 0;
    });
    if (!hasActivity) {
      list.innerHTML =
        '<div class="sv-empty">' +
        '<span class="sv-empty-icon">' +
        iconMarkup("vault", "w-5 h-5") +
        "</span>" +
        "<h3>No savings activity yet</h3>" +
        "<p>Add a Savings allocation in Budget and it will appear here automatically. You do not need to enter it again.</p>" +
        "</div>";
      return;
    }
    var activeRows = rows.filter(function (row) {
      return Number(row.saved || 0) > 0 || Number(row.withdrawn || 0) > 0;
    });
    var shownRows = state.historyExpanded
      ? activeRows
      : activeRows.slice(0, INITIAL_HISTORY_SIZE);
    list.innerHTML =
      shownRows.map(historyMonth).join("") +
      (activeRows.length > INITIAL_HISTORY_SIZE
        ? '<button type="button" class="sv-btn sv-btn-ghost sv-btn-small sv-list-toggle" data-sv-action="toggle-history">' +
          iconMarkup(state.historyExpanded ? "chevron-up" : "chevron-down", "w-4 h-4") +
          "<span>" +
          (state.historyExpanded
            ? "Show fewer"
            : "View all " + activeRows.length + " months") +
          "</span></button>"
        : "");
  }

  function renderAll() {
    renderPageStatus();
    renderStorageStatus();
    renderSummary();
    renderMonthSelect();
    renderMonthly();
    renderHistory();
    refreshIcons();
  }

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

  function showToast(title, message, type) {
    var toast = byId("toast");
    var toastTitle = byId("toast-title");
    var toastMessage = byId("toast-message");
    var toastIcon = byId("toast-icon");
    if (!toast || !toastTitle || !toastMessage) return;
    var toastType = type || "success";
    toastTitle.textContent = title;
    toastMessage.textContent = message;
    toast.className = "toast sv-toast show";
    if (toastIcon) {
      toastIcon.className =
        "sv-toast-icon is-" +
        toastType +
        " flex items-center justify-center flex-shrink-0";
      toastIcon.innerHTML = iconMarkup(TOAST_ICONS[toastType] || "check", "w-5 h-5");
    }
    refreshIcons();
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toast.classList.remove("show");
    }, 3200);
  }

  function findWithdrawal(withdrawalId) {
    var match = null;
    (state.withdrawals || []).forEach(function (item) {
      if (String(item.id) === String(withdrawalId)) match = item;
    });
    return match;
  }

  function availableForWithdrawal(withdrawal) {
    var available = Number(state.summary.currentBalance || 0);
    if (!Number.isFinite(available)) available = 0;
    if (withdrawal) {
      var currentAmount = Number(withdrawal.amount || 0);
      if (Number.isFinite(currentAmount)) available += currentAmount;
    }
    return roundMoney(available);
  }

  function openWithdrawModal(withdrawalId) {
    var form = byId("sv-withdraw-form");
    if (!form) return;
    var withdrawal = withdrawalId ? findWithdrawal(withdrawalId) : null;
    if (withdrawalId && !withdrawal) return;
    form.reset();
    state.editingId = withdrawal ? withdrawal.id : null;
    state.clientReference = withdrawal
      ? null
      : "svw-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10);
    setFormError("sv-withdraw-error", "");
    setText("sv-withdraw-title", withdrawal ? "Edit withdrawal" : "Withdraw money");
    setText(
      "sv-withdraw-submit-label",
      withdrawal ? "Save changes" : "Record withdrawal"
    );
    byId("sv-withdraw-date").value = withdrawal ? withdrawal.date : getTodayKey();
    byId("sv-withdraw-amount").value = withdrawal ? withdrawal.amount : "";
    byId("sv-withdraw-reason").value = withdrawal ? withdrawal.reason : "";
    var informational = Number(state.summary && state.summary.currentBalance);
    if (!Number.isFinite(informational)) informational = 0;
    setText("sv-withdraw-available", money(roundMoney(informational)));
    showModal("sv-withdraw-modal");
    window.setTimeout(function () {
      var amount = byId("sv-withdraw-amount");
      if (amount) amount.focus();
    }, 80);
  }

  function submitWithdrawal(event) {
    event.preventDefault();
    if (state.submitting) return;
    var amount = Number(byId("sv-withdraw-amount").value);
    var date = byId("sv-withdraw-date").value;
    var reason = byId("sv-withdraw-reason").value.trim();
    var editing = findWithdrawal(state.editingId);
    var available = availableForWithdrawal(editing);

    if (!Number.isFinite(amount) || amount <= 0) {
      setFormError("sv-withdraw-error", "Enter a withdrawal amount greater than zero.");
      return;
    }
    if (amount > available + 0.005) {
      setFormError(
        "sv-withdraw-error",
        "Withdrawal cannot exceed your available savings of " + money(available) + "."
      );
      return;
    }
    if (!data.isDateOnly(date)) {
      setFormError("sv-withdraw-error", "Choose a valid withdrawal date.");
      return;
    }
    if (!reason) {
      setFormError("sv-withdraw-error", "Enter what you used the withdrawn money for.");
      return;
    }

    var submit = byId("sv-withdraw-submit");
    state.submitting = true;
    if (submit) submit.disabled = true;

    var isEditing = Boolean(editing);
    var endpoint = isEditing
      ? API + "withdrawals/" + editing.id + "/"
      : API + "withdrawals/";
    var payload = {
      amount: roundMoney(amount).toFixed(2),
      date: date,
      reason: reason
    };
    if (!isEditing) payload.clientReference = state.clientReference;

    apiRequest(endpoint, {
      method: "POST",
      body: JSON.stringify(payload)
    })
      .then(function () {
        state.submitting = false;
        if (submit) submit.disabled = false;
        state.clientReference = null;
        state.editingId = null;
        hideModal("sv-withdraw-modal");
        showToast(
          isEditing ? "Withdrawal updated" : "Withdrawal recorded",
          isEditing
            ? "Your savings balance was recalculated. No income or expense was changed."
            : money(amount) + " was withdrawn from your savings. No income or expense was created.",
          "success"
        );
        return loadSavings();
      })
      .catch(function (error) {
        state.submitting = false;
        if (submit) submit.disabled = false;
        setFormError(
          "sv-withdraw-error",
          errorText(error, "Could not record this withdrawal.")
        );
      });
  }

  function openConfirm(options) {
    var settings = options || {};
    setText("sv-confirm-title", settings.title || "Confirm action");
    setText(
      "sv-confirm-subtitle",
      settings.subtitle || "This action needs your confirmation."
    );
    setText("sv-confirm-copy", settings.message || "");
    var action = byId("sv-confirm-action");
    if (action) {
      action.textContent = settings.confirmLabel || "Confirm";
      action.className =
        "sv-btn " + (settings.danger === false ? "sv-btn-primary" : "sv-btn-danger");
    }
    state.confirmHandler = settings.onConfirm || null;
    showModal("sv-confirm-modal");
  }

  function requestDeleteWithdrawal(withdrawalId) {
    var withdrawal = findWithdrawal(withdrawalId);
    if (!withdrawal) return;
    openConfirm({
      title: "Delete withdrawal?",
      subtitle: "Your savings balance will be recalculated.",
      message:
        "The " +
        money(withdrawal.amount) +
        " withdrawal for “" +
        withdrawal.reason +
        "” on " +
        dateText(withdrawal.date) +
        " will be removed. Your current savings balance will increase by that amount.",
      confirmLabel: "Delete withdrawal",
      onConfirm: function () {
        apiRequest(API + "withdrawals/" + withdrawal.id + "/delete/", {
          method: "POST"
        })
          .then(function () {
            hideModal("sv-confirm-modal");
            showToast(
              "Withdrawal deleted",
              "Your savings balance was recalculated.",
              "success"
            );
            return loadSavings();
          })
          .catch(function (error) {
            hideModal("sv-confirm-modal");
            showToast(
              "Could not delete withdrawal",
              errorText(error, "Please try again."),
              "error"
            );
          });
      }
    });
  }

  function handleAction(event) {
    var target = event.target.closest("[data-sv-action]");
    if (!target) return;
    var action = target.getAttribute("data-sv-action");
    if (action === "retry") {
      loadSavings();
    } else if (action === "toggle-history") {
      state.historyExpanded = !state.historyExpanded;
      renderHistory();
      refreshIcons();
    } else if (action === "edit-withdrawal") {
      openWithdrawModal(target.getAttribute("data-sv-id"));
    } else if (action === "delete-withdrawal") {
      requestDeleteWithdrawal(target.getAttribute("data-sv-id"));
    }
  }

  function bindEvents() {
    var open = byId("sv-withdraw-open");
    if (open) {
      open.addEventListener("click", function () {
        openWithdrawModal();
      });
    }

    var monthSelect = byId("sv-month-select");
    if (monthSelect) {
      monthSelect.addEventListener("change", function () {
        state.selectedMonth = monthSelect.value;
        loadSavings();
      });
    }

    var form = byId("sv-withdraw-form");
    if (form) form.addEventListener("submit", submitWithdrawal);

    var history = byId("sv-history-list");
    if (history) history.addEventListener("click", handleAction);

    var status = byId("sv-page-status");
    if (status) status.addEventListener("click", handleAction);

    var confirmAction = byId("sv-confirm-action");
    if (confirmAction) {
      confirmAction.addEventListener("click", function () {
        if (typeof state.confirmHandler === "function") {
          state.confirmHandler();
        }
      });
    }

    document.addEventListener("click", function (event) {
      var close = event.target.closest("[data-sv-close]");
      if (close) {
        hideModal(close.getAttribute("data-sv-close"));
        return;
      }
      if (event.target.classList && event.target.classList.contains("modal")) {
        hideModal(event.target.id);
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      document.querySelectorAll(".modal.open").forEach(function (modal) {
        hideModal(modal.id);
      });
    });
  }

  function init() {
    bindEvents();
    renderAll();
    refreshIcons();
    loadSavings();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
