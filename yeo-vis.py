import os
import re
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
from influxdb_client import InfluxDBClient
from astral import LocationInfo
from astral.sun import sun
from datetime import datetime, timedelta
import warnings

warnings.simplefilter("ignore")

def natural_sort_key(s):
    """Sort strings with embedded numbers naturally (e.g. unoq-2 before unoq-10)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]

# --- DEPLOYED DEVICES SPECIFICATION ---
DEPLOYED_NAMES = {"UNOQ-10-YV", "UNOQ-11-YV", "UNOQ-12-YV"}

# --- DEVICE NAME OVERRIDES / ALIASES ---
# Map MAC addresses or hardware IDs to friendly names shown across the dashboard
DEVICE_NAME_OVERRIDES = {
    "14:b5:cd:ea:9c:27": "UNOQ-4-allotment-cellular",
    "14:b5:cd:ea:f9:f5": "UNOQ-2-allotment-wifi",
    "14:b5:cd:ea:12:1f": "UNOQ-6-garden-lab",
    "a8-40-41-4a-65-5d-11-3c": "UNOQ-3-garden-lab-LA66",
    "a8:40:41:6a:c9:5d:11:3d": "UNOQ-7-lab-LA66"
}

def is_deployed(dev_id, deployments_map):
    """Checks if a device ID or friendly name belongs to the deployed set."""
    if dev_id in deployments_map and deployments_map[dev_id].get("name") in DEPLOYED_NAMES:
        return True
    if dev_id in DEPLOYED_NAMES:
        return True
    return False

# --- CONFIGURATION & SECRETS ---
st.set_page_config(
    page_title="Yeo Acoupi Bioacoustic Dashboard",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded"
)

def get_secret(key):
    """Retrieve configuration secrets with multi-tier fallback."""
    if key in st.secrets:
        return st.secrets[key]
    if f"STREAMLIT_{key}" in os.environ:
        return os.environ[f"STREAMLIT_{key}"]
    if key in os.environ:
        return os.environ[key]
    raise KeyError(f"Could not find secret '{key}' in Streamlit secrets or environment variables.")

try:
    URL = get_secret("INFLUX_URL")
    TOKEN = get_secret("INFLUX_TOKEN")
    ORG = get_secret("INFLUX_ORG")
    BUCKET = get_secret("INFLUX_BUCKET")
except Exception as err:
    st.error(f"Configuration Error: {err}")
    st.stop()

# Connect to InfluxDB
@st.cache_resource
def get_influx_client(url, token, org):
    return InfluxDBClient(url=url, token=token, org=org, timeout=30000)

client = get_influx_client(URL, TOKEN, ORG)
query_api = client.query_api()

# --- SPECIES REFERENCE DATABASE ---
@st.cache_data
def load_species_database():
    """
    Loads bird and bat reference species lists from CSV files.
    Builds comprehensive lookup dictionaries by numeric species_id, class_name, scientific name, and common name.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    bird_paths = [
        os.path.join(base_dir, "data", "yeo_valley_bird_species_list.csv"),
        os.path.join(base_dir, "yeo_valley_bird_species_list.csv"),
        "/Users/dunc/Library/CloudStorage/Dropbox/code/CASA/yeo/src/acoupi_yeo_valley/yeo_valley_bird_species_list.csv"
    ]
    bat_paths = [
        os.path.join(base_dir, "data", "yeo_valley_bat_species_list.csv"),
        os.path.join(base_dir, "yeo_valley_bat_species_list.csv"),
        "/Users/dunc/Library/CloudStorage/Dropbox/code/CASA/yeo/src/acoupi_yeo_valley/yeo_valley_bat_species_list.csv"
    ]
    
    bird_df = None
    for p in bird_paths:
        if os.path.exists(p):
            try:
                bird_df = pd.read_csv(p)
                bird_df["category"] = "Bird"
                break
            except Exception:
                pass
                
    if bird_df is None:
        try:
            url = "https://raw.githubusercontent.com/djdunc/acoupi-yeo-valley/main/src/acoupi_yeo_valley/yeo_valley_bird_species_list.csv"
            bird_df = pd.read_csv(url)
            bird_df["category"] = "Bird"
        except Exception:
            bird_df = pd.DataFrame(columns=["species_id", "class_name", "common_name", "species"])
            bird_df["category"] = "Bird"
            
    bat_df = None
    for p in bat_paths:
        if os.path.exists(p):
            try:
                bat_df = pd.read_csv(p)
                bat_df["category"] = "Bat"
                break
            except Exception:
                pass
                
    if bat_df is None:
        try:
            url = "https://raw.githubusercontent.com/djdunc/acoupi-yeo-valley/main/src/acoupi_yeo_valley/yeo_valley_bat_species_list.csv"
            bat_df = pd.read_csv(url)
            bat_df["category"] = "Bat"
        except Exception:
            bat_df = pd.DataFrame(columns=["species_id", "class_name", "common_name", "species"])
            bat_df["category"] = "Bat"
            
    combined = pd.concat([bird_df, bat_df], ignore_index=True)
    
    id_map = {}
    class_map = {}
    species_map = {}
    common_map = {}
    
    def normalize_str(s):
        if not s:
            return ""
        return str(s).strip().lower().replace("’", "'").replace("-", " ")
    
    for _, row in combined.iterrows():
        sid = str(row.get("species_id", "")).strip()
        cname = str(row.get("common_name", "")).strip()
        sname = str(row.get("species", "")).strip()
        cls = str(row.get("class_name", "")).strip()
        cat = str(row.get("category", "Unknown")).strip()
        
        meta = {
            "common_name": cname if cname else sname if sname else cls,
            "species": sname if sname else cname,
            "class_name": cls,
            "category": cat
        }
        
        if sid:
            id_map[sid] = meta
            try:
                id_map[str(int(float(sid)))] = meta
            except (ValueError, TypeError):
                pass
                
        if cls:
            class_map[cls.lower()] = meta
            class_map[normalize_str(cls)] = meta
            if "_" in cls:
                parts = cls.split("_", 1)
                species_map[parts[0].lower()] = meta
                species_map[normalize_str(parts[0])] = meta
                common_map[parts[1].lower()] = meta
                common_map[normalize_str(parts[1])] = meta
                
        if sname:
            species_map[sname.lower()] = meta
            species_map[normalize_str(sname)] = meta
            
        if cname:
            common_map[cname.lower()] = meta
            common_map[normalize_str(cname)] = meta
            
    return combined, id_map, class_map, species_map, common_map

