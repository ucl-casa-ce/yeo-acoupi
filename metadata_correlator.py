"""
Metadata Correlator for Yeo Acoupi
Queries InfluxDB detection telemetry and correlates it with audio/spectrogram recordings
by timestamp and device ID.
"""

import os
import toml
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import streamlit as st
from influxdb_client import InfluxDBClient

# Import species resolver from yeo-vis if available, or initialize local database
try:
    from s3_service import S3_FOLDER_TO_MAC, S3_FOLDER_TO_DEVICE_NAME
except ImportError:
    S3_FOLDER_TO_MAC = {}
    S3_FOLDER_TO_DEVICE_NAME = {}

def load_influx_config() -> dict:
    """Loads InfluxDB configuration from secrets or environment."""
    config = {
        "URL": os.getenv("STREAMLIT_INFLUX_URL") or os.getenv("INFLUX_URL"),
        "TOKEN": os.getenv("STREAMLIT_INFLUX_TOKEN") or os.getenv("INFLUX_TOKEN"),
        "ORG": os.getenv("STREAMLIT_INFLUX_ORG") or os.getenv("INFLUX_ORG"),
        "BUCKET": os.getenv("STREAMLIT_INFLUX_BUCKET") or os.getenv("INFLUX_BUCKET", "CE_yeo"),
    }
    
    # Check Streamlit secrets
    try:
        if hasattr(st, "secrets"):
            for k in ["INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET"]:
                short_k = k.replace("INFLUX_", "")
                if k in st.secrets:
                    config[short_k] = st.secrets[k]
                elif f"STREAMLIT_{k}" in st.secrets:
                    config[short_k] = st.secrets[f"STREAMLIT_{k}"]
    except Exception:
        pass

    # Check local .streamlit/secrets.toml
    secrets_path = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")
    if os.path.exists(secrets_path):
        try:
            data = toml.load(secrets_path)
            for k in ["INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET"]:
                short_k = k.replace("INFLUX_", "")
                if k in data and not config.get(short_k):
                    config[short_k] = data[k]
                elif f"STREAMLIT_{k}" in data and not config.get(short_k):
                    config[short_k] = data[f"STREAMLIT_{k}"]
        except Exception:
            pass

    return config

# Cache species lookup table
@st.cache_data
def get_species_database():
    """Loads bat and bird species lists for code/name resolution."""
    base_dir = os.path.dirname(__file__)
    data_dir = os.path.join(base_dir, "data")
    bat_file = os.path.join(data_dir, "yeo_valley_bat_species_list.csv")
    bird_file = os.path.join(data_dir, "yeo_valley_bird_species_list.csv")

    id_map = {}
    class_map = {}
    common_map = {}
    species_map = {}

    def process_file(filepath, category):
        if not os.path.exists(filepath):
            return
        df = pd.read_csv(filepath)
        for _, row in df.iterrows():
            item = {
                "species_id": str(row.get("species_id", "")).strip(),
                "class_name": str(row.get("class_name", "")).strip(),
                "common_name": str(row.get("common_name", "")).strip(),
                "species": str(row.get("species", "")).strip(),
                "category": category,
            }
            if item["species_id"]:
                id_map[item["species_id"]] = item
            if item["class_name"]:
                class_map[item["class_name"].lower()] = item
            if item["common_name"]:
                common_map[item["common_name"].lower()] = item
            if item["species"]:
                species_map[item["species"].lower()] = item

    process_file(bat_file, "Bat")
    process_file(bird_file, "Bird")
    return id_map, class_map, common_map, species_map

def resolve_species(val) -> Dict[str, str]:
    """Resolves a raw species ID or string to common name, species, and taxa category."""
    if pd.isna(val) or val is None:
        return {"common_name": "Unspecified", "species": "Unknown", "category": "Unknown"}

    val_str = str(val).strip()
    if not val_str or val_str.lower() in ("nan", "none", "null", "<na>"):
        return {"common_name": "Unspecified", "species": "Unknown", "category": "Unknown"}

    id_map, class_map, common_map, species_map = get_species_database()

    # 1. Check numeric ID match
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

    # 2. Check class map
    if val_lower in class_map:
        return class_map[val_lower]
    if norm_val in class_map:
        return class_map[norm_val]

    # 3. Check common name
    if val_lower in common_map:
        return common_map[val_lower]
    if norm_val in common_map:
        return common_map[norm_val]

    # 4. Check scientific name
    if val_lower in species_map:
        return species_map[val_lower]
    if norm_val in species_map:
        return species_map[norm_val]

    # 5. Check underscore formatted class (e.g. Accipiter nisus_Eurasian Sparrowhawk)
    if "_" in val_str:
        parts = val_str.split("_", 1)
        if len(parts) == 2 and parts[1].strip():
            cname = parts[1].strip()
            sname = parts[0].strip()
            if cname.lower() in common_map:
                return common_map[cname.lower()]
            if sname.lower() in species_map:
                return species_map[sname.lower()]
            return {"common_name": cname, "species": sname, "category": "Bird"}

    return {"common_name": val_str, "species": val_str, "category": "Other"}

