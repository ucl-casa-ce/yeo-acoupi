# Architectural Evaluation: Options 1, 3 & 5
### (Decoupled Jamstack vs. Real-Time MQTT vs. Unified Portainer Stack)

> **Context**: Evaluation of three primary candidate architectures for the next-generation environmental IoT platform (Bioacoustics, Weather, Soil Moisture, Urban Heat Island, and optional RTSP video). All options replace the current monolithic Streamlit application while maintaining **InfluxDB v2** for time-series persistence and **MQTT** for edge ingestion.

---

## 1. Executive Summary & Comparison

| Criterion | **Option 1: Decoupled Jamstack**<br>*(GitHub Pages + Portainer API)* | **Option 3: Real-Time Live Edge**<br>*(Browser MQTT over WSS)* | **Option 5: Unified Portainer Stack**<br>*(Nginx + FastAPI + Static + go2rtc)* |
| :--- | :--- | :--- | :--- |
| **Hosting Location** | **Split**: Frontend on GitHub Pages, API on Portainer | GitHub Pages or Portainer | **Single Server**: 100% inside Portainer |
| **Backend Runtime** | Lightweight API (FastAPI / Go) on Portainer | Direct broker/DB or minimal proxy | Multi-container stack (Nginx + FastAPI + optional `go2rtc`) |
| **Data Ingestion Model** | Client REST polling / Server-Sent Events | Sub-second MQTT pub/sub push | Client REST / SSE via local container network |
| **CORS & Domain Setup** | Requires cross-origin configuration | Requires broker WSS access & token scoping | **Zero CORS issues**: Single origin via Nginx reverse proxy |
| **Portainer RAM / CPU** | Minimal (~40–60 MB) | Negligible | Very Low (~80–120 MB for entire stack) |
| **Optional RTSP Video** | Requires separate streaming proxy URL | Requires external gateway | **Best**: Built-in `go2rtc` container on same internal Docker network |
| **UI Customization** | 100% unrestricted (Vite, React, Svelte, D3) | 100% unrestricted | 100% unrestricted |
| **Deployment Complexity** | Two deployment targets (GitHub + Portainer) | Low to Medium | **Single target**: One `compose.yaml` in Portainer |

---

## 2. Option 1: Decoupled Jamstack (GitHub Pages Frontend + Portainer Micro-API)

### 2.1 Architecture Diagram

```
[ Edge Sensors ] ──(MQTT)──> [ Mosquitto ] ──(Telegraf)──> [ InfluxDB v2 ]
                                                                 ▲
                                                                 │ Internal Flux Query
                                                                 ▼
[ User Web Browser ]                                   [ Portainer Container ]
       │                                                         │
       ├── 1. Load HTML/JS/CSS assets ─────────────────> [ GitHub Pages CDN ]
       │
       └── 2. Fetch JSON telemetry & S3 media URLs ────> [ FastAPI Gateway ]
                                                         (https://api.cetools.org)
                                                                 │
                                                                 ▼
                                                        [ MinIO / Silo S3 ]
```

### 2.2 How It Works
* **Frontend**: A Single Page Application (SPA) built with Vite (React, Svelte, or modern Vanilla JS) compiled to static assets and deployed to **GitHub Pages**.
* **Backend**: A small, stateless **FastAPI** service running in a Portainer container behind Nginx. It translates client requests into optimized Flux queries and generates presigned MinIO/S3 URLs.
* **Why Choose Option 1**:
  * Free, resilient global asset distribution via GitHub's CDN.
  * Completely offloads frontend static asset delivery from your Portainer host.
  * InfluxDB tokens and S3 credentials remain completely private within the Portainer container.

---

## 3. Option 3: Real-Time "Live Edge" (Browser MQTT over WebSockets + InfluxDB)

### 3.1 Architecture Diagram

```
[ Edge Sensors ]
       │
       ▼ (MQTT TCP :1883)
[ Mosquitto Broker ]
       │
       ├── Telegraf ──────────────────────────────────────────> [ InfluxDB v2 ]
       │                                                               ▲
       │ (MQTT over WebSockets :8083)                                  │
       │                                                        Historical Flux
       ▼ (Sub-second live pub/sub push)                         Queries (HTTP)
[ User Web Browser ] ──────────────────────────────────────────────────┘
       ▲
       └── 1. Initial page load (from GitHub Pages or Portainer Nginx)
```

