# AGENTS.md — Environmental & Bioacoustic IoT Platform Guide

> **Context & Purpose**: This document synthesizes all architectural designs, production learnings, data pipelines, hardware constraints, UI patterns, and operational gotchas from the **Yeo Bioacoustic Monitoring Platform** (`yeo-acoupi`). It is tailored for AI agents and developers building the next-generation environmental monitoring system, which expands from bioacoustics (bats & birds) to multi-modal sensor networks: **weather stations, soil moisture arrays, urban heat island (UHI) mapping, and live RTSP video feeds**.

---

## 1. System Architecture Overview

The platform uses an edge-to-cloud decoupled architecture designed for off-grid, low-power, and bandwidth-constrained IoT deployments:

```
[ Edge Sensors & Micro-controllers ]
  │
  ├── Bioacoustic Nodes (Arduino UNO Q / AudioMoth / BatDetect2 & BirdNET)
  ├── Weather Stations (Campbell / Davis / Modbus / SDI-12 / I2C)
  ├── Soil Moisture Probes (Multi-depth Capacitive / TDR)
  ├── Microclimate / UHI Nodes (SHT4x / BME280 / PT100)
  └── IP / Trail Cameras (RTSP H.264/H.265 video streams)
          │
          │ Telemetry & Metadata (LoRaWAN / Cellular 4G / Wi-Fi)
          ▼
[ Ingestion & Transport Layer ]
  ├── Gateway (MultiTech Conduit with built-in Network Server / Packet Forwarder)
  ├── MQTT Broker (`mqtt.cetools.org:1883`)
  ├── Telegraf Ingestion Daemon (`json_v2` topic & payload parsing)
  └── RTSP Streaming Gateway (`go2rtc` / `mediamtx` WebRTC/HLS proxy)
          │
          ├── High-frequency metrics & telemetry
          ▼
[ Storage Layer ]
  ├── Time-Series Database: **InfluxDB v2** (`http://...:8086`, Bucket: `CE_yeo` / multi-bucket)
  └── Media / Object Storage: **MinIO / Silo S3** (`https://buckets.cetools.org`, Bucket: `yeo`)
          │
          ▼
[ Presentation & Application Layer ]
  ├── Application Server: Python 3.11 + **Streamlit** (interactive analytics & map explorer)
  ├── Data Visualizations: Plotly Express / Graph Objects, PyDeck, Leaflet
  ├── Audio Engine: Web Audio API (custom time-expansion ultrasonic bat player)
  ├── Reverse Proxy: **Nginx** (WebSocket upgrade handling & gzip compression)
  └── Orchestration: Docker Compose / Portainer on CETools infrastructure
