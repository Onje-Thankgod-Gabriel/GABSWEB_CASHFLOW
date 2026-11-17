(function () {
  "use strict";

  function onReady(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, { once: true });
    } else {
      callback();
    }
  }

  onReady(function () {
    var header = document.getElementById("header");
    if (header) {
      var syncHeader = function () {
        header.classList.toggle("gabs-header--scrolled", window.scrollY > 4);
      };
      syncHeader();
      window.addEventListener("scroll", syncHeader, { passive: true });
    }

    var current = document.querySelector("#nav-sidebar .current-model > th > a");
    if (current) {
      current.setAttribute("aria-current", "page");
    }

    if (
      window.matchMedia("(max-width: 1024px)").matches &&
      localStorage.getItem("django.admin.navSidebarIsOpen") === null
    ) {
      var main = document.getElementById("main");
      var nav = document.getElementById("nav-sidebar");
      if (main) {
        main.classList.remove("shifted");
      }
      if (nav) {
        nav.setAttribute("aria-expanded", "false");
      }
    }

    var filterGroups = Array.prototype.slice.call(
      document.querySelectorAll("#changelist-filter details")
    );

    filterGroups.forEach(function (group) {
      group.addEventListener("toggle", function () {
        if (!group.open) {
          return;
        }
        filterGroups.forEach(function (other) {
          if (other !== group) {
            other.open = false;
          }
        });
      });
    });

    document.addEventListener("click", function (event) {
      if (!event.target.closest || !event.target.closest("#changelist-filter")) {
        filterGroups.forEach(function (group) {
          group.open = false;
        });
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") {
        return;
      }
      filterGroups.forEach(function (group) {
        group.open = false;
      });
      var shifted = document.querySelector(".main.shifted");
      var toggle = document.querySelector(".toggle-nav-sidebar");
      if (shifted && toggle) {
        toggle.click();
      }
    });

    document.documentElement.classList.add("gabs-admin-ready");
  });
})();
