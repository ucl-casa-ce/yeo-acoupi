"""
S3 Media Service for Yeo Acoupi
Provides discovery, listing, and URL generation for spectrograms and audio recordings
stored on Silo / MinIO S3 bucket.
"""

import os
import re
import boto3
import toml
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import streamlit as st

# Default fallback device to S3 folder mapping
DEVICE_TO_S3_FOLDER = {
    # Friendly names - Deployed
    "UNOQ-17-YV": "YV17-recordings",
    "UNOQ-18-YV": "YV18-recordings",
    "UNOQ-21-YV": "YV21-recordings",
    "UNOQ-22-YV": "YV22-recordings",
    # Friendly names - Reassigned Test Devices
    "UNOQ-29-YV": "YV29-recordings",
    "UNOQ-30-YV": "YV30-recordings",
    "UNOQ-31-YV": "YV31-recordings",
    # Legacy names for historical S3 folders
    "UNOQ-10-YV": "YV10-recordings",
    "UNOQ-11-YV": "YV11-recordings",
    "UNOQ-12-YV": "YV12-recordings",
    # Hardware MAC addresses
    "14:b5:cd:ea:c5:4b": "YV17-recordings",
    "14:b5:cd:ea:08:1b": "YV18-recordings",
    "14:b5:cd:e8:b1:43": "YV21-recordings",
    "14:b5:cd:e9:f5:eb": "YV22-recordings",
    "14:b5:cd:ea:1e:6d": "YV29-recordings",
    "14:b5:cd:ea:1d:a5": "YV30-recordings",
    "14:b5:cd:ea:07:9f": "YV31-recordings",
    # Hostnames & Lowercase
    "yeo-unoq-17": "YV17-recordings",
    "yeo-unoq-18": "YV18-recordings",
    "yeo-unoq-21": "YV21-recordings",
    "yeo-unoq-22": "YV22-recordings",
    "unoq-17-yv": "YV17-recordings",
    "unoq-18-yv": "YV18-recordings",
    "unoq-21-yv": "YV21-recordings",
    "unoq-22-yv": "YV22-recordings",
    "unoq-29-yv": "YV29-recordings",
    "unoq-30-yv": "YV30-recordings",
    "unoq-31-yv": "YV31-recordings",
    "unoq-10-yv": "YV10-recordings",
    "unoq-11-yv": "YV11-recordings",
    "unoq-12-yv": "YV12-recordings",
    # Direct folder names
    "YV17-recordings": "YV17-recordings",
    "YV18-recordings": "YV18-recordings",
    "YV21-recordings": "YV21-recordings",
    "YV22-recordings": "YV22-recordings",
    "YV29-recordings": "YV29-recordings",
    "YV30-recordings": "YV30-recordings",
    "YV31-recordings": "YV31-recordings",
    "YV10-recordings": "YV10-recordings",
    "YV11-recordings": "YV11-recordings",
    "YV12-recordings": "YV12-recordings",
}

# Reverse mapping for Friendly Device Display
S3_FOLDER_TO_DEVICE_NAME = {
    "YV17-recordings": "UNOQ-17-YV",
    "YV18-recordings": "UNOQ-18-YV",
    "YV21-recordings": "UNOQ-21-YV",
    "YV22-recordings": "UNOQ-22-YV",
    "YV29-recordings": "UNOQ-29-YV",
    "YV30-recordings": "UNOQ-30-YV",
    "YV31-recordings": "UNOQ-31-YV",
    "YV10-recordings": "UNOQ-10-YV",
    "YV11-recordings": "UNOQ-11-YV",
    "YV12-recordings": "UNOQ-12-YV",
}

S3_FOLDER_TO_MAC = {
    "YV17-recordings": "14:b5:cd:ea:c5:4b",
    "YV18-recordings": "14:b5:cd:ea:08:1b",
    "YV21-recordings": "14:b5:cd:e8:b1:43",
    "YV22-recordings": "14:b5:cd:e9:f5:eb",
    "YV29-recordings": "14:b5:cd:ea:1e:6d",
    "YV30-recordings": "14:b5:cd:ea:1d:a5",
    "YV31-recordings": "14:b5:cd:ea:07:9f",
    "YV10-recordings": "14:b5:cd:ea:1e:6d",
    "YV11-recordings": "14:b5:cd:ea:1d:a5",
    "YV12-recordings": "14:b5:cd:ea:07:9f",
}