```

---

## 2. Proven Learnings from Yeo Bioacoustics (`yeo-acoupi`)

### 2.1 The Two-Tier Data Paradigm (Edge Metadata vs. Heavy Media)
* **Never send raw audio over cellular or LoRaWAN.** 192 kHz 16-bit uncompressed audio generates ~1.4 GB/hour (~33 GB/day per node). Sending this over cellular drains solar batteries and explodes SIM data tariffs.
* **Inference at the Edge**: Models (**BatDetect2** for ultrasound, **BirdNET** for acoustic birds) run directly on edge nodes (e.g. Qualcomm Linux MPU on Arduino UNO Q).
* **Only detection metadata is transmitted live**:
  - Species ID / numeric classification code (`s`)
  - Confidence score (`c`, 0.0 – 1.0)
  - Detection timestamp (`t`)
  - System health (CPU, memory, storage, temperature)
* **Audio & Spectrograms spooled locally**: Recordings and pre-generated spectrogram PNGs are saved to on-board high-endurance SD cards (~1 TB) or synced asynchronously over high-bandwidth links (Wi-Fi / S3 batch uploads).

### 2.2 S3 Media Structure & Zero-Padding Gotchas
* S3 hierarchy convention:
  ```
  <device-folder>/<taxa-spectrogram-type>/<year>/<month>/<day>/<filename>.png
  <device-folder>/<taxa-audio-type>/<year>/<month>/<day>/<filename>.wav
  ```
  Example: `YV14-recordings/bat-spect/2026/08/26/batdetect2_20260826_213540_uuid.png`
* **Zero-padding trap**: Different firmware versions or upload scripts may format date paths with or without leading zeros (e.g., `2026/8/4/` vs `2026/08/04/`).
* **Rule**: S3 listing routines must query *both* unpadded and zero-padded prefixes before reporting a date as empty.

### 2.3 Offline Time-Correlation (Matching S3 Recordings to InfluxDB)
* InfluxDB detection telemetry and S3 media files do not share an explicit database foreign key.
* Correlation is achieved via a **sliding time window** (tolerance $\pm 4$ to $5$ seconds):
  1. Extract UTC timestamp from S3 media filename (regex: `r"(\d{8})_(\d{6})"`).
  2. Query InfluxDB telemetry within `[media_time - tolerance, media_time + tolerance]` filtered by device ID.
  3. Enrich the media object with scientific name, common name, confidence score, and taxonomy group.

### 2.4 Bat Ultrasonic Audio Playback (Crucial Domain Physics)
* Bats echolocate at **20 kHz – 120 kHz**, well beyond the human hearing range (20 Hz – 20 kHz).
* **Browser Trap**: Standard HTML5 `<audio>` elements preserve pitch when changing `playbackRate`. Slowing audio down via browser defaults keeps the high frequencies ultrasonic, resulting in total silence!
* **Solution**: Use the **Web Audio API** with pitch preservation explicitly disabled:
  ```javascript
  const audioContext = new (window.AudioContext || window.webkitAudioContext)();
  const source = audioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.playbackRate.value = 0.1; // 10x slowdown: drops 40 kHz down to audible 4 kHz
  source.connect(audioContext.destination);
  source.start(0);
  ```

### 2.5 Multi-Tier Device Identity & Aliasing
* Real hardware transitions across multiple identifiers during development:
  1. Hardware MAC address (`14:b5:cd:e9:a9:c7`)
  2. Linux hostname (`yeo-unoq-14`)
  3. Formal deployment tag (`UNOQ-14-YV`)
  4. S3 storage folder (`YV14-recordings`)
  5. LoRaWAN DevEUI / MQTT topic (`yeo/acoupi/unoq-14/detection`)
* **Rule**: Centralize an explicit lookup map (`DEVICE_NAME_OVERRIDES`, `DEVICE_TO_S3_FOLDER`, `DEPLOYED_METADATA`) to prevent UI fragmentation. Always sort device lists using a **natural sort algorithm** (so `UNOQ-2` precedes `UNOQ-10`).

---

## 3. Communication Links & Bandwidth Budgets

### 3.1 LoRaWAN (EU868)
* **Duty Cycle Constraint**: Under EU868 regulations, maximum duty cycle is 1% (or 0.1% on sub-bands). At Spreading Factor 12 (SF12), a single 20-byte uplink can require an airtime of ~1.3 seconds, mandating a mandatory **~131-second off-air cooldown**.
* **Payload Compaction**: Bit-pack fields into tight binary structs (e.g., 3 bytes: `uint16` species ID + `uint8` confidence percentage). Field decoding is done downstream in Telegraf or ingestion workers.
* **Gateway Architecture**: MultiTech Conduit can run in **Packet Forwarder** mode (feeds TTN / Loriot) or **Built-in Network Server** mode (local Mosquitto MQTT bridge). *These two modes are mutually exclusive gateway-wide.*

### 3.2 Cellular (4G LTE-M / NB-IoT / Cat-1)
* **Connection Overhead Dominance**: Analysis shows **~92% of hourly cellular traffic is TCP/TLS and handshake overhead**, not the sensor payload itself.
* **Cadence Rules**:
  - Hourly transmissions: ~109 hours/year radio uptime.
  - Daily batch push: ~4.6 hours/year radio uptime, saving ~157 MB/year per node.
* **Hardware Gotchas (A7670E & SIMs)**:
  - ModemManager on Linux hosts will lock the AT command serial port. Disable ModemManager or filter the device via udev.
  - APN Mismatch: Mismatched APN (e.g. `giffgaff.com` vs `key`) results in `AT+CEREG? -> 0,3` (Data Registration Denied).
  - SIM Tariffs: For IoT data volumes (<50 MB/year), 1NCE (€12 for 500 MB over 10 years) is ~30× cheaper than recurring monthly consumer SIM rentals.

---

## 4. Multi-Modal Extension: Architectural Patterns for the New Site

The new website will integrate four additional telemetry streams alongside bioacoustics. Below is the blueprint for each stream:

### 4.1 Weather Station Data
* **Parameters**: Ambient Air Temp (°C), Relative Humidity (%), Atmospheric Pressure (hPa), Wind Speed (m/s), Wind Direction (0–360°), Solar Irradiance ($W/m^2$), Rainfall (mm / tipping bucket count).
* **Cadence**: 1-minute to 15-minute averaged intervals.
* **InfluxDB Schema**:
  ```influxql
  Measurement: weather_telemetry
  Tags: device_id, station_model, site_location
  Fields: temp_c (float), humidity_pct (float), pressure_hpa (float), wind_speed_mps (float), wind_dir_deg (float), solar_radiation_wm2 (float), rain_mm (float)
  ```
* **Dashboard Widgets**:
  - Polar Wind Rose (Plotly `Barpolar`) showing speed distribution by cardinal direction.
  - Solar Irradiance vs. Ambient Temp dual-axis time series.
  - Cumulative daily / weekly rainfall bar charts.

### 4.2 Soil Moisture & Subsurface Profiling
* **Parameters**: Volumetric Water Content (VWC % at depths e.g. 10cm, 30cm, 50cm, 100cm), Soil Temperature (°C), Bulk Electrical Conductivity (EC in mS/cm).
* **Sensor Types**: SDI-12 or RS-485 Modbus multi-depth capacitive or TDR/FDR probes.
* **Cadence**: 15 to 60 minutes (soil dynamics change slowly).
* **InfluxDB Schema**:
  ```influxql
  Measurement: soil_telemetry
  Tags: device_id, site_location, probe_id
  Fields: vwc_10cm (float), vwc_30cm (float), vwc_50cm (float), temp_soil_10cm (float), ec_bulk (float)
  ```
* **Dashboard Widgets**:
  - Depth Profile Contour / Heatmap (depth vs. time with VWC % color-coded).
  - Field capacity and permanent wilting point indicator thresholds.

### 4.3 Urban Heat Island (UHI) & Microclimate Transects
* **Parameters**: Air Temperature ($T_a$), Surface Radiometric Temp ($T_s$), Relative Humidity ($RH$), Vapor Pressure Deficit ($VPD$).
* **Core Concept**: UHI is calculated as the differential against an unurbanized rural baseline reference station:
  $$\Delta T_{UHI} = T_{urban\_sensor} - T_{rural\_reference}$$
* **InfluxDB Schema**:
  ```influxql
  Measurement: uhi_microclimate
  Tags: device_id, zone_type (urban, park, canopy, water, baseline), surface_cover
  Fields: temp_c (float), delta_uhi_c (float), humidity_pct (float), heat_index_c (float)
  ```
* **Dashboard Widgets**:
  - Interactive Spatial Heatmap: PyDeck with Mapbox / Carto tiles rendering a `HexagonLayer` or `GridLayer` interpolated across sensors.
  - Diurnal UHI Intensity Curve: Hourly line chart showing peak daytime vs. nocturnal heat trapping.

### 4.4 Live RTSP Video Feeds (The Browser Challenge & Solution)
* **The Problem**: Web browsers **cannot natively decode RTSP streams** (`rtsp://...`). Direct `<video src="rtsp://...">` will fail in all modern browsers.
* **The Solution**: Deploy a dedicated lightweight streaming gateway container (such as **`go2rtc`** or **`mediamtx`**) alongside the application:
  ```
  [ RTSP Camera / Trailcam ]
             │ RTSP (H.264 / AAC)
             ▼
  [ go2rtc / mediamtx Gateway Container ]
             │ Converts on-the-fly to low-latency WebRTC, MSE, or HLS
             ▼
  [ Streamlit / Web Client UI ]
             └─ Embed via WebRTC iframe or custom Streamlit HTML component
  ```
