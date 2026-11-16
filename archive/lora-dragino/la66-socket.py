import socket
import sys
import threading
import time

HOST = '127.0.0.1'
PORT = 7500

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))
except Exception as e:
    print(f"Error connecting to bridge socket: {e}")
    sys.exit(1)

print(f"--- Connected to LA66 via Internal Linux Bridge Loopback (Port {PORT}) ---")
print("Type your AT commands below. Type 'exit' to quit.")

def receive_data(sock):
    while True:
        try:
            data = sock.recv(1024)
            if not data:
                print("\nBridge connection closed by host.")
                break
            sys.stdout.write(data.decode('utf-8', errors='ignore'))
            sys.stdout.flush()
        except Exception as e:
            break
        time.sleep(0.01)

recv_thread = threading.Thread(target=receive_data, args=(s,), daemon=True)
read_thread = recv_thread.start()

try:
    while True:
        user_input = input()
        if user_input.lower() == 'exit':
            break

        # Force uppercase as required by Dragino firmware
        command_text = user_input.upper()

        # Send the command word as a single quick burst
        s.sendall(command_text.encode('utf-8'))
        time.sleep(0.01) # Tiny pause before termination

        # Send the exact carriage return + line feed the parser expects
        s.sendall(b"\r\n")

except KeyboardInterrupt:
    pass

finally:
    s.close()
    print("\nSocket disconnected safely.")