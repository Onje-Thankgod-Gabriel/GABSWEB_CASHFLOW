"use strict";

var fs = require("fs");
var path = require("path");
var vm = require("vm");
var assert = require("assert");

var rootDir = path.resolve(__dirname, "..", "..", "..");
var serviceWorkerSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "service-worker.js"),
  "utf8"
);
var pwaSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "pwa.js"),
  "utf8"
);
var offlineHtml = fs.readFileSync(
  path.join(rootDir, "static", "assets", "offline.html"),
  "utf8"
);
var manifest = JSON.parse(
  fs.readFileSync(
    path.join(rootDir, "static", "assets", "manifest.webmanifest"),
    "utf8"
  )
);
var themeColorSource = fs.readFileSync(
  path.join(rootDir, "static", "assets", "js", "theme-color.js"),
  "utf8"
);

function loadThemeColor(options) {
  options = options || {};
  var classes = (options.classes || []).slice();
  var observerCallback = null;

  function createMeta() {
    return {
      content: "",
      setAttribute: function (name, value) {
        if (name === "content") this.content = value;
      }
    };
  }

  var themeMeta = createMeta();

  function MutationObserver(callback) {
    observerCallback = callback;
    this.observe = function () {};
  }

  var sandbox = {
    document: {
      querySelector: function (selector) {
        if (selector === 'meta[name="theme-color"]') return themeMeta;
        return null;
      },
      documentElement: {
        classList: {
          contains: function (name) {
            return classes.indexOf(name) !== -1;
          }
        }
      }
    },
    localStorage: {
      getItem: function () {
        return options.stored || null;
      }
    },
    MutationObserver: MutationObserver,
    window: { MutationObserver: MutationObserver }
  };

  vm.runInNewContext(themeColorSource, sandbox, {
    filename: "theme-color.js"
  });

  return {
    meta: themeMeta,
    setClasses: function (next) {
      classes = next.slice();
      if (observerCallback) observerCallback();
    }
  };
}

function response(body, options) {
  return new Response(body, options || {});
}

function createCache() {
  var store = new Map();
  return {
    store: store,
    add: function (url) {
      store.set(
        url,
        response(
          url.indexOf("/offline.html") !== -1
            ? offlineHtml
            : "precached:" + url
        )
      );
      return Promise.resolve();
    },
    put: function (request, value) {
      store.set(typeof request === "string" ? request : request.url, value);
      return Promise.resolve();
    },
    match: function (key) {
      var url = typeof key === "string" ? key : key.url;
      return Promise.resolve(store.get(url));
    }
  };
}

function loadServiceWorker(options) {
  options = options || {};
  var listeners = {};
  var cacheMap = new Map();
  var skippedWaiting = false;
  var claimed = false;
  var fetchImpl = options.fetch || function () {
    return Promise.reject(new Error("network unavailable"));
  };

  var self = {
    location: { origin: "https://cashflow.test" },
    registration: { active: options.active === false ? null : {} },
    clients: {
      claim: function () {
        claimed = true;
        return Promise.resolve();
      }
    },
    addEventListener: function (type, handler) {
      if (!listeners[type]) listeners[type] = [];
      listeners[type].push(handler);
    },
    skipWaiting: function () {
      skippedWaiting = true;
      return Promise.resolve();
    }
  };

  var caches = {
    open: function (name) {
      if (!cacheMap.has(name)) cacheMap.set(name, createCache());
      return Promise.resolve(cacheMap.get(name));
    },
    keys: function () {
      return Promise.resolve(Array.from(cacheMap.keys()));
    },
    delete: function (name) {
      cacheMap.delete(name);
      return Promise.resolve(true);
    },
    match: function (request) {
      return Promise.all(
        Array.from(cacheMap.values()).map(function (cache) {
          return cache.match(request);
        })
      ).then(function (matches) {
        return matches.find(Boolean);
      });
    }
  };

  var sandbox = {
    self: self,
    caches: caches,
    fetch: function (request) {
      return fetchImpl(request);
    },
    URL: URL,
    Response: Response,
    Headers: Headers,
    console: console,
    Promise: Promise,
    setTimeout: setTimeout
  };

  vm.runInNewContext(serviceWorkerSource, sandbox, {
    filename: "service-worker.js"
  });

  function dispatch(type, event) {
    (listeners[type] || []).forEach(function (handler) {
      handler(event);
    });
  }

  function dispatchFetch(request) {
    var responded = null;
    var event = {
      request: request,
      respondWith: function (value) {
        responded = value;
      }
    };
    dispatch("fetch", event);
    if (!responded) return Promise.resolve({ handled: false });
    return Promise.resolve(responded).then(function (value) {
      return { handled: true, response: value };
    });
  }

  return {
    self: self,
    caches: caches,
    cacheMap: cacheMap,
    dispatch: dispatch,
    dispatchFetch: dispatchFetch,
    setFetch: function (fn) {
      fetchImpl = fn;
    },
    wasSkipped: function () {
      return skippedWaiting;
    },
    wasClaimed: function () {
      return claimed;
    }
  };
}

