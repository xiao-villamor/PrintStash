(function () {
  try {
    var theme =
      localStorage.getItem("printstash.theme") ||
      localStorage.getItem("nexus3d.theme") ||
      (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light");
    document.documentElement.classList.toggle("dark", theme === "dark");

    var favicon = document.createElement("link");
    favicon.id = "app-favicon";
    favicon.rel = "icon";
    favicon.type = "image/svg+xml";
    favicon.href = theme === "dark" ? "/icon-dark.svg?v=2" : "/icon-light.svg?v=2";
    document.head.appendChild(favicon);
  } catch {
    // Storage can be unavailable in hardened browser contexts. Light mode and
    // the manifest icon remain usable defaults when bootstrap cannot inspect it.
  }
})();