### 3.2 How It Works
* **Live Streaming**: The Mosquitto broker exposes a WebSockets listener (`wss://mqtt.cetools.org:8083`). The browser uses `mqtt.js` to subscribe directly to `yeo/+/detection` and `yeo/+/weather`.
* **Historical Persistence**: For past trends (past 24h/7d), the browser queries InfluxDB v2 via its native HTTP REST API (`/api/v2/query`) with a read-only token, or through a thin proxy.
* **Why Choose Option 3**:
  * **Instantaneous responsiveness (<500ms)**: Weather dials twitch with wind gusts; detection badges flash the instant a bat or bird is classified.
  * Eliminates polling loops and periodic dashboard reloads.

---

## 4. Option 5: Unified Portainer Stack (Nginx + FastAPI + Static SPA + Streaming Gateway)

### 4.1 Architecture Diagram

```
                              [ Portainer Host (cetools) ]
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                        │
│                                [ Nginx Reverse Proxy ]                                 │
│                                (Port 80 / 443 with SSL)                                │
│                                            │                                           │
│               ┌────────────────────────────┼────────────────────────────┐              │
│               ▼                            ▼                            ▼              │
│      location / { ... }           location /api/ { ... }       location /stream/ {...} │
│               │                            │                            │              │
│      [ Static Web Assets ]         [ FastAPI Service ]             [ go2rtc ]          │
│      (Compiled Vite/React/Svelte   (Async InfluxDB Flux        (Transcodes RTSP to     │
│       HTML, JS, CSS, Leaflet)       & S3 Media Resolver)        WebRTC / HLS on-demand)│
│                                            │                            ▲              │
│                                            │                            │ RTSP         │
└────────────────────────────────────────────┼────────────────────────────┼──────────────┘
                                             │                            │
                                             ▼                            │
                                      [ InfluxDB v2 ]            [ IP / Trail Camera ]
```

### 4.2 How It Works
Option 5 packages the entire presentation and API layer into a **single, cohesive Docker Compose stack** managed entirely inside Portainer:
1. **Nginx Container**:
   * Serves pre-compiled static frontend assets directly with gzip compression and aggressive caching headers.
   * Reverse-proxies `/api/` requests internally to the FastAPI container (`http://fastapi:8000`).
   * Reverse-proxies `/stream/` to `go2rtc` (`http://go2rtc:1984`) whenever video is active.
   * Handles SSL/TLS termination under a single clean domain (e.g. `https://monitoring.cetools.org`).
2. **FastAPI Container**:
   * Pure data service. Handles InfluxDB Flux queries, taxonomy lookups, date walkings, and MinIO/S3 presigned URL creation.
   * Runs asynchronously with Python 3.11 (`uvicorn`).
3. **go2rtc Container (Lightweight Streaming Gateway)**:
   * Sits idle with negligible CPU/RAM usage (~15 MB) until an RTSP camera stream is requested.
   * When requested, dynamically pulls the RTSP H.264 feed and delivers ultra-low-latency **WebRTC** or **HLS** directly to an embeddable HTML5 video element.
   * If RTSP is postponed, this service is simply commented out in `compose.yaml` with zero changes required to the rest of the stack.

### 4.3 Why Option 5 is an Enormous Upgrade over Streamlit
* **Memory & Stability**: Replaces Streamlit's multi-process, memory-hungry architecture with an ultra-lightweight stack (Nginx + FastAPI uses ~80 MB RAM total vs. Streamlit's 400 MB–1 GB+ under concurrent loads).
* **Zero WebSocket Drops**: Eliminates the persistent WebSocket disconnects and timeout headaches typical of Streamlit behind reverse proxies.
* **Single Deployment Target**: Everything is deployed, updated, and monitored inside Portainer using one standard `compose.yaml` file.
* **Zero CORS Configuration**: Because Nginx routes both `/` and `/api/` under the same hostname, you never experience browser cross-origin blocking or complex domain header management.

