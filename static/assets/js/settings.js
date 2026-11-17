(function () {
  "use strict";

  var THEME_KEY = "savvyfi-theme";
  var VALID_THEMES = ["dark", "light"];
  var SUPPORTED_CURRENCIES = ["NGN"];
  var MAX_IMAGE_DIMENSION = 360;
  var MAX_IMAGE_FILE_SIZE = 8 * 1024 * 1024;
  var profileImageSaved = "";
  var profileImageDraft = "";
  var profileImageFile = null;
  var profileImageRemove = false;
  var profileImageRequestId = 0;
  var profileSaving = false;
  var defaultProfileImage = "/static/assets/images/profiles/gabsweb-default-avatar.png";
  var profileData = null;
  var workProfile = null;
  var editingSupervisorId = "";
  var editingShiftId = "";

  function getCashflowData() {
    return window.GabswebCashFlowData || null;
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function notify(title, message, type) {
    var toast = byId("toast");
    var toastTitle = byId("toast-title");
    var toastMessage = byId("toast-message");
    if (!toast || !toastTitle || !toastMessage) return;
    toastTitle.textContent = title;
    toastMessage.textContent = message;
    toast.className = "toast " + (type || "success");
    toast.classList.add("show");
    window.clearTimeout(notify.timer);
    notify.timer = window.setTimeout(function () {
      toast.classList.remove("show");
    }, 3200);
  }

  function isValidEmail(value) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
  }

  function isValidImageSource(value) {
    return (
      typeof value === "string" &&
      (/^data:image\/(png|jpe?g|webp);base64,/i.test(value) ||
        /^(?:https?:\/\/|\/)[^\s]+$/i.test(value))
    );
  }

  function getInitials(name) {
    var parts = String(name || "")
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2);
    if (!parts.length) return "--";
    return parts
      .map(function (part) {
        return part.charAt(0);
      })
      .join("")
      .toUpperCase();
  }

  function getSavedProfileImage() {
    var image = document.body ? document.body.dataset.userAvatarUrl : "";
    return isValidImageSource(image) ? image : "";
  }

  function formatShift(value) {
    if (value === "morning") return "Morning · 7:00 AM – 1:00 PM";
    if (value === "afternoon") return "Afternoon · 1:00 PM – 7:00 PM";
    if (value === "night") return "Night · 7:00 PM – 7:00 AM";
    return "Not set";
  }

  function renderAvatar(imageId, initialsId, name, image, fallbackImage) {
    var imageElement = byId(imageId);
    var initialsElement = byId(initialsId);
    var hasImage = isValidImageSource(image);
    var imageSource = hasImage ? image : fallbackImage || "";
    if (imageElement) {
      if (imageSource) {
        imageElement.src = imageSource;
        imageElement.hidden = false;
      } else {
        imageElement.removeAttribute("src");
        imageElement.hidden = true;
      }
    }
    if (initialsElement) {
      initialsElement.textContent = getInitials(name);
      initialsElement.hidden = Boolean(imageSource);
    }
  }

  function formatShiftTime(value) {
    if (!value) return "";
    var parts = String(value).split(":");
    var hours = Number(parts[0]);
    var minutes = Number(parts[1] || 0);
    if (Number.isNaN(hours) || Number.isNaN(minutes)) return value;
    var suffix = hours >= 12 ? "PM" : "AM";
    var displayHour = hours % 12 || 12;
    return displayHour + ":" + String(minutes).padStart(2, "0") + " " + suffix;
  }

  function formatPersonalShift(shift) {
    if (!shift) return "Not set";
    return (
      shift.name +
      " · " +
      formatShiftTime(shift.startTime) +
      " – " +
      formatShiftTime(shift.endTime)
    );
  }

  function csrfToken(form) {
    var input = form
      ? form.querySelector('input[name="csrfmiddlewaretoken"]')
      : null;
    if (input && input.value) return input.value;
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  function setManagerError(inputId, message) {
    var input = byId(inputId);
    var error = byId(inputId + "-error");
    if (input) {
      input.classList.toggle("is-invalid", Boolean(message));
      if (message) input.setAttribute("aria-invalid", "true");
      else input.removeAttribute("aria-invalid");
    }
    if (error) error.textContent = message || "";
  }

  function clearManagerErrors(ids) {
    ids.forEach(function (id) {
      setManagerError(id, "");
    });
  }

  function firstError(errors, fields) {
    for (var index = 0; index < fields.length; index += 1) {
      var message = fieldErrorMessage(errors, fields[index]);
      if (message) return message;
    }
    return "";
  }

  function jsonRequest(url, method, body, token) {
    return fetch(url, {
      method: method,
      headers: {
        "X-CSRFToken": token,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json"
      },
      body: body ? JSON.stringify(body) : undefined,
      credentials: "same-origin"
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (payload) {
          return { ok: response.ok, payload: payload };
        });
    });
  }

  function createIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function profileApiUrl() {
    var section = byId("settings-profile");
    return section ? section.dataset.profileUrl || "" : "";
  }

  function clearFieldError(inputId) {
    var input = byId(inputId);
    var field = byId(inputId + "-field");
    var error = byId(inputId + "-error");
    if (field) field.classList.remove("is-invalid");
    if (input) input.removeAttribute("aria-invalid");
    if (error) error.textContent = "";
  }

  function setFieldError(inputId, message) {
    var input = byId(inputId);
    var field = byId(inputId + "-field");
    var error = byId(inputId + "-error");
    if (field) field.classList.add("is-invalid");
    if (input) input.setAttribute("aria-invalid", "true");
    if (error) error.textContent = message;
  }

  function setSelectValue(selectId, value) {
    var select = byId(selectId);
    if (!select) return;
    var target = value || "";
    var exists = Array.prototype.some.call(select.options || [], function (option) {
      return option.value === target;
    });
    if (!exists && target) {
      var option = document.createElement("option");
      option.value = target;
      option.textContent = target + " (saved value)";
      select.appendChild(option);
    }
    select.value = target;
    if (select.selectedIndex === -1) select.selectedIndex = 0;
  }

  function setProfileEditMode(editing) {
    var viewCard = byId("profile-view-card");
    var form = byId("profile-form");
    if (viewCard) viewCard.hidden = editing;
    if (form) form.hidden = !editing;
  }

  function renderWorkVisibility(profile) {
    var approved = Boolean(profile && profile.hasApprovedAccess);
    var isGeneral = Boolean(profile && profile.isGeneral);
    var isPending = Boolean(profile && profile.isPending);
    var approvedSection = byId("work-approved-section");
    var generalSection = byId("work-general-section");
    var pendingSection = byId("work-pending-section");
    if (approvedSection) approvedSection.hidden = !approved;
    if (generalSection) generalSection.hidden = !isGeneral;
    if (pendingSection) pendingSection.hidden = !isPending;
    var badge = byId("work-role-badge");
    if (badge && profile) badge.textContent = profile.statusLabel || "Confirmed";
  }

  function renderSupervisors(supervisors) {
    var list = byId("supervisors-list");
    var empty = byId("supervisors-empty");
    if (!list) return;
    list.textContent = "";
    (supervisors || []).forEach(function (supervisor) {
      var item = document.createElement("li");
      item.className = "gs-manager-item";
      var name = document.createElement("strong");
      name.textContent = supervisor.name;
      var actions = document.createElement("span");
      actions.className = "gs-manager-item-actions";
      var edit = document.createElement("button");
      edit.type = "button";
      edit.className = "gs-icon-btn";
      edit.setAttribute("aria-label", "Edit " + supervisor.name);
      edit.innerHTML = '<i data-lucide="pencil" class="w-4 h-4"></i>';
      edit.addEventListener("click", function () {
        startSupervisorEdit(supervisor);
      });
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "gs-icon-btn gs-icon-btn-danger";
      remove.setAttribute("aria-label", "Delete " + supervisor.name);
      remove.innerHTML = '<i data-lucide="trash-2" class="w-4 h-4"></i>';
      remove.addEventListener("click", function () {
        deleteSupervisor(supervisor);
      });
      actions.appendChild(edit);
      actions.appendChild(remove);
      item.appendChild(name);
      item.appendChild(actions);
      list.appendChild(item);
    });
    if (empty) empty.hidden = Boolean(supervisors && supervisors.length);
    createIcons();
  }

  function renderPersonalShifts(shifts) {
    var list = byId("personal-shifts-list");
    var empty = byId("personal-shifts-empty");
    if (!list) return;
    list.textContent = "";
    (shifts || []).forEach(function (shift) {
      var item = document.createElement("li");
      item.className = "gs-manager-item";
      var copy = document.createElement("span");
      copy.className = "gs-manager-item-copy";
      var name = document.createElement("strong");
      name.textContent = shift.name;
      var detail = document.createElement("small");
      detail.textContent =
        formatShiftTime(shift.startTime) +
        " – " +
        formatShiftTime(shift.endTime) +
        (shift.isActive ? "" : " · Inactive");
      copy.appendChild(name);
      copy.appendChild(detail);
      var actions = document.createElement("span");
      actions.className = "gs-manager-item-actions";
      var edit = document.createElement("button");
      edit.type = "button";
      edit.className = "gs-icon-btn";
      edit.setAttribute("aria-label", "Edit " + shift.name);
      edit.innerHTML = '<i data-lucide="pencil" class="w-4 h-4"></i>';
      edit.addEventListener("click", function () {
        startShiftEdit(shift);
      });
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "gs-icon-btn gs-icon-btn-danger";
      remove.setAttribute("aria-label", "Delete " + shift.name);
      remove.innerHTML = '<i data-lucide="trash-2" class="w-4 h-4"></i>';
      remove.addEventListener("click", function () {
        deleteShift(shift);
      });
      actions.appendChild(edit);
      actions.appendChild(remove);
      item.appendChild(copy);
      item.appendChild(actions);
      list.appendChild(item);
    });
    if (empty) empty.hidden = Boolean(shifts && shifts.length);
    createIcons();
  }

  function updateWorkSummary() {
    var profile = workProfile || {};
    var detailTeam = byId("profile-detail-team");
    var detailSupervisor = byId("profile-detail-supervisor");
    var detailShift = byId("profile-detail-shift");
    var supervisors = profile.supervisors || [];
    var shifts = profile.shifts || [];
    var activeShift =
      shifts.filter(function (shift) {
        return shift.isActive;
      })[0] || shifts[0];
    if (detailTeam) detailTeam.textContent = profile.workTeam || "Not set";
    if (detailSupervisor) {
      detailSupervisor.textContent = supervisors.length
        ? supervisors
            .map(function (supervisor) {
              return supervisor.name;
            })
            .join(", ")
        : "Not set";
    }
    if (detailShift) {
      detailShift.textContent = activeShift
        ? formatPersonalShift(activeShift)
        : profile.joiningArrangement || "Not set";
    }
    var summary = byId("work-joining-summary");
    if (summary) {
      summary.textContent =
        [profile.joiningArrangement, profile.joiningDate]
          .filter(Boolean)
          .join(" · ") || "Not set";
    }
    var joiningBadge = byId("work-joining-badge");
    if (joiningBadge) {
      joiningBadge.textContent = profile.joiningDate ? "Saved" : "Optional";
    }
    var shiftCopy = byId("work-shift-copy");
    var shiftLabel = byId("work-shift-label");
    var shiftGrid = byId("work-shift-grid");
    if (shiftCopy) {
      shiftCopy.textContent = profile.worksShifts
        ? "Morning → Afternoon → Night → repeat."
        : "Shift work is currently turned off for your profile.";
    }
    if (shiftLabel) {
      shiftLabel.textContent = profile.workplaceLabel || "GCTN/Apex Pay";
    }
    if (shiftGrid) shiftGrid.hidden = !profile.worksShifts;
    renderProfileSummary({
      name: profileData ? profileData.full_name : "",
      email: profileData ? profileData.email : ""
    });
  }

  function applyWorkProfile(profile) {
    workProfile = profile || null;
    var body = document.body;
    if (body && profile) {
      body.dataset.userAccountStatus = profile.accountStatus || "";
      body.dataset.userWorkplaceCompany = profile.workplaceCompany || "";
      body.dataset.userWorkplaceLabel = profile.workplaceLabel || "";
      body.dataset.userHasWorkplaceAccess = profile.hasApprovedAccess
        ? "true"
        : "false";
      body.dataset.userWorksShifts = profile.worksShifts ? "true" : "false";
    }
    renderWorkVisibility(profile);
    var teamField = byId("work-team");
    var dateField = byId("work-joining-date");
    var arrangementField = byId("work-joining-arrangement");
    var worksShiftsField = byId("work-works-shifts");
    if (teamField) teamField.value = (profile && profile.workTeam) || "";
    if (dateField) dateField.value = (profile && profile.joiningDate) || "";
    if (arrangementField) {
      arrangementField.value = (profile && profile.joiningArrangement) || "";
    }
    if (worksShiftsField) {
      worksShiftsField.checked = Boolean(profile && profile.worksShifts);
    }
    renderSupervisors(profile ? profile.supervisors : []);
    renderPersonalShifts(profile ? profile.shifts : []);
    updateWorkSummary();
    clearManagerErrors([
      "work-team",
      "work-joining_date",
      "work-joining_arrangement"
    ]);
  }

  function loadWorkProfile() {
    var form = byId("work-preferences-form");
    var url = form ? form.dataset.url : "";
    if (!url) return Promise.resolve(false);
    return fetch(url, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin"
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          return { ok: response.ok, payload: payload };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.payload || !result.payload.ok) {
          throw new Error("Workplace request failed.");
        }
        applyWorkProfile(result.payload.workProfile);
        return true;
      })
      .catch(function () {
        notify(
          "Workplace Information Not Loaded",
          "We couldn't load workplace details for this account. Personal profile information is still available.",
          "error"
        );
        return false;
      });
  }

  function saveWorkPreferences(event) {
    if (event) event.preventDefault();
    var form = byId("work-preferences-form");
    var url = form ? form.dataset.url : "";
    if (!form || !url) return;
    var teamField = byId("work-team");
    var dateField = byId("work-joining-date");
    var arrangementField = byId("work-joining-arrangement");
    var worksShiftsField = byId("work-works-shifts");
    clearManagerErrors([
      "work-team",
      "work-joining_date",
      "work-joining_arrangement"
    ]);
    var button = byId("save-work-preferences-btn");
    if (button) button.disabled = true;
    var payload = {
      work_team: teamField ? teamField.value.trim() : "",
      joining_date: dateField ? dateField.value : "",
      joining_arrangement: arrangementField
        ? arrangementField.value.trim()
        : "",
      works_shifts: worksShiftsField && worksShiftsField.checked ? "on" : ""
    };
    jsonRequest(url, "POST", payload, csrfToken(form))
      .then(function (result) {
        if (!result.ok || !result.payload || !result.payload.ok) {
          var errors = result.payload ? result.payload.errors : null;
          setManagerError("work-team", fieldErrorMessage(errors, "work_team"));
          setManagerError(
            "work-joining_date",
            fieldErrorMessage(errors, "joining_date")
          );
          setManagerError(
            "work-joining_arrangement",
            fieldErrorMessage(errors, "joining_arrangement")
          );
          notify(
            "Workplace Details Not Saved",
            firstError(errors, [
              "joining_date",
              "work_team",
              "joining_arrangement",
              "__all__"
            ]) || "Your workplace details could not be saved.",
            "error"
          );
          return;
        }
        applyWorkProfile(result.payload.workProfile);
        notify(
          "Workplace Details Updated",
          "Your workplace details have been saved.",
          "success"
        );
      })
      .catch(function () {
        notify(
          "Workplace Details Not Saved",
          "Your workplace details could not be saved. Check your connection and try again.",
          "error"
        );
      })
      .finally(function () {
        if (button) button.disabled = false;
      });
  }

  function resetSupervisorForm() {
    editingSupervisorId = "";
    var idField = byId("supervisor-id");
    var nameField = byId("supervisor-name");
    var button = byId("save-supervisor-btn");
    var cancel = byId("cancel-supervisor-btn");
    if (idField) idField.value = "";
    if (nameField) nameField.value = "";
    if (button) {
      button.innerHTML =
        '<i data-lucide="plus" class="w-4 h-4"></i> Add supervisor';
    }
    if (cancel) cancel.hidden = true;
    setManagerError("supervisor-name", "");
    createIcons();
  }

  function startSupervisorEdit(supervisor) {
    editingSupervisorId = supervisor.id;
    var idField = byId("supervisor-id");
    var nameField = byId("supervisor-name");
    var button = byId("save-supervisor-btn");
    var cancel = byId("cancel-supervisor-btn");
    if (idField) idField.value = supervisor.id;
    if (nameField) {
      nameField.value = supervisor.name;
      nameField.focus();
    }
    if (button) {
      button.innerHTML =
        '<i data-lucide="check" class="w-4 h-4"></i> Save supervisor';
    }
    if (cancel) cancel.hidden = false;
    createIcons();
  }

  function saveSupervisor(event) {
    if (event) event.preventDefault();
    var form = byId("supervisor-form");
    var url = form ? form.dataset.url : "";
    var nameField = byId("supervisor-name");
    if (!form || !url || !nameField) return;
    var name = nameField.value.trim();
    clearManagerErrors(["supervisor-name"]);
    if (!name) {
      setManagerError("supervisor-name", "Enter a supervisor name.");
      notify("Supervisor Not Saved", "Enter a supervisor name.", "error");
      return;
    }
    var target = editingSupervisorId
      ? url + encodeURIComponent(editingSupervisorId) + "/"
      : url;
    var button = byId("save-supervisor-btn");
    if (button) button.disabled = true;
    jsonRequest(target, "POST", { name: name }, csrfToken(form))
      .then(function (result) {
        if (!result.ok || !result.payload || !result.payload.ok) {
          var errors = result.payload ? result.payload.errors : null;
          var message =
            fieldErrorMessage(errors, "name") ||
            firstError(errors, ["__all__"]) ||
            "The supervisor could not be saved.";
          setManagerError("supervisor-name", message);
          notify("Supervisor Not Saved", message, "error");
          return;
        }
        resetSupervisorForm();
        loadWorkProfile();
        notify(
          "Supervisor Saved",
          "Your supervisor list has been updated.",
          "success"
        );
      })
      .catch(function () {
        notify(
          "Supervisor Not Saved",
          "The supervisor could not be saved. Check your connection and try again.",
          "error"
        );
      })
      .finally(function () {
        if (button) button.disabled = false;
      });
  }

  function deleteSupervisor(supervisor) {
    var form = byId("supervisor-form");
    var url = form ? form.dataset.url : "";
    if (!url) return;
    jsonRequest(
      url + encodeURIComponent(supervisor.id) + "/",
      "DELETE",
      null,
      csrfToken(form)
    )
      .then(function (result) {
        if (!result.ok || !result.payload || !result.payload.ok) {
          notify(
            "Supervisor Not Deleted",
            "The supervisor could not be removed.",
            "error"
          );
          return;
        }
        if (editingSupervisorId === supervisor.id) resetSupervisorForm();
        loadWorkProfile();
        notify(
          "Supervisor Removed",
          "The supervisor has been removed.",
          "success"
        );
      })
      .catch(function () {
        notify(
          "Supervisor Not Deleted",
          "The supervisor could not be removed. Check your connection and try again.",
          "error"
        );
      });
  }

  function resetShiftForm() {
    editingShiftId = "";
    var idField = byId("personal-shift-id");
    var nameField = byId("personal-shift-name");
    var startField = byId("personal-shift-start");
    var endField = byId("personal-shift-end");
    var activeField = byId("personal-shift-active");
    var button = byId("save-personal-shift-btn");
    var cancel = byId("cancel-personal-shift-btn");
    if (idField) idField.value = "";
    if (nameField) nameField.value = "";
    if (startField) startField.value = "";
    if (endField) endField.value = "";
    if (activeField) activeField.checked = true;
    if (button) {
      button.innerHTML = '<i data-lucide="plus" class="w-4 h-4"></i> Add shift';
    }
    if (cancel) cancel.hidden = true;
    clearManagerErrors([
      "personal-shift-name",
      "personal-shift-start_time",
      "personal-shift-end_time"
    ]);
    createIcons();
  }

  function startShiftEdit(shift) {
    editingShiftId = shift.id;
    var idField = byId("personal-shift-id");
    var nameField = byId("personal-shift-name");
    var startField = byId("personal-shift-start");
    var endField = byId("personal-shift-end");
    var activeField = byId("personal-shift-active");
    var button = byId("save-personal-shift-btn");
    var cancel = byId("cancel-personal-shift-btn");
    if (idField) idField.value = shift.id;
    if (nameField) {
      nameField.value = shift.name;
      nameField.focus();
    }
    if (startField) startField.value = shift.startTime || "";
    if (endField) endField.value = shift.endTime || "";
    if (activeField) activeField.checked = Boolean(shift.isActive);
    if (button) {
      button.innerHTML =
        '<i data-lucide="check" class="w-4 h-4"></i> Save shift';
    }
    if (cancel) cancel.hidden = false;
    createIcons();
  }

  function savePersonalShift(event) {
    if (event) event.preventDefault();
    var form = byId("personal-shift-form");
    var url = form ? form.dataset.url : "";
    var nameField = byId("personal-shift-name");
    var startField = byId("personal-shift-start");
    var endField = byId("personal-shift-end");
    var activeField = byId("personal-shift-active");
    if (!form || !url || !nameField) return;
    var name = nameField.value.trim();
    clearManagerErrors([
      "personal-shift-name",
      "personal-shift-start_time",
      "personal-shift-end_time"
    ]);
    if (!name) {
      setManagerError("personal-shift-name", "Enter a shift name.");
      notify("Shift Not Saved", "Enter a shift name.", "error");
      return;
    }
    if (!startField || !startField.value || !endField || !endField.value) {
      setManagerError(
        !startField || !startField.value
          ? "personal-shift-start_time"
          : "personal-shift-end_time",
        "Choose both a start and end time."
      );
      notify("Shift Not Saved", "Choose both a start and end time.", "error");
      return;
    }
    var target = editingShiftId
      ? url + encodeURIComponent(editingShiftId) + "/"
      : url;
    var button = byId("save-personal-shift-btn");
    if (button) button.disabled = true;
    jsonRequest(
      target,
      "POST",
      {
        name: name,
        start_time: startField.value,
        end_time: endField.value,
        is_active: activeField && activeField.checked ? "on" : ""
      },
      csrfToken(form)
    )
      .then(function (result) {
        if (!result.ok || !result.payload || !result.payload.ok) {
          var errors = result.payload ? result.payload.errors : null;
          setManagerError(
            "personal-shift-name",
            fieldErrorMessage(errors, "name")
          );
          setManagerError(
            "personal-shift-start_time",
            fieldErrorMessage(errors, "start_time")
          );
          setManagerError(
            "personal-shift-end_time",
            fieldErrorMessage(errors, "end_time")
          );
          notify(
            "Shift Not Saved",
            firstError(errors, [
              "name",
              "start_time",
              "end_time",
              "__all__"
            ]) || "The shift could not be saved.",
            "error"
          );
          return;
        }
        resetShiftForm();
        loadWorkProfile();
        notify(
          "Shift Saved",
          "Your personal shifts have been updated.",
          "success"
        );
      })
      .catch(function () {
        notify(
          "Shift Not Saved",
          "The shift could not be saved. Check your connection and try again.",
          "error"
        );
      })
      .finally(function () {
        if (button) button.disabled = false;
      });
  }

  function deleteShift(shift) {
    var form = byId("personal-shift-form");
    var url = form ? form.dataset.url : "";
    if (!url) return;
    jsonRequest(
      url + encodeURIComponent(shift.id) + "/",
      "DELETE",
      null,
      csrfToken(form)
    )
      .then(function (result) {
        if (!result.ok || !result.payload || !result.payload.ok) {
          notify(
            "Shift Not Deleted",
            "The shift could not be removed.",
            "error"
          );
          return;
        }
        if (editingShiftId === shift.id) resetShiftForm();
        loadWorkProfile();
        notify(
          "Shift Removed",
          "The personal shift has been removed.",
          "success"
        );
      })
      .catch(function () {
        notify(
          "Shift Not Deleted",
          "The shift could not be removed. Check your connection and try again.",
          "error"
        );
      });
  }

  function renderProfileSummary(profile) {
    var name = profile && profile.name ? profile.name : "";
    var email = profile && profile.email ? profile.email : "";
    var team =
      workProfile && workProfile.workTeam
        ? workProfile.workTeam
        : profile && profile.workTeam
          ? profile.workTeam
          : "";
    var supervisor =
      workProfile && workProfile.supervisors && workProfile.supervisors.length
        ? workProfile.supervisors
            .map(function (item) {
              return item.name;
            })
            .join(", ")
        : profile && profile.supervisor
          ? profile.supervisor
          : "";
    var shift =
      workProfile && workProfile.shifts && workProfile.shifts.length
        ? formatPersonalShift(
            workProfile.shifts.filter(function (item) {
              return item.isActive;
            })[0] || workProfile.shifts[0]
          )
        : profile && profile.shiftPreferences
          ? profile.shiftPreferences.preferredShift
          : "";
    var role = document.body
      ? String(document.body.dataset.userProfessionalRole || "").trim()
      : "";
    var displayName = byId("profile-display-name");
    var displayEmail = byId("profile-display-email");
    var detailName = byId("profile-detail-name");
    var detailEmail = byId("profile-detail-email");
    var detailRole = byId("profile-detail-role");
    var workRoleTitle = byId("work-role-title");
    var detailTeam = byId("profile-detail-team");
    var detailSupervisor = byId("profile-detail-supervisor");
    var detailShift = byId("profile-detail-shift");
    var summaryName = byId("profile-summary-name");
    var summaryEmail = byId("profile-summary-email");
    var summaryInitials = byId("profile-summary-initials");

    if (displayName) displayName.textContent = name || "Not set";
    if (displayEmail) displayEmail.textContent = email || "No email saved";
    if (detailName) detailName.textContent = name || "Not set";
    if (detailEmail) detailEmail.textContent = email || "Not set";
    if (detailRole) detailRole.textContent = role || "Not set";
    if (workRoleTitle) workRoleTitle.textContent = role || "Not set";
    if (detailTeam) detailTeam.textContent = team || "Not set";
    if (detailSupervisor) detailSupervisor.textContent = supervisor || "Not set";
    if (detailShift) {
      detailShift.textContent =
        shift === undefined || shift === null || shift === ""
          ? "Not set"
          : typeof shift === "string" && formatShift(shift) !== "Not set"
            ? formatShift(shift)
            : shift;
    }
    if (summaryName) summaryName.textContent = name || "Not set";
    if (summaryEmail) summaryEmail.textContent = email || "Not set";
    if (summaryInitials) summaryInitials.textContent = getInitials(name);

    renderAvatar(
      "profile-avatar-image",
      "profile-avatar-initials",
      name,
      profileImageSaved,
      defaultProfileImage
    );
    renderAvatar(
      "profile-image-preview-img",
      "profile-image-preview-initials",
      name,
      profileImageDraft,
      defaultProfileImage
    );
  }

  function applyProfilePayload(payload) {
    profileData = payload || null;
    if (!payload) return;
    applyServerIdentity(payload);
    var nameField = byId("profile-name");
    var emailField = byId("profile-email");
    var roleField = byId("profile-role");
    var imageInput = byId("profile-image-input");
    if (nameField) nameField.value = payload.full_name || "";
    if (emailField) emailField.value = payload.email || "";
    if (roleField) roleField.value = payload.professional_role || "";
    if (imageInput) imageInput.value = "";
    profileImageSaved = isValidImageSource(payload.profile_image_url)
      ? payload.profile_image_url
      : "";
    profileImageDraft = profileImageSaved;
    profileImageFile = null;
    profileImageRemove = false;
    clearFieldError("profile-name");
    clearFieldError("profile-email");
    clearFieldError("profile-role");
    clearFieldError("profile-image");
    renderProfileSummary({
      name: payload.full_name || "",
      email: payload.email || ""
    });
  }

  function loadProfileFields() {
    var url = profileApiUrl();
    if (!url) {
      notify(
        "Profile Not Loaded",
        "This page is missing its profile endpoint. Refresh and try again.",
        "error"
      );
      return Promise.resolve(false);
    }
    cancelPendingProfileImage();
    return fetch(url, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin"
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          return { ok: response.ok, payload: payload };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.payload || !result.payload.ok) {
          throw new Error("Profile request failed.");
        }
        applyProfilePayload(result.payload);
        return true;
      })
      .catch(function () {
        profileData = null;
        profileImageSaved = "";
        profileImageDraft = "";
        var nameField = byId("profile-name");
        var emailField = byId("profile-email");
        var roleField = byId("profile-role");
        if (nameField) nameField.value = "";
        if (emailField) emailField.value = "";
        if (roleField) roleField.value = "";
        renderProfileSummary({ name: "", email: "" });
        notify(
          "Profile Not Loaded",
          "We couldn't load the profile for the signed-in account. Check your connection and try again.",
          "error"
        );
        return false;
      });
  }

  function openProfileEdit() {
    if (!profileData) {
      notify(
        "Profile Not Ready",
        "The signed-in profile has not loaded yet. Try again in a moment.",
        "error"
      );
      return;
    }
    applyProfilePayload(profileData);
    setProfileEditMode(true);
    var nameField = byId("profile-name");
    if (nameField) nameField.focus();
  }

  function setProfileSaving(saving) {
    profileSaving = Boolean(saving);
    var saveButton = byId("save-profile-btn");
    var saveLabel = byId("save-profile-label");
    if (saveButton) {
      saveButton.disabled = profileSaving;
      if (profileSaving) {
        saveButton.setAttribute("aria-busy", "true");
      } else {
        saveButton.removeAttribute("aria-busy");
      }
    }
    if (saveLabel) {
      saveLabel.textContent = profileSaving ? "Saving Changes..." : "Save changes";
    }
  }

  function cancelPendingProfileImage() {
    profileImageRequestId += 1;
    if (!profileSaving) {
      var saveButton = byId("save-profile-btn");
      if (saveButton) saveButton.disabled = false;
    }
  }

  function cancelProfileEdit() {
    if (profileSaving) return;
    cancelPendingProfileImage();
    profileImageDraft = profileImageSaved;
    profileImageFile = null;
    profileImageRemove = false;
    clearFieldError("profile-image");
    var imageInput = byId("profile-image-input");
    if (imageInput) imageInput.value = "";
    setProfileEditMode(false);
    loadProfileFields();
  }

  function processProfileImage(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onerror = function () {
        reject(new Error("The selected image could not be read."));
      };
      reader.onload = function (event) {
        var image = new Image();
        image.onerror = function () {
          reject(new Error("The selected image could not be loaded."));
        };
        image.onload = function () {
          var scale = Math.min(
            1,
            MAX_IMAGE_DIMENSION / Math.max(image.width, image.height)
          );
          var width = Math.max(1, Math.round(image.width * scale));
          var height = Math.max(1, Math.round(image.height * scale));
          var canvas = document.createElement("canvas");
          canvas.width = width;
          canvas.height = height;
          var context = canvas.getContext("2d");
          if (!context) {
            reject(new Error("The selected image could not be prepared."));
            return;
          }
          context.drawImage(image, 0, 0, width, height);
          try {
            resolve(
              canvas.toDataURL(
                file.type === "image/png" ? "image/png" : "image/jpeg",
                0.84
              )
            );
          } catch (error) {
            reject(new Error("The selected image could not be prepared."));
          }
        };
        image.src = event.target.result;
      };
      reader.readAsDataURL(file);
    });
  }

  function handleProfileImageChange(event) {
    var input = event.target;
    var file = input.files && input.files[0];
    if (!file) return;
    if (!/^image\/(png|jpeg|webp)$/.test(file.type)) {
      input.value = "";
      notify("Image Not Added", "Choose a JPG, PNG or WebP image.", "error");
      return;
    }
    if (file.size > MAX_IMAGE_FILE_SIZE) {
      input.value = "";
      notify("Image Too Large", "Choose an image smaller than 8 MB.", "error");
      return;
    }

    profileImageFile = file;
    profileImageRemove = false;
    clearFieldError("profile-image");

    var requestId = profileImageRequestId + 1;
    profileImageRequestId = requestId;
    processProfileImage(file)
      .then(function (dataUrl) {
        if (requestId !== profileImageRequestId) return;
        profileImageDraft = dataUrl;
        var nameField = byId("profile-name");
        renderAvatar(
          "profile-image-preview-img",
          "profile-image-preview-initials",
          nameField ? nameField.value : "",
          profileImageDraft
        );
      })
      .catch(function (error) {
        if (requestId !== profileImageRequestId) return;
        profileImageFile = null;
        input.value = "";
        notify(
          "Image Not Added",
          error && error.message ? error.message : "The selected image could not be used.",
          "error"
        );
      });
  }

  function removeProfileImage() {
    cancelPendingProfileImage();
    var imageInput = byId("profile-image-input");
    var nameField = byId("profile-name");
    profileImageDraft = "";
    profileImageFile = null;
    profileImageRemove = true;
    clearFieldError("profile-image");
    if (imageInput) imageInput.value = "";
    renderAvatar(
      "profile-image-preview-img",
      "profile-image-preview-initials",
      nameField ? nameField.value : "",
      "",
      defaultProfileImage
    );
  }

  function applyServerIdentity(payload) {
    if (!payload) return;
    var body = document.body;
    if (payload.full_name) body.dataset.userFullName = payload.full_name;
    if (payload.first_name) body.dataset.userFirstName = payload.first_name;
    if (payload.two_part_name) body.dataset.userTwoPartName = payload.two_part_name;
    if (payload.email) body.dataset.userEmail = payload.email;
    if (Object.prototype.hasOwnProperty.call(payload, "professional_role")) {
      body.dataset.userProfessionalRole = payload.professional_role || "";
      var workRoleTitle = byId("work-role-title");
      if (workRoleTitle) {
        workRoleTitle.textContent = payload.professional_role || "Not set";
      }
    }
    if (Object.prototype.hasOwnProperty.call(payload, "profile_image_url")) {
      body.dataset.userAvatarUrl = payload.profile_image_url || "";
    }

    var sidebarName = byId("sidebar-user-name");
    if (sidebarName && payload.two_part_name) {
      sidebarName.textContent = payload.two_part_name;
    }
    var welcome = byId("header-welcome");
    if (welcome && payload.first_name) {
      welcome.textContent = "Welcome back, " + payload.first_name + "!";
    }
  }

  function fieldErrorMessage(errors, field) {
    if (!errors || !errors[field] || !errors[field].length) return "";
    return errors[field][0].message || "";
  }

  function saveProfile(event) {
    if (event) event.preventDefault();
    if (profileSaving) return;
    var form = byId("profile-form");
    var nameField = byId("profile-name");
    var emailField = byId("profile-email");
    var roleField = byId("profile-role");
    var name = nameField ? nameField.value.trim() : "";
    var email = emailField ? emailField.value.trim() : "";
    var role = roleField ? roleField.value.trim() : "";
    clearFieldError("profile-name");
    clearFieldError("profile-email");
    clearFieldError("profile-role");
    clearFieldError("profile-image");

    if (!name) {
      setFieldError("profile-name", "Enter your full name.");
      if (nameField) nameField.focus();
      notify("Validation Error", "Full name is required.", "error");
      return;
    }

    if (!email) {
      setFieldError("profile-email", "Enter your email address.");
      if (emailField) emailField.focus();
      notify("Validation Error", "Email address is required.", "error");
      return;
    }

    if (!isValidEmail(email)) {
      setFieldError("profile-email", "Enter a valid email address.");
      if (emailField) emailField.focus();
      notify("Validation Error", "Enter a valid email address.", "error");
      return;
    }

    if (role.length > 80) {
      setFieldError("profile-role", "Professional role must be 80 characters or fewer.");
      if (roleField) roleField.focus();
      notify("Validation Error", "Professional role must be 80 characters or fewer.", "error");
      return;
    }

    var updateUrl = form ? form.dataset.updateUrl : "";
    var tokenInput = form
      ? form.querySelector('input[name="csrfmiddlewaretoken"]')
      : null;
    var token = tokenInput ? tokenInput.value : "";
    if (!updateUrl || !token) {
      notify(
        "Profile Not Saved",
        "We couldn't save your profile right now. Refresh the page and try again.",
        "error"
      );
      return;
    }

    setProfileSaving(true);

    var requestBody = new FormData();
    requestBody.append("name", name);
    requestBody.append("email", email);
    requestBody.append("professional_role", role);
    if (profileImageRemove) {
      requestBody.append("remove_profile_image", "1");
    } else if (profileImageFile) {
      requestBody.append("profile_image", profileImageFile);
    }

    fetch(updateUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": token,
        "X-Requested-With": "XMLHttpRequest"
      },
      body: requestBody
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          return { ok: response.ok, payload: payload };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.payload || !result.payload.ok) {
          var errors = result.payload ? result.payload.errors : null;
          var nameError = fieldErrorMessage(errors, "name");
          var emailError = fieldErrorMessage(errors, "email");
          var roleError = fieldErrorMessage(errors, "professional_role");
          var imageError = fieldErrorMessage(errors, "profile_image");
          if (roleError) {
            setFieldError("profile-role", roleError);
          }
          if (imageError) {
            setFieldError("profile-image", imageError);
          }
          if (nameError) {
            setFieldError("profile-name", nameError);
            if (nameField) nameField.focus();
          }
          if (emailError) {
            setFieldError("profile-email", emailError);
            if (!nameError && emailField) emailField.focus();
          }
          notify(
            "Profile Not Saved",
            imageError || emailError || nameError || roleError || "Your profile could not be saved.",
            "error"
          );
          return;
        }

        applyProfilePayload(result.payload);
        window.dispatchEvent(new CustomEvent("gabsweb:profile-updated"));
        window.dispatchEvent(new CustomEvent("gabsweb:profile-image-updated"));
        setProfileEditMode(false);
        renderSidebarBalance();
        notify(
          "Profile Updated",
          "Your profile information has been saved.",
          "success"
        );
      })
      .catch(function () {
        notify(
          "Profile Not Saved",
          "Your profile could not be saved. Check your connection and try again.",
          "error"
        );
      })
      .finally(function () {
        setProfileSaving(false);
      });
  }

  function loadFinancialFields() {
    var data = getCashflowData();
    if (!data) return;
    try {
      var settings = data.getSettings();
      setSelectValue("settings-currency", settings.currency || "NGN");
      var monthField = byId("settings-financial-month");
      if (monthField) monthField.value = "calendar";
    } catch (error) {
      notify("Preferences Unavailable", "Saved financial preferences could not be loaded.", "error");
    }
  }

  function saveFinancialPreferences(event) {
    if (event) event.preventDefault();
    var data = getCashflowData();
    if (!data) {
      notify("Settings Unavailable", "Your settings are temporarily unavailable. Refresh the page and try again.", "error");
      return;
    }

    var currencyField = byId("settings-currency");
    var currency = currencyField ? currencyField.value : "";
    if (SUPPORTED_CURRENCIES.indexOf(currency) === -1) {
      notify(
        "Currency Not Supported",
        "Only Nigerian Naira (NGN) is currently supported.",
        "error"
      );
      return;
    }

    try {
      data.updateSettings({
        currency: currency,
        financialMonthPreference: "calendar"
      });
      loadFinancialFields();
      notify("Preferences Saved", "Your financial preferences have been saved.", "success");
    } catch (error) {
      notify(
        "Preferences Not Saved",
        error && error.message ? error.message : "Your financial preferences could not be saved.",
        "error"
      );
    }
  }

  function getStoredTheme() {
    try {
      var stored = localStorage.getItem(THEME_KEY);
      if (VALID_THEMES.indexOf(stored) !== -1) return stored;
    } catch (error) {
      return "dark";
    }
    return "dark";
  }

  function updateThemeSelection(theme) {
    document.querySelectorAll("[data-theme-option]").forEach(function (option) {
      var isSelected = option.getAttribute("data-theme-option") === theme;
      option.classList.toggle("is-selected", isSelected);
      option.setAttribute("aria-pressed", isSelected ? "true" : "false");
    });
    var label = byId("current-theme-label");
    if (label) label.textContent = theme === "light" ? "Light mode" : "Dark mode";
  }

  function syncThemeSelection() {
    var theme = getStoredTheme();
    var isDark = document.documentElement.classList.contains("dark");
    if (isDark) theme = "dark";
    updateThemeSelection(theme);
  }

  function applyTheme(theme, announce) {
    var normalized = VALID_THEMES.indexOf(theme) === -1 ? "dark" : theme;
    document.documentElement.classList.remove("light", "dark");
    document.documentElement.classList.add(normalized);
    document.documentElement.style.colorScheme = normalized;
    try {
      localStorage.setItem(THEME_KEY, normalized);
    } catch (error) {
      notify("Theme Not Saved", "Your theme was applied, but we couldn't save the preference. It may reset when you reopen the app.", "error");
      updateThemeSelection(normalized);
      return;
    }
    updateThemeSelection(normalized);
    if (announce) {
      notify(
        "Theme Applied",
        normalized === "light" ? "Light mode is now active." : "Dark mode is now active.",
        "success"
      );
    }
  }

  function initThemePreferences() {
    updateThemeSelection(getStoredTheme());
    document.querySelectorAll("[data-theme-option]").forEach(function (option) {
      option.addEventListener("click", function () {
        applyTheme(option.getAttribute("data-theme-option"), true);
      });
    });

    var headerToggle = byId("theme-toggle");
    if (headerToggle) {
      headerToggle.addEventListener("click", function () {
        window.setTimeout(syncThemeSelection, 0);
      });
    }

    if (window.MutationObserver) {
      var observer = new MutationObserver(syncThemeSelection);
      observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["class"]
      });
    }
  }

  function activatePanel(name) {
    var buttons = Array.prototype.slice.call(
      document.querySelectorAll("[data-settings-target]")
    );
    var panels = Array.prototype.slice.call(
      document.querySelectorAll("[data-settings-panel]")
    );
    buttons.forEach(function (button) {
      var isActive = button.getAttribute("data-settings-target") === name;
      button.classList.toggle("active", isActive);
      button.setAttribute("aria-selected", isActive ? "true" : "false");
      button.tabIndex = isActive ? 0 : -1;
    });
    panels.forEach(function (panel) {
      var isActive = panel.getAttribute("data-settings-panel") === name;
      panel.classList.toggle("active", isActive);
      panel.hidden = !isActive;
    });
  }

  function initSettingsNav() {
    var buttons = Array.prototype.slice.call(
      document.querySelectorAll("[data-settings-target]")
    );
    buttons.forEach(function (button, index) {
      button.tabIndex = button.classList.contains("active") ? 0 : -1;
      button.addEventListener("click", function () {
        activatePanel(button.getAttribute("data-settings-target"));
      });
      button.addEventListener("keydown", function (event) {
        var nextIndex = index;
        if (event.key === "ArrowDown" || event.key === "ArrowRight") {
          nextIndex = (index + 1) % buttons.length;
        } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
          nextIndex = (index - 1 + buttons.length) % buttons.length;
        } else if (event.key === "Home") {
          nextIndex = 0;
        } else if (event.key === "End") {
          nextIndex = buttons.length - 1;
        } else {
          return;
        }
        event.preventDefault();
        buttons[nextIndex].focus();
        buttons[nextIndex].click();
      });
    });
  }

  function updateStorageStatus() {
    var data = getCashflowData();
    var chip = byId("settings-storage-chip");
    var title = byId("settings-storage-title");
    var copy = byId("settings-storage-copy");
    var available = true;
    if (data && typeof data.isStorageAvailable === "function") {
      try {
        available = data.isStorageAvailable();
      } catch (error) {
        available = false;
      }
    }
    if (chip) chip.classList.toggle("is-warning", !available);
    if (title) title.textContent = available ? "Settings saved" : "Changes may not be saved";
    if (copy) copy.textContent = available ? "Your preferences are stored in your account" : "Changes may not be kept";
  }

  function bindForms() {
    var profileForm = byId("profile-form");
    if (profileForm) profileForm.addEventListener("submit", saveProfile);
    var editButton = byId("edit-profile-btn");
    if (editButton) editButton.addEventListener("click", openProfileEdit);
    var resetButton = byId("reset-profile-btn");
    if (resetButton) resetButton.addEventListener("click", cancelProfileEdit);
    var imageInput = byId("profile-image-input");
    if (imageInput) imageInput.addEventListener("change", handleProfileImageChange);
    var removeImageButton = byId("remove-profile-image-btn");
    if (removeImageButton) removeImageButton.addEventListener("click", removeProfileImage);

    var nameField = byId("profile-name");
    if (nameField) {
      nameField.addEventListener("input", function () {
        clearFieldError("profile-name");
        if (!profileImageDraft) {
          renderAvatar(
            "profile-image-preview-img",
            "profile-image-preview-initials",
            nameField.value,
            "",
            defaultProfileImage
          );
        }
      });
    }
    var emailField = byId("profile-email");
    if (emailField) {
      emailField.addEventListener("input", function () {
        clearFieldError("profile-email");
      });
    }
    var roleField = byId("profile-role");
    if (roleField) {
      roleField.addEventListener("input", function () {
        clearFieldError("profile-role");
      });
    }

    var financialForm = byId("financial-preferences-form");
    if (financialForm) {
      financialForm.addEventListener("submit", saveFinancialPreferences);
    }

    var workForm = byId("work-preferences-form");
    if (workForm) workForm.addEventListener("submit", saveWorkPreferences);
    var supervisorForm = byId("supervisor-form");
    if (supervisorForm) supervisorForm.addEventListener("submit", saveSupervisor);
    var cancelSupervisor = byId("cancel-supervisor-btn");
    if (cancelSupervisor) {
      cancelSupervisor.addEventListener("click", resetSupervisorForm);
    }
    var shiftForm = byId("personal-shift-form");
    if (shiftForm) shiftForm.addEventListener("submit", savePersonalShift);
    var cancelShift = byId("cancel-personal-shift-btn");
    if (cancelShift) cancelShift.addEventListener("click", resetShiftForm);
  }

  function renderSidebarBalance() {
    var data = getCashflowData();
    if (!data) return;
    var balanceElement = byId("sidebar-total-balance");
    var noteElement = byId("sidebar-balance-note");
    var total = data.getTotalBalance();
    var month = data.getMonthKey(data.todayKey());
    var income = data.getTotalIncome({ month: month });
    var expenses = data.getTotalExpenses({ month: month });
    var net = Math.round((income - expenses + Number.EPSILON) * 100) / 100;
    if (balanceElement) balanceElement.textContent = data.formatNaira(total);
    if (noteElement) {
      noteElement.textContent =
        income === 0 && expenses === 0
          ? "No activity this month"
          : (net > 0 ? "+" : "") + data.formatNaira(net) + " net this month";
    }
  }

  function initSettings() {
    var data = getCashflowData();
    updateStorageStatus();
    initSettingsNav();
    initThemePreferences();
    bindForms();
    setProfileEditMode(false);

    loadProfileFields();
    loadWorkProfile();

    if (!data) {
      notify(
        "Financial Information Not Loaded",
        "Your personal profile is independent, but this browser could not load financial preferences. Refresh and try again.",
        "error"
      );
      return;
    }

    loadFinancialFields();
    renderSidebarBalance();
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSettings);
  } else {
    initSettings();
  }
})();
