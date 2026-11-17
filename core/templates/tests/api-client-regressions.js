"use strict";

var fs = require("fs");
var path = require("path");
var vm = require("vm");
var assert = require("assert");

var rootDir = path.resolve(__dirname, "..", "..", "..");
var source = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "api-client.js"),
  "utf8"
);

function response(status, body, options) {
  var settings = options || {};
  return {
    ok: status >= 200 && status < 300,
    status: status,
    redirected: Boolean(settings.redirected),
    headers: {
      get: function (name) {
        if (name.toLowerCase() !== "content-type") return null;
        return settings.contentType || "application/json";
      }
    },
    text: function () {
      return Promise.resolve(body);
    }
  };
}

function loadClient(handler) {
  var context = {
    console: console,
    Promise: Promise,
    fetch: handler
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "api-client.js" });
  return context.GabswebApi;
}

async function expectFailure(promise, check) {
  try {
    await promise;
  } catch (error) {
    check(error);
    return;
  }
  assert.fail("Expected the request to fail.");
}

(async function () {
  var client = loadClient(function () {
    return Promise.resolve(
      response(200, JSON.stringify({ ok: true, goals: [], summary: {} }))
    );
  });
  var empty = await client.request("/goals-api/");
  assert.strictEqual(JSON.stringify(empty.goals), "[]");
  assert.strictEqual(JSON.stringify(empty.summary), "{}");

  client = loadClient(function () {
    return Promise.resolve(
      response(
        400,
        JSON.stringify({
          ok: false,
          errors: { planned_amount: ["Enter an allocation amount greater than zero."] }
        })
      )
    );
  });
  await expectFailure(client.request("/budget-api/allocations/"), function (error) {
    assert.strictEqual(error.status, 400);
    assert.strictEqual(
      error.message,
      "Enter an allocation amount greater than zero."
    );
    assert.strictEqual(
      JSON.stringify(client.fieldErrors(error.payload)),
      JSON.stringify({
        planned_amount: ["Enter an allocation amount greater than zero."]
      })
    );
  });

  client = loadClient(function () {
    return Promise.resolve(
      response(
        409,
        JSON.stringify({
          ok: false,
          errors: { __all__: ["This goal has funding history and cannot be deleted."] }
        })
      )
    );
  });
  await expectFailure(client.request("/goals-api/goals/1/delete/"), function (error) {
    assert.strictEqual(error.status, 409);
    assert.strictEqual(
      error.message,
      "This goal has funding history and cannot be deleted."
    );
  });

  client = loadClient(function () {
    return Promise.resolve(
      response(
        500,
        "<html>OperationalError: no such table core_goal at C:\\secret\\path</html>",
        { contentType: "text/html" }
      )
    );
  });
  await expectFailure(client.request("/goals-api/"), function (error) {
    assert.strictEqual(error.status, 500);
    assert.strictEqual(
      error.message,
      "Something went wrong. Please try again. If the problem continues, contact support."
    );
    assert.ok(error.message.indexOf("OperationalError") === -1);
    assert.ok(error.message.indexOf("secret") === -1);
  });

  client = loadClient(function () {
    return Promise.resolve(
      response(200, "<html>login</html>", { contentType: "text/html" })
    );
  });
  await expectFailure(client.request("/goals-api/"), function (error) {
    assert.strictEqual(error.kind, "malformed");
    assert.strictEqual(
      error.message,
      "We received an unexpected response. Refresh the page and try again."
    );
  });

  client = loadClient(function () {
    return Promise.resolve(
      response(200, "", { redirected: true, contentType: "text/html" })
    );
  });
  await expectFailure(client.request("/goals-api/"), function (error) {
    assert.strictEqual(error.status, 401);
    assert.strictEqual(
      error.message,
      "Your session has expired. Sign in again and retry."
    );
  });

  client = loadClient(function () {
    return Promise.reject(new TypeError("Failed to fetch"));
  });
  await expectFailure(client.request("/goals-api/"), function (error) {
    assert.strictEqual(error.kind, "network");
    assert.strictEqual(
      error.message,
      "Couldn't connect. Check your internet connection and try again."
    );
  });

  console.log("api-client regressions passed");
})().catch(function (error) {
  console.error(error);
  process.exit(1);
});
