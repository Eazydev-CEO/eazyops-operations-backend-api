/* Runs synchronously in <head> to apply the saved theme before first paint. */
(function () {
  var theme = "dark";
  try {
    theme = localStorage.getItem("ezops-theme") || "dark";
  } catch (e) { /* storage unavailable */ }
  document.documentElement.setAttribute("data-bs-theme", theme === "light" ? "light" : "dark");
  try {
    if (localStorage.getItem("ezops-sidebar") === "collapsed") {
      document.documentElement.classList.add("boot-sidebar-collapsed");
    }
  } catch (e) { /* ignore */ }
})();
