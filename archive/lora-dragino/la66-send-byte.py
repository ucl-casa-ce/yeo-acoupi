#!/usr/bin/env python3
import socket
import time

def send_true_hex_payload(raw_bytes, fport=2):
    """
    Sends a literal bytearray or list of bytes over the network.
    e.g., raw_bytes = [0x00, 0xFA]
    """
    # Convert byte list to string format for the Dragino AT parser command line
    hex_str = "".join(f"{b:02X}" for b in raw_bytes)
    payload_len = len(raw_bytes)

    # Structure the command
    command = f"AT+SENDB=0,{fport},{payload_len},{hex_str}\r\n"

    print(f"[*] Dispatching Command: {command.strip()}")

    # Send instantly to the local socket port 7500
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(('127.0.0.1', 7500))  #
    s.sendall(command.encode('utf-8'))  #

    # Give the bridge a moment to process before closing the connection
    time.sleep(0.5)
    s.close()
    print("[*] Payload sent to bridge successfully.")

if __name__ == "__main__":
    # Example: Simulating a 2-byte sensor value payload
    # Let's say your sensor output reads 250 -> 0x00FA in Hex
    sensor_data = [0x00, 0xFA]

    # Corrected function call name
    send_true_hex_payload(sensor_data, fport=2)