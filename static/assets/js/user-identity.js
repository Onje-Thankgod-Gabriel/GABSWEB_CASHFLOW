(function () {
  "use strict";

  var body = document.body;
  var data = window.GabswebCashFlowData;
  if (!body || !data) return;

  var fullName = String(body.dataset.userFullName || "").trim();
  var email = String(body.dataset.userEmail || "").trim();
  if (!fullName && !email) return;

  try {
    var profile = data.getProfile() || {};
    var update = {};
    if (fullName && String(profile.name || "").trim() !== fullName) {
      update.name = fullName;
    }
    if (email && String(profile.email || "").trim() !== email) {
      update.email = email;
    }
    if (Object.keys(update).length) data.updateProfile(update);
  } catch (error) {
    return;
  }
})();
