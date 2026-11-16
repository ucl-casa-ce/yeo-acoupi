import socket
import sys

HOST = '127.0.0.1'
PORT = 7500

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)

print(f"--- Listening to port {PORT}. Press the Dragino physical RST button now... ---")

try:
    while True:
        data = s.recv(4096)
        if not data:
            print("\nConnection closed.")
            break

        # Print raw string representation
        print(f"Raw Text: {data.decode('utf-8', errors='ignore')}")
        # Print exact Hex representation to catch hidden structural headers
        print(f"Raw Hex : {data.hex()}\n")
except KeyboardInterrupt:
    print("\nExiting.")
finally:
    s.close()