_, id_map, class_map, species_map, common_map = load_species_database()

def resolve_species(val):
    """
    Translates raw species strings or numeric IDs into common name, scientific name, and category.
    """
    if pd.isna(val) or val is None:
        return {"common_name": "Unknown", "species": "Unknown", "category": "Unknown"}
        
    val_str = str(val).strip()
    if not val_str or val_str.lower() in ("nan", "none", "null", "<na>"):
        return {"common_name": "Unknown", "species": "Unknown", "category": "Unknown"}
        
    # 1. Check numeric ID match (e.g. '83' -> Robin, '220' -> Soprano pipistrelle)
    if val_str in id_map:
        return id_map[val_str]
    try:
        int_key = str(int(float(val_str)))
        if int_key in id_map:
            return id_map[int_key]
    except (ValueError, TypeError):
        pass
        
    val_lower = val_str.lower()
    norm_val = val_lower.replace("’", "'").replace("-", " ")
    
    # 2. Check class name match (e.g. 'barbar' -> Barbastelle)
    if val_lower in class_map:
        return class_map[val_lower]
    if norm_val in class_map:
        return class_map[norm_val]
        
    # 3. Check common name match (e.g. 'Robin' -> European Robin, 'Common pipistrelle')
    if val_lower in common_map:
        return common_map[val_lower]
    if norm_val in common_map:
        return common_map[norm_val]
        
    # 4. Check scientific species name match (e.g. 'pipistrellus pipistrellus' -> Common pipistrelle)
    if val_lower in species_map:
        return species_map[val_lower]
    if norm_val in species_map:
        return species_map[norm_val]
        
    # 5. Check underscore separated class format (e.g. 'Accipiter nisus_Eurasian Sparrowhawk')
    if "_" in val_str:
        parts = val_str.split("_", 1)
        if len(parts) == 2 and parts[1].strip():
            cname = parts[1].strip()
            sname = parts[0].strip()
            # Check if either part matches our database
            if cname.lower() in common_map:
                return common_map[cname.lower()]
            if sname.lower() in species_map:
                return species_map[sname.lower()]
            return {
                "common_name": cname,
                "species": sname,
                "category": "Bird"
            }
            
    # Default fallback for unmapped strings / environmental sounds
    return {
        "common_name": val_str,
        "species": val_str,
        "category": "Other"
    }

