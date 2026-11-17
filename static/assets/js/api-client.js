/* Gabsweb CashFlow — Shared JSON request and error handling */
(function () {
  "use strict";

  function apiError(message, status, payload, kind) {
    var error = new Error(message);
    error.name = "ApiError";
    error.status = status || 0;
    error.payload = payload || null;
    error.kind = kind || "request";
    return error;
  }

  function fieldErrors(payload) {
    var result = Object.create(null);
    if (!payload || !payload.errors || typeof payload.errors !== "object") {
      return result;
    }
    Object.keys(payload.errors).forEach(function (field) {
      var messages = payload.errors[field];
      if (Array.isArray(messages)) {
        result[field] = messages
          .map(function (message) {
            return String(message || "").trim();
          })
          .filter(Boolean);
      } else if (messages != null) {
        var text = String(messages).trim();
        if (text) result[field] = [text];
      }
    });
    return result;
  }

  function firstFieldError(payload) {
    var errors = fieldErrors(payload);
    var keys = Object.keys(errors);
    for (var index = 0; index < keys.length; index += 1) {
      if (errors[keys[index]].length) return errors[keys[index]][0];
    }
    return "";
  }

  function payloadMessage(payload) {
    if (payload && typeof payload.message === "string") {
      return payload.message.trim();
    }
    return "";
  }

  function errorMessageFor(status, payload) {
    if (status === 401) {
      return "Your session has expired. Sign in again and retry.";
    }
    if (status === 403) {
      return "You don't have permission to do that.";
    }
    if (status === 404) {
      return "The requested item could not be found. Refresh the page and try again.";
    }
    if (status === 429) {
      return "Too many attempts. Wait a moment, then try again.";
    }
    if (status >= 500) {
      return "Something went wrong. Please try again. If the problem continues, contact support.";
    }
    return (
      firstFieldError(payload) ||
      payloadMessage(payload) ||
      "We couldn't complete this request. Please try again."
    );
  }

  function parsePayload(response, text) {
    if (!text) return null;
    var contentType = response.headers.get("content-type") || "";
    var looksJson =
      contentType.indexOf("application/json") !== -1 ||
      /^\s*[\[{]/.test(text);
    if (!looksJson) return null;
    try {
      return JSON.parse(text);
    } catch (error) {
      return null;
    }
  }

  function request(url, options) {
    var config = options || {};
    var headers = {
      Accept: "application/json"
    };
    if (config.csrfToken) headers["X-CSRFToken"] = config.csrfToken;
    if (config.body) headers["Content-Type"] = "application/json";

    return fetch(url, {
      method: config.method || "GET",
      credentials: "same-origin",
      headers: headers,
      body: config.body
    }).then(
      function (response) {
        return response.text().then(function (text) {
          if (response.redirected) {
            throw apiError(
              "Your session has expired. Sign in again and retry.",
              401,
              null,
              "auth"
            );
          }

          var payload = parsePayload(response, text);

          if (!response.ok) {
            throw apiError(
              errorMessageFor(response.status, payload),
              response.status,
              payload,
              response.status >= 500 ? "server" : "request"
            );
          }

          if (!payload || typeof payload !== "object") {
            throw apiError(
              "We received an unexpected response. Refresh the page and try again.",
              response.status,
              null,
              "malformed"
            );
          }

          return payload;
        });
      },
      function () {
        throw apiError(
          "Couldn't connect. Check your internet connection and try again.",
          0,
          null,
          "network"
        );
      }
    );
  }

  window.GabswebApi = {
    request: request,
    fieldErrors: fieldErrors,
    firstFieldError: firstFieldError,
    errorMessage: function (error, fallback) {
      if (error && typeof error.message === "string" && error.message) {
        return error.message;
      }
      return fallback || "We couldn't complete this request. Please try again.";
    }
  };
})();
