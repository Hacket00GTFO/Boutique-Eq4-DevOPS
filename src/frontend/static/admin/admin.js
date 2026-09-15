// Admin panel — chart init.
// Reads series from `data-values="1,2,3"` attributes rendered by Go templates:
//   - <canvas id="monthlySales">  → "Ventas del Mes" bar chart
//   - <canvas class="spark">      → stat-card sparklines (data-color optional)
// Requires Chart.js 4 (loaded via CDN in the page template).

(function () {
    'use strict';
    if (typeof Chart === 'undefined') return;

    function values(el) {
        return (el.dataset.values || '')
            .split(',')
            .map(function (v) { return parseFloat(v); })
            .filter(function (v) { return !isNaN(v); });
    }

    // Monthly sales bar chart (dashboard).
    var sales = document.getElementById('monthlySales');
    if (sales) {
        var data = values(sales);
        new Chart(sales, {
            type: 'bar',
            data: {
                labels: ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'].slice(0, data.length),
                datasets: [{
                    data: data,
                    backgroundColor: data.map(function (_, i) {
                        return i === data.length - 1 ? '#3a3835' : '#e4e1dc';
                    }),
                    borderRadius: 3,
                    borderSkipped: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { display: false }, ticks: { color: '#8a8680' } },
                    y: { display: false }
                }
            }
        });
    }

    // Sparklines in stat cards.
    document.querySelectorAll('canvas.spark').forEach(function (el) {
        var data = values(el);
        if (!data.length) return;
        new Chart(el, {
            type: 'line',
            data: {
                labels: data.map(function (_, i) { return i; }),
                datasets: [{
                    data: data,
                    borderColor: el.dataset.color || '#1a1a1a',
                    borderWidth: 1.6,
                    pointRadius: 0,
                    tension: 0.35,
                    fill: false
                }]
            },
            options: {
                responsive: false,
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                scales: { x: { display: false }, y: { display: false } },
                events: []
            }
        });
    });
})();
