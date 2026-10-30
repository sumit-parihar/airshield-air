import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import plotly.graph_objects as go

# ------------------------
# PAGE CONFIG
# ------------------------
st.set_page_config(page_title="AirShield", layout="wide")

# ------------------------
# DARK THEME
# ------------------------
st.markdown("""
<style>

/* Hide sidebar */
[data-testid="stSidebar"] {
    display: none;
}

/* Remove top menu & footer */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

/* Dark theme */
html, body, [data-testid="stAppViewContainer"], .main {
    background-color: #0F172A !important;
    color: white !important;
}

/* Layout */
.block-container {
    padding-top: 2rem;
}

/* Cards */
.card {
    background: #1E293B;
    padding: 18px;
    border-radius: 12px;
    margin-bottom: 15px;
    border: 1px solid #334155;
}

/* Text */
.metric {
    font-size: 26px;
    font-weight: bold;
}

.label {
    color: #94A3B8;
    font-size: 13px;
}

.good { color: #10B981; }
.warning { color: #F59E0B; }
.critical { color: #EF4444; }

h1, h2, h3 {
    color: white !important;
}

</style>
""", unsafe_allow_html=True)

st.markdown("<h1>🌫️ AirShield Dashboard</h1>", unsafe_allow_html=True)

# ------------------------
# FIXED DATE: 10 MARCH 2026
# ------------------------
base_time = datetime(2026, 3, 10, 10, 0)

times = [base_time + timedelta(minutes=10*i) for i in range(7)]
time_labels = [t.strftime("%d %b %H:%M") for t in times]

# ------------------------
# SPIKE POSITIONS
# ------------------------
voc_spike_index = 4   # 10:40
pm_spike_index = 5    # 10:50

# ------------------------
# DATA
# ------------------------
co2 = [415, 420, 418, 425, 430, 428, 420]

pm25 = [10, 11, 12, 11, 12, 35, 12]   # clear spike at 10:50
voc = [120, 130, 140, 135, 400, 150, 140]  # spike at 10:40

# ------------------------
# ALERT TIMES
# ------------------------
alert1_time = times[voc_spike_index].strftime("%d %B %Y, %I:%M %p")
alert2_time = times[pm_spike_index].strftime("%d %B %Y, %I:%M %p")

# ------------------------
# GRAPH FUNCTION (FIXED)
# ------------------------
def create_chart(x, y, title, color, spike_index=None, threshold=None):
    fig = go.Figure()

    # Main line
    fig.add_trace(go.Scatter(
        x=x,
        y=y,
        mode='lines+markers',
        line=dict(color=color, width=4),
        marker=dict(size=6, color=color),
        name=title
    ))

    # Highlight spike
    if spike_index is not None:
        fig.add_trace(go.Scatter(
            x=[x[spike_index]],
            y=[y[spike_index]],
            mode='markers',
            marker=dict(size=14, color='red'),
            name='Spike'
        ))

    # Threshold line
    if threshold is not None:
        fig.add_hline(
            y=threshold,
            line_dash="dash",
            line_color="red",
            annotation_text="Threshold",
            annotation_position="top right"
        )

    # ✅ ALWAYS APPLY (THIS WAS YOUR BUG)
    fig.update_layout(
        title=dict(
            text=title,
            font=dict(size=20, color="white")
        ),
        plot_bgcolor="#1E293B",
        paper_bgcolor="#0F172A",
        font=dict(color="white"),
        margin=dict(l=20, r=20, t=40, b=20),
        xaxis=dict(showgrid=True, gridcolor="#334155"),
        yaxis=dict(showgrid=True, gridcolor="#334155")
    )

    return fig

# ------------------------
# LAYOUT
# ------------------------
left, right = st.columns([1,2])

# ------------------------
# LEFT PANEL
# ------------------------
with left:
    st.markdown("""
    <div class="card">
        <div class="label">Overall Air Quality</div>
        <div class="metric good">GOOD</div>
        <div class="label">All parameters are within healthy limits</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### Metrics")

    col1, col2 = st.columns(2)

    col1.markdown(f"""
    <div class="card">
        <div class="label">CO2 (ppm)</div>
        <div class="metric good">{co2[-1]}</div>
    </div>
    """, unsafe_allow_html=True)

    col2.markdown(f"""
    <div class="card">
        <div class="label">PM2.5 (µg/m³)</div>
        <div class="metric warning">{pm25[pm_spike_index]}</div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    col1.markdown(f"""
    <div class="card">
        <div class="label">VOC (ppb)</div>
        <div class="metric critical">{voc[voc_spike_index]}</div>
    </div>
    """, unsafe_allow_html=True)

    col2.markdown("""
    <div class="card">
        <div class="label">Temperature (°C)</div>
        <div class="metric">22.4</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="card">
        <div class="label">Humidity (%)</div>
        <div class="metric">45</div>
    </div>
    """, unsafe_allow_html=True)

# ------------------------
# RIGHT PANEL (GRAPHS)
# ------------------------
with right:
    st.plotly_chart(
        create_chart(time_labels, co2, "CO2 Levels", "#10B981"),
        use_container_width=True
    )

    st.plotly_chart(
        create_chart(time_labels, pm25, "PM2.5 Levels", "#F59E0B",
                     spike_index=pm_spike_index,
                     threshold=25),
        use_container_width=True
    )

    st.plotly_chart(
        create_chart(time_labels, voc, "VOC Levels", "#EF4444",
                     spike_index=voc_spike_index,
                     threshold=300),
        use_container_width=True
    )

    st.markdown("### Alerts")

    st.markdown(f"""
    <div class="card">
        ⚠️ VOC Level Spike Detected <br>
        <span class="label">{alert1_time}</span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="card">
        ❌ PM2.5 Exceeds Threshold <br>
        <span class="label">{alert2_time}</span>
    </div>
    """, unsafe_allow_html=True)