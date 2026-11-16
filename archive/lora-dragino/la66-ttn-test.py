#!/usr/bin/env python3
import socket
import time
import struct
from datetime import datetime

# Simple Lookup Table for Species IDs
SPECIES_LUT = {
    "carwre": 1001,  # Carolina Wren
    "norcar": 1002,  # Northern Cardinal
    "UNKNOWN": 9999
}

def encode_and_send_birdnet(birdnet_json):
    # 1. Parse out the ISO timestamp string and convert to Unix Epoch (4 bytes)
    # Target format: 2026-06-25T11:45:00.000Z -> strip trailing Z for parsing
    time_str = birdnet_json["timestamp"].replace("Z", "")
    dt = datetime.fromisoformat(time_str)
    epoch_timestamp = int(dt.timestamp())

    # Start building our raw bytes payload with the 4-byte timestamp
    # '>' = big-endian, 'I' = unsigned 32-bit int
    payload_bytes = bytearray(struct.pack(">I", epoch_timestamp))

    # 2. Append each individual detection block (3 bytes each)
    detections = birdnet_json["payload"]["detections"]
    for det in detections:
        code = det["species_code"]
        species_id = SPECIES_LUT.get(code, SPECIES_LUT["UNKNOWN"])
        
        # Convert confidence float (0.94) to integer percentage (94)
        confidence_pct = int(round(det["confidence"] * 100))
        
        # 'H' = unsigned 16-bit int (2 bytes), 'B' = unsigned 8-bit int (1 byte)
        detection_block = struct.pack(">HB", species_id, confidence_pct)
        payload_bytes.extend(detection_block)

    # 3. Format into the strict Dragino AT execution command frame
    fport = 2
    payload_len = len(payload_bytes)
    hex_str = payload_bytes.hex().upper()
    command = f"AT+SENDB=0,{fport},{payload_len},{hex_str}\r\n"

    print(f"[*] Raw Encoded Hex: {hex_str}")
    print(f"[*] Dispatching Command: {command.strip()}")

    # 4. Ship instantly over the internal Linux system bridge
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(('127.0.0.1', 7500))  #
        s.sendall(command.encode('utf-8'))  #
        time.sleep(0.5)
        s.close()
        print("[*] BirdNET data safely offloaded to LoRa queue.")
    except Exception as e:
        print(f"[!] Transmission failed: {e}")

if __name__ == "__main__":
    # Your sample sample detection input
    sample_data = {
        "spec_version": "1.2",
        "timestamp": "2026-06-25T11:45:00.000Z",
        "event": "detection",
        "payload": {
            "recording_id": "rec-003948572",
            "filename": "backyard_soundscape_001.wav",
            "start_time": 30.0,
            "end_time": 33.0,
            "duration": 3.0,
            "detections": [
                { "species_code": "carwre", "confidence": 0.94 },
                { "species_code": "norcar", "confidence": 0.78 }
            ]
        }
    }
    
    encode_and_send_birdnet(sample_data)