* **Edge & Power Rules for Cameras**:
  - Continuous streaming over 4G consumes **0.5 GB – 2 GB per hour**.
  - Off-grid / solar nodes should **never stream 24/7**. Implement on-demand streaming (starts stream only when a user views the dashboard) or snapshot-only mode (uploading 1 frame per minute).

---

## 5. Ingestion Pipeline & Telegraf Configurations

Telegraf subscribes to MQTT and writes normalized data directly to InfluxDB v2:

```toml
# sample-telegraf.conf excerpt for multi-modal ingestion
[[inputs.mqtt_consumer]]
  servers = ["tcp://mqtt.cetools.org:1883"]
  topics = [
    "yeo/+/detection",        # Bioacoustic detections
    "yeo/+/heartbeat",        # System hardware telemetry
    "yeo/+/weather",          # Weather station metrics
    "yeo/+/soil",             # Soil moisture sensors
    "yeo/+/uhi"               # Urban heat island nodes
  ]
  data_format = "json_v2"

  # Dynamic tag extraction from topic: yeo/<device_id>/<measurement_type>
  [[inputs.mqtt_consumer.topic_parsing]]
    topic = "yeo/+/+"
    tags = "_/device_id/measurement_type"

  [[inputs.mqtt_consumer.json_v2]]
    measurement_name_path = "measurement_type"

    # Support compacted payloads (c=confidence, s=species, t=unix_time)
    [[inputs.mqtt_consumer.json_v2.object]]
      path = "d"
      optional = true
      disable_prepend_keys = true
      tags = ["s"]
      [inputs.mqtt_consumer.json_v2.object.fields]
        confidence = "c"

    # Support standard weather fields
    [[inputs.mqtt_consumer.json_v2.object]]
      path = "@this"
      optional = true
      [inputs.mqtt_consumer.json_v2.object.fields]
        temp_c = "temp"
        humidity_pct = "humidity"
        soil_moisture = "moisture"
        solar_radiation = "solar"
```