function makeEvent(overrides) {
  return Object.assign(
    {
      request: null,
      respondWith: function () {},
      waitUntil: function () {}
    },
    overrides || {}
  );
}

function install(worker) {
  var promise = null;
  worker.dispatch(
    "install",
    makeEvent({
      waitUntil: function (value) {
        promise = value;
      }
    })
  );
  return Promise.resolve(promise);
}

function activate(worker) {
  var promise = null;
  worker.dispatch(
    "activate",
    makeEvent({
      waitUntil: function (value) {
        promise = value;
      }
    })
  );
  return Promise.resolve(promise);
}

function request(url, overrides) {
  return Object.assign(
    {
      method: "GET",
      url: url,
      mode: "no-cors",
      headers: new Headers()
    },
    overrides || {}
  );
}

async function run() {
  // Manifest classification.
  var purposes = manifest.icons.map(function (icon) {
    return (icon.purpose || "any") + ":" + icon.sizes;
  });
  assert.ok(purposes.indexOf("any:192x192") !== -1);
  assert.ok(purposes.indexOf("any:512x512") !== -1);
  assert.ok(purposes.indexOf("maskable:1254x1254") !== -1);
  manifest.icons.forEach(function (icon) {
    assert.ok(icon.src.indexOf("/static/") === 0);
  });
  assert.strictEqual(manifest.start_url, "/");
  assert.strictEqual(manifest.scope, "/");
  assert.strictEqual(manifest.theme_color, "#13151c");
  assert.strictEqual(manifest.background_color, "#0c0d11");

  // System chrome follows the real page background tokens so the status bar and
  // header blend instead of using the platform's black/white dark-mode chrome.
  var darkThemeColor = loadThemeColor({ stored: "dark" });
  assert.strictEqual(darkThemeColor.meta.content, "#0c0d11");
  darkThemeColor.setClasses(["light"]);
  assert.strictEqual(darkThemeColor.meta.content, "#fafbfc");
  darkThemeColor.setClasses(["dark"]);
  assert.strictEqual(darkThemeColor.meta.content, "#0c0d11");
  // Dark tokens win in the stylesheet when stale classes overlap, so the system
  // colour must use the same precedence instead of reporting the light surface.
  darkThemeColor.setClasses(["light", "dark"]);
  assert.strictEqual(darkThemeColor.meta.content, "#0c0d11");

  var lightThemeColor = loadThemeColor({ stored: "light" });
  assert.strictEqual(lightThemeColor.meta.content, "#fafbfc");
  lightThemeColor.setClasses(["light"]);
  assert.strictEqual(lightThemeColor.meta.content, "#fafbfc");

  // Every authenticated app header removes only its top border and uses the
  // same --bg token as the status bar so the two surfaces meet seamlessly. The
  // bottom navigation keeps the panel surface and extends it through the
  // gesture area.
  var indexCss = fs.readFileSync(
    path.join(rootDir, "static", "assets", "css", "index.css"),
    "utf8"
  );
  var headerMatch = indexCss.match(
    /\[data-page\]\s+\.app-header\s*\{([^}]*)\}/
  );
  assert.ok(headerMatch, "The shared app header rule is missing");
  assert.ok(
    /border-top:\s*0;/.test(headerMatch[1]),
    "The shared app header must remove its top border only"
  );
  assert.ok(
    /background:\s*var\(--bg\)/.test(headerMatch[1]),
    "The shared app header must use the theme background token"
  );
  assert.strictEqual(
    indexCss.indexOf('[data-page="Dashboard"] .app-header'),
    -1,
    "The top treatment must not be limited to the dashboard"
  );
  var bottomNavMatch = indexCss.match(/\.bottom-nav\s*\{\s*position: fixed;/);
  assert.ok(bottomNavMatch, "The fixed bottom navigation rule is missing");
  var bottomNavStart = bottomNavMatch.index;
  var bottomNavRule = indexCss.slice(
    bottomNavStart,
    indexCss.indexOf("}", bottomNavStart)
  );
  assert.ok(
    bottomNavRule.indexOf("background-color: var(--panel)") !== -1,
    "The bottom navigation must use the panel token"
  );
  assert.strictEqual(
    bottomNavRule.indexOf("#6366f1"),
    -1,
    "The bottom navigation must not add a purple strip"
  );
  assert.strictEqual(
    bottomNavRule.indexOf("linear-gradient"),
    -1,
    "The bottom navigation must not add a gradient strip"
  );
  var safeAreaNavMatch = indexCss.match(
    /nav\.bottom-nav\s*\{\s*padding-bottom:\s*env\(safe-area-inset-bottom, 0px\);\s*background-color:\s*var\(--panel\);\s*\}/
  );
  assert.ok(
    safeAreaNavMatch,
    "The navigation surface must paint the panel token through the gesture area"
  );

  // Theme initialisation and the theme-colour script must run before any
  // blocking external script or stylesheet so the first system-bar paint is
  // already correct.
  var baseSource = fs.readFileSync(
    path.join(rootDir, "core", "templates", "layouts", "base.html"),
    "utf8"
  );
  var themeLoaderIndex = baseSource.indexOf("theme_scripts");
  var themeColorIndex = baseSource.indexOf("assets/js/theme-color.js");
  var lucideIndex = baseSource.indexOf("unpkg.com/lucide");
  var stylesheetIndex = baseSource.indexOf("assets/css/index.css");
  assert.ok(themeLoaderIndex !== -1 && themeColorIndex !== -1);
  assert.ok(
    themeLoaderIndex < themeColorIndex,
    "theme-loader must run before theme-color"
  );
  assert.ok(
    themeColorIndex < lucideIndex,
    "theme-color must run before the blocking Lucide script"
  );
  assert.ok(
    themeColorIndex < stylesheetIndex,
    "theme-color must run before the application stylesheet"
  );
  assert.strictEqual(
    (baseSource.match(/assets\/js\/theme-color\.js/g) || []).length,
    1,
    "base.html must load exactly one theme-colour script"
  );

  // Capture the browser install event before the deferred PWA module runs so a
  // fast beforeinstallprompt event can never be missed.
  assert.ok(baseSource.indexOf("__gabswebDeferredInstallPrompt") !== -1);
  assert.ok(pwaSource.indexOf("__gabswebDeferredInstallPrompt") !== -1);
  assert.ok(
    baseSource.indexOf("__gabswebDeferredInstallPrompt") <
      baseSource.indexOf("assets/js/pwa.js"),
    "The install event must be captured before pwa.js loads"
  );
  assert.ok(pwaSource.indexOf("promptEvent.userChoice") !== -1);
  assert.ok(pwaSource.indexOf("clearDeferredPrompt();") !== -1);

  // The shared header toggle must replace the theme classes instead of
  // layering both and leaving the status colour out of sync.
  var appScript = fs.readFileSync(
    path.join(rootDir, "static", "assets", "js", "index.js"),
    "utf8"
  );
  var toggleStart = appScript.indexOf("function toggleTheme()");
  var toggleEnd = appScript.indexOf("}\\n\\n", toggleStart);
  assert.ok(toggleStart !== -1 && toggleEnd !== -1, "The shared theme toggle is missing");
  var toggleSource = appScript.slice(toggleStart, toggleEnd);
  assert.ok(
    toggleSource.indexOf('classList.remove(\\"light\\", \\"dark\\")') !== -1,
    "The theme toggle must clear the previous theme class"
  );
  assert.ok(
    toggleSource.indexOf("style.colorScheme") !== -1,
    "The theme toggle must update the colour scheme"
  );

  // Every layout initialises the stored theme before first paint so the system
  // colour is correct during loading, not only after a lower script runs.
  ["app_base.html", "auth_base.html", "error_base.html", "legal_base.html"].forEach(
    function (layout) {
      var source = fs.readFileSync(
        path.join(rootDir, "core", "templates", "layouts", layout),
        "utf8"
      );
      assert.ok(
        source.indexOf("assets/js/theme-loader.js") !== -1,
        layout + " must load theme-loader before the theme-colour script"
      );
      assert.strictEqual(
        (source.match(/theme-loader\.js/g) || []).length,
        1,
        layout + " must load theme-loader exactly once"
      );
    }
  );

  // First install activates immediately and precaches the offline page.
  var first = loadServiceWorker({ active: false });
  await install(first);
  assert.strictEqual(first.wasSkipped(), true);
  var precache = await first.caches.open(
    "gabsweb-precache-" +
      serviceWorkerSource.match(/CACHE_VERSION = "([^"]+)"/)[1]
  );
  assert.ok(await precache.match("/static/assets/offline.html"));
  assert.ok(await precache.match("/static/assets/icons/gabsweb_PWA_Maskable_Icon.png?v=20261115d"));

  // An update waits for the explicit SKIP_WAITING message.
  var update = loadServiceWorker({ active: true });
  await install(update);
  assert.strictEqual(update.wasSkipped(), false);

  // Offline navigation returns the branded offline page, never an app page.
  var navigation = loadServiceWorker();
  await install(navigation);
  var offline = await navigation.dispatchFetch(
    request("https://cashflow.test/", { mode: "navigate" })
  );
  assert.strictEqual(offline.handled, true);
  var offlineText = await offline.response.text();
  assert.ok(offlineText.indexOf("You are offline") !== -1);
  assert.ok(offlineText.indexOf("GABSWEB") !== -1 || offlineText.indexOf("Gabsweb") !== -1);
  assert.ok(offlineHtml.indexOf('<meta name="theme-color" content="#0c0d11" />') !== -1);
  assert.ok(offlineHtml.indexOf('<meta name="color-scheme"') === -1);

  // Private API requests are left entirely to the network layer.
  var privateRequest = await navigation.dispatchFetch(
    request("https://cashflow.test/transactions/")
  );
  assert.strictEqual(privateRequest.handled, false);

  var apiRequest = await navigation.dispatchFetch(
    request("https://cashflow.test/budget-api/")
  );
  assert.strictEqual(apiRequest.handled, false);

  // Non-GET, cross-origin and range requests are never intercepted.
  assert.strictEqual(
    (await navigation.dispatchFetch(
      request("https://cashflow.test/static/assets/js/app.js", {
        method: "POST"
      })
    )).handled,
    false
  );
  assert.strictEqual(
    (await navigation.dispatchFetch(
      request("https://other.test/static/assets/js/app.js")
    )).handled,
    false
  );
  assert.strictEqual(
    (await navigation.dispatchFetch(
      request("https://cashflow.test/static/assets/js/app.js", {
        headers: new Headers({ Range: "bytes=0-0" })
      })
    )).handled,
    false
  );

  // JavaScript/CSS use network-first so a deployment is picked up immediately.
  var code = loadServiceWorker();
  var codeResponse = response("fresh-code", {
    headers: { "Cache-Control": "max-age=60" }
  });
  code.setFetch(function () {
    return Promise.resolve(codeResponse.clone());
  });
  var codeFirst = await code.dispatchFetch(
    request("https://cashflow.test/static/assets/js/app.js")
  );
  assert.strictEqual(await codeFirst.response.text(), "fresh-code");
  code.setFetch(function () {
    return Promise.reject(new Error("offline"));
  });
  var codeOffline = await code.dispatchFetch(
    request("https://cashflow.test/static/assets/js/app.js")
  );
  assert.strictEqual(await codeOffline.response.text(), "fresh-code");

  // Images use stale-while-revalidate and refresh in the background.
  var image = loadServiceWorker();
  var staticCache = await image.caches.open(
    "gabsweb-static-" + serviceWorkerSource.match(/CACHE_VERSION = "([^"]+)"/)[1]
  );
  var imageUrl = "https://cashflow.test/static/assets/icons/gabsweb_PWA_Maskable_Icon.png";
  await staticCache.put(imageUrl, response("cached-image"));
  image.setFetch(function () {
    return Promise.resolve(
      response("fresh-image", { headers: { "Cache-Control": "max-age=60" } })
    );
  });
  var imageResult = await image.dispatchFetch(request(imageUrl));
  assert.strictEqual(await imageResult.response.text(), "cached-image");
  await new Promise(function (resolve) {
    setTimeout(resolve, 0);
  });
  var refreshed = await staticCache.match(imageUrl);
  assert.strictEqual(await refreshed.text(), "fresh-image");

  // Responses marked private or no-store are never written to a cache.
  var privateStatic = loadServiceWorker();
  var privateUrl = "https://cashflow.test/static/assets/js/private.js";
  privateStatic.setFetch(function () {
    return Promise.resolve(
      response("private", { headers: { "Cache-Control": "private, no-store" } })
    );
  });
  await privateStatic.dispatchFetch(request(privateUrl));
  var privateCache = await privateStatic.caches.open(
    "gabsweb-static-" + serviceWorkerSource.match(/CACHE_VERSION = "([^"]+)"/)[1]
  );
  assert.strictEqual(await privateCache.match(privateUrl), undefined);

  // Activation removes obsolete Gabsweb caches but keeps current and foreign.
  var lifecycle = loadServiceWorker();
  var version = serviceWorkerSource.match(/CACHE_VERSION = "([^"]+)"/)[1];
  await lifecycle.caches.open("gabsweb-precache-" + version);
  await lifecycle.caches.open("gabsweb-static-" + version);
  await lifecycle.caches.open("gabsweb-precache-old");
  await lifecycle.caches.open("unrelated-cache");
  await activate(lifecycle);
  assert.strictEqual(lifecycle.cacheMap.has("gabsweb-precache-old"), false);
  assert.strictEqual(lifecycle.cacheMap.has("gabsweb-static-" + version), true);
  assert.strictEqual(lifecycle.cacheMap.has("unrelated-cache"), true);
  assert.strictEqual(lifecycle.wasClaimed(), true);

  // Installation onboarding is platform-aware and honest about availability.
  assert.ok(pwaSource.indexOf("Add to Home Screen") !== -1);
  assert.ok(pwaSource.indexOf("Add to Dock") !== -1);
  assert.ok(pwaSource.indexOf("Install app") !== -1);
  assert.ok(pwaSource.indexOf("does not offer web app installation") !== -1);
  assert.ok(pwaSource.indexOf("beforeinstallprompt") !== -1);
  assert.ok(pwaSource.indexOf("appinstalled") !== -1);
  assert.ok(pwaSource.indexOf("promptEvent.prompt()") !== -1);
  assert.ok(pwaSource.indexOf('type: "SKIP_WAITING"') !== -1);
  assert.ok(pwaSource.indexOf("if (!reloadRequested) return;") !== -1);
  assert.strictEqual(
    (pwaSource.match(/window\.location\.reload\(\)/g) || []).length,
    1,
    "The page may only reload after an explicit service-worker update"
  );
  assert.ok(pwaSource.indexOf("getRegistration") !== -1);
  assert.ok(pwaSource.indexOf("visibilitychange") !== -1);

  // Deployments are detected without reinstalling: the page checks the
  // registration while visible, hourly, and immediately after registering, and
  // activating the new worker still waits for the explicit Refresh action.
  assert.ok(pwaSource.indexOf("function checkForUpdate") !== -1);
  assert.ok(pwaSource.indexOf("registration.update()") !== -1);
  assert.ok(pwaSource.indexOf("UPDATE_CHECK_INTERVAL") !== -1);
  assert.ok(pwaSource.indexOf("checkForUpdate(false)") !== -1);
  assert.ok(pwaSource.indexOf("checkForUpdate(true)") !== -1);
  assert.ok(
    pwaSource.indexOf("window.setInterval") !== -1,
    "The worker must be rechecked periodically while the app stays open"
  );
  assert.ok(
    pwaSource.indexOf("postMessage({ type: \"SKIP_WAITING\" })") !== -1,
    "The update must only activate after the user clicks Refresh"
  );
  assert.ok(
    /if \(!reloadRequested\) return;/.test(pwaSource),
    "Automatic activation must never reload the page without a user request"
  );
  assert.ok(pwaSource.indexOf("isLoginPrompt") !== -1);
  assert.ok(pwaSource.indexOf("data-pwa-login-prompt") !== -1);
  assert.strictEqual(pwaSource.indexOf("state.never"), -1);
  assert.strictEqual(pwaSource.indexOf("sessionStorage"), -1);
  assert.ok(pwaSource.indexOf("display-mode: standalone") !== -1);
  assert.ok(pwaSource.indexOf("getInstalledRelatedApps") !== -1);
  assert.ok(pwaSource.indexOf("refreshInstallState") !== -1);
  assert.ok(
    pwaSource.indexOf(
      "Browsers only offer this prompt while the app is not installed"
    ) !== -1,
    "A stale completed flag must be cleared when the browser offers an install prompt"
  );

  console.log("PWA regressions passed");
}

run().then(
  function () {
    process.exit(0);
  },
  function (error) {
    console.error(error && error.stack ? error.stack : error);
    process.exit(1);
  }
);