# --- DISCOVER KNOWN DEVICES & DEPLOYMENTS ---
@st.cache_data(ttl=120)
def fetch_device_deployments():
    """
    Fetches the mapping of device_id to latest deployment details (name, lat, lon).
    Returns a dict: {device_id: {"name": name, "latitude": lat, "longitude": lon, "version": version}}
    """
    q = f'''
    from(bucket: "{BUCKET}")
      |> range(start: -30d)
      |> filter(fn: (r) => r["_measurement"] == "acoupi_deployment")
      |> last()
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    mapping = {}
    try:
        res = query_api.query_data_frame(q)
        if isinstance(res, list):
            res = pd.concat(res, ignore_index=True) if len(res) > 0 else pd.DataFrame()
        if not res.empty and "device_id" in res.columns:
            if "_time" in res.columns:
                res = res.sort_values("_time", ascending=False)
            for _, row in res.iterrows():
                dev_id = str(row["device_id"]).strip()
                if dev_id not in mapping:
                    mapping[dev_id] = {
                        "name": str(row.get("name", dev_id)).strip(),
                        "latitude": float(row["latitude"]) if pd.notna(row.get("latitude")) else None,
                        "longitude": float(row["longitude"]) if pd.notna(row.get("longitude")) else None,
                        "version": str(row.get("version", "")).strip() if pd.notna(row.get("version")) else None
                    }
    except Exception:
        pass

    # Apply manual device name overrides / aliases
    for dev_id, override_name in DEVICE_NAME_OVERRIDES.items():
        if dev_id in mapping:
            if override_name:
                mapping[dev_id]["name"] = override_name
        else:
            mapping[dev_id] = {
                "name": override_name,
                "latitude": None,
                "longitude": None,
                "version": None
            }

    return mapping

@st.cache_data(ttl=120)
def fetch_known_devices():
    """Fetches unique device identifiers with active data from InfluxDB."""
    q_dev = f'''
    from(bucket: "{BUCKET}")
      |> range(start: -30d)
      |> filter(fn: (r) => r["_measurement"] == "acoupi_detections" or r["_measurement"] == "acoupi_data" or r["_measurement"] == "acoupi_heartbeat" or r["_measurement"] == "acoupi_deployment")
      |> keep(columns: ["device_id"])
      |> group()
      |> distinct(column: "device_id")
    '''
    q_top = f'''
    from(bucket: "{BUCKET}")
      |> range(start: -30d)
      |> filter(fn: (r) => r["_measurement"] == "acoupi_detections" or r["_measurement"] == "acoupi_data" or r["_measurement"] == "acoupi_heartbeat" or r["_measurement"] == "acoupi_deployment")
      |> keep(columns: ["topic"])
      |> group()
      |> distinct(column: "topic")
    '''
    devs = set()
    try:
        res_dev = query_api.query_data_frame(q_dev)
        if isinstance(res_dev, list):
            res_dev = pd.concat(res_dev, ignore_index=True) if len(res_dev) > 0 else pd.DataFrame()
        if not res_dev.empty:
            col = "device_id" if "device_id" in res_dev.columns else ("_value" if "_value" in res_dev.columns else None)
            if col:
                for d in res_dev[col].dropna().unique():
                    d_str = str(d).strip()
                    if d_str:
                        devs.add(d_str)
    except Exception:
        pass

    try:
        res_top = query_api.query_data_frame(q_top)
        if isinstance(res_top, list):
            res_top = pd.concat(res_top, ignore_index=True) if len(res_top) > 0 else pd.DataFrame()
        if not res_top.empty:
            col = "topic" if "topic" in res_top.columns else ("_value" if "_value" in res_top.columns else None)
            if col:
                for t in res_top[col].dropna().unique():
                    t_str = str(t).strip()
                    parts = t_str.split("/")
                    if len(parts) >= 3 and parts[0] == "yeo" and parts[1] == "acoupi":
                        devs.add(parts[2])
                    elif len(parts) >= 2 and parts[0] == "yeo":
                        devs.add(parts[1])
    except Exception:
        pass
        
    cleaned = []
    for d in devs:
        if d.lower() not in ("nan", "none", "null", "unknown", ""):
            cleaned.append(d)
    if cleaned:
        return sorted(cleaned, key=natural_sort_key)
    return ["uno4-cellular", "unoq-2", "unoq-4-cellular", "unoq-bat"]

known_devices = fetch_known_devices()
deployments = fetch_device_deployments()

# Merge devices from deployment map
for d in deployments.keys():
    if d not in known_devices and d.lower() not in ("nan", "none", "null", "unknown", ""):
        known_devices.append(d)
known_devices = sorted(list(set(known_devices)), key=natural_sort_key)

# --- SIDEBAR NAVIGATION ---
st.sidebar.markdown("## Devices")
page = st.sidebar.radio(
    "Select:",
    ["Deployed Devices", "Test / Setup Devices", "Info + Map"],
    index=0
)

# Determine active devices based on current page
if page == "Deployed Devices":
    active_devices = [d for d in known_devices if is_deployed(d, deployments)]
elif page == "Test / Setup Devices":
    active_devices = [d for d in known_devices if not is_deployed(d, deployments)]
else:
    active_devices = known_devices

# --- SIDEBAR CONTROLS ---
if page != "Info + Map":
    st.sidebar.markdown("## Dashboard Controls")

    # 1. Device Selection Dropdown (Placed ABOVE Time Selector)
    device_display_options = {}
    display_to_id = {"All Devices": "All Devices"}

    for dev_id in active_devices:
        if dev_id in deployments and deployments[dev_id]["name"]:
            disp_name = f"{deployments[dev_id]['name']} ({dev_id})"
        else:
            disp_name = dev_id
        device_display_options[dev_id] = disp_name
        display_to_id[disp_name] = dev_id

    # Sort active devices naturally by friendly display name (alphabetical + numerical)
    sorted_display_names = sorted(
        [device_display_options[d] for d in active_devices if d in device_display_options],
        key=natural_sort_key
    )
    device_options = ["All Devices"] + sorted_display_names

    # Retain previously selected display or default
    current_device_selection = st.session_state.get("device_select_key", "All Devices")
    device_idx = device_options.index(current_device_selection) if current_device_selection in device_options else 0

    selected_display = st.sidebar.selectbox(
        "Device Selection",
        options=device_options,
        index=device_idx,
        key="device_select_key"
    )
    selected_device = display_to_id.get(selected_display, selected_display)

    # 2. Time Window Selector
    time_options = {
        "Last 24 Hours": "-24h",
        "Last 2 Days": "-2d",
        "Last 7 Days": "-7d",
        "Last 14 Days": "-14d",
        "Last 30 Days": "-30d"
    }
    time_labels = list(time_options.keys())

    current_time_selection = st.session_state.get("time_window_key", "Last 7 Days")
    time_idx = time_labels.index(current_time_selection) if current_time_selection in time_labels else 2

    selected_time_label = st.sidebar.selectbox(
        "Time Window",
        options=time_labels,
        index=time_idx,
        key="time_window_key"
    )
    selected_time_val = time_options[selected_time_label]

    # 3. Confidence Threshold
    confidence_threshold = st.sidebar.slider(
        "Confidence Threshold",
        min_value=0.0,
        max_value=1.0,
        value=float(st.session_state.get("conf_threshold_key", 0.50)),
        step=0.05,
        key="conf_threshold_key",
        help="Filter detections below this model confidence score"
    )

    # 4. Taxa / Category Filter
    category_options = ["All Species (Birds & Bats)", "Birds Only 🐦", "Bats Only 🦇", "All Detections (incl. Other)"]
    current_cat_selection = st.session_state.get("category_filter_key", "All Species (Birds & Bats)")
    cat_idx = category_options.index(current_cat_selection) if current_cat_selection in category_options else 0

    category_filter = st.sidebar.selectbox(
        "Taxa / Category Filter",
        options=category_options,
        index=cat_idx,
        key="category_filter_key"
    )

    st.sidebar.divider()
else:
    # Fallback default values for variables to avoid UnboundLocalError when skipping controls
    selected_device = "All Devices"
    selected_display = "All Devices"
    selected_time_val = "-7d"
    selected_time_label = "Last 7 Days"
    confidence_threshold = 0.50
    category_filter = "All Species (Birds & Bats)"

# --- HEARTBEAT & SYSTEM TELEMETRY QUERY ---
@st.cache_data(ttl=60)
def fetch_heartbeat_data():
    hb_query = f'''
    from(bucket: "{BUCKET}")
      |> range(start: -24h)
      |> filter(fn: (r) => r["_measurement"] == "acoupi_heartbeat")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    try:
        hb_df = query_api.query_data_frame(hb_query)
        if isinstance(hb_df, list):
            hb_df = pd.concat(hb_df, ignore_index=True) if len(hb_df) > 0 else pd.DataFrame()
        return hb_df
    except Exception:
        return pd.DataFrame()

