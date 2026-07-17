/* Dashboard widgets: charts + AJAX range refresh. */
(function ($) {
  "use strict";

  var charts = {};

  function readData() {
    var el = document.getElementById("dashboard-data");
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  function buildStatusChart(data) {
    var ctx = document.getElementById("chart-status");
    if (!ctx) return;
    var rows = data.by_status.filter(function (r) { return r.count > 0; });
    if (!rows.length) rows = data.by_status;
    charts.status = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: rows.map(function (r) { return r.label; }),
        datasets: [{ data: rows.map(function (r) { return r.count; }),
                     backgroundColor: rows.map(function (r) { return r.color; }),
                     borderWidth: 0 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "64%",
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  function buildPriorityChart(data) {
    var ctx = document.getElementById("chart-priority");
    if (!ctx) return;
    charts.priority = new Chart(ctx, {
      type: "bar",
      data: {
        labels: data.by_priority.map(function (r) { return r.label; }),
        datasets: [{ data: data.by_priority.map(function (r) { return r.count; }),
                     backgroundColor: data.by_priority.map(function (r) { return r.color; }),
                     borderRadius: 8, maxBarThickness: 46 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  function buildTrendChart(data) {
    var ctx = document.getElementById("chart-trend");
    if (!ctx) return;
    charts.trend = new Chart(ctx, {
      type: "line",
      data: {
        labels: data.weekly_trend.map(function (r) { return r.week; }),
        datasets: [
          {
            label: "Completed",
            data: data.weekly_trend.map(function (r) { return r.completed; }),
            borderColor: "#22c55e", backgroundColor: "rgba(34,197,94,.15)",
            fill: true, tension: 0.35, pointRadius: 2.5, borderWidth: 2,
          },
          {
            label: "Created",
            data: data.weekly_trend.map(function (r) { return r.created; }),
            borderColor: "#6366f1", backgroundColor: "rgba(99,102,241,.12)",
            fill: true, tension: 0.35, pointRadius: 2.5, borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: "bottom" } },
        scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  function updateCharts(data) {
    if (charts.status) {
      var rows = data.by_status.filter(function (r) { return r.count > 0; });
      if (!rows.length) rows = data.by_status;
      charts.status.data.labels = rows.map(function (r) { return r.label; });
      charts.status.data.datasets[0].data = rows.map(function (r) { return r.count; });
      charts.status.data.datasets[0].backgroundColor = rows.map(function (r) { return r.color; });
      charts.status.update();
    }
    if (charts.priority) {
      charts.priority.data.datasets[0].data = data.by_priority.map(function (r) { return r.count; });
      charts.priority.update();
    }
    if (charts.trend) {
      charts.trend.data.labels = data.weekly_trend.map(function (r) { return r.week; });
      charts.trend.data.datasets[0].data = data.weekly_trend.map(function (r) { return r.completed; });
      charts.trend.data.datasets[1].data = data.weekly_trend.map(function (r) { return r.created; });
      charts.trend.update();
    }
  }

  function updateStats(stats) {
    Object.keys(stats).forEach(function (key) {
      var el = document.querySelector('[data-stat="' + key + '"]');
      if (el) el.textContent = stats[key];
    });
  }

  function setLoading(loading) {
    $("#dashboard-widgets").toggleClass("opacity-50", loading);
    $("#range-select").prop("disabled", loading);
  }

  function refresh(days) {
    setLoading(true);
    $.getJSON("/dashboard/data/", { range: days })
      .done(function (data) {
        updateStats(data.stats);
        updateCharts(data);
        $("[data-range-label]").text(days);
      })
      .fail(function () { toastr.error("Could not refresh dashboard data."); })
      .always(function () { setLoading(false); });
  }

  $(function () {
    var data = readData();
    if (!data || !window.Chart) return;
    buildStatusChart(data);
    buildPriorityChart(data);
    buildTrendChart(data);
    $("#range-select").on("change", function () { refresh(this.value); });
    $(document).on("ezo:theme", function () {
      /* Chart.defaults were re-styled; instances already updated by app.js */
    });
  });
})(jQuery);
