/* Gabsweb CashFlow — documentation table-of-contents highlighting */
(function () {
  "use strict";

  var links = Array.prototype.slice.call(
    document.querySelectorAll(".docs-toc-link[data-docs-target]")
  );
  if (!links.length) return;

  var sections = [];
  var seen = {};

  links.forEach(function (link) {
    var id = link.getAttribute("data-docs-target");
    if (!id || seen[id]) return;
    var section = document.getElementById(id);
    if (!section) return;
    seen[id] = true;
    sections.push({ id: id, element: section });
  });

  if (!sections.length) return;

  function setActive(id) {
    links.forEach(function (link) {
      var isActive = link.getAttribute("data-docs-target") === id;
      link.classList.toggle("is-active", isActive);
      if (isActive) {
        link.setAttribute("aria-current", "true");
      } else {
        link.removeAttribute("aria-current");
      }
    });
  }

  var ticking = false;

  function updateActive() {
    ticking = false;
    var threshold = 130;
    var current = sections[0].id;
    var atBottom =
      window.innerHeight + window.pageYOffset >=
      document.documentElement.scrollHeight - 4;

    if (atBottom) {
      current = sections[sections.length - 1].id;
    } else {
      sections.forEach(function (section) {
        if (section.element.getBoundingClientRect().top <= threshold) {
          current = section.id;
        }
      });
    }

    setActive(current);
  }

  function requestUpdate() {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(updateActive);
  }

  var mobileToc = document.querySelector(".docs-toc-mobile");
  if (mobileToc) {
    mobileToc.querySelectorAll(".docs-toc-link").forEach(function (link) {
      link.addEventListener("click", function () {
        mobileToc.removeAttribute("open");
      });
    });
  }

  window.addEventListener("scroll", requestUpdate, { passive: true });
  window.addEventListener("resize", requestUpdate);
  updateActive();
})();