hb_df = pd.DataFrame()
if page != "Info + Map":
    hb_df = fetch_heartbeat_data()

# Process Heartbeat Data
active_devices_count = 0

def resolve_device_id(row):
    dev = row.get("device_id")
    if pd.notna(dev) and str(dev).strip() and str(dev).strip().lower() not in ("nan", "none", "null", "unknown"):
        return str(dev).strip()
    topic = row.get("topic")
    if pd.notna(topic) and isinstance(topic, str):
        parts = topic.split("/")
        if len(parts) >= 3 and parts[0] == "yeo" and parts[1] == "acoupi":
            return parts[2]
        if len(parts) >= 2 and parts[0] == "yeo":
            return parts[1]
    return "unknown"

if page != "Info + Map" and not hb_df.empty:
    hb_df["Device"] = hb_df.apply(resolve_device_id, axis=1)
    hb_df = hb_df[hb_df["Device"].isin(active_devices)]
    
    if not hb_df.empty:
        # Ensure standard telemetry fields exist
        for col in ["shm", "mem_free", "cpu", "mem_used"]:
            if col not in hb_df.columns:
                hb_df[col] = np.nan

        # Scale/fill compact telemetry fields if present
        if "s" in hb_df.columns:
            hb_df["shm"] = hb_df["shm"].fillna(hb_df["s"] / (1024 * 1024))
        if "m" in hb_df.columns:
            hb_df["mem_free"] = hb_df["mem_free"].fillna(hb_df["m"] / (1024 * 1024))
        if "q" in hb_df.columns:
            hb_df["cpu"] = hb_df["cpu"].fillna(hb_df["q"])
        if "t" in hb_df.columns:
            hb_df["mem_used"] = hb_df["mem_used"].fillna(hb_df["t"])
        
        if "_time" in hb_df.columns:
            hb_df["_time"] = pd.to_datetime(hb_df["_time"])
            latest_hb = hb_df.sort_values("_time", ascending=False).groupby("Device").first().reset_index()
            now_utc = pd.Timestamp.now(tz="UTC")
            
            st.sidebar.markdown("### Device Status (24h)")
            
            # Helper to get friendly name for sorting and display
            def get_friendly_name(dev_id):
                if dev_id in deployments and deployments[dev_id].get("name"):
                    return deployments[dev_id]["name"]
                return dev_id

            hb_rows = latest_hb.to_dict("records")
            hb_rows.sort(key=lambda r: natural_sort_key(get_friendly_name(r.get("Device", ""))))

            for dev_row in hb_rows:
                dev_id = dev_row["Device"]
                friendly_name = get_friendly_name(dev_id)
                last_seen = dev_row["_time"]
                diff_hours = (now_utc - last_seen).total_seconds() / 3600.0
                
                if diff_hours <= 2:
                    status_icon = "🟢"
                    status_text = "Online"
                elif diff_hours <= 12:
                    status_icon = "🟡"
                    status_text = "Stale"
                else:
                    status_icon = "🔴"
                    status_text = "Offline"
                    
                time_str = last_seen.strftime("%H:%M:%S (%d %b)")
                st.sidebar.markdown(f"**{status_icon} `{friendly_name}`**: {status_text}  \n<small>Last: {time_str}</small>", unsafe_allow_html=True)
                active_devices_count += 1
                
            spark_df = hb_df.set_index("_time").resample("1h").size().reset_index(name="Pulses")
            st.sidebar.markdown("**Heartbeats / Hour**")
            st.sidebar.line_chart(spark_df, x="_time", y="Pulses", height=130)
    else:
        st.sidebar.warning("No heartbeat data in the last 24h")
else:
    if page != "Info + Map":
        st.sidebar.warning("No heartbeat data in the last 24h")