@st.cache_data(ttl=120)
def fetch_day_detections(device_identifiers: List[str], date_str: str) -> pd.DataFrame:
    """
    Fetches all InfluxDB detection telemetry for specified device IDs on a given date.
    Returns a pandas DataFrame sorted by timestamp.
    """
    cfg = load_influx_config()
    url = cfg.get("URL")
    token = cfg.get("TOKEN")
    org = cfg.get("ORG")
    bucket = cfg.get("BUCKET", "CE_yeo")

    if not (url and token and org):
        return pd.DataFrame()

    try:
        start_time = f"{date_str}T00:00:00Z"
        stop_time = f"{date_str}T23:59:59Z"
    except Exception:
        return pd.DataFrame()

    # Build device filter clause
    device_filters = []
    for d in device_identifiers:
        d_clean = d.strip()
        if d_clean:
            device_filters.append(f'r["device_id"] == "{d_clean}"')
            device_filters.append(f'r["topic"] =~ /.*{d_clean}.*/')

    dev_filter_clause = " or ".join(device_filters)
    if dev_filter_clause:
        dev_filter_clause = f'|> filter(fn: (r) => {dev_filter_clause})'
    else:
        dev_filter_clause = ""

    flux_query = f'''
    from(bucket: "{bucket}")
      |> range(start: {start_time}, stop: {stop_time})
      |> filter(fn: (r) => r["_measurement"] == "acoupi_detections" or r["_measurement"] == "acoupi_data")
      {dev_filter_clause}
      |> filter(fn: (r) => r["_field"] == "c" or r["_field"] == "confidence" or r["_field"] == "confidence_score" or r["_field"] == "detection_score" or r["_field"] == "value" or r["_field"] == "s" or r["_field"] == "label" or r["_field"] == "species")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''

    try:
        client = InfluxDBClient(url=url, token=token, org=org, timeout=15_000)
        query_api = client.query_api()
        df = query_api.query_data_frame(flux_query)
        client.close()

        if isinstance(df, list):
            df = pd.concat(df, ignore_index=True) if len(df) > 0 else pd.DataFrame()

        if df.empty:
            return pd.DataFrame()

        # Parse Time
        df["_time"] = pd.to_datetime(df["_time"], utc=True)
        df = df.sort_values(by="_time")

        # Resolve Confidence
        conf_series = pd.Series(index=df.index, dtype="float64")
        for col in ["c", "confidence", "confidence_score", "detection_score"]:
            if col in df.columns:
                conf_series = conf_series.fillna(pd.to_numeric(df[col], errors="coerce"))
        df["Confidence"] = conf_series

        # Resolve Species
        sp_series = pd.Series(index=df.index, dtype="object")
        for col in ["s", "label", "species", "value"]:
            if col in df.columns:
                valid_vals = df[col].astype(str).replace({"nan": None, "None": None, "": None, "<NA>": None})
                sp_series = sp_series.fillna(valid_vals)
        df["Raw_Species"] = sp_series

        resolved_meta = [resolve_species(v) for v in df["Raw_Species"]]
        df["Common_Name"] = [m["common_name"] for m in resolved_meta]
        df["Scientific_Name"] = [m["species"] for m in resolved_meta]
        df["Category"] = [m["category"] for m in resolved_meta]

        return df

    except Exception as e:
        print(f"Error querying InfluxDB for detections: {e}")
        return pd.DataFrame()

def correlate_recordings_with_detections(
    recordings: List[Dict],
    s3_folder: str,
    date_str: str,
    tolerance_seconds: int = 5
) -> List[Dict]:
    """
    Enriches each S3 recording dict with InfluxDB detection telemetry by matching timestamps.
    """
    if not recordings:
        return []

    # Identify all possible identifiers for this device
    dev_identifiers = [s3_folder]
    if s3_folder in S3_FOLDER_TO_MAC:
        dev_identifiers.append(S3_FOLDER_TO_MAC[s3_folder])
    if s3_folder in S3_FOLDER_TO_DEVICE_NAME:
        dev_identifiers.append(S3_FOLDER_TO_DEVICE_NAME[s3_folder])
        dev_identifiers.append(S3_FOLDER_TO_DEVICE_NAME[s3_folder].lower())

    # Fetch detections from InfluxDB
    det_df = fetch_day_detections(dev_identifiers, date_str)

    enriched_recordings = []
    for rec in recordings:
        item = dict(rec)
        rec_time = item.get("timestamp")

        matched = False
        matched_row = None

        if rec_time and not det_df.empty:
            # Find detections within tolerance window
            diffs = (det_df["_time"] - rec_time).abs()
            min_diff_idx = diffs.idxmin()
            min_diff_sec = diffs.loc[min_diff_idx].total_seconds()

            if min_diff_sec <= tolerance_seconds:
                matched = True
                matched_row = det_df.loc[min_diff_idx]

        if matched and matched_row is not None:
            item["matched"] = True
            item["common_name"] = str(matched_row.get("Common_Name", "Unknown"))
            item["scientific_name"] = str(matched_row.get("Scientific_Name", ""))
            item["confidence"] = float(matched_row["Confidence"]) if pd.notna(matched_row.get("Confidence")) else None
            item["category"] = str(matched_row.get("Category", rec.get("taxa", "Other")))
        else:
            item["matched"] = False
            item["common_name"] = "Uncorrelated Detection"
            item["scientific_name"] = ""
            item["confidence"] = None
            item["category"] = rec.get("taxa", "Unknown")

        # Format display confidence
        if item["confidence"] is not None:
            item["confidence_pct"] = f"{item['confidence'] * 100:.1f}%"
        else:
            item["confidence_pct"] = "N/A"

        enriched_recordings.append(item)

    return enriched_recordings
