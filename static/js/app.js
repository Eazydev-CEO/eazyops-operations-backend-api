/* ==========================================================================
   EazyOps shared front-end behaviour (jQuery + Bootstrap + Chart.js)
   ========================================================================== */
(function ($) {
  "use strict";

  window.EZO = window.EZO || {};

  /* ------------------------------------------------------------------ *
   * Helpers
   * ------------------------------------------------------------------ */
  function getCookie(name) {
    var value = "; " + document.cookie;
    var parts = value.split("; " + name + "=");
    if (parts.length === 2) return decodeURIComponent(parts.pop().split(";").shift());
    return null;
  }

  EZO.csrf = function () { return getCookie("csrftoken"); };

  EZO.post = function (url, data) {
    return $.ajax({
      url: url,
      method: "POST",
      data: data || {},
      headers: { "X-CSRFToken": EZO.csrf(), "X-Requested-With": "XMLHttpRequest" },
    });
  };

  EZO.escape = function (text) {
    return $("<div>").text(text == null ? "" : String(text)).html();
  };

  /* ------------------------------------------------------------------ *
   * Toastr-style notifications
   * ------------------------------------------------------------------ */
  var TOAST_ICONS = {
    success: "bi-check-circle-fill",
    error: "bi-x-circle-fill",
    warning: "bi-exclamation-triangle-fill",
    info: "bi-info-circle-fill",
  };

  function showToast(level, message, timeout) {
    var stack = document.getElementById("toast-stack");
    if (!stack) return;
    level = TOAST_ICONS[level] ? level : "info";
    var $toast = $(
      '<div class="ez-toast ' + level + '" role="alert">' +
        '<i class="bi ' + TOAST_ICONS[level] + '"></i>' +
        '<div class="msg">' + EZO.escape(message) + "</div>" +
        '<button type="button" class="btn-close btn-close-white btn-sm" aria-label="Close"></button>' +
      "</div>"
    );
    $toast.find(".btn-close").on("click", function () { dismiss($toast); });
    $(stack).append($toast);
    window.setTimeout(function () { dismiss($toast); }, timeout || 5000);
  }

  function dismiss($toast) {
    $toast.addClass("hide");
    window.setTimeout(function () { $toast.remove(); }, 280);
  }

  window.toastr = {
    success: function (m) { showToast("success", m); },
    error: function (m) { showToast("error", m, 8000); },
    warning: function (m) { showToast("warning", m, 6500); },
    info: function (m) { showToast("info", m); },
  };

  function flashDjangoMessages() {
    var el = document.getElementById("dj-messages");
    if (!el) return;
    var messages;
    try { messages = JSON.parse(el.textContent); } catch (e) { return; }
    (messages || []).forEach(function (m) {
      var tags = m.level || "";
      var level = "info";
      if (tags.indexOf("success") !== -1) level = "success";
      else if (tags.indexOf("error") !== -1 || tags.indexOf("danger") !== -1) level = "error";
      else if (tags.indexOf("warning") !== -1) level = "warning";
      showToast(level, m.text);
    });
  }

  /* ------------------------------------------------------------------ *
   * Theme toggle
   * ------------------------------------------------------------------ */
  function currentTheme() {
    return document.documentElement.getAttribute("data-bs-theme") === "light" ? "light" : "dark";
  }

  function applyThemeIcon() {
    var icon = currentTheme() === "light" ? "bi-moon-stars" : "bi-sun";
    $("#theme-toggle i").attr("class", "bi " + icon);
  }

  function toggleTheme() {
    var next = currentTheme() === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-bs-theme", next);
    try { localStorage.setItem("ezops-theme", next); } catch (e) { /* ignore */ }
    applyThemeIcon();
    if (window.Chart) { styleCharts(); $(document).trigger("ezo:theme", [next]); }
  }

  /* ------------------------------------------------------------------ *
   * Sidebar
   * ------------------------------------------------------------------ */
  function initSidebar() {
    if (document.documentElement.classList.contains("boot-sidebar-collapsed")) {
      document.body.classList.add("sidebar-collapsed");
      document.documentElement.classList.remove("boot-sidebar-collapsed");
    }
    $("#sidebar-toggle").on("click", function () {
      if (window.matchMedia("(max-width: 991.98px)").matches) {
        document.body.classList.toggle("sidebar-open");
      } else {
        document.body.classList.toggle("sidebar-collapsed");
        try {
          localStorage.setItem(
            "ezops-sidebar",
            document.body.classList.contains("sidebar-collapsed") ? "collapsed" : "open"
          );
        } catch (e) { /* ignore */ }
      }
    });
    $("#sidebar-backdrop").on("click", function () {
      document.body.classList.remove("sidebar-open");
    });
    $(".ez-nav-link").on("click", function () {
      if (window.matchMedia("(max-width: 991.98px)").matches) {
        document.body.classList.remove("sidebar-open");
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * Notification bell
   * ------------------------------------------------------------------ */
  var FEED_URL = "/notifications/feed/";

  function renderFeed(data) {
    var $count = $("#notif-count");
    if (data.unread > 0) {
      $count.text(data.unread > 99 ? "99+" : data.unread).show();
    } else {
      $count.hide();
    }
    var $list = $("#notif-list");
    if (!$list.length) return;
    if (!data.items || !data.items.length) {
      $list.html(
        '<div class="notif-empty"><i class="bi bi-bell-slash d-block mb-2" style="font-size:1.6rem"></i>' +
        "You're all caught up.</div>"
      );
      return;
    }
    var html = data.items.map(function (n) {
      var target = n.url || "/notifications/";
      return (
        '<a class="notif-item d-block ' + (n.is_read ? "" : "unread") + '" href="' + EZO.escape(target) + '" ' +
        'data-notif-id="' + n.id + '">' +
          '<div class="flex-grow-1">' +
            '<div class="title">' + EZO.escape(n.title) + "</div>" +
            '<div class="meta">' + EZO.escape(n.created_at) + "</div>" +
          "</div>" +
        "</a>"
      );
    }).join("");
    $list.html(html);
  }

  function refreshFeed() {
    if (!document.getElementById("notif-bell")) return;
    $.getJSON(FEED_URL).done(renderFeed);
  }

  function initBell() {
    if (!document.getElementById("notif-bell")) return;
    refreshFeed();
    window.setInterval(refreshFeed, 60000);
    $(document).on("click", "#notif-mark-all", function (e) {
      e.preventDefault();
      EZO.post("/notifications/read-all/").done(function () {
        refreshFeed();
        toastr.success("All notifications marked as read.");
      });
    });
    $(document).on("click", ".notif-item[data-notif-id]", function () {
      var id = $(this).data("notif-id");
      // fire-and-forget: mark read before navigating
      navigator.sendBeacon && EZO.csrf()
        ? EZO.post("/notifications/" + id + "/read/")
        : null;
    });
  }

  /* ------------------------------------------------------------------ *
   * Confirm dialogs + auto-submit filters
   * ------------------------------------------------------------------ */
  function initConfirms() {
    $(document).on("submit", "form[data-confirm]", function (e) {
      var message = $(this).attr("data-confirm") || "Are you sure?";
      if (!window.confirm(message)) {
        e.preventDefault();
        return false;
      }
    });
  }

  function initAutoSubmit() {
    $(document).on("change", "form.js-auto-submit select, form.js-auto-submit input[type=date]", function () {
      $(this).closest("form").trigger("submit");
    });
  }

  /* System settings page: save a single setting via AJAX */
  function initSettingSave() {
    $(document).on("click", ".js-setting-save", function () {
      var $btn = $(this);
      var $row = $btn.closest("[data-setting-row]");
      var value = $row.find(".js-setting-input").val();
      $btn.prop("disabled", true);
      EZO.post(window.location.pathname, { key: $btn.data("key"), value: value })
        .done(function () { toastr.success("Setting saved."); })
        .fail(function (xhr) {
          var detail = "Could not save setting.";
          try { detail = xhr.responseJSON.error || detail; } catch (e) { /* ignore */ }
          toastr.error(detail);
        })
        .always(function () { $btn.prop("disabled", false); });
    });
  }

  /* ------------------------------------------------------------------ *
   * Charts
   * ------------------------------------------------------------------ */
  function chartColors() {
    var light = currentTheme() === "light";
    return {
      text: light ? "#64748b" : "#94a3b8",
      grid: light ? "rgba(15,23,42,.08)" : "rgba(148,163,184,.12)",
    };
  }

  function styleCharts() {
    if (!window.Chart) return;
    var c = chartColors();
    Chart.defaults.color = c.text;
    Chart.defaults.borderColor = c.grid;
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 11.5;
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    Object.values(Chart.instances || {}).forEach(function (instance) {
      instance.options.scales && Object.values(instance.options.scales).forEach(function (scale) {
        if (scale.ticks) scale.ticks.color = c.text;
        if (scale.grid) scale.grid.color = c.grid;
      });
      instance.update();
    });
  }

  /* Build charts from <canvas data-chart data-src="#json-id" data-type="bar"> */
  function initDeclarativeCharts() {
    if (!window.Chart) return;
    $("canvas[data-chart]").each(function () {
      var $canvas = $(this);
      var src = document.querySelector($canvas.attr("data-src"));
      if (!src) return;
      var payload;
      try { payload = JSON.parse(src.textContent); } catch (e) { return; }
      var type = $canvas.attr("data-type") || "bar";
      var horizontal = $canvas.attr("data-horizontal") === "1";
      var config = {
        type: type,
        data: {
          labels: payload.labels,
          datasets: [{
            label: $canvas.attr("data-label") || "Count",
            data: payload.values,
            backgroundColor: payload.colors || "rgba(99,102,241,.65)",
            borderColor: type === "line" ? "#6366f1" : "transparent",
            borderWidth: type === "line" ? 2 : 0,
            fill: type === "line" ? { target: "origin", above: "rgba(99,102,241,.12)" } : undefined,
            tension: 0.35,
            borderRadius: 6,
            pointRadius: type === "line" ? 2.5 : 0,
          }],
        },
        options: {
          indexAxis: horizontal ? "y" : "x",
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: type === "doughnut" || type === "pie" } },
          scales: (type === "doughnut" || type === "pie") ? {} : {
            x: { grid: { display: false } },
            y: { beginAtZero: true, ticks: { precision: 0 } },
          },
        },
      };
      if (type === "doughnut" || type === "pie") {
        config.options.cutout = type === "doughnut" ? "62%" : 0;
        config.options.plugins.legend.position = "bottom";
      }
      new Chart(this, config);
    });
  }

  EZO.styleCharts = styleCharts;

  /* ------------------------------------------------------------------ */
  $(function () {
    initSidebar();
    initBell();
    initConfirms();
    initAutoSubmit();
    initSettingSave();
    applyThemeIcon();
    $("#theme-toggle").on("click", toggleTheme);
    if (window.Chart) styleCharts();
    initDeclarativeCharts();
    flashDjangoMessages();
  });
})(jQuery);
