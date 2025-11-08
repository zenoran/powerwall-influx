# Grafana Dashboard

The `powerwall-dashboard.json` file provides an importable Grafana dashboard for visualising the metrics written by this service.

## Import steps

1. Open **Grafana → Dashboards → New → Import**.
2. Click **Upload JSON file** and select `grafana/powerwall-dashboard.json` from this repository.
3. When prompted, choose the InfluxDB data source you already configured (Flux query language). This must be an InfluxDB 2.x connection.
4. Provide defaults for the dashboard variables if Grafana requests them:
   - **Bucket** – the Influx bucket where Powerwall metrics are stored (default `powerwall`).
   - **Measurement** – typically `powerwall` unless you customised it.
   - **Site** – the site tag value. The export ships with `Bzzzt` pre-selected based on the captured dataset—replace it with your own site tag if Grafana prompts.

## Dashboard contents

The dashboard is organised into several focus areas:

- **Battery State of Charge** – Stat panel summarising the current battery percentage.
- **Energy Remaining** – Gauge derived from the remaining vs. full pack energy to show available energy (%).
- **Alert Count** – Highlights how many alerts are currently active.
- **Latest Alert** – Compact stat that displays the most recent non-empty alert payload (falls back to “No active alerts” when the field is empty).
- **Power Flows** – Time series of site, solar, battery, and load power (Watts) plotted together for quick comparisons.
- **Solar Generation, Home Load, Grid Power, Battery Power** – Dedicated time-series panels for each major power stream so you can drill into individual trends without the overlay.
- **PV String Power & PV String Current** – Multi-series charts that plot every string’s power (W) and current (A) simultaneously.
- **String Status** – Table summarising each string’s PV state label and whether the inverter reports the string as connected (`string_string{letter}_connected`).

Each panel uses Flux queries with the selected bucket/measurement/site, so they adjust automatically if you change variables at the top of the dashboard. The dashboard refreshes every 30 seconds by default.

## Customising

- Adjust the refresh cadence in Grafana if you prefer faster or slower updates.
- Duplicate panels or add new ones using the same query patterns to highlight additional metrics (for example, MQTT availability or temperature fields if you extend the service).
- Tailor the power panels’ window period or legend settings to highlight the trends you care about (for example, switch to area mode for solar generation).
- If you rename the measurement or bucket in the service configuration, update the defaults in the dashboard variables so the schema helpers keep returning the correct options.