---

## 5. Architectural Syntheses: How to Combine These Approaches

Notice that **Option 3 (Real-Time MQTT)** is a transport mechanism that can be layered onto either **Option 1** or **Option 5**:

### Synthesis A: "Live Jamstack" (Option 1 + Option 3)
* **Frontend**: GitHub Pages (Vite SPA).
* **Live Updates**: Browser connects directly to Mosquitto MQTT via WebSockets (`wss://`).
* **Historical & Media**: Browser calls Portainer FastAPI for InfluxDB summaries and S3 spectrograms.
* **Best When**: You want zero server hosting overhead for frontend assets and want to leverage GitHub's free global CDN.

### Synthesis B: "Unified Live Appliance" (Option 5 + Option 3)
* **Frontend & API**: Entirely self-contained in Portainer (Nginx + FastAPI + optional `go2rtc`).
* **Live Updates**: Browser connects to Mosquitto MQTT via WebSockets (`wss://`).
* **Best When**: You want a dedicated, professional single-domain deployment managed entirely within your institutional Docker/Portainer infrastructure, with ready-to-go RTSP video support.

---

## 6. Implementation Blueprints for Option 5

### 6.1 Unified `compose.yaml` (for Portainer)

```yaml
name: environmental-iot-platform

services:
  # 1. Reverse Proxy & Static Asset Server
  nginx:
    image: nginx:alpine
    container_name: iot-nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./frontend/dist:/usr/share/nginx/html:ro
    depends_on:
      - api

  # 2. High-Performance REST API Gateway
  api:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: iot-api
    restart: unless-stopped
    expose:
      - "8000"
    environment:
      - INFLUX_URL=http://128.40.193.25:8086
      - INFLUX_TOKEN=${INFLUX_TOKEN}
      - INFLUX_ORG=UCL-CE
      - INFLUX_BUCKET=CE_yeo
      - S3_ENDPOINT_URL=https://buckets.cetools.org
      - S3_ACCESS_KEY=${S3_ACCESS_KEY}
      - S3_SECRET_KEY=${S3_SECRET_KEY}
      - S3_BUCKET_NAME=yeo

  # 3. Optional RTSP-to-WebRTC Video Gateway (Nice-to-have)
  go2rtc:
    image: alexxit/go2rtc:latest
    container_name: iot-streamer
    restart: unless-stopped
    expose:
      - "1984" # API / WebRTC signaling
      - "8554" # RTSP proxy
    volumes:
      - ./go2rtc.yaml:/config/go2rtc.yaml:ro
```

### 6.2 Reverse Proxy Configuration (`nginx.conf`)

```nginx
events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml;

    server {
        listen 80;
        server_name localhost;

        # 1. Static Frontend Application
        location / {
            root /usr/share/nginx/html;
            try_files $uri $uri/ /index.html;
            expires 1h;
        }

        # 2. FastAPI Backend
        location /api/ {
            proxy_pass http://api:8000/api/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        }

        # 3. Optional Video Stream Gateway (WebRTC & HLS)
        location /stream/ {
            proxy_pass http://go2rtc:1984/;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
        }
    }
}
```

### 6.3 Minimal Video Configuration (`go2rtc.yaml`)

```yaml
streams:
  trailcam:
    - rtsp://admin:password@192.168.1.120:554/stream1

webrtc:
  listen: ":8555"
```

---

## 7. Strategic Decision Guide

* **Choose Option 1 (GitHub Pages + Portainer API)** if:
  * You want zero frontend maintenance on Portainer.
  * The site is heavily public-facing and benefits from global CDN caching.
  * RTSP video is definitively shelved or handled via an external link.

* **Choose Option 5 (Unified Portainer Stack)** if:
  * You prefer managing **one single deployment** in Portainer.
  * You want **zero CORS complications** between the web UI and InfluxDB/S3 data APIs.
  * You want to keep the door wide open for **RTSP Video Feeds** with zero structural changes (just enable the `go2rtc` container).

* **Add Option 3 (Real-Time MQTT WebSockets) to either** if:
  * You want live dials, instant bat/bird detection notifications, and sub-second field reactivity rather than interval polling.