---

## 6. Streamlit Dashboard Architecture & Best Practices

### 6.1 Caching Strategy
* Use `@st.cache_resource` for connection clients that must persist across reruns:
  ```python
  @st.cache_resource
  def get_influx_client(url, token, org):
      return InfluxDBClient(url=url, token=token, org=org, timeout=30_000)
  ```
* Use `@st.cache_data(ttl=...)` for data queries to avoid query bombardment during filter adjustments:
  - Device listings / deployments: `ttl=300` (5 minutes)
  - Detection / sensor queries: `ttl=120` (2 minutes)
  - Reference species catalogs: static / no TTL

### 6.2 Uptime & Reliability Visualization
* Field IoT sensors experience intermittent dropouts due to solar depletion or signal attenuation.
* Render a 24-hour $\times$ Day **Operational Matrix** (Plotly `Heatmap`) showing green ($1 = \text{online}$) vs. red ($0 = \text{offline}$) for every single hour to provide instant diagnostic health at a glance.

### 6.3 Responsive Plotly & PyDeck Layouts
* Always set `use_container_width=True` on `st.plotly_chart` and `pdk.Deck`.
* Position Plotly legends horizontally above or below the plot (`orientation="h", yanchor="bottom", y=1.02`) so mobile screens do not truncate charts.

---

## 7. Deployment, Docker & DevOps

### 7.1 Secrets Management Precedence
Code must look up secrets in this hierarchical order to ensure local dev and Portainer production both work seamlessly:
1. `st.secrets` (via `.streamlit/secrets.toml`)
2. `os.environ.get("STREAMLIT_<KEY>")`
3. `os.environ.get("<KEY>")`
4. Safe fallback / exception handling

### 7.2 Dockerfile Gotchas
* **Secrets Stub**: Streamlit will crash if `.streamlit/secrets.toml` is referenced but missing. Always add:
  ```dockerfile
  RUN mkdir -p .streamlit && touch .streamlit/secrets.toml
  ```
* **Source Copying**: Never do `COPY yeo-vis.py .` alone. Multi-file modular applications need:
  ```dockerfile
  COPY *.py .
  COPY data/ ./data/
  ```

### 7.3 Nginx Reverse Proxy & WebSockets (Mandatory)
Streamlit relies entirely on persistent WebSockets (`/_stcore/stream`). Nginx reverse proxies **must** include:
```nginx
location / {
    proxy_pass http://streamlit:8501;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Streamlit WebSocket Upgrade
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    # Prevent drops during long idle or streaming sessions
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
}
```

### 7.4 CI/CD Workflow
* Pushing to `main` triggers GitHub Actions (`docker-publish.yml`).
* Image builds for `linux/amd64` and pushes to GitHub Container Registry: `ghcr.io/<org>/<repo>:latest`.
* In Portainer: Pull updated image $\rightarrow$ Recreate container with existing environment variables.

---

## 8. Common Pitfalls & Troubleshooting Playbook

| Issue / Symptom | Root Cause | Solution |
| :--- | :--- | :--- |
| **Silent bat audio on playback** | Standard HTML `<audio>` preserves pitch when speed is changed. | Use Web Audio API without pitch correction (`playbackRate.value = 0.1`). |
| **Cellular modem registration denied (`0,3`)** | Incorrect APN configured in SIM context profile. | Run `AT+CGDCONT=1,"IP","<correct_apn>"` followed by `AT+CFUN=1,1`. |
| **Streamlit reloads constantly / disconnects** | Nginx or Cloudflare dropping idle WebSocket connection. | Ensure `Upgrade $http_upgrade`, `Connection "upgrade"`, and `proxy_read_timeout 86400` are set. |
| **Missing S3 dates in Explorer** | Unpadded vs. zero-padded directory names (`2026/8/5/` vs `2026/08/05/`). | Check both folder prefix variations during S3 pagination. |
| **High cellular bills on IoT SIMs** | Connecting and handshaking every minute. | Batch payloads and transmit once per hour or once per day. |
| **RTSP stream fails in browser** | Modern browsers lack native RTSP decoding. | Transcode via `go2rtc` or `mediamtx` to WebRTC or HLS. |
| **Phantom / retired devices polluting graphs** | InfluxDB stores historical series indefinitely. | Run `db-cleaner.py` using Flux delete API filtered by `device_id`. |
| **Microphone sample rate mismatch** | PipeWire falling back to 44.1/48 kHz default source instead of 192 kHz. | Specify the exact full PipeWire node name in `program.conf`, not a friendly label. |
