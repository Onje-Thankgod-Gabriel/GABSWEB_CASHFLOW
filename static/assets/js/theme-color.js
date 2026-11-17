(function () {
  "use strict";

  var THEME_KEY = "savvyfi-theme";
  var COLORS = {
    light: "#fafbfc",
    dark: "#0c0d11"
  };

  function storedTheme() {
    try {
      return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
    } catch (error) {
      return "dark";
    }
  }

  function activeTheme() {
    var root = document.documentElement;
    // The dark token block wins in the stylesheet when both classes are
    // present, so resolve the system colour with the same precedence.
    if (root.classList.contains("dark")) return "dark";
    if (root.classList.contains("light")) return "light";
    return storedTheme();
  }

  function themeMeta() {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement("meta");
      meta.setAttribute("name", "theme-color");
      document.head.appendChild(meta);
    }
    return meta;
  }

  function applyThemeColor() {
    themeMeta().setAttribute("content", COLORS[activeTheme()]);
  }

  applyThemeColor();

  if (window.MutationObserver) {
    new MutationObserver(applyThemeColor).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"]
    });
  }
})();