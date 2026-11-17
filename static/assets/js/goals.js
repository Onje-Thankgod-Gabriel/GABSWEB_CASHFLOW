/* Gabsweb CashFlow — Goals */
(function () {
  "use strict";

  var data = window.GabswebCashFlowData;
  if (!data) return;

  var API = "/goals-api/";
  var TOAST_ICONS = {
    success: "check",
    error: "alert-circle",
    info: "info",
    warning: "alert-triangle"
  };

  var state = {
    loading: true,
    hasLoaded: false,
    error: "",
    summary: null,
    goals: [],
    editingId: null,
    assignGoalId: null,
    confirmHandler: null,
    submitting: false,
    activeExpanded: false,
    historyExpanded: false
  };

  var INITIAL_GOAL_LIST_SIZE = 6;

  var toastTimer = null;

  function byId(id) {
    return document.getElementById(id);
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

  function dateText(value) {
    return value ? data.formatDate(value) : "";
  }

  function percentText(value) {
    return Math.round(Number(value) || 0) + "%";
  }

  function setText(id, value) {
    var element = byId(id);
    if (element) element.textContent = value;
  }

  function setFormError(id, message) {
    var element = byId(id);
    if (!element) return;
    if (message) {
      element.textContent = message;
      element.classList.add("is-visible");
    } else {
      element.textContent = "";
      element.classList.remove("is-visible");
    }
  }

  function openModal(id) {
    var modal = byId(id);
    if (!modal) return;
    modal.classList.add("open");
    document.body.style.overflow = "hidden";
    refreshIcons();
  }

  function closeModal(id) {
    var modal = byId(id);
    if (!modal) return;
    modal.classList.remove("open");
    if (!document.querySelector(".modal.open")) {
      document.body.style.overflow = "";
    }
  }

  function showToast(title, message, tone) {
    var toast = byId("toast");
    if (!toast) return;
    var icon = byId("toast-icon");
    setText("toast-title", title);
    setText("toast-message", message || "");
    if (icon) {
      icon.className =
        "gl-toast-icon is-" +
        (tone || "success") +
        " flex items-center justify-center flex-shrink-0";
      icon.innerHTML = iconMarkup(TOAST_ICONS[tone] || "check", "w-5 h-5");
    }
    toast.classList.add("show");
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toast.classList.remove("show");
    }, 3800);
    refreshIcons();
  }

  function csrfToken() {
    var token = byId("gl-csrf-token");
    return token ? token.value : "";
  }

  function apiRequest(url, options) {
    var config = options || {};
    return window.GabswebApi.request(url, {
      method: config.method || "GET",
      body: config.body,
      csrfToken: csrfToken()
    });
  }

  function setFieldError(scope, field, message) {
    var element = document.querySelector(
      "[data-" + scope + '-field-error="' + field + '"]'
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

  function clearFieldErrors(scope) {
    document
      .querySelectorAll("[data-" + scope + "-field-error]")
      .forEach(function (element) {
        element.textContent = "";
        element.classList.remove("is-visible");
      });
  }

  function showFieldErrors(scope, error) {
    var fields = window.GabswebApi.fieldErrors(error && error.payload);
    var keys = Object.keys(fields);
    var shown = false;
    keys.forEach(function (field) {
      if (field === "__all__" || !fields[field].length) return;
      setFieldError(scope, field, fields[field][0]);
      shown = true;
    });
    return shown;
  }

  function clientReference(prefix) {
    var random =
      window.crypto && typeof window.crypto.randomUUID === "function"
        ? window.crypto.randomUUID()
        : Date.now() + "-" + Math.random().toString(16).slice(2);
    return prefix + ":" + random;
  }

  function applyPayload(payload) {
    state.summary = payload.summary || null;
    state.goals = Array.isArray(payload.goals) ? payload.goals : [];
    state.error = "";
    state.loading = false;
    state.hasLoaded = true;
  }

  function loadGoals() {
    var hasPreviousData = state.hasLoaded;
    state.loading = !hasPreviousData;
    state.error = "";
    renderAll();
    return apiRequest(API)
      .then(function (payload) {
        applyPayload(payload);
      })
      .catch(function (error) {
        state.error = window.GabswebApi.errorMessage(
          error,
          "We couldn't load your goals. Please try again."
        );
      })
      .then(function () {
        state.loading = false;
        renderAll();
      });
  }

  function renderStorageStatus() {
    var chip = byId("gl-storage-chip");
    var label = byId("gl-storage-label");
    if (!chip || !label) return;
    chip.classList.toggle("is-warning", Boolean(state.error));
    if (state.loading) {
      label.textContent = "Loading…";
      chip.title = "Loading your goals…";
    } else if (state.error) {
      label.textContent = state.hasLoaded ? "Couldn't refresh goals" : "Couldn't load goals";
      chip.title = state.error;
    } else {
      label.textContent = "Goals up to date";
      chip.title = "Your goals are saved to your account.";
    }
  }

  function renderOverview() {
    var summary = state.summary || {};
    var goalCount = Number(summary.goalCount) || 0;
    var reached = Number(summary.reachedCount) || 0;
    var funding = Number(summary.totalFunding) || 0;

    setText("gl-available-funding", money(summary.availableFunding));
    setText("gl-total-assigned", money(summary.totalAssigned));
    setText("gl-total-target", money(summary.totalTarget));
    setText("gl-remaining-target", money(summary.remainingToTarget));

    setText(
      "gl-available-note",
      funding > 0
        ? "From your Budget Goals allocation"
        : "Add a Goals allocation in Budget to fund goals"
    );
    setText(
      "gl-assigned-note",
      Number(summary.totalAssigned) > 0
        ? "Money currently reserved for goals"
        : "No money reserved yet"
    );
    setText(
      "gl-target-note",
      goalCount
        ? goalCount + " goal" + (goalCount === 1 ? "" : "s")
        : "No goals yet"
    );
    setText(
      "gl-remaining-note",
      goalCount
        ? reached + " of " + goalCount + " reached"
        : "Add a goal to start planning"
    );

    setText(
      "gl-goal-summary",
      state.loading
        ? "Loading your goals…"
        : state.error
          ? state.hasLoaded
            ? "Couldn't refresh. Showing the last loaded goals."
            : state.error
          : goalCount
            ? goalCount +
              " goal" +
              (goalCount === 1 ? "" : "s") +
              " · " +
              reached +
              " reached"
            : "No goals yet."
    );
  }

  function renderEmptyState() {
    if (state.loading) {
      return (
        '<div class="gl-empty gl-empty-wide">' +
        '<span class="gl-empty-icon">' + iconMarkup("loader", "w-4 h-4") + "</span>" +
        "<h3>Loading goals</h3>" +
        "<p>Loading your goals and available funding.</p>" +
        "</div>"
      );
    }
    if (state.error) {
      return (
        '<div class="gl-empty gl-empty-wide">' +
        '<span class="gl-empty-icon">' + iconMarkup("alert-circle", "w-4 h-4") + "</span>" +
        "<h3>Goals unavailable</h3>" +
        "<p>" + escapeHtml(state.error) + "</p>" +
        '<button type="button" class="gl-btn gl-btn-ghost" data-gl-action="retry">Try again</button>' +
        "</div>"
      );
    }
    return (
      '<div class="gl-empty gl-empty-wide">' +
      '<span class="gl-empty-icon">' + iconMarkup("target", "w-4 h-4") + "</span>" +
      "<h3>No goals yet</h3>" +
      "<p>You haven't created any goals yet. Add a goal, then assign money from your Budget Goals funding.</p>" +
      '<button type="button" class="gl-btn gl-btn-primary" data-gl-action="add">Add a goal</button>' +
      "</div>"
    );
  }

  function renderAssignmentRow(goal, assignment) {
    var canReverse = assignment.active && !assignment.released;
    var stateLabel = assignment.reversed
      ? " · Reversed"
      : assignment.released
        ? " · Applied to purchase"
        : "";
    return (
      '<div class="gl-funding-row">' +
      '<div class="gl-funding-row-main">' +
      "<strong>" + escapeHtml(money(assignment.amount)) + "</strong>" +
      "<span>Assigned " +
      escapeHtml(dateText(assignment.assignedAt)) +
      escapeHtml(stateLabel) +
      "</span>" +
      "</div>" +
      (canReverse
        ? '<button type="button" class="gl-funding-reverse" data-gl-action="reverse" data-gl-id="' +
          escapeHtml(goal.id) +
          '" data-gl-assignment-id="' +
          escapeHtml(assignment.id) +
          '">' + iconMarkup("rotate-ccw", "w-3 h-3") + "<span>Reverse</span></button>"
        : assignment.released
          ? '<span class="gl-funding-released">' +
            iconMarkup("unlock", "w-3 h-3") +
            "<span>Applied</span></span>"
          : "") +
      "</div>"
    );
  }

  function renderGoalCard(goal) {
    var percent = Math.min(100, Math.max(0, Number(goal.progressPercent) || 0));
    var status = goal.status || "active";
    var purchased = status === "purchased";
    var fullyFunded = status === "fully_funded";
    var activeAssignments = (goal.assignments || []).filter(function (assignment) {
      return assignment.active;
    });
    var visibleAssignments = purchased
      ? (goal.assignments || []).filter(function (assignment) {
          return !assignment.reversed;
        })
      : activeAssignments;
    var reached = Boolean(goal.reached);
    var deadline = goal.targetDate
      ? "Target " + dateText(goal.targetDate)
      : "No target date";
    var remaining = reached
      ? "Target reached"
      : money(goal.remainingAmount) + " remaining";
    var statusPill = purchased
      ? "is-completed"
      : fullyFunded
        ? "is-funded"
        : "is-active";
    var statusLabel = purchased
      ? "Purchased"
      : fullyFunded
        ? "Fully funded"
        : "In progress";
    var history = visibleAssignments.length
      ? '<div class="gl-funding-history">' +
        '<span class="gl-funding-history-title">' +
        (purchased ? "Funding history" : "Money assigned") +
        "</span>" +
        visibleAssignments.map(function (assignment) {
          return renderAssignmentRow(goal, assignment);
        }).join("") +
        "</div>"
      : "";
    var releasedAmount = Number(goal.releasedAmount) || 0;
    var purchaseBreakdown = purchased
      ? money(goal.consumedAmount) +
        " consumed · " +
        (releasedAmount > 0
          ? money(releasedAmount) + " returned to available funding"
          : "no unused funding returned")
      : "";
    var purchaseNote =
      purchased && goal.purchaseAmount !== null
        ? '<div class="gl-purchase-note app-message-card app-message-card--success app-message-card--has-icon">' +
          iconMarkup("shopping-bag", "w-4 h-4") +
          "<div><strong>Purchase recorded · " +
          escapeHtml(money(goal.purchaseAmount)) +
          "</strong><span>" +
          escapeHtml(
            (goal.purchaseDate ? dateText(goal.purchaseDate) + " · " : "") +
              (goal.purchaseDescription || "Linked expense")
          ) +
          "</span><span>" +
          escapeHtml(purchaseBreakdown) +
          "</span></div></div>"
        : fullyFunded
          ? '<div class="gl-purchase-note is-warning app-message-card app-message-card--warning app-message-card--has-icon">' +
            iconMarkup("alert-triangle", "w-4 h-4") +
            "<div><strong>Fully funded — purchase not recorded</strong>" +
            "<span>Link a real expense to this goal from My Finances when you buy it.</span></div></div>"
          : "";
    var assignButton = purchased || fullyFunded
      ? ""
      : '<button type="button" class="gl-btn gl-btn-soft gl-btn-small" data-gl-action="assign" data-gl-id="' +
        escapeHtml(goal.id) +
        '">' + iconMarkup("plus", "w-3 h-3") + "Assign money</button>";

    return (
      '<article class="gl-goal-card' +
      (purchased ? " is-purchased" : fullyFunded ? " is-funded" : "") +
      '" data-gl-goal-id="' + escapeHtml(goal.id) + '">' +
      '<div class="gl-goal-top">' +
      '<div class="gl-goal-identity">' +
      '<span class="gl-goal-icon ' +
      (purchased ? "is-income" : fullyFunded ? "is-purple" : "is-transfer") +
      '">' + iconMarkup(purchased ? "check-circle" : "target", "w-4 h-4") + "</span>" +
      '<div class="gl-goal-copy">' +
      '<h3 class="gl-goal-name">' + escapeHtml(goal.name) + "</h3>" +
      '<p class="gl-goal-type">' + iconMarkup("calendar", "w-3 h-3") + escapeHtml(deadline) + "</p>" +
      "</div></div>" +
      '<span class="gl-status-pill ' + statusPill + '">' +
      escapeHtml(statusLabel) +
      "</span>" +
      "</div>" +
      (goal.description
        ? '<p class="gl-goal-objective">' + escapeHtml(goal.description) + "</p>"
        : "") +
      '<div class="gl-goal-funding">' +
      '<div class="gl-goal-amount">' +
      "<span>Assigned of target</span>" +
      "<strong>" + escapeHtml(money(goal.assignedAmount)) +
      " <small>of " + escapeHtml(money(goal.targetAmount)) + "</small></strong>" +
      "</div>" +
      '<div class="gl-progress" role="progressbar" aria-valuenow="' + percent +
      '" aria-valuemin="0" aria-valuemax="100" aria-label="' +
      escapeHtml(goal.name) + ' progress">' +
      '<span class="gl-progress-fill' + (reached ? " is-complete" : "") +
      '" style="width:' + percent + '%"></span>' +
      "</div>" +
      '<div class="gl-goal-meta">' +
      "<span>" + escapeHtml(percentText(percent)) + " funded</span>" +
      '<span class="' + (reached ? "is-positive" : "") + '">' + escapeHtml(remaining) + "</span>" +
      "</div>" +
      "</div>" +
      purchaseNote +
      history +
      '<div class="gl-goal-footer">' +
      assignButton +
      '<div class="gl-goal-actions">' +
      '<button type="button" class="gl-icon-button" data-gl-action="edit" data-gl-id="' +
      escapeHtml(goal.id) +
      '" aria-label="Edit ' + escapeHtml(goal.name) + '" title="Edit goal">' +
      iconMarkup("pencil", "w-4 h-4") +
      "</button>" +
      '<button type="button" class="gl-icon-button is-danger" data-gl-action="delete" data-gl-id="' +
      escapeHtml(goal.id) +
      '" aria-label="Delete ' + escapeHtml(goal.name) + '" title="Delete goal">' +
      iconMarkup("trash-2", "w-4 h-4") +
      "</button>" +
      "</div></div></article>"
    );
  }

  function renderHistoryEmpty() {
    return (
      '<div class="gl-empty is-compact gl-empty-wide">' +
      '<span class="gl-empty-icon">' + iconMarkup("archive", "w-4 h-4") + "</span>" +
      "<h3>No goal history yet</h3>" +
      "<p>Fully funded and purchased goals appear here with their original target and purchase details.</p>" +
      "</div>"
    );
  }

  function goalListToggle(action, total, expanded) {
    if (total <= INITIAL_GOAL_LIST_SIZE) return "";
    return (
      '<button type="button" class="gl-btn gl-btn-ghost gl-btn-small gl-list-toggle" data-gl-action="' +
      action +
      '">' +
      iconMarkup(expanded ? "chevron-up" : "chevron-down", "w-4 h-4") +
      "<span>" +
      (expanded ? "Show fewer" : "View all " + total + " goals") +
      "</span></button>"
    );
  }

  function renderGoals() {
    var grid = byId("gl-goal-grid");
    var historyGrid = byId("gl-goal-history-grid");
    if (!grid) return;
    if (state.loading || (!state.goals.length && state.error)) {
      grid.innerHTML = renderEmptyState();
      if (historyGrid) historyGrid.innerHTML = "";
      refreshIcons();
      return;
    }
    var activeGoals = state.goals.filter(function (goal) {
      return (goal.status || "active") === "active";
    });
    var historyGoals = state.goals.filter(function (goal) {
      return (goal.status || "active") !== "active";
    });
    var shownActive = state.activeExpanded
      ? activeGoals
      : activeGoals.slice(0, INITIAL_GOAL_LIST_SIZE);
    var shownHistory = state.historyExpanded
      ? historyGoals
      : historyGoals.slice(0, INITIAL_GOAL_LIST_SIZE);
    grid.innerHTML = activeGoals.length
      ? shownActive.map(renderGoalCard).join("") +
        goalListToggle("toggle-active", activeGoals.length, state.activeExpanded)
      : state.goals.length
        ? renderHistoryEmpty()
        : renderEmptyState();
    if (historyGrid) {
      historyGrid.innerHTML = historyGoals.length
        ? shownHistory.map(renderGoalCard).join("") +
          goalListToggle(
            "toggle-history",
            historyGoals.length,
            state.historyExpanded
          )
        : renderHistoryEmpty();
    }
    refreshIcons();
  }

  function renderAll() {
    renderStorageStatus();
    renderOverview();
    renderGoals();
  }

  function findGoal(id) {
    for (var index = 0; index < state.goals.length; index += 1) {
      if (state.goals[index].id === id) return state.goals[index];
    }
    return null;
  }

  function openGoalEditor(goal) {
    state.editingId = goal ? goal.id : null;
    setText("gl-modal-title", goal ? "Edit goal" : "Add goal");
    setText(
      "gl-modal-subtitle",
      goal
        ? "Update this goal's target. Funding history is not changed."
        : "Create something you want to save towards."
    );
    setText("gl-form-submit-label", goal ? "Save changes" : "Create goal");
    byId("gl-form-name").value = goal ? goal.name : "";
    byId("gl-form-target").value = goal ? goal.targetAmount : "";
    byId("gl-form-date").value = goal && goal.targetDate ? goal.targetDate : "";
    byId("gl-form-description").value = goal ? goal.description || "" : "";
    setFormError("gl-form-error", "");
    clearFieldErrors("gl");
    openModal("gl-goal-modal");
  }

  function openAssignModal(goal) {
    state.assignGoalId = goal.id;
    setText("gl-funds-goal-name", goal.name);
    setText("gl-funds-available", money((state.summary || {}).availableFunding));
    byId("gl-funds-amount").value = "";
    setFormError("gl-funds-error", "");
    clearFieldErrors("gl");
    openModal("gl-funds-modal");
  }

  function openConfirm(options) {
    state.confirmHandler = options.onConfirm;
    setText("gl-confirm-title", options.title || "Confirm action");
    setText("gl-confirm-copy", options.message || "Please confirm this action.");
    var action = byId("gl-confirm-action");
    if (action) action.textContent = options.confirmLabel || "Confirm";
    openModal("gl-confirm-modal");
  }

  function setSubmitting(button, submitting) {
    if (!button) return;
    button.disabled = Boolean(submitting);
    button.classList.toggle("is-loading", Boolean(submitting));
  }

  function submitGoal(event) {
    event.preventDefault();
    if (state.submitting) return;
    var form = byId("gl-goal-form");
    var payload = {
      name: byId("gl-form-name").value.trim(),
      targetAmount: byId("gl-form-target").value,
      targetDate: byId("gl-form-date").value || null,
      description: byId("gl-form-description").value.trim()
    };
    clearFieldErrors("gl");
    if (!payload.name) {
      setFieldError("gl", "name", "Enter a goal name.");
      setFormError("gl-form-error", "Please correct the highlighted field.");
      byId("gl-form-name").focus();
      return;
    }
    if (!payload.targetAmount || Number(payload.targetAmount) <= 0) {
      setFieldError(
        "gl",
        "target_amount",
        "Enter a target amount greater than zero."
      );
      setFormError("gl-form-error", "Please correct the highlighted field.");
      byId("gl-form-target").focus();
      return;
    }
    state.submitting = true;
    setSubmitting(byId("gl-form-submit"), true);
    setFormError("gl-form-error", "");
    var url = state.editingId
      ? API + "goals/" + encodeURIComponent(state.editingId) + "/"
      : API + "goals/";
    payload.clientReference = state.editingId
      ? undefined
      : clientReference("goal");
    apiRequest(url, {
      method: "POST",
      body: JSON.stringify(payload)
    })
      .then(function (response) {
        applyPayload(response);
        closeModal("gl-goal-modal");
        renderAll();
        showToast(
          state.editingId ? "Goal updated" : "Goal created",
          state.editingId
            ? "Your goal changes were saved."
            : "You can now assign money from your available Goals funding.",
          "success"
        );
      })
      .catch(function (error) {
        clearFieldErrors("gl");
        var highlighted = showFieldErrors("gl", error);
        setFormError(
          "gl-form-error",
          highlighted
            ? "Please correct the highlighted fields."
            : window.GabswebApi.errorMessage(
                error,
                "We couldn't save this goal. Please try again."
              )
        );
      })
      .then(function () {
        state.submitting = false;
        setSubmitting(byId("gl-form-submit"), false);
      });
  }

  function submitAssignment(event) {
    event.preventDefault();
    if (state.submitting || !state.assignGoalId) return;
    var amount = byId("gl-funds-amount").value;
    clearFieldErrors("gl");
    if (!amount || Number(amount) <= 0) {
      setFieldError("gl", "amount", "Enter an amount greater than zero.");
      setFormError("gl-funds-error", "Please correct the highlighted field.");
      byId("gl-funds-amount").focus();
      return;
    }
    var available = Number((state.summary || {}).availableFunding) || 0;
    if (Number(amount) > available) {
      var limitMessage =
        "This change would use more money than is available for your Goals. " +
        "Reduce the assignment or add more money to your Goals budget before trying again.";
      setFieldError("gl", "amount", limitMessage);
      setFormError("gl-funds-error", "Please correct the highlighted field.");
      return;
    }
    state.submitting = true;
    setSubmitting(byId("gl-funds-submit"), true);
    setFormError("gl-funds-error", "");
    apiRequest(
      API + "goals/" + encodeURIComponent(state.assignGoalId) + "/assignments/",
      {
        method: "POST",
        body: JSON.stringify({
          amount: amount,
          clientReference: clientReference("assignment")
        })
      }
    )
      .then(function (response) {
        applyPayload(response);
        closeModal("gl-funds-modal");
        renderAll();
        showToast(
          "Money assigned",
          "Available Goals funding has been updated.",
          "success"
        );
      })
      .catch(function (error) {
        clearFieldErrors("gl");
        var highlighted = showFieldErrors("gl", error);
        setFormError(
          "gl-funds-error",
          highlighted
            ? "Please correct the highlighted fields."
            : window.GabswebApi.errorMessage(
                error,
                "We couldn't assign this money. Please try again."
              )
        );
      })
      .then(function () {
        state.submitting = false;
        setSubmitting(byId("gl-funds-submit"), false);
      });
  }

  function reverseAssignment(goalId, assignmentId) {
    var goal = findGoal(goalId);
    if (!goal) return;
    var assignment = (goal.assignments || []).filter(function (item) {
      return item.id === assignmentId;
    })[0];
    if (!assignment) return;
    openConfirm({
      title: "Reverse assignment",
      message:
        money(assignment.amount) +
        " will return to your available Goals funding. The goal's funding history keeps a record of this reversal.",
      confirmLabel: "Reverse",
      onConfirm: function () {
        apiRequest(
          API +
            "goals/" +
            encodeURIComponent(goalId) +
            "/assignments/" +
            encodeURIComponent(assignmentId) +
            "/reverse/",
          { method: "POST", body: JSON.stringify({}) }
        )
          .then(function (response) {
            applyPayload(response);
            closeModal("gl-confirm-modal");
            renderAll();
            showToast(
              "Assignment reversed",
              "The money is available to assign again.",
              "success"
            );
          })
          .catch(function (error) {
            showToast(
              "Couldn't reverse assignment",
              error.message || "Please try again.",
              "error"
            );
          });
      }
    });
  }

  function deleteGoal(goalId) {
    var goal = findGoal(goalId);
    if (!goal) return;
    if ((goal.assignments || []).length) {
      showToast(
        "Goal can't be deleted",
        "This goal has funding history. Goals with any assignment history are kept so your records stay accurate.",
        "warning"
      );
      return;
    }
    openConfirm({
      title: "Delete goal",
      message:
        "Delete \"" + goal.name + "\"? This cannot be undone.",
      confirmLabel: "Delete",
      onConfirm: function () {
        apiRequest(API + "goals/" + encodeURIComponent(goalId) + "/delete/", {
          method: "POST",
          body: JSON.stringify({})
        })
          .then(function (response) {
            applyPayload(response);
            closeModal("gl-confirm-modal");
            renderAll();
            showToast("Goal deleted", "The goal was removed.", "success");
          })
          .catch(function (error) {
            showToast(
              "Couldn't delete goal",
              error.message || "Please try again.",
              "error"
            );
          });
      }
    });
  }

  function handleGridClick(event) {
    var target = event.target.closest("[data-gl-action]");
    if (!target) return;
    var action = target.getAttribute("data-gl-action");
    var goalId = target.getAttribute("data-gl-id");
    if (action === "add") {
      openGoalEditor(null);
    } else if (action === "toggle-active") {
      state.activeExpanded = !state.activeExpanded;
      renderGoals();
    } else if (action === "toggle-history") {
      state.historyExpanded = !state.historyExpanded;
      renderGoals();
    } else if (action === "retry") {
      loadGoals();
    } else if (action === "assign") {
      var assignGoal = findGoal(goalId);
      if (assignGoal) openAssignModal(assignGoal);
    } else if (action === "edit") {
      var editGoal = findGoal(goalId);
      if (editGoal) openGoalEditor(editGoal);
    } else if (action === "delete") {
      deleteGoal(goalId);
    } else if (action === "reverse") {
      reverseAssignment(goalId, target.getAttribute("data-gl-assignment-id"));
    }
  }

  function bindEvents() {
    var addButton = byId("gl-add-goal");
    if (addButton) {
      addButton.addEventListener("click", function () {
        openGoalEditor(null);
      });
    }

    var grid = byId("gl-goal-grid");
    if (grid) grid.addEventListener("click", handleGridClick);
    var historyGrid = byId("gl-goal-history-grid");
    if (historyGrid) historyGrid.addEventListener("click", handleGridClick);

    var goalForm = byId("gl-goal-form");
    if (goalForm) goalForm.addEventListener("submit", submitGoal);

    var fundsForm = byId("gl-funds-form");
    if (fundsForm) fundsForm.addEventListener("submit", submitAssignment);

    document.querySelectorAll("[data-gl-close]").forEach(function (button) {
      button.addEventListener("click", function () {
        closeModal(button.getAttribute("data-gl-close"));
      });
    });

    var confirmAction = byId("gl-confirm-action");
    if (confirmAction) {
      confirmAction.addEventListener("click", function () {
        var handler = state.confirmHandler;
        state.confirmHandler = null;
        if (typeof handler === "function") handler();
      });
    }

    document.querySelectorAll(".modal").forEach(function (modal) {
      modal.addEventListener("click", function (event) {
        if (event.target === modal) closeModal(modal.id);
      });
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      document.querySelectorAll(".modal.open").forEach(function (modal) {
        closeModal(modal.id);
      });
    });
  }

  function init() {
    bindEvents();
    renderAll();
    loadGoals();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