# --- DETECTIONS QUERY ---
@st.cache_data(ttl=60)
def fetch_detection_data(time_val):
    flux_query = f'''
    from(bucket: "{BUCKET}")
      |> range(start: {time_val})
      |> filter(fn: (r) => r["_measurement"] == "acoupi_detections" or r["_measurement"] == "acoupi_data")
      |> filter(fn: (r) => r["_field"] == "c" or r["_field"] == "confidence" or r["_field"] == "confidence_score" or r["_field"] == "detection_score" or r["_field"] == "value" or r["_field"] == "s" or r["_field"] == "label" or r["_field"] == "species")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    try:
        df = query_api.query_data_frame(flux_query)
        if isinstance(df, list):
            df = pd.concat(df, ignore_index=True) if len(df) > 0 else pd.DataFrame()
        return df
    except Exception:
        return pd.DataFrame()

def render_sensor_map(loc_df, key_suffix=""):
    """Renders a Leaflet.js map with Satellite / Street toggle and auto-fit zoom."""
    if loc_df.empty:
        st.info("No active device locations mapped in current view.")
        return

    # Normalize column names for the Leaflet script
    plot_df = loc_df.copy()
    if "Device Name" not in plot_df.columns and "Name" in plot_df.columns:
        plot_df["Device Name"] = plot_df["Name"]
    if "Device ID / MAC" not in plot_df.columns and "Device ID" in plot_df.columns:
        plot_df["Device ID / MAC"] = plot_df["Device ID"]

    # Convert to JSON for JS consumption
    markers_json = plot_df[["Device Name", "Device ID / MAC", "latitude", "longitude"]].to_json(orient="records")

    # Generate Leaflet HTML
    leaflet_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
        <style>
            html, body, #map {{
                width: 100%;
                height: 100%;
                margin: 0;
                padding: 0;
            }}
        </style>
        <!-- Matomo -->
        <script>
            var _paq = window._paq = window._paq || [];
            /* tracker methods like "setCustomDimension" should be called before "trackPageView" */
            _paq.push(['trackPageView']);
            _paq.push(['enableLinkTracking']);
            (function() {{
                var u="https://matomo.cetools.org/";
                _paq.push(['setTrackerUrl', u+'matomo.php']);
                _paq.push(['setSiteId', '9']);
                var d=document, g=d.createElement('script'), s=d.getElementsByTagName('script')[0];
                g.async=true; g.src=u+'matomo.js'; s.parentNode.insertBefore(g,s);
            }})();
        </script>
        <noscript><p><img referrerpolicy="no-referrer-when-downgrade" src="https://matomo.cetools.org/matomo.php?idsite=9&amp;rec=1" style="border:0;" alt="" /></p></noscript>
        <!-- End Matomo Code -->
    </head>
    <body>
        <div id="map"></div>
        <script>
            // Initialize map with a fallback center
            var map = L.map('map').setView([51.32, -2.71], 11);

            // Esri World Imagery (Satellite)
            var satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
                attribution: 'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
                maxZoom: 19
            }});

            // OpenStreetMap (Streets)
            var streets = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                maxZoom: 19
            }});

            // Default to satellite basemap
            satellite.addTo(map);

            var baseMaps = {{
                "Satellite View": satellite,
                "Standard Map": streets
            }};

            L.control.layers(baseMaps).addTo(map);

            // Load and plot markers
            var markersData = {markers_json};
            var bounds = [];

            markersData.forEach(function(m) {{
                var lat = m.latitude;
                var lon = m.longitude;
                if (lat !== null && lon !== null) {{
                    var marker = L.marker([lat, lon]).addTo(map);
                    var popupContent = "<b>" + m["Device Name"] + "</b><br>ID: " + m["Device ID / MAC"];
                    marker.bindPopup(popupContent);
                    bounds.push([lat, lon]);
                }}
            }});

            // Use IntersectionObserver to fit bounds once the map container becomes visible in the DOM
            var mapEl = document.getElementById('map');
            var observer = new IntersectionObserver(function(entries) {{
                entries.forEach(function(entry) {{
                    if (entry.isIntersecting) {{
                        map.invalidateSize();
                        if (bounds.length > 0) {{
                            map.fitBounds(bounds, {{ padding: [30, 30] }});
                        }}
                        // Trigger only once on first visibility
                        observer.unobserve(entry.target);
                    }}
                }});
            }}, {{ threshold: 0.1 }});

            observer.observe(mapEl);
        </script>
    </body>
    </html>
    """

    # Render in Streamlit using the HTML component
    st.components.v1.html(leaflet_html, height=500)

raw_df = pd.DataFrame()
if page != "Info + Map":
    raw_df = fetch_detection_data(selected_time_val)

# --- HEADER TITLE ---
st.title("🌿 Yeo Valley Bioacoustic Monitoring")

if page == "Info + Map":
    st.markdown("### About the Project")
    st.markdown("""
    This project monitors bioacoustic activity across the Yeo Valley estate. Smart bioacoustic monitoring nodes (**Acoupi Uno Q**) capture ambient recordings in the field, process them using BirdNET models, and stream detection metadata and system telemetry in real-time over Wi-Fi, LoraWAN and Cellular networks to a central InfluxDB database.

    The monitoring nodes are classified into two groups:
    * **Deployed Devices**: Officially deployed nodes actively recording biodiversity across specific sectors.
    * **Test / Setup Devices**: Protoype or temporary nodes undergoing testing, calibration, or setup before field installation.
    """)

    # Generate Map and List of Deployed Devices
    loc_data = []
    for dev_id, info in deployments.items():
        if is_deployed(dev_id, deployments):
            if info["latitude"] is not None and info["longitude"] is not None:
                loc_data.append({
                    "Device Name": info["name"],
                    "Device ID / MAC": dev_id,
                    "latitude": info["latitude"],
                    "longitude": info["longitude"],
                    "Firmware Version": info["version"] or "Unknown"
                })

    if loc_data:
        loc_data.sort(key=lambda x: natural_sort_key(x["Device Name"]))
        st.markdown("### Deployed Device Locations")
        loc_df = pd.DataFrame(loc_data)
        render_sensor_map(loc_df, key_suffix="info_map")

        st.markdown("### Deployed Nodes Details")
        cols = st.columns(3)
        for idx, row in loc_df.iterrows():
            col_idx = idx % 3
            with cols[col_idx]:
                st.markdown(f"#### {row['Device Name']}")
                
                # Try finding the image in data/assets/img/
                img_filename = f"{row['Device Name']}.jpeg"
                img_path = os.path.join(os.path.dirname(__file__), "data", "assets", "img", img_filename)
                
                if os.path.exists(img_path):
                    st.image(img_path, width="stretch")
                else:
                    st.caption("*(No deployment image available)*")
                
                maps_url = f"https://www.google.com/maps/search/?api=1&query={row['latitude']},{row['longitude']}"
                st.markdown(f"**MAC Address:** `{row['Device ID / MAC']}`  \n**Firmware:** `{row['Firmware Version']}`  \n**Coordinates:** [{row['latitude']:.5f}, {row['longitude']:.5f}]({maps_url})")
    else:
        st.info("No deployed device locations found in InfluxDB metadata.")

    st.divider()
    st.caption("Page developed by UCL in collaboration with Yeo Valley.  \nAny errors or issues with this page please contact Duncan Wilson (d.j.wilson@ucl.ac.uk)")
    st.stop()

else:
    st.markdown("Real-time bioacoustic detection & telemetry dashboard powered by **Acoupi Uno Q** & **InfluxDB**.")

# --- DATA PROCESSING & RESOLUTION ---
df = pd.DataFrame()
has_raw_data = False

if not raw_df.empty:
    df = raw_df.copy()
    
    # 1. Format Time
    if "_time" in df.columns:
        df["Time"] = pd.to_datetime(df["_time"])
    elif "Time" in df.columns:
        df["Time"] = pd.to_datetime(df["Time"])
    else:
        df["Time"] = pd.NaT

    df = df.dropna(subset=["Time"])

    # 2. Extract Device
    df["Device"] = df.apply(resolve_device_id, axis=1)
    df = df[df["Device"].isin(active_devices)]
    df["Device_Friendly"] = df["Device"].apply(
        lambda d: f"{deployments[d]['name']} ({d})" if d in deployments and deployments[d]["name"] else d
    )

    # 3. Extract Confidence Score
    confidence_series = pd.Series(index=df.index, dtype="float64")
    for col in ["c", "confidence", "confidence_score", "detection_score"]:
        if col in df.columns:
            confidence_series = confidence_series.fillna(pd.to_numeric(df[col], errors="coerce"))
    df["Confidence"] = confidence_series

    # 4. Extract and Resolve Species Identity
    species_raw = pd.Series(index=df.index, dtype="object")
    for col in ["s", "label", "species", "value"]:
        if col in df.columns:
            valid_vals = df[col].astype(str).replace({"nan": None, "None": None, "": None, "<NA>": None})
            species_raw = species_raw.fillna(valid_vals)

    resolved_meta = [resolve_species(v) for v in species_raw]
    df["Common_Name"] = [m["common_name"] for m in resolved_meta]
    df["Scientific_Name"] = [m["species"] for m in resolved_meta]
    df["Category"] = [m["category"] for m in resolved_meta]

    # Drop records missing confidence or common name
    df = df.dropna(subset=["Confidence", "Common_Name"])
    if not df.empty:
        has_raw_data = True

# 5. Apply Device Filter
if has_raw_data and selected_device != "All Devices":
    match_mask = (df["Device"] == selected_device)
    if "device_id" in df.columns:
        match_mask = match_mask | (df["device_id"].astype(str) == selected_device)
    if "topic" in df.columns:
        match_mask = match_mask | (df["topic"].astype(str).str.contains(selected_device, na=False))
    if "Device_Friendly" in df.columns:
        match_mask = match_mask | (df["Device_Friendly"] == selected_display)
    df = df[match_mask]

# 6. Apply Taxa Filter
if has_raw_data:
    if category_filter == "Birds Only 🐦":
        df = df[df["Category"] == "Bird"]
    elif category_filter == "Bats Only 🦇":
        df = df[df["Category"] == "Bat"]
    elif category_filter == "All Species (Birds & Bats)":
        df = df[df["Category"].isin(["Bird", "Bat"])]

# 7. Apply Confidence Threshold Filter
high_conf_df = df[df["Confidence"] >= confidence_threshold] if not df.empty else pd.DataFrame()

# --- KPI METRICS ROW ---
col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)

total_detections = len(high_conf_df)
unique_species = high_conf_df["Common_Name"].nunique() if not high_conf_df.empty else 0
top_species = high_conf_df["Common_Name"].mode().iloc[0] if not high_conf_df.empty else "None"
avg_confidence = f"{high_conf_df['Confidence'].mean():.1%}" if not high_conf_df.empty else "N/A"
reporting_nodes = df["Device"].nunique() if not df.empty else 0

col_m1.metric("High-Conf Detections", f"{total_detections:,}")
col_m2.metric("Species Identified", f"{unique_species}")
col_m3.metric("Top Species", f"{top_species}")
col_m4.metric("Avg Confidence", f"{avg_confidence}")
col_m5.metric("Active Nodes", f"{reporting_nodes}")

st.divider()

if not has_raw_data:
    st.warning(f"No acoustic detection data found for the selected time range ({selected_time_label}).")
elif high_conf_df.empty:
    st.info(
        f"ℹ️ No detections match the current selection: **{selected_display}** | **{category_filter}** | Confidence ≥ **{confidence_threshold:.2f}**. "
        "Try lowering the confidence threshold or selecting 'All Devices' / 'All Species'."
    )
else:
    # --- TIMELINE VISUALIZATION (FULL WIDTH) ---
    st.subheader(f"Species Activity Timeline (Confidence ≥ {confidence_threshold:.2f})")

    # Limit to top 15 species for timeline visual clarity
    top_timeline_species = high_conf_df["Common_Name"].value_counts().nlargest(15).index.tolist()
    plot_df = high_conf_df[high_conf_df["Common_Name"].isin(top_timeline_species)].copy()

    species_order = plot_df["Common_Name"].value_counts().index.tolist()

    fig_timeline = px.scatter(
        plot_df,
        x="Time",
        y="Common_Name",
        color="Category",
        size="Confidence",
        size_max=9,
        opacity=0.8,
        category_orders={"Common_Name": species_order},
        color_discrete_map={"Bird": "#2b8a3e", "Bat": "#5f3dc4", "Other": "#868e96"},
        hover_data={
            "Time": "|%Y-%m-%d %H:%M:%S",
            "Common_Name": True,
            "Scientific_Name": True,
            "Confidence": ":.2f",
            "Device_Friendly": True,
            "Category": True
        },
        labels={"Device_Friendly": "Device"},
        template="plotly_white",
        height=420
    )

    # Solar Calculations for selected device or Yeo Valley
    loc_lat, loc_lon = 51.32, -2.71
    loc_name = "Yeo Valley"
    if selected_device != "All Devices" and selected_device in deployments:
        dev_info = deployments[selected_device]
        if dev_info["latitude"] is not None and dev_info["longitude"] is not None:
            loc_lat = dev_info["latitude"]
            loc_lon = dev_info["longitude"]
            loc_name = dev_info["name"]

    loc = LocationInfo(loc_name, "UK", "Europe/London", loc_lat, loc_lon)
    min_date = plot_df["Time"].min().date() - timedelta(days=1)
    max_date = plot_df["Time"].max().date() + timedelta(days=1)

    curr_date = min_date
    while curr_date <= max_date:
        try:
            s_curr = sun(loc.observer, date=curr_date)
            s_next = sun(loc.observer, date=curr_date + timedelta(days=1))
            kwargs = {
                "x0": s_curr["sunset"],
                "x1": s_next["sunrise"],
                "fillcolor": "rgba(30, 41, 59, 0.08)",
                "opacity": 1,
                "layer": "below",
                "line_width": 0,
            }
            if curr_date == min_date + timedelta(days=1):
                kwargs["annotation_text"] = "Night"
                kwargs["annotation_position"] = "top left"
            fig_timeline.add_vrect(**kwargs)
        except Exception:
            pass
        curr_date += timedelta(days=1)

    x_min = plot_df["Time"].min() - pd.Timedelta(hours=1)
    x_max = plot_df["Time"].max() + pd.Timedelta(hours=1)
    fig_timeline.update_xaxes(range=[x_min, x_max], title="Timestamp (UTC)")
    fig_timeline.update_yaxes(title="Common Name")
    fig_timeline.update_layout(
        margin=dict(l=0, r=0, t=20, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )

    st.plotly_chart(fig_timeline, use_container_width=True)

    st.divider()

    # --- SPECIES LEADERBOARD (FULL PANEL WIDTH) ---
    st.subheader("Species Leaderboard")
    leaderboard = high_conf_df.groupby(["Common_Name", "Scientific_Name", "Category"]).agg(
        Count=("Common_Name", "count"),
        Max_Confidence=("Confidence", "max"),
        Avg_Confidence=("Confidence", "mean")
    ).sort_values(by="Count", ascending=False).reset_index()

    leaderboard["Max_Confidence"] = leaderboard["Max_Confidence"].apply(lambda x: f"{x:.2f}")
    leaderboard["Avg_Confidence"] = leaderboard["Avg_Confidence"].apply(lambda x: f"{x:.2f}")

    st.dataframe(
        leaderboard,
        column_config={
            "Common_Name": st.column_config.TextColumn("Common Name", width="medium"),
            "Category": st.column_config.TextColumn("Category", width="small"),
            "Scientific_Name": st.column_config.TextColumn("Scientific Name", width="medium"),
            "Count": st.column_config.ProgressColumn(
                "Detections",
                format="%d",
                min_value=0,
                max_value=int(leaderboard["Count"].max()) if not leaderboard.empty else 100
            ),
            "Max_Confidence": st.column_config.TextColumn("Max Conf", width="small"),
            "Avg_Confidence": st.column_config.TextColumn("Avg Conf", width="small"),
        },
        hide_index=True,
        use_container_width=True,
        height=400
    )

    st.divider()

    # --- DIURNAL & NOCTURNAL ACTIVITY PROFILE (FULL PANEL WIDTH) ---
    st.subheader("Diurnal & Nocturnal Activity Profile (24-Hour Distribution)")
    dist_df = high_conf_df.copy()
    dist_df["Hour"] = dist_df["Time"].dt.hour

    hourly_counts = dist_df.groupby(["Hour", "Category"]).size().reset_index(name="Count")

    # Ensure all 24 hours (0-23) are represented
    all_hours_df = pd.DataFrame({"Hour": list(range(24))})
    categories_present = hourly_counts["Category"].unique() if not hourly_counts.empty else ["Bird", "Bat"]
    full_grid = []
    for h in range(24):
        for cat in categories_present:
            full_grid.append({"Hour": h, "Category": cat})
    full_grid_df = pd.DataFrame(full_grid)
    hourly_merged = pd.merge(full_grid_df, hourly_counts, on=["Hour", "Category"], how="left").fillna(0)

    fig_hourly = px.bar(
        hourly_merged,
        x="Hour",
        y="Count",
        color="Category",
        color_discrete_map={"Bird": "#2b8a3e", "Bat": "#5f3dc4", "Other": "#868e96"},
        barmode="stack",
        template="plotly_white",
        labels={"Hour": "Hour of Day (00:00 - 23:00 UTC)", "Count": "Detections Count"},
        height=400
    )
    fig_hourly.update_layout(
        margin=dict(l=0, r=0, t=20, b=10),
        xaxis=dict(tickmode="linear", tick0=0, dtick=1),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig_hourly, use_container_width=True)

    st.divider()

# --- RECENT DETECTIONS & TELEMETRY TABS ---
tab_recent, tab_telemetry, tab_map, tab_species_ref = st.tabs(["🕒 Recent Detections Feed", "📊 Hardware & System Telemetry", "📍 Map View", "📖 Species Reference List"])

with tab_recent:
    st.subheader("Latest Recorded Detections")
    if not high_conf_df.empty:
        recent = high_conf_df.sort_values("Time", ascending=False).head(50)[
            ["Time", "Device_Friendly", "Common_Name", "Scientific_Name", "Category", "Confidence"]
        ].copy()
        
        recent["Formatted_Time"] = recent["Time"].dt.strftime("%Y-%m-%d %H:%M:%S")
        recent_display = recent[["Formatted_Time", "Device_Friendly", "Common_Name", "Scientific_Name", "Category", "Confidence"]]
        
        st.dataframe(
            recent_display,
            column_config={
                "Formatted_Time": st.column_config.TextColumn("Timestamp (UTC)"),
                "Device_Friendly": st.column_config.TextColumn("Device Node"),
                "Common_Name": st.column_config.TextColumn("Common Name"),
                "Scientific_Name": st.column_config.TextColumn("Scientific Name"),
                "Category": st.column_config.TextColumn("Category"),
                "Confidence": st.column_config.NumberColumn("Confidence", format="%.2f")
            },
            hide_index=True,
            use_container_width=True,
            height=350
        )
    else:
        st.info("No high-confidence detections recorded for the selected filters.")

with tab_telemetry:
    st.subheader("Hardware Telemetry (Cellular & Wi-Fi Nodes)")

    if not hb_df.empty and ("cpu" in hb_df.columns or "mem_free" in hb_df.columns or "shm" in hb_df.columns):
        telemetry_df = hb_df.dropna(subset=["_time"]).copy()
        t_cols = [c for c in ["cpu", "mem_free", "mem_used", "shm"] if c in telemetry_df.columns]
        valid_telemetry = telemetry_df.dropna(subset=t_cols, how="all")
        
        if not valid_telemetry.empty:
            valid_telemetry["Device_Friendly"] = valid_telemetry["Device"].apply(
                lambda d: f"{deployments[d]['name']} ({d})" if d in deployments and deployments[d]["name"] else d
            )
            
            # Filter telemetry charts by selected device if specific device selected
            tel_plot_df = valid_telemetry
            if selected_device != "All Devices" and "Device" in valid_telemetry.columns:
                specific_tel = valid_telemetry[valid_telemetry["Device"] == selected_device]
                if not specific_tel.empty:
                    tel_plot_df = specific_tel
            
            # Reusable legend and layout settings for full-width telemetry charts
            tel_legend_layout = dict(
                orientation="h",
                yanchor="top",
                y=-0.22,
                xanchor="center",
                x=0.5,
                title=None
            )
            
            if "cpu" in tel_plot_df.columns:
                fig_cpu = px.line(
                    tel_plot_df,
                    x="_time",
                    y="cpu",
                    color="Device_Friendly",
                    title="CPU Utilization (%)",
                    template="plotly_white",
                    labels={"_time": "Time (UTC)", "cpu": "CPU %", "Device_Friendly": "Device"},
                    height=350
                )
                fig_cpu.update_layout(
                    margin=dict(l=10, r=10, t=40, b=60),
                    legend=tel_legend_layout,
                    hovermode="x unified"
                )
                st.plotly_chart(fig_cpu, use_container_width=True)
                
            if "mem_free" in tel_plot_df.columns:
                fig_mem = px.line(
                    tel_plot_df,
                    x="_time",
                    y="mem_free",
                    color="Device_Friendly",
                    title="Free Memory (MB)",
                    template="plotly_white",
                    labels={"_time": "Time (UTC)", "mem_free": "RAM Free (MB)", "Device_Friendly": "Device"},
                    height=350
                )
                fig_mem.update_layout(
                    margin=dict(l=10, r=10, t=40, b=60),
                    legend=tel_legend_layout,
                    hovermode="x unified"
                )
                fig_mem.update_yaxes(rangemode="tozero")
                st.plotly_chart(fig_mem, use_container_width=True)
                
            if "shm" in tel_plot_df.columns:
                fig_shm = px.line(
                    tel_plot_df,
                    x="_time",
                    y="shm",
                    color="Device_Friendly",
                    title="RAM Disk (/run/shm) Space (MB)",
                    template="plotly_white",
                    labels={"_time": "Time (UTC)", "shm": "SHM Available (MB)", "Device_Friendly": "Device"},
                    height=350
                )
                fig_shm.update_layout(
                    margin=dict(l=10, r=10, t=40, b=60),
                    legend=tel_legend_layout,
                    hovermode="x unified"
                )
                st.plotly_chart(fig_shm, use_container_width=True)
        else:
            st.info("No numerical telemetry (CPU/Memory/SHM) reported in current window.")
    else:
        st.info("Telemetry data (CPU/RAM/SHM) is available when cellular nodes report heartbeats.")

with tab_map:
    st.subheader("Active Node Locations")
    loc_data = []
    for dev_id, info in deployments.items():
        if dev_id in active_devices:
            if info["latitude"] is not None and info["longitude"] is not None:
                loc_data.append({
                    "Device ID": dev_id,
                    "Name": info["name"],
                    "Version": info["version"],
                    "latitude": info["latitude"],
                    "longitude": info["longitude"]
                })
    if loc_data:
        loc_data.sort(key=lambda x: natural_sort_key(x["Name"]))
        loc_df = pd.DataFrame(loc_data)
        render_sensor_map(loc_df, key_suffix="tab_map")
    else:
        st.info("No active device locations mapped in current view.")

with tab_species_ref:
    st.subheader("Reference Taxa Catalog")
    st.caption("Mapped species database containing 208 British birds and 17 British bat species.")
    species_catalog, _, _, _, _ = load_species_database()
    st.dataframe(
        species_catalog[["species_id", "category", "common_name", "species", "class_name"]],
        column_config={
            "species_id": st.column_config.NumberColumn("Numeric ID", format="%d"),
            "category": st.column_config.TextColumn("Category"),
            "common_name": st.column_config.TextColumn("Common Name"),
            "species": st.column_config.TextColumn("Scientific Name"),
            "class_name": st.column_config.TextColumn("Class Identifier")
        },
        hide_index=True,
        use_container_width=True,
        height=350
    )

st.divider()
st.caption("Page developed by UCL in collaboration with Yeo Valley.  \nAny errors or issues with this page please contact Duncan Wilson (d.j.wilson@ucl.ac.uk)")