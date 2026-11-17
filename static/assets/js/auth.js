(function () {
  "use strict";

  var THEME_KEY = "savvyfi-theme";

  function byId(id) {
    return document.getElementById(id);
  }

  function validEmail(value) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
  }

  function applyTheme(theme) {
    var nextTheme = theme === "light" ? "light" : "dark";
    document.documentElement.classList.remove("light", "dark");
    document.documentElement.classList.add(nextTheme);
    document.documentElement.style.colorScheme = nextTheme;
    localStorage.setItem(THEME_KEY, nextTheme);
    var toggle = byId("theme-toggle");
    if (toggle) {
      toggle.setAttribute("aria-label", nextTheme === "dark" ? "Switch to light mode" : "Switch to dark mode");
      toggle.setAttribute("aria-pressed", nextTheme === "dark" ? "true" : "false");
      var icon = toggle.querySelector("[data-lucide]");
      if (icon) icon.setAttribute("data-lucide", nextTheme === "dark" ? "sun" : "moon");
      if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons();
      }
    }
  }

  function setStatus(message, isInfo) {
    var status = byId("auth-status");
    if (!status) return;
    status.textContent = message;
    status.classList.toggle("is-info", Boolean(isInfo));
    if (message && message.length > 0) {
      startDismissTimer(status);
    } else {
      if (dismissTimer) {
        clearTimeout(dismissTimer);
        dismissTimer = null;
      }
    }
  }

  var dismissTimer = null;

  function startDismissTimer(statusElement) {
    if (dismissTimer) {
        clearTimeout(dismissTimer);
    }
    dismissTimer = setTimeout(function () {
        fadeOutStatus(statusElement);
    }, 2500);
  }

  function fadeOutStatus(statusElement) {
    if (!statusElement) return;
    statusElement.style.transition = "opacity 280ms ease-out";
    statusElement.style.opacity = "0";
    setTimeout(function () {
        statusElement.textContent = "";
        statusElement.style.opacity = "";
        statusElement.style.display = "none";
    }, 280);
  }

  function togglePassword(button) {
    var input = byId(button.getAttribute("aria-controls"));
    var icon = button.querySelector("[data-lucide]");
    if (!input) return;
    var showing = input.type === "text";
    input.type = showing ? "password" : "text";
    button.setAttribute("aria-label", showing ? "Show password" : "Hide password");
    if (icon) icon.setAttribute("data-lucide", showing ? "eye" : "eye-off");
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function validateLogin(event) {
    var email = byId("email").value.trim();
    var password = byId("password").value;
    if (!email || !password) {
      event.preventDefault();
      setStatus("Enter your email address and password.");
      return;
    }
    if (!validEmail(email)) {
      event.preventDefault();
      setStatus("Enter a valid email address.");
      return;
    }
    setStatus("");
  }

  function validateRegistration(event) {
    var name = byId("name").value.trim();
    var email = byId("email").value.trim();
    var password = byId("password").value;
    var confirmation = byId("password-confirmation").value;
    var terms = byId("terms").checked;
    var workplace = document.querySelector('input[name="workplace_access"]:checked');
    if (!name || !email || !password || !confirmation) {
      event.preventDefault();
      setStatus("Complete all required fields.");
      return;
    }
    if (!validEmail(email)) {
      event.preventDefault();
      setStatus("Enter a valid email address.");
      return;
    }
    if (password.length < 8) {
      event.preventDefault();
      setStatus("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirmation) {
      event.preventDefault();
      setStatus("Passwords do not match.");
      return;
    }
    if (!workplace) {
      event.preventDefault();
      setStatus("Tell us whether you work at GCTN or Apex Pay.");
      return;
    }
    if (!terms) {
      event.preventDefault();
      setStatus("Agree to the terms before creating your account.");
      return;
    }
    setStatus("");
  }

  function validateResetRequest(event) {
    var email = byId("email").value.trim();
    if (!email) {
      event.preventDefault();
      setStatus("Enter the email address associated with your account.");
      return;
    }
    if (!validEmail(email)) {
      event.preventDefault();
      setStatus("Enter a valid email address.");
      return;
    }
    setStatus("");
  }

  function validateResetConfirm(event) {
    var password = byId("password").value;
    var confirmation = byId("password-confirmation").value;
    if (!password || !confirmation) {
      event.preventDefault();
      setStatus("Enter and confirm your new password.");
      return;
    }
    if (password.length < 8) {
      event.preventDefault();
      setStatus("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirmation) {
      event.preventDefault();
      setStatus("Passwords do not match.");
      return;
    }
    setStatus("");
  }

  document.querySelectorAll("[data-password-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      togglePassword(button);
    });
  });

  function setSubmitButtonState(form, disabled, loadingLabel) {
    var button = form.querySelector('[type="submit"]');
    if (!button) return;
    if (disabled) {
      if (!button.dataset.originalLabel) {
        button.dataset.originalLabel = button.textContent;
      }
      button.disabled = true;
      button.setAttribute("data-processing", "");
      button.setAttribute("aria-busy", "true");
      if (loadingLabel) button.textContent = loadingLabel;
    } else {
      button.disabled = false;
      button.removeAttribute("data-processing");
      button.removeAttribute("aria-busy");
      if (button.dataset.originalLabel) {
        button.textContent = button.dataset.originalLabel;
        delete button.dataset.originalLabel;
      }
    }
  }

  function restoreForm(form) {
    delete form.dataset.submitting;
    form.removeAttribute("aria-busy");
    setSubmitButtonState(form, false);
  }

  document.querySelectorAll("[data-auth-form]").forEach(function (form) {
    var formType = form.getAttribute("data-auth-form");
    var handler = validateLogin;
    var loadingLabel = "Signing in…";
    if (formType === "register") {
      handler = validateRegistration;
      loadingLabel = "Creating account…";
    }
    if (formType === "reset-request") {
      handler = validateResetRequest;
      loadingLabel = "Sending…";
    }
    if (formType === "reset-confirm") {
      handler = validateResetConfirm;
      loadingLabel = "Resetting…";
    }

    form.addEventListener("submit", function (event) {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      handler(event);
      if (event.defaultPrevented) {
        restoreForm(form);
        return;
      }
      form.dataset.submitting = "true";
      form.setAttribute("aria-busy", "true");
      setSubmitButtonState(form, true, loadingLabel);
    });
    form.addEventListener("reset", function () {
      restoreForm(form);
    });
  });

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".auth-form").forEach(restoreForm);
  });

  window.addEventListener("pageshow", function () {
    document.querySelectorAll(".auth-form").forEach(restoreForm);
  });

  document.querySelectorAll("[data-google-auth]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (button.getAttribute("aria-disabled") === "true") return;
      button.classList.add("is-loading");
      button.setAttribute("aria-busy", "true");
      var label = button.querySelector("[data-google-label]");
      if (label) label.textContent = "Redirecting to Google…";
      setStatus("Redirecting to Google…", false);
    });
  });

  document.querySelectorAll("[data-oauth-form]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "true";
      form.setAttribute("aria-busy", "true");
      var submit = form.querySelector('button[type="submit"]');
      if (submit) {
        submit.disabled = true;
        submit.textContent = "Redirecting to Google…";
      }
    });
  });

  function initAuthToasts() {
    var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.querySelectorAll(".auth-toast[data-auto-dismiss]").forEach(function (toast) {
      window.setTimeout(function () {
        toast.classList.add("is-leaving");
        window.setTimeout(function () {
          var region = toast.parentElement;
          toast.remove();
          if (region && !region.children.length) region.remove();
        }, reducedMotion ? 0 : 260);
      }, 2400);
    });
  }

  initAuthToasts();

  var savedTheme = localStorage.getItem(THEME_KEY);
  applyTheme(savedTheme === "light" ? "light" : "dark");
  var themeToggle = byId("theme-toggle");
  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      applyTheme(document.documentElement.classList.contains("dark") ? "light" : "dark");
    });
  }
})();