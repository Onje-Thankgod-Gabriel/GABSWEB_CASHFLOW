/* Gabsweb CashFlow — timed account notices */
(function () {
  "use strict";

  var LOGIN_SUCCESS_PATTERNS = [
    /^Successfully signed in as /i,
    /^Logged in as /i,
    /^Your account was created and you are now signed in\.?$/i
  ];

  function isLoginSuccess(message) {
    return LOGIN_SUCCESS_PATTERNS.some(function (pattern) {
      return pattern.test(message);
    });
  }

  function dismissMessage(element) {
    element.classList.add("is-dismissing");
    window.setTimeout(function () {
      element.remove();
    }, 240);
  }

  document.querySelectorAll(".app-notices-message").forEach(function (element) {
    var message = (element.textContent || "").trim();
    if (!isLoginSuccess(message)) return;
    window.setTimeout(function () {
      dismissMessage(element);
    }, 2500);
  });

  var pendingNotice = document.querySelector(
    '[data-pending-notice][data-pending-expanded="true"]'
  );

  if (pendingNotice) {
    window.setTimeout(function () {
      pendingNotice.classList.remove("is-expanded");
      pendingNotice.classList.add("is-compact");
      pendingNotice.removeAttribute("data-pending-expanded");
    }, 5000);
  }
})();
