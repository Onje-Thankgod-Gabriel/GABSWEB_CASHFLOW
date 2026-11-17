/* Gabsweb CashFlow — shared navigation notification panel */
(function () {
  "use strict";

  var API_URL = "/notifications-api/";
  var state = {
    serverItems: [],
    unread: 0,
    loading: false,
    refreshQueued: false,
    request: null
  };
  var loadedOnce = false;

  function byId(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    var token = meta ? meta.getAttribute("content") : "";
    if (token) return token;
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function request(url, options) {
    var config = options || {};
    var headers = { Accept: "application/json" };
    if (config.method && config.method !== "GET") {
      var token = csrfToken();
      if (token) headers["X-CSRFToken"] = token;
    }
    return fetch(url, {
      method: config.method || "GET",
      credentials: "same-origin",
      headers: headers
    }).then(function (response) {
      return response.text().then(function (text) {
        var payload = null;
        if (text) {
          try {
            payload = JSON.parse(text);
          } catch (error) {
            payload = null;
          }
        }
        if (!response.ok) {
          throw new Error("Notification request failed.");
        }
        return payload;
      });
    });
  }

  function emptyMarkup(title, note) {
    return (
      '<div class="app-notification-empty">' +
      '<span class="app-notification-empty-icon"><i data-lucide="bell" class="w-4 h-4"></i></span>' +
      "<p>" +
      escapeHtml(title) +
      "</p>" +
      "<small>" +
      escapeHtml(note) +
      "</small>" +
      "</div>"
    );
  }

  function dataLayer() {
    return window.GabswebCashFlowData || null;
  }

  function currentMonthKey(data) {
    if (
      data &&
      typeof data.getMonthKey === "function" &&
      typeof data.todayKey === "function"
    ) {
      return data.getMonthKey(data.todayKey());
    }
    return "";
  }

  /* Live, data-backed alerts that always reflect current account state.
     These are not stored messages, so they carry no unread state and do not
     affect the unread badge. They are recomputed on every render. */
  function buildLocalAlerts() {
    var data = dataLayer();
    if (!data) return [];
    var alerts = [];

    try {
      if (typeof data.getOverdueMilestones === "function") {
        var overdue = data.getOverdueMilestones() || [];
        if (overdue.length) {
          var firstMilestone = overdue[0] || {};
          alerts.push({
            id: "local-overdue-milestones",
            local: true,
            icon: "alert-triangle",
            tone: "warning",
            title:
              overdue.length === 1
                ? "1 milestone is overdue"
                : overdue.length + " milestones are overdue",
            message:
              (firstMilestone.title || "Milestone") +
              (firstMilestone.projectName
                ? " · " + firstMilestone.projectName
                : ""),
            destinationUrl: "/planner/",
            actionLabel: "Needs attention"
          });
        }
      }
    } catch (error) {
      /* A partial local store must never break the panel. */
    }

    try {
      if (typeof data.getAllocationSummary === "function") {
        var month = currentMonthKey(data);
        var summary = data.getAllocationSummary(month) || {};
        var allocations = Array.isArray(summary.allocations)
          ? summary.allocations
          : [];
        var overPlan = allocations.filter(function (allocation) {
          return (
            Number(allocation.plannedAmount) > 0 &&
            Number(allocation.actualSpending) >
              Number(allocation.plannedAmount)
          );
        });
        if (overPlan.length) {
          alerts.push({
            id: "local-over-plan-allocations",
            local: true,
            icon: "pie-chart",
            tone: "warning",
            title:
              overPlan.length === 1
                ? "1 allocation is over plan"
                : overPlan.length + " allocations are over plan",
            message:
              (overPlan[0].name || "Allocation") + (month ? " · " + month : ""),
            destinationUrl: "/budgets/",
            actionLabel: "Needs attention"
          });
        }
      }
    } catch (error) {
      /* A partial local store must never break the panel. */
    }

    try {
      if (
        typeof data.getDailyPlanByDate === "function" &&
        typeof data.todayKey === "function"
      ) {
        var plan = data.getDailyPlanByDate(data.todayKey());
        var tasks = plan && Array.isArray(plan.tasks) ? plan.tasks : [];
        var open = tasks.filter(function (task) {
          return !task.completed;
        }).length;
        if (open) {
          alerts.push({
            id: "local-open-tasks",
            local: true,
            icon: "check-square",
            tone: "neutral",
            title:
              open === 1
                ? "1 task is still open today"
                : open + " tasks are still open today",
            message: "Open the daily planner to finish them.",
            destinationUrl: "/planner/",
            actionLabel: "Today"
          });
        }
      }
    } catch (error) {
      /* A partial local store must never break the panel. */
    }

    return alerts;
  }

  function itemMarkup(item) {
    var local = Boolean(item.local);
    var unread = !local && !item.isRead;
    var destination =
      typeof item.destinationUrl === "string" && item.destinationUrl
        ? item.destinationUrl
        : "";
    var marker = local
      ? '<span class="app-notification-item-icon is-' +
        escapeHtml(item.tone || "neutral") +
        '"><i data-lucide="' +
        escapeHtml(item.icon || "bell") +
        '" class="w-4 h-4"></i></span>'
      : '<span class="app-notification-item-dot" aria-hidden="true"></span>';
    var timeLabel = item.createdLabel || item.actionLabel || "";
    return (
      '<button type="button" class="app-notification-item' +
      (unread ? " is-unread" : "") +
      (local ? " is-alert" : "") +
      '" data-notification-id="' +
      escapeHtml(item.id) +
      '"' +
      (local ? ' data-notification-local="true"' : "") +
      (destination
        ? ' data-notification-destination="' + escapeHtml(destination) + '"'
        : "") +
      ' aria-label="' +
      escapeHtml(item.title) +
      '">' +
      marker +
      '<span class="app-notification-item-body">' +
      '<span class="app-notification-item-title">' +
      escapeHtml(item.title) +
      "</span>" +
      '<span class="app-notification-item-message">' +
      escapeHtml(item.message) +
      "</span>" +
      (timeLabel
        ? '<span class="app-notification-item-time">' +
          escapeHtml(timeLabel) +
          "</span>"
        : "") +
      "</span>" +
      "</button>"
    );
  }

  function render() {
    var list = byId("notification-list");
    var badge = byId("notification-badge");
    var markAll = byId("mark-all-notifications");
    if (!list) return;

    var items = buildLocalAlerts().concat(state.serverItems);

    if (!items.length) {
      list.innerHTML = state.loading
        ? emptyMarkup("Loading notifications…", "Checking your CashFlow activity.")
        : emptyMarkup(
            "No new notifications",
            "Notifications from your CashFlow activity will appear here."
          );
    } else {
      list.innerHTML = items.map(itemMarkup).join("");
    }

    if (badge) {
      if (state.unread > 0) {
        badge.textContent = state.unread > 99 ? "99+" : String(state.unread);
        badge.hidden = false;
      } else {
        badge.textContent = "";
        badge.hidden = true;
      }
    }
    if (markAll) markAll.hidden = state.unread <= 0;

    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function load() {
    if (state.loading) {
      /* Coalesce overlapping refreshes so a slow in-flight response can never
         overwrite a newer one or undo an optimistic read action. */
      state.refreshQueued = true;
      return state.request || Promise.resolve();
    }
    state.loading = true;
    if (!loadedOnce) render();
    var pending = request(API_URL)
      .then(function (payload) {
        loadedOnce = true;
        state.serverItems = (payload && payload.notifications) || [];
        state.unread = Number(payload && payload.unreadCount) || 0;
      })
      .catch(function () {
        loadedOnce = true;
        state.serverItems = [];
        state.unread = 0;
      })
      .finally(function () {
        state.loading = false;
        state.request = null;
        render();
        if (state.refreshQueued) {
          state.refreshQueued = false;
          load();
        }
      });
    state.request = pending;
    return pending;
  }

  function markRead(id) {
    var target = null;
    state.serverItems.forEach(function (item) {
      if (String(item.id) === String(id)) target = item;
    });
    if (!target) return Promise.resolve();
    if (target.isRead) return Promise.resolve();
    target.isRead = true;
    state.unread = Math.max(0, state.unread - 1);
    render();
    return request(API_URL + encodeURIComponent(id) + "/read/", {
      method: "POST"
    })
      .then(function () {
        return load();
      })
      .catch(function () {
        return load();
      });
  }

  function markAllRead() {
    if (state.unread <= 0) return;
    state.serverItems.forEach(function (item) {
      item.isRead = true;
    });
    state.unread = 0;
    render();
    return request(API_URL + "read-all/", { method: "POST" })
      .then(function () {
        return load();
      })
      .catch(function () {
        return load();
      });
  }

  function initNotifications() {
    var wrapper = byId("notification-wrapper");
    var button = byId("notification-btn");
    var dropdown = byId("notification-dropdown");
    if (!wrapper || !button || !dropdown) return;
    if (wrapper.getAttribute("data-notifications-ready") === "true") return;
    wrapper.setAttribute("data-notifications-ready", "true");

    var closeTimer = null;

    if (!dropdown.id) dropdown.id = "notification-dropdown";
    if (!button.getAttribute("aria-label")) {
      button.setAttribute("aria-label", "Notifications");
    }
    button.setAttribute("aria-controls", dropdown.id);
    button.setAttribute("aria-haspopup", "true");
    button.setAttribute("aria-expanded", "false");
    dropdown.setAttribute("role", "region");
    dropdown.setAttribute("aria-label", "Notifications");

    function isOpen() {
      return !dropdown.classList.contains("hidden");
    }

    function open() {
      window.clearTimeout(closeTimer);
      dropdown.classList.remove("hidden");
      button.setAttribute("aria-expanded", "true");
      window.requestAnimationFrame(function () {
        dropdown.classList.add("opacity-100", "translate-y-0");
        dropdown.classList.remove("opacity-0", "translate-y-2");
      });
      render();
      load();
    }

    function close(returnFocus) {
      if (!isOpen()) return;
      window.clearTimeout(closeTimer);
      dropdown.classList.remove("opacity-100", "translate-y-0");
      dropdown.classList.add("opacity-0", "translate-y-2");
      button.setAttribute("aria-expanded", "false");
      closeTimer = window.setTimeout(function () {
        if (!dropdown.classList.contains("opacity-100")) {
          dropdown.classList.add("hidden");
        }
      }, 160);
      if (returnFocus) button.focus();
    }

    button.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (isOpen()) {
        close(false);
      } else {
        open();
      }
    });

    document.addEventListener("click", function (event) {
      if (!isOpen()) return;
      if (wrapper.contains(event.target)) return;
      close(false);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !isOpen()) return;
      event.stopPropagation();
      close(true);
    });

    var list = dropdown.querySelector("#notification-list");
    if (list) {
      list.addEventListener("click", function (event) {
        var item = event.target.closest("[data-notification-id]");
        if (!item) return;
        var destination = item.getAttribute("data-notification-destination");
        var isLocal = item.getAttribute("data-notification-local") === "true";
        var marked = isLocal
          ? Promise.resolve()
          : markRead(item.getAttribute("data-notification-id"));
        if (destination) {
          Promise.resolve(marked)
            .catch(function () {
              return null;
            })
            .then(function () {
              window.location.assign(destination);
            });
        }
      });
    }

    var markAll = dropdown.querySelector("#mark-all-notifications");
    if (markAll) {
      markAll.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        markAllRead();
      });
    }

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) load();
    });
    window.addEventListener("gabsweb:notifications-updated", load);

    var data = dataLayer();
    if (data && typeof data.subscribe === "function") {
      data.subscribe(function () {
        render();
      });
    }

    load();
    window.setInterval(function () {
      if (!document.hidden) load();
    }, 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initNotifications);
  } else {
    initNotifications();
  }
})();