def load_s3_config() -> dict:
    """Loads S3 credentials and endpoint configuration."""
    # 1. Check Streamlit secrets
    try:
        if hasattr(st, "secrets") and "s3" in st.secrets:
            return dict(st.secrets["s3"])
    except Exception:
        pass

    # 2. Check local .streamlit/secrets.toml
    secrets_path = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")
    if os.path.exists(secrets_path):
        try:
            data = toml.load(secrets_path)
            if "s3" in data:
                return data["s3"]
        except Exception:
            pass

    # 3. Fallback environment variables (support both S3_* and STREAMLIT_S3_* prefixes)
    return {
        "ENDPOINT_URL": os.getenv("STREAMLIT_S3_ENDPOINT_URL") or os.getenv("S3_ENDPOINT_URL", "https://buckets.cetools.org"),
        "ACCESS_KEY": os.getenv("STREAMLIT_S3_ACCESS_KEY") or os.getenv("S3_ACCESS_KEY", ""),
        "SECRET_KEY": os.getenv("STREAMLIT_S3_SECRET_KEY") or os.getenv("S3_SECRET_KEY", ""),
        "BUCKET_NAME": os.getenv("STREAMLIT_S3_BUCKET_NAME") or os.getenv("S3_BUCKET_NAME", "yeo"),
    }

def get_s3_client():
    """Initializes and returns a boto3 S3 client."""
    cfg = load_s3_config()
    endpoint = cfg.get("ENDPOINT_URL", "https://buckets.cetools.org")
    if not endpoint.startswith("http"):
        endpoint = "https://" + endpoint

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=cfg.get("ACCESS_KEY"),
        aws_secret_access_key=cfg.get("SECRET_KEY"),
    )

def resolve_s3_folder(device_identifier: str) -> str:
    """Resolves any device name, MAC or ID to its corresponding S3 folder name."""
    if device_identifier in DEVICE_TO_S3_FOLDER:
        return DEVICE_TO_S3_FOLDER[device_identifier]
    
    # Forward-compatible: unoq-xx-yv format
    clean_id = device_identifier.strip().lower()
    if clean_id in DEVICE_TO_S3_FOLDER:
        return DEVICE_TO_S3_FOLDER[clean_id]
        
    return device_identifier

@st.cache_data(ttl=300)
def list_available_s3_devices() -> List[str]:
    """Lists top-level recording folders available in the S3 bucket."""
    cfg = load_s3_config()
    bucket = cfg.get("BUCKET_NAME", "yeo")
    try:
        s3 = get_s3_client()
        resp = s3.list_objects_v2(Bucket=bucket, Delimiter="/")
        folders = [
            p["Prefix"].rstrip("/")
            for p in resp.get("CommonPrefixes", [])
            if p["Prefix"].rstrip("/").endswith("-recordings") or "unoq" in p["Prefix"].lower()
        ]
        return sorted(folders)
    except Exception as e:
        print(f"Error listing S3 devices: {e}")
        return ["YV10-recordings", "YV11-recordings", "YV12-recordings"]

@st.cache_data(ttl=300)
def list_available_dates(s3_folder: str, taxa: str = "bat") -> List[str]:
    """
    Finds all distinct YYYY-MM-DD dates that have recordings for a device and taxa.
    Uses S3 delimiter hierarchies for fast directory walking.
    """
    cfg = load_s3_config()
    bucket = cfg.get("BUCKET_NAME", "yeo")
    spect_prefix = "bat-spect" if taxa.lower() == "bat" else "bird-spect"
    base_prefix = f"{s3_folder}/{spect_prefix}/"
    
    found_dates = set()
    try:
        s3 = get_s3_client()
        # 1. Find Years
        resp_years = s3.list_objects_v2(Bucket=bucket, Prefix=base_prefix, Delimiter="/")
        years = [p["Prefix"] for p in resp_years.get("CommonPrefixes", [])]
        
        for y_prefix in years:
            # e.g. YV10-recordings/bat-spect/2026/
            y_str = y_prefix.rstrip("/").split("/")[-1]
            if not y_str.isdigit():
                continue
            year = int(y_str)

            # 2. Find Months
            resp_months = s3.list_objects_v2(Bucket=bucket, Prefix=y_prefix, Delimiter="/")
            months = [p["Prefix"] for p in resp_months.get("CommonPrefixes", [])]
            for m_prefix in months:
                m_str = m_prefix.rstrip("/").split("/")[-1]
                if not m_str.isdigit():
                    continue
                month = int(m_str)

                # 3. Find Days
                resp_days = s3.list_objects_v2(Bucket=bucket, Prefix=m_prefix, Delimiter="/")
                days = [p["Prefix"] for p in resp_days.get("CommonPrefixes", [])]
                for d_prefix in days:
                    d_str = d_prefix.rstrip("/").split("/")[-1]
                    if not d_str.isdigit():
                        continue
                    day = int(d_str)
                    
                    try:
                        date_obj = datetime(year, month, day)
                        found_dates.add(date_obj.strftime("%Y-%m-%d"))
                    except ValueError:
                        continue

    except Exception as e:
        print(f"Error discovering dates on S3 for {s3_folder}: {e}")

    # Return dates descending (most recent first)
    return sorted(list(found_dates), reverse=True)

