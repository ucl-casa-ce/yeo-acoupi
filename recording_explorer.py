"""
Recording & Spectrogram Explorer for Yeo Acoupi
Provides an interactive gallery to browse spectrograms, listen to corresponding audio,
and view matched machine learning classification telemetry from InfluxDB.
"""

import math
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import datetime, timezone

import s3_service
import metadata_correlator

def get_species_group(common_name: str, scientific_name: str, taxa: str) -> str:
    """Classifies a species into a higher-level ecological type / guild / family."""
    if not common_name or common_name == "Uncorrelated Detection":
        return "Uncorrelated / Unclassified"

    c = common_name.lower()
    s = scientific_name.lower() if scientific_name else ""

    if taxa.lower() == "bat":
        if "pipistrelle" in c or "pipistrellus" in s:
            return "Pipistrelles (Pipistrellus)"
        if "myotis" in s or any(x in c for x in ["daubenton", "natterer", "whiskered", "brandt", "alcathoe", "bechstein"]):
            return "Myotis Bats"
        if any(x in c for x in ["noctule", "leisler", "serotine"]):
            return "Big Bats (Nyctalus / Serotine)"
        if "horseshoe" in c or "rhinolophus" in s:
            return "Horseshoe Bats (Rhinolophus)"
        if "long-eared" in c or "plecotus" in s:
            return "Long-eared Bats (Plecotus)"
        if "barbastelle" in c or "barbastella" in s:
            return "Barbastelle"
        return "Other Bats"
    else:
        if any(x in c for x in ["warbler", "chiffchaff", "blackcap", "whitethroat"]):
            return "Warblers"
        if "tit" in c:
            return "Tits"
        if any(x in c for x in ["finch", "linnet", "redpoll", "siskin", "bunting"]):
            return "Finches & Buntings"
        if any(x in c for x in ["thrush", "blackbird", "fieldfare", "redwing"]):
            return "Thrushes"
        if any(x in c for x in ["sparrowhawk", "buzzard", "kestrel", "owl", "falcon", "kite", "hawk"]):
            return "Raptors & Owls"
        if any(x in c for x in ["duck", "goose", "swan", "crake", "moorhen", "coot", "grebe", "teal", "mallard"]):
            return "Waterfowl & Rails"
        if any(x in c for x in ["pigeon", "dove"]):
            return "Pigeons & Doves"
        if any(x in c for x in ["crow", "magpie", "jay", "rook", "jackdaw", "raven"]):
            return "Corvids"
        if any(x in c for x in ["woodpecker"]):
            return "Woodpeckers"
        return "Other Birds"

