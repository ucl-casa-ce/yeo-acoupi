#!/usr/bin/env python3
"""
InfluxDB Data Cleaner for Yeo Acoupi
Finds and removes invalid or specific device records from InfluxDB.
Uses Python standard library (zero external dependencies required).

Usage:
  python3 db-cleaner.py --device uno4-cellular              # Dry-run for uno4-cellular
  python3 db-cleaner.py --device uno4-cellular --execute    # Delete uno4-cellular records
  python3 db-cleaner.py                                     # Dry-run for all non-uno devices
  python3 db-cleaner.py --execute                           # Delete all non-uno devices
  python3 db-cleaner.py --help                              # Show options
"""

import os
import sys
import csv
import io
import json
import urllib.request
import urllib.parse
import urllib.error
import argparse
from datetime import datetime, timezone


def load_config():
    """Load configuration from environment variables or .streamlit/secrets.toml."""
    config = {
        "URL": os.getenv("STREAMLIT_INFLUX_URL") or os.getenv("INFLUX_URL"),
        "TOKEN": os.getenv("STREAMLIT_INFLUX_TOKEN") or os.getenv("INFLUX_TOKEN"),
        "ORG": os.getenv("STREAMLIT_INFLUX_ORG") or os.getenv("INFLUX_ORG"),
        "BUCKET": os.getenv("STREAMLIT_INFLUX_BUCKET") or os.getenv("INFLUX_BUCKET"),
    }

    # Fallback to .streamlit/secrets.toml if available
    secrets_path = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")
    if os.path.exists(secrets_path):
        try:
            with open(secrets_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k.startswith("INFLUX_"):
                            short_k = k[7:]
                            if short_k in config and not config[short_k]:
                                config[short_k] = v
                        elif k in config and not config[k]:
                            config[k] = v
        except Exception:
            pass

    # Defaults fallback if still missing
    if not config["URL"]:
        config["URL"] = "http://128.40.193.25:8086"
    if not config["ORG"]:
        config["ORG"] = "UCL-CE"
    if not config["BUCKET"]:
        config["BUCKET"] = "CE_yeo"

    return config


def influx_query_flux(url, token, org, flux_query):
    """Execute a Flux query against InfluxDB v2 and parse CSV results."""
    endpoint = f"{url.rstrip('/')}/api/v2/query?org={urllib.parse.quote(org)}"
    req = urllib.request.Request(
        endpoint,
        data=flux_query.encode("utf-8"),
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read().decode("utf-8")

    rows = []
    reader = csv.reader(io.StringIO(content))
    header = None
    for row in reader:
        if not row:
            continue
        # Skip comment/metadata lines starting with #
        if row[0].startswith("#"):
            continue
        # Header row contains column names like _value, result, table
        if "_value" in row:
            header = [col.strip() for col in row]
            continue
        if header and len(row) == len(header):
            rows.append(dict(zip(header, [c.strip() for c in row])))
    return rows


def influx_delete(url, token, org, bucket, predicate, start="1970-01-01T00:00:00Z", stop=None):
    """Execute a delete request against InfluxDB v2."""
    if stop is None:
        stop = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    endpoint = (
        f"{url.rstrip('/')}/api/v2/delete?"
        f"org={urllib.parse.quote(org)}&bucket={urllib.parse.quote(bucket)}"
    )
    payload = json.dumps({
        "start": start,
        "stop": stop,
        "predicate": predicate,
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status in (200, 204)


def main():
    parser = argparse.ArgumentParser(
        description="Clean invalid or specific device records from InfluxDB."
    )
    parser.add_argument(
        "--device",
        "-d",
        nargs="+",
        help="Target specific device name(s) to delete (e.g. --device uno4-cellular).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the deletion (default is DRY-RUN / test mode).",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Bypass confirmation prompt when --execute is specified.",
    )
    parser.add_argument(
        "--prefix",
        default="uno",
        help="Valid device prefix to keep when auto-scanning (default: 'uno'). Ignored if --device is passed.",
    )
    parser.add_argument(
        "--tag",
        default="device_id",
        help="Tag key to inspect (default: 'device_id').",
    )
    parser.add_argument(
        "--start",
        default="1970-01-01T00:00:00Z",
        help="Start time for deletion in RFC3339 format (default: '1970-01-01T00:00:00Z').",
    )
    parser.add_argument(
        "--stop",
        help="Stop time for deletion in RFC3339 format (default: current time).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="How many days back to scan for tag values (default: 365).",
    )
    parser.add_argument(
        "--skip-count",
        action="store_true",
        help="Skip counting matching data points (faster execution).",
    )
    parser.add_argument(
        "--url",
        help="InfluxDB URL (overrides env / secrets).",
    )
    parser.add_argument(
        "--token",
        help="InfluxDB Token (overrides env / secrets).",
    )
    parser.add_argument(
        "--org",
        help="InfluxDB Organization (overrides env / secrets).",
    )
    parser.add_argument(
        "--bucket",
        help="InfluxDB Bucket (overrides env / secrets).",
    )

    args = parser.parse_args()
    config = load_config()

    url = args.url or config["URL"]
    token = args.token or config["TOKEN"]
    org = args.org or config["ORG"]
    bucket = args.bucket or config["BUCKET"]

    if not token:
        print("❌ Error: INFLUX_TOKEN not found in environment, .streamlit/secrets.toml, or CLI arguments.")
        sys.exit(1)

    print("=" * 65)
    print("🌿 InfluxDB Device Cleanup Tool")
    print("=" * 65)
    print(f"• URL:        {url}")
    print(f"• Org:        {org}")
    print(f"• Bucket:     {bucket}")
    print(f"• Target Tag: {args.tag}")
    if args.device:
        print(f"• Target Dev: {args.device}")
    else:
        print(f"• Valid Pref: '{args.prefix}' (case-insensitive)")
    print(f"• Mode:       {'🔴 EXECUTE (DESTRUCTIVE)' if args.execute else '🟢 DRY-RUN / TEST MODE (NO CHANGES)'}")
    print("=" * 65)

    if args.device:
        targeted_devices = args.device
    else:
        # Discover devices with active data
        print(f"\n🔍 Scanning for '{args.tag}' tag values with active data in the last {args.days} days...")
        tag_query = f'''
        from(bucket: "{bucket}")
          |> range(start: -{args.days}d)
          |> keep(columns: ["{args.tag}"])
          |> distinct(column: "{args.tag}")
        '''

        try:
            rows = influx_query_flux(url, token, org, tag_query)
        except Exception as e:
            print(f"❌ Failed to query InfluxDB: {e}")
            sys.exit(1)

        all_devices = set()
        for row in rows:
            val = row.get(args.tag) or row.get("_value")
            if val and val.strip():
                all_devices.add(val.strip())

        if not all_devices:
            print(f"⚠️  No '{args.tag}' values found in bucket '{bucket}' within the last {args.days} days.")
            return

        valid_devices = sorted([d for d in all_devices if d.lower().startswith(args.prefix.lower())])
        targeted_devices = sorted([d for d in all_devices if not d.lower().startswith(args.prefix.lower())])

        print(f"\n✅ Valid Devices (will be KEPT, {len(valid_devices)}):")
        for d in valid_devices:
            print(f"   • {d}")

    print(f"\n❌ Devices targeted for REMOVAL ({len(targeted_devices)}):")
    for d in targeted_devices:
        print(f"   • {d}")

    if not targeted_devices:
        print("\n🎉 No targeted devices found! Nothing to clean.")
        return

    # Count records for targeted devices
    if not args.skip_count:
        print(f"\n📊 Checking record counts in the last {args.days} days...")
        for d in targeted_devices:
            count_query = f'''
            from(bucket: "{bucket}")
              |> range(start: -{args.days}d)
              |> filter(fn: (r) => r["{args.tag}"] == "{d}" or r["topic"] == "yeo/{d}/acoupi")
              |> count()
            '''
            try:
                c_rows = influx_query_flux(url, token, org, count_query)
                total = sum(int(r.get("_value", 0)) for r in c_rows if r.get("_value", "").isdigit())
                print(f"   • `{d}`: ~{total} data point(s)")
            except Exception:
                print(f"   • `{d}`: (count check skipped)")

    # Perform Deletion if --execute is passed
    start_time = args.start
    stop_time = args.stop or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not args.execute:
        print("\n" + "=" * 65)
        print("💡 DRY-RUN COMPLETE — No records were deleted.")
        print(f"Targeted {len(targeted_devices)} device(s) to remove.")
        print("To permanently delete the records for the devices above, run:")
        cmd = "python3 db-cleaner.py"
        if args.device:
            dev_str = " ".join(args.device)
            cmd += f" --device {dev_str}"
        if args.start != "1970-01-01T00:00:00Z":
            cmd += f" --start '{args.start}'"
        if args.stop:
            cmd += f" --stop '{args.stop}'"
        cmd += " --execute"
        print(f"   {cmd}")
        print("=" * 65)
    else:
        print("\n" + "⚠️ " * 20)
        print(f"DELETION WARNING: You are about to permanently delete {len(targeted_devices)} device(s):")
        for d in targeted_devices:
            print(f"   • {d}")
        print("⚠️ " * 20)

        if not args.yes:
            confirm = input("\nType 'DELETE' to confirm and proceed: ").strip()
            if confirm != "DELETE":
                print("❌ Deletion aborted by user.")
                return

        print("\n🚀 Executing deletion...")
        success_count = 0
        for d in targeted_devices:
            predicates = [
                f'{args.tag} = "{d}"',
                f'topic = "yeo/{d}/acoupi"',
            ]
            dev_success = True
            for predicate in predicates:
                try:
                    influx_delete(url, token, org, bucket, predicate, start=start_time, stop=stop_time)
                    print(f"   ✅ Successfully deleted records where `{predicate}`.")
                except urllib.error.HTTPError as e:
                    err_msg = e.read().decode("utf-8", errors="ignore")
                    print(f"   ❌ HTTP {e.code} failed for `{predicate}`: {err_msg}")
                    dev_success = False
                except Exception as e:
                    print(f"   ❌ Failed for `{predicate}`: {e}")
                    dev_success = False

            if dev_success:
                success_count += 1

        print(f"\n✨ Deletion process complete! ({success_count}/{len(targeted_devices)} devices cleaned)")


if __name__ == "__main__":
    main()
