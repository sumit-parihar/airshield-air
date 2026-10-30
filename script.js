document.addEventListener('DOMContentLoaded', () => {
    // Shared chart options for dark theme
    Chart.defaults.color = '#94A3B8';
    Chart.defaults.font.family = "'Outfit', sans-serif";
    
    const commonOptions = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                display: false
            },
            tooltip: {
                backgroundColor: '#1E293B',
                titleColor: '#F8FAFC',
                bodyColor: '#F8FAFC',
                borderColor: '#334155',
                borderWidth: 1,
                padding: 10,
                displayColors: false,
                callbacks: {
                    label: function(context) {
                        return `${context.parsed.y}`;
                    }
                }
            }
        },
        scales: {
            x: {
                grid: {
                    color: 'rgba(51, 65, 85, 0.5)',
                    drawBorder: false,
                },
                ticks: {
                    maxTicksLimit: 6
                }
            },
            y: {
                grid: {
                    color: 'rgba(51, 65, 85, 0.5)',
                    drawBorder: false,
                },
                beginAtZero: false
            }
        },
        interaction: {
            intersect: false,
            mode: 'index',
        },
    };

    // CO2 Chart
    const co2Ctx = document.getElementById('co2Chart').getContext('2d');
    
    // Gradient for CO2
    const co2Gradient = co2Ctx.createLinearGradient(0, 0, 0, 400);
    co2Gradient.addColorStop(0, 'rgba(16, 185, 129, 0.5)');
    co2Gradient.addColorStop(1, 'rgba(16, 185, 129, 0.0)');

    new Chart(co2Ctx, {
        type: 'line',
        data: {
            labels: ['10:00', '10:10', '10:20', '10:30', '10:40', '10:50', '11:00'],
            datasets: [{
                label: 'CO2 Level',
                data: [415, 420, 418, 425, 430, 428, 420],
                borderColor: '#10B981',
                backgroundColor: co2Gradient,
                borderWidth: 2,
                pointBackgroundColor: '#10B981',
                pointBorderColor: '#fff',
                pointHoverBackgroundColor: '#fff',
                pointHoverBorderColor: '#10B981',
                pointRadius: 3,
                pointHoverRadius: 5,
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            ...commonOptions,
            plugins: {
                ...commonOptions.plugins,
                tooltip: {
                    ...commonOptions.plugins.tooltip,
                    callbacks: {
                        label: function(context) {
                            return `${context.parsed.y} ppm`;
                        }
                    }
                }
            }
        }
    });

    // PM2.5 Chart
    const pmCtx = document.getElementById('pm25Chart').getContext('2d');
    
    // Gradient for PM2.5
    const pmGradient = pmCtx.createLinearGradient(0, 0, 0, 400);
    pmGradient.addColorStop(0, 'rgba(56, 189, 248, 0.5)');
    pmGradient.addColorStop(1, 'rgba(56, 189, 248, 0.0)');

    new Chart(pmCtx, {
        type: 'line',
        data: {
            labels: ['10:00', '10:10', '10:20', '10:30', '10:40', '10:50', '11:00'],
            datasets: [{
                label: 'PM2.5 Level',
                data: [10.2, 11.5, 12.0, 11.8, 12.5, 13.1, 12.5],
                borderColor: '#38BDF8',
                backgroundColor: pmGradient,
                borderWidth: 2,
                pointBackgroundColor: '#38BDF8',
                pointBorderColor: '#fff',
                pointHoverBackgroundColor: '#fff',
                pointHoverBorderColor: '#38BDF8',
                pointRadius: 3,
                pointHoverRadius: 5,
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            ...commonOptions,
            plugins: {
                ...commonOptions.plugins,
                tooltip: {
                    ...commonOptions.plugins.tooltip,
                    callbacks: {
                        label: function(context) {
                            return `${context.parsed.y} µg/m³`;
                        }
                    }
                }
            }
        }
    });
});