def render_bat_audio_player(audio_url: str, default_rate: float = 0.1):
    """
    Renders a Web Audio API player for ultrasonic bat audio.
    Crucially drops pitch without preservation so ultrasound (>20kHz) becomes audible to humans.
    """
    html_code = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <style>
      body {{
        margin: 0;
        padding: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        color: #1e293b;
        background: transparent;
      }}
      .player-box {{
        background: #f1f5f9;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        padding: 6px 10px;
        box-sizing: border-box;
      }}
      .controls-row {{
        display: flex;
        align-items: center;
        gap: 8px;
      }}
      .play-btn {{
        background: #0284c7;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 5px 12px;
        font-size: 0.8rem;
        font-weight: 600;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 4px;
        flex-shrink: 0;
        transition: background 0.15s ease;
      }}
      .play-btn:hover {{
        background: #0369a1;
      }}
      .time-text {{
        font-size: 0.72rem;
        color: #475569;
        font-variant-numeric: tabular-nums;
        min-width: 70px;
        text-align: right;
        flex-shrink: 0;
      }}
      .seek-bar {{
        flex-grow: 1;
        cursor: pointer;
        accent-color: #0284c7;
        height: 6px;
      }}
      .speed-row {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-top: 5px;
        padding-top: 4px;
        border-top: 1px dashed #cbd5e1;
        font-size: 0.72rem;
        color: #64748b;
      }}
      .speed-btn-group {{
        display: flex;
        gap: 4px;
      }}
      .speed-btn {{
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 4px;
        padding: 1px 6px;
        font-size: 0.68rem;
        cursor: pointer;
        color: #334155;
        transition: all 0.15s ease;
      }}
      .speed-btn.active {{
        background: #0284c7;
        color: #ffffff;
        border-color: #0284c7;
        font-weight: 600;
      }}
      .status-badge {{
        font-size: 0.7rem;
        color: #0369a1;
        font-weight: 500;
      }}
    </style>
    </head>
    <body>
    <div class="player-box">
      <div class="controls-row">
        <button id="playBtn" class="play-btn" onclick="togglePlay()">
          <span id="playIcon">▶</span> <span id="playLabel">Play</span>
        </button>
        <input type="range" id="seekBar" class="seek-bar" value="0" min="0" max="100" step="0.1" oninput="onSeekInput()" onchange="onSeekChange()">
        <span id="timeDisplay" class="time-text">0:00 / --:--</span>
      </div>
      <div class="speed-row">
        <span class="status-badge" id="statusBadge">🦇 10x Slower (Audible)</span>
        <div class="speed-btn-group">
          <button class="speed-btn {'active' if default_rate == 0.1 else ''}" id="btn-0.1" onclick="setSpeed(0.1)">0.1x (Audible)</button>
          <button class="speed-btn {'active' if default_rate == 0.25 else ''}" id="btn-0.25" onclick="setSpeed(0.25)">0.25x</button>
          <button class="speed-btn {'active' if default_rate == 0.5 else ''}" id="btn-0.5" onclick="setSpeed(0.5)">0.5x</button>
          <button class="speed-btn {'active' if default_rate == 1.0 else ''}" id="btn-1.0" onclick="setSpeed(1.0)">1.0x</button>
        </div>
      </div>
    </div>

    <script>
      let audioCtx = null;
      let audioBuffer = null;
      let currentSource = null;
      let isPlaying = false;
      let startCtxTime = 0;
      let offsetBufferTime = 0;
      let currentRate = {default_rate};
      let isSeeking = false;
      let animId = null;
      const audioUrl = "{audio_url}";

      function getAudioContext() {{
        if (!audioCtx) {{
          audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }}
        if (audioCtx.state === 'suspended') {{
          audioCtx.resume();
        }}
        return audioCtx;
      }}

      function formatTime(sec) {{
        if (isNaN(sec) || sec < 0) sec = 0;
        const m = Math.floor(sec / 60);
        const s = Math.floor(sec % 60);
        return m + ":" + (s < 10 ? "0" : "") + s;
      }}

      function updateUI() {{
        if (!audioBuffer) return;
        const bufDuration = audioBuffer.duration;
        const playDuration = bufDuration / currentRate;

        if (isPlaying && !isSeeking) {{
          const elapsedCtx = audioCtx.currentTime - startCtxTime;
          const currentBufPos = offsetBufferTime + (elapsedCtx * currentRate);
          
          if (currentBufPos >= bufDuration) {{
            stopPlayback(true);
            return;
          }}
          
          const currentPlayPos = currentBufPos / currentRate;
          document.getElementById("seekBar").value = (currentBufPos / bufDuration) * 100;
          document.getElementById("timeDisplay").textContent = formatTime(currentPlayPos) + " / " + formatTime(playDuration);
        }}
        
        if (isPlaying) {{
          animId = requestAnimationFrame(updateUI);
        }}
      }}

      async function togglePlay() {{
        const ctx = getAudioContext();
        if (isPlaying) {{
          pausePlayback();
          return;
        }}

        if (!audioBuffer) {{
          document.getElementById("playLabel").textContent = "Loading...";
          document.getElementById("playBtn").disabled = true;
          try {{
            const resp = await fetch(audioUrl);
            const arrayBuf = await resp.arrayBuffer();
            audioBuffer = await ctx.decodeAudioData(arrayBuf);
          }} catch (err) {{
            document.getElementById("playLabel").textContent = "Error";
            document.getElementById("statusBadge").textContent = "Failed to load audio";
            document.getElementById("playBtn").disabled = false;
            return;
          }}
          document.getElementById("playBtn").disabled = false;
        }}

        startPlaybackAt(offsetBufferTime);
      }}

      function startPlaybackAt(bufOffset) {{
        const ctx = getAudioContext();
        if (currentSource) {{
          try {{ currentSource.stop(); }} catch (e) {{}}
        }}

        offsetBufferTime = bufOffset;
        currentSource = ctx.createBufferSource();
        currentSource.buffer = audioBuffer;
        currentSource.playbackRate.value = currentRate;
        currentSource.connect(ctx.destination);
        
        startCtxTime = ctx.currentTime;
        currentSource.start(0, offsetBufferTime);
        isPlaying = true;

        document.getElementById("playIcon").textContent = "⏸";
        document.getElementById("playLabel").textContent = "Pause";
        
        cancelAnimationFrame(animId);
        animId = requestAnimationFrame(updateUI);
      }}

      function pausePlayback() {{
        if (!isPlaying) return;
        const ctx = getAudioContext();
        const elapsedCtx = ctx.currentTime - startCtxTime;
        offsetBufferTime = offsetBufferTime + (elapsedCtx * currentRate);
        
        if (currentSource) {{
          try {{ currentSource.stop(); }} catch (e) {{}}
          currentSource = null;
        }}
        isPlaying = false;
        cancelAnimationFrame(animId);

        document.getElementById("playIcon").textContent = "▶";
        document.getElementById("playLabel").textContent = "Play";
      }}

      function stopPlayback(resetToStart = false) {{
        if (currentSource) {{
          try {{ currentSource.stop(); }} catch (e) {{}}
          currentSource = null;
        }}
        isPlaying = false;
        cancelAnimationFrame(animId);
        
        document.getElementById("playIcon").textContent = "▶";
        document.getElementById("playLabel").textContent = "Play";
        
        if (resetToStart) {{
          offsetBufferTime = 0;
          document.getElementById("seekBar").value = 0;
          if (audioBuffer) {{
            const playDuration = audioBuffer.duration / currentRate;
            document.getElementById("timeDisplay").textContent = "0:00 / " + formatTime(playDuration);
          }}
        }}
      }}

      function onSeekInput() {{
        isSeeking = true;
        if (audioBuffer) {{
          const pct = parseFloat(document.getElementById("seekBar").value) / 100;
          const targetBufPos = pct * audioBuffer.duration;
          const targetPlayPos = targetBufPos / currentRate;
          const playDuration = audioBuffer.duration / currentRate;
          document.getElementById("timeDisplay").textContent = formatTime(targetPlayPos) + " / " + formatTime(playDuration);
        }}
      }}

      function onSeekChange() {{
        isSeeking = false;
        if (audioBuffer) {{
          const pct = parseFloat(document.getElementById("seekBar").value) / 100;
          const targetBufPos = pct * audioBuffer.duration;
          if (isPlaying) {{
            startPlaybackAt(targetBufPos);
          }} else {{
            offsetBufferTime = targetBufPos;
          }}
        }}
      }}

      function setSpeed(rate) {{
        currentRate = rate;
        ['0.1', '0.25', '0.5', '1.0'].forEach(s => {{
          const btn = document.getElementById('btn-' + s);
          if (btn) btn.classList.remove('active');
        }});
        const activeBtn = document.getElementById('btn-' + rate.toString());
        if (activeBtn) activeBtn.classList.add('active');

        if (rate === 0.1) {{
          document.getElementById("statusBadge").textContent = "🦇 10x Slower (Audible)";
        }} else if (rate === 1.0) {{
          document.getElementById("statusBadge").textContent = "⚡ Real-time (Ultrasonic)";
        }} else {{
          document.getElementById("statusBadge").textContent = (1 / rate).toFixed(1) + "x Slower";
        }}

        if (isPlaying) {{
          const ctx = getAudioContext();
          const elapsedCtx = ctx.currentTime - startCtxTime;
          const currentBufPos = offsetBufferTime + (elapsedCtx * currentSource.playbackRate.value);
          startPlaybackAt(currentBufPos);
        }} else if (audioBuffer) {{
          const playDuration = audioBuffer.duration / currentRate;
          const currentPlayPos = offsetBufferTime / currentRate;
          document.getElementById("timeDisplay").textContent = formatTime(currentPlayPos) + " / " + formatTime(playDuration);
        }}
      }}
    </script>
    </body>
    </html>
    """
    components.html(html_code, height=72)

def render_recording_explorer(deployments=None):
    """Renders the main Recording & Spectrogram Explorer page."""
    st.markdown("## 🎙️ Bioacoustic Recording & Spectrogram Explorer")
    st.caption(
        "Browse edge-captured spectrograms, listen to synced audio recordings, and audit "
        "machine learning classifications (**BatDetect2** & **BirdNET**) against central InfluxDB telemetry."
    )

    # --- SIDEBAR CONTROLS ---
    st.sidebar.markdown("### 🎛️ Explorer Controls")

    # 1. Device Selection
    available_s3_folders = s3_service.list_available_s3_devices()
    if not available_s3_folders:
        st.warning("No recording folders detected in the S3 bucket.")
        return

    # Build friendly device names for display
    device_options = {}
    for folder in available_s3_folders:
        friendly = s3_service.S3_FOLDER_TO_DEVICE_NAME.get(folder, folder)
        disp = f"{friendly} ({folder})"
        device_options[disp] = folder

    selected_disp = st.sidebar.selectbox(
        "Select Device Node:",
        options=list(device_options.keys()),
        index=0,
        help="Select which Acoupi monitoring node to explore."
    )
    selected_s3_folder = device_options[selected_disp]

    # 2. Taxa Category Selection
    taxa_option = st.sidebar.radio(
        "Taxa Category:",
        options=["🦇 Bats (BatDetect2)", "🐦 Birds (BirdNET)"],
        index=0,
        help="Switch between ultrasonic bat recordings and acoustic bird recordings."
    )
    taxa_key = "bat" if "bat" in taxa_option.lower() else "bird"

    # 3. Date Selection
    available_dates = s3_service.list_available_dates(selected_s3_folder, taxa_key)
    if not available_dates:
        st.info(f"No {taxa_key} recordings found for **{selected_disp}** in the S3 bucket.")
        return

    selected_date = st.sidebar.selectbox(
        "Select Recording Date:",
        options=available_dates,
        index=0,
        help="Dates with recordings available in the bucket."
    )

    # 4. Bat Ultrasonic Playback Speed Setting
    is_bat = (taxa_key == "bat")
    playback_speed = "0.1x (10x slower - Audible)"
    speed_float = 0.1
    if is_bat:
        playback_speed = st.sidebar.select_slider(
            "Default Bat Playback Rate:",
            options=["0.1x (10x slower - Audible)", "0.25x (4x slower)", "0.5x (2x slower)", "1.0x (Real-time)"],
            value="0.1x (10x slower - Audible)",
            help="Bat calls are ultrasonic (>20kHz). Slowing down audio time-expands high frequencies into the human hearing range."
        )
        if "0.1x" in playback_speed:
            speed_float = 0.1
        elif "0.25x" in playback_speed:
            speed_float = 0.25
        elif "0.5x" in playback_speed:
            speed_float = 0.5
        elif "1.0x" in playback_speed:
            speed_float = 1.0

    # --- FETCH & CORRELATE DATA ---
    with st.spinner(f"Loading recordings for {selected_date}..."):
        raw_recordings = s3_service.list_recordings_for_day(selected_s3_folder, taxa_key, selected_date)
        enriched_recordings = metadata_correlator.correlate_recordings_with_detections(
            raw_recordings, selected_s3_folder, selected_date, tolerance_seconds=4
        )

    if not enriched_recordings:
        st.warning(f"No recordings found for {selected_disp} on {selected_date}.")
        return

    # Attach species type / group to each recording
    for r in enriched_recordings:
        r["species_group"] = get_species_group(
            r.get("common_name", ""),
            r.get("scientific_name", ""),
            taxa_key.capitalize()
        )

    # --- FILTERS IN SIDEBAR ---
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔍 Filters & Sorting")

    # 1. Species Group / Type Filter
    available_groups = sorted(list(set(r["species_group"] for r in enriched_recordings)))
    group_options = ["All Species Types"] + [g for g in available_groups if g != "Uncorrelated / Unclassified"]
    if "Uncorrelated / Unclassified" in available_groups:
        group_options.append("Uncorrelated / Unclassified")

    selected_group = st.sidebar.selectbox(
        "Species Type / Group:",
        options=group_options,
        index=0,
        help="Filter by ecological group (e.g. Pipistrelles, Myotis, Warblers, Finches)."
    )

    # 2. Specific Species Multiselect Filter with Counts
    species_counts = {}
    for r in enriched_recordings:
        name = r.get("common_name")
        if name and name != "Uncorrelated Detection":
            species_counts[name] = species_counts.get(name, 0) + 1

    species_display_map = {}
    for sp_name, count in sorted(species_counts.items(), key=lambda x: -x[1]):
        label = f"{sp_name} ({count})"
        species_display_map[label] = sp_name

    selected_species_labels = st.sidebar.multiselect(
        "Filter Specific Species:",
        options=list(species_display_map.keys()),
        default=[],
        help="Choose one or more specific species to isolate (leave empty to show all)."
    )
    selected_species_names = [species_display_map[lbl] for lbl in selected_species_labels]

    # 3. Minimum Confidence Threshold
    min_confidence = st.sidebar.slider(
        "Min Confidence Threshold:",
        min_value=0.0,
        max_value=1.0,
        value=0.0,
        step=0.05,
        format="%.2f",
        help="Filter out detections below this model confidence score."
    )

    # 4. Telemetry Match Status Filter
    match_filter = st.sidebar.radio(
        "Classification Status:",
        options=["All Recordings", "Matched Telemetry Only", "Uncorrelated Audio Only"],
        index=0
    )

    # 5. Sort By Option
    sort_option = st.sidebar.selectbox(
        "Sort Recordings By:",
        options=[
            "🕒 Time: Earliest First (00:00 → 23:59)",
            "🕒 Time: Latest First (23:59 → 00:00)",
            "🎯 Confidence: Highest First (100% → 0%)",
            "🎯 Confidence: Lowest First (0% → 100%)",
            "🔤 Species Name: A → Z",
            "🔤 Species Name: Z → A",
        ],
        index=0
    )

    # --- APPLY FILTERS ---
    filtered_recordings = []
    for r in enriched_recordings:
        # Group filter
        if selected_group != "All Species Types" and r["species_group"] != selected_group:
            continue
        # Specific species multiselect filter
        if selected_species_names and r["common_name"] not in selected_species_names:
            continue
        # Confidence filter
        if min_confidence > 0:
            if r["confidence"] is None or r["confidence"] < min_confidence:
                continue
        # Match status filter
        if match_filter == "Matched Telemetry Only" and not r["matched"]:
            continue
        if match_filter == "Uncorrelated Audio Only" and r["matched"]:
            continue

        filtered_recordings.append(r)

    # --- APPLY SORTING ---
    if "Earliest First" in sort_option:
        filtered_recordings.sort(key=lambda r: r["timestamp"] if r.get("timestamp") else datetime.min.replace(tzinfo=timezone.utc))
    elif "Latest First" in sort_option:
        filtered_recordings.sort(key=lambda r: r["timestamp"] if r.get("timestamp") else datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    elif "Confidence: Highest First" in sort_option:
        filtered_recordings.sort(key=lambda r: (r["confidence"] if r.get("confidence") is not None else -1.0, r["timestamp"]), reverse=True)
    elif "Confidence: Lowest First" in sort_option:
        filtered_recordings.sort(key=lambda r: (r["confidence"] if r.get("confidence") is not None else 999.0, r["timestamp"]))
    elif "Species Name: A → Z" in sort_option:
        filtered_recordings.sort(key=lambda r: (r.get("common_name", "zzz"), r["timestamp"]))
    elif "Species Name: Z → A" in sort_option:
        filtered_recordings.sort(key=lambda r: (r.get("common_name", ""), r["timestamp"]), reverse=True)

    # --- SUMMARY KPI METRICS ---
    total_recs = len(enriched_recordings)
    matched_count = sum(1 for r in enriched_recordings if r["matched"])
    match_pct = (matched_count / total_recs * 100) if total_recs > 0 else 0

    matched_species_list = [r["common_name"] for r in enriched_recordings if r["matched"] and r["common_name"] != "Uncorrelated Detection"]
    top_species = pd.Series(matched_species_list).mode().iloc[0] if matched_species_list else "None"

    conf_values = [r["confidence"] for r in enriched_recordings if r.get("confidence") is not None]
    avg_conf = f"{sum(conf_values) / len(conf_values):.1%}" if conf_values else "N/A"

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Day Recordings", f"{total_recs:,}")
    m2.metric("Telemetry Matched", f"{matched_count:,} ({match_pct:.0f}%)")
    m3.metric("Species Detected", f"{len(set(matched_species_list))}")
    m4.metric("Top Species", top_species)
    m5.metric("Avg Confidence", avg_conf)

    st.markdown("---")

    # --- PAGINATION & VIEW CONTROLS ---
    items_per_page = 12
    total_filtered = len(filtered_recordings)

    if total_filtered == 0:
        st.info("No recordings match your current filters. Try resetting the species type, clearing the species multiselect, or lowering the confidence slider.")
        return

    total_pages = max(1, math.ceil(total_filtered / items_per_page))

    col_nav1, col_nav2 = st.columns([3, 2])
    with col_nav1:
        st.markdown(f"**Showing {total_filtered} of {total_recs} recordings** · Sorted by: *{sort_option.split('(')[0].strip()}*")
    with col_nav2:
        current_page = st.number_input(
            f"Page (1 to {total_pages}):",
            min_value=1,
            max_value=total_pages,
            value=1,
            step=1,
            key="rec_explorer_page"
        )

    start_idx = (current_page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, total_filtered)
    page_items = filtered_recordings[start_idx:end_idx]

    # --- RECORDING GALLERY GRID (3 Columns) ---
    st.markdown(f"#### 🖼️ Gallery — Page {current_page} of {total_pages}")
    cols_per_row = 3

    for row_start in range(0, len(page_items), cols_per_row):
        row_items = page_items[row_start : row_start + cols_per_row]
        cols = st.columns(cols_per_row)

        for idx, item in enumerate(row_items):
            with cols[idx]:
                with st.container(border=True):
                    # 1. Header with Time & Telemetry Status
                    time_label = item["time_display"]
                    if item["matched"]:
                        conf = item.get("confidence") or 0.0
                        if conf >= 0.8:
                            badge_color = "#10b981"  # Emerald green
                            conf_label = "High Confidence"
                        elif conf >= 0.5:
                            badge_color = "#f59e0b"  # Amber
                            conf_label = "Medium Confidence"
                        else:
                            badge_color = "#ef4444"  # Red
                            conf_label = "Low Confidence"

                        st.markdown(
                            f"<div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;'>"
                            f"  <span style='font-size: 0.95rem; font-weight: 600;'>⏱️ {time_label}</span>"
                            f"  <span style='background: {badge_color}22; color: {badge_color}; border: 1px solid {badge_color}; border-radius: 12px; padding: 2px 8px; font-size: 0.75rem; font-weight: 600;'>{conf_label}</span>"
                            f"</div>",
                            unsafe_allow_html=True
                        )
                    else:
                        st.markdown(
                            f"<div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;'>"
                            f"  <span style='font-size: 0.95rem; font-weight: 600;'>⏱️ {time_label}</span>"
                            f"  <span style='background: #64748b22; color: #64748b; border: 1px solid #64748b; border-radius: 12px; padding: 2px 8px; font-size: 0.75rem;'>No Influx Record</span>"
                            f"</div>",
                            unsafe_allow_html=True
                        )

                    # 2. Spectrogram Image
                    st.image(
                        item["spect_url"],
                        caption=f"{item['filename']}",
                        width="stretch"
                    )

                    # 3. Species Classification Pill with Species Group Tag
                    if item["matched"]:
                        taxa_icon = "🦇" if item.get("category") == "Bat" else "🐦"
                        sp_group = item.get("species_group", "")
                        st.markdown(
                            f"<div style='background: #f8fafc; dark:background: #1e293b; padding: 8px; border-radius: 8px; margin-bottom: 8px;'>"
                            f"  <div style='display: flex; justify-content: space-between; align-items: baseline;'>"
                            f"    <span style='font-size: 1.02rem; font-weight: bold;'>{taxa_icon} {item['common_name']}</span>"
                            f"    <span style='font-size: 0.72rem; color: #64748b; background: #e2e8f0; border-radius: 4px; padding: 1px 5px;'>{sp_group}</span>"
                            f"  </div>"
                            f"  <div style='font-size: 0.8rem; color: #64748b; font-style: italic; margin-top: 2px;'>{item['scientific_name']}</div>"
                            f"  <div style='font-size: 0.85rem; margin-top: 4px;'>Score: <b>{item['confidence_pct']}</b></div>"
                            f"</div>",
                            unsafe_allow_html=True
                        )
                    else:
                        st.markdown(
                            f"<div style='background: #f8fafc; padding: 8px; border-radius: 8px; margin-bottom: 8px;'>"
                            f"  <div style='font-size: 0.95rem; color: #64748b;'><i>Audio captured — no paired telemetry</i></div>"
                            f"</div>",
                            unsafe_allow_html=True
                        )

                    # 4. Audio Player
                    audio_url = item["audio_url"]
                    if is_bat:
                        # Dedicated Web Audio API player for ultrasonic time expansion
                        render_bat_audio_player(audio_url, default_rate=speed_float)
                    else:
                        st.audio(audio_url, format="audio/wav")

                    # 5. Direct Links to Audio and Spectrogram
                    col_link1, col_link2 = st.columns(2)
                    with col_link1:
                        st.markdown(f"[📥 WAV File]({audio_url})")
                    with col_link2:
                        st.markdown(f"[🔍 Full PNG]({item['spect_url']})")

    st.markdown("---")
    st.caption("Bioacoustic Spectrogram & Audio Explorer · Powered by Silo S3, InfluxDB, & Streamlit")
