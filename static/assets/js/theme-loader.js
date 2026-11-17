(function () {
  var theme = "dark";

  try {
    if (localStorage.getItem("savvyfi-theme") === "light") {
      theme = "light";
    }
  } catch (error) {
    theme = "dark";
  }

  var root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(theme);
  root.style.colorScheme = theme;

  try {
    if (localStorage.getItem("gabsweb-sidebar-collapsed") === "1") {
      root.classList.add("sidebar-collapsed");
    }
  } catch (error) {
    /* Ignore storage access failures; the shell still boots in dark mode. */
  }
})();