@st.cache_data(ttl=120)
def list_recordings_for_day(s3_folder: str, taxa: str, date_str: str) -> List[Dict]:
    """
    Lists all spectrogram PNGs and pairs them with their corresponding audio WAVs
    for a given device, taxa ('bat' or 'bird'), and date ('YYYY-MM-DD').
    """
    cfg = load_s3_config()
    bucket = cfg.get("BUCKET_NAME", "yeo")
    endpoint = cfg.get("ENDPOINT_URL", "https://buckets.cetools.org").rstrip("/")

    is_bat = (taxa.lower() == "bat")
    spect_folder = "bat-spect" if is_bat else "bird-spect"
    audio_folder = "bats" if is_bat else "birds"

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        year = dt.year
        month = dt.month
        day = dt.day
    except ValueError:
        return []

    # Format paths - check unpadded (e.g. 2026/8/26/) and zero-padded (2026/08/26/)
    prefixes_to_try = [
        f"{s3_folder}/{spect_folder}/{year}/{month}/{day}/",
        f"{s3_folder}/{spect_folder}/{year}/{month:02d}/{day:02d}/",
    ]

    s3 = get_s3_client()
    contents = []

    for prefix in prefixes_to_try:
        paginator = s3.get_paginator("list_objects_v2")
        page_iterator = paginator.paginate(Bucket=bucket, Prefix=prefix)
        for page in page_iterator:
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if key.lower().endswith(".png"):
                    contents.append(obj)
        if contents:
            break

    recordings = []
    # Regex to extract timestamp from filenames like:
    # batdetect2_20260826_213540_335e1e17-1b4e-4c19-a639-9f59a295bf0c.png
    # birdnet_20260826_213540_...png
    # or 20260826_213540.png
    ts_pattern = re.compile(r"(\d{8})_(\d{6})")

    for obj in contents:
        png_key = obj["Key"]
        filename = png_key.split("/")[-1]
        
        # Audio key by substituting the folder and extension
        wav_key = png_key.replace(f"/{spect_folder}/", f"/{audio_folder}/").rsplit(".", 1)[0] + ".wav"

        spect_url = f"{endpoint}/{bucket}/{png_key}"
        audio_url = f"{endpoint}/{bucket}/{wav_key}"

        # Parse timestamp
        timestamp_dt = None
        match = ts_pattern.search(filename)
        if match:
            date_part, time_part = match.groups()
            try:
                timestamp_dt = datetime.strptime(
                    f"{date_part}_{time_part}", "%Y%m%d_%H%M%S"
                ).replace(tzinfo=timezone.utc)
            except Exception:
                timestamp_dt = None

        time_display = timestamp_dt.strftime("%H:%M:%S UTC") if timestamp_dt else "Unknown Time"

        # Extract UUID if present
        uuid_val = ""
        uuid_match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", filename)
        if uuid_match:
            uuid_val = uuid_match.group(1)

        recordings.append({
            "filename": filename,
            "png_key": png_key,
            "wav_key": wav_key,
            "spect_url": spect_url,
            "audio_url": audio_url,
            "timestamp": timestamp_dt,
            "time_display": time_display,
            "uuid": uuid_val,
            "size_bytes": obj.get("Size", 0),
            "taxa": "Bat" if is_bat else "Bird",
        })

    # Sort recordings chronologically by timestamp
    recordings.sort(
        key=lambda r: r["timestamp"] if r["timestamp"] else datetime.min.replace(tzinfo=timezone.utc)
    )

    return recordings
