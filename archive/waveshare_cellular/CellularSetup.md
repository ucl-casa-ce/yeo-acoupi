# UNO Q Cellular Setup

> **See also: [`UNO_Q_CELLULAR_SETUP.md`](../../docs/UNO_Q_CELLULAR_SETUP.md) (in `docs/`)** —
> the full end-to-end runbook (driver + app + systemd service + cetools MQTT +
> radio-off), the cellular equivalent of `UNO_Q_LA66_SETUP.md`. **This file** is the
> lower-level bring-up/troubleshooting background (power isolation, minicom, AT
> provisioning, SMS, and why PPP fails); start with the runbook, come here for depth.

Comprehensive Setup & Troubleshooting Guide: Arduino UNO Q + Waveshare A7670E (Cat-1)
This document outlines the architecture and configuration required to successfully bridge an Arduino UNO Q to a Waveshare A7670E Cellular HAT running on the UK Giffgaff/O2 network.

### 1. Hardware Architecture & Power Isolation
The Issue: Power Brownouts and Network Drops
During initial setup, attempting to send the first AT command via SSH caused the UNO Q to immediately drop its network connection and lock up, returning a No route to host error.

This happens because the Cat-1 cellular modem pulls massive transient current spikes (up to 2 Amps) when waking up the radio from idle or initiating a transmission. Standard computer USB ports (capped at 500mA - 900mA) cannot sustain this load. The resulting voltage drop causes the UNO Q's Linux environment to silently crash and reboot.

The Solution: Split Power Domains
To bypass the USB bottleneck, the power domains must be completely isolated while bridging the data via UART.

UNO Q Power: Plugged directly into the host PC via USB-C. This provides standard 5V/500mA operating power and maintains the local data/SSH connection.

Waveshare HAT Power: Plugged into an independent 2A+ USB wall adapter via its own USB port to absorb the cellular transmission spikes.

Data Bridge (Jumper Wires):

GND on HAT ➔ GND on UNO Q (Critical: They must share a common ground reference for data to be readable).

TX on HAT ➔ RX (Pin 0) on UNO Q

RX on HAT ➔ TX (Pin 1) on UNO Q

WARNING: The 5V pins must never be connected between the two boards.

### 2. Serial Communication Diagnostics
The Issue: Frozen Terminal Input
When opening a serial bridge to the modem using minicom, the terminal completely rejected keyboard input. This is caused by minicom enabling Hardware Flow Control by default, causing the software to wait indefinitely for RTS/CTS hardware signals that are not physically wired.

The Solution: Bypassing Flow Control
Launch minicom with the -o flag to skip initialization and flow control, targeting the assigned USB serial port:

Bash
sudo minicom -D /dev/ttyUSB2 -b 115200 -o
(To exit cleanly and release the port: Ctrl+A ➔ Z ➔ X)

### 3. Network Provisioning (Giffgaff/O2)
The Issue: +CREG: 3 (Registration Denied)
Even with a verified, active Giffgaff SIM card, the modem successfully read the SIM (+CPIN: READY) and registered strong signal (+CSQ: 28,0), but the network explicitly rejected the connection (+CREG: 3).

This occurs because many Cat-1 IoT modems default to an "Auto" network scan and attempt to attach to legacy 3G bands. UK providers (including O2/Giffgaff) are actively decommissioning 3G networks, resulting in an immediate denial of service.

The Solution: Forcing 4G LTE
The modem must be locked strictly to LTE bands. In the serial terminal, issue the following sequence:

AT+CFUN=1,1 (Reboots the modem to clear cached 3G network states)

AT+CNMP=38 (Forces LTE-only mode)

AT+CEREG? (Checks EPS Network Registration for 4G. Look for the second digit to be 1 (Home) or 5 (Roaming).

### 4. SMS Transmission via Python
The Issue: +CMS ERROR: 305
When executing a Python script via pyserial to send a test SMS, the modem threw Error 305 (Invalid Text Mode Parameter). The Cat-1 module firmware requires explicit formatting headers before it will accept an outgoing message payload.

The Solution: Defining Text Parameters
The script must explicitly set the character set to GSM and define the text mode validity parameters using AT+CSMP before initiating the message.

Python
```
import serial, time

PORT = "/dev/ttyUSB2"
modem = serial.Serial(PORT, baudrate=115200, timeout=1)

# Format headers required for Cat-1 Modems
modem.write(b'AT+CSCS="GSM"\r')
time.sleep(1)
modem.write(b'AT+CMGF=1\r')
time.sleep(1)
modem.write(b'AT+CSMP=17,167,0,0\r') # Resolves Error 305
time.sleep(1)

# Send Message
modem.write(b'AT+CMGS="+447000000000"\r')
time.sleep(1)
modem.write(b'Hello from the UNO Q!\x1A') # \x1A represents Ctrl+Z
```

### 5. Architectural Strategy: Telemetry Routing
For continuous IoT data transmission, sending IP packets is vastly more efficient than SMS. We proved the hardware can successfully push JSON payloads via the modem's internal MQTT stack using AT commands (AT+CMQTTSTART, AT+CMQTTCONNECT, etc.).

However, for production deployment, configuring the Point-to-Point Protocol (PPP) daemon is recommended over managing raw AT commands in the application layer.

Rationale for PPP
Because the UNO Q features a dual-brain architecture, the Qualcomm Linux MPU is already running. Bypassing standard microcontroller constraints opens up a much more robust approach to data handling.

By configuring Linux chatscripts to dial the modem, the Waveshare HAT binds to the OS as a native network interface (ppp0). This perfectly supports an offline-first architecture. If the cellular connection drops in the field, the local Python application doesn't need to wrestle with raw serial timeouts or manual AT command retries. It simply caches the sensor data locally. Once the OS re-establishes the ppp0 connection, standard Python libraries (paho-mqtt or requests) handle the socket routing to smoothly synchronize the backlog to the cloud.

To maintain battery efficiency in this always-on Linux state, the application can still explicitly call pon and poff to bring the ppp0 interface up and down, dropping the modem's radio into a low-power idle state between sync windows.


## Final Version:

### The New Architecture
Local Queue (Offline-First): Your main application reads sensors and saves the JSON payloads to a local queue on the UNO Q (e.g., a simple SQLite database, a Redis queue, or even appending to a .jsonl file).

The "Flusher" Script: A secondary Python script runs on a cron schedule (e.g., every 15 minutes) or is triggered when the queue hits a certain size.

The Execution: The flusher script wakes the modem via serial, iterates through the local queue, uses AT commands to publish the data to your MQTT broker, deletes the successfully transmitted rows from the local database, and then turns the modem radio off.

The Production-Ready AT MQTT Script
Here is the blueprint for that "Flusher" script. It wraps the AT commands we tested earlier into a robust function that can process a queue of data, complete with error handling.

Create a new file called mqtt_gateway.py:

Python
```
import serial
import time
import json

PORT = "/dev/ttyUSB2"
BAUDRATE = 115200

def send_at(ser, command, wait=1, expected_prompt=False):
    ser.write((command + "\r").encode())
    time.sleep(wait)
    if expected_prompt:
        return
    return ser.read_all().decode(errors='ignore').strip()

def flush_queue_to_cloud():
    # In a real app, you would load these from your SQLite DB or local file
    # For this example, here is our "local queue" of offline data
    local_queue = [
        {"timestamp": 1718000000, "sensor": "temp", "val": 22.4},
        {"timestamp": 1718003600, "sensor": "temp", "val": 23.1}
    ]
    
    if not local_queue:
        print("Queue empty. Nothing to send.")
        return

    try:
        modem = serial.Serial(PORT, baudrate=BAUDRATE, timeout=1)
        
        print("Waking modem and attaching to network...")
        send_at(modem, "AT+CFUN=1")
        send_at(modem, "AT+CGACT=1,1", wait=3)
        
        print("Starting MQTT Session...")
        send_at(modem, "AT+CMQTTSTART", wait=2)
        send_at(modem, 'AT+CMQTTACCQ=0,"unoq_gateway"', wait=1)
        send_at(modem, 'AT+CMQTTCONNECT=0,"tcp://broker.hivemq.com:1883",60,1', wait=4)
        
        topic = "unoq/telemetry"
        
        # Iterate through your saved offline data
        for item in local_queue:
            payload = json.dumps(item)
            print(f"Publishing: {payload}")
            
            # 1. Set Topic
            send_at(modem, f'AT+CMQTTTOPIC=0,{len(topic)}', wait=0.5, expected_prompt=True)
            modem.write((topic + "\r").encode())
            time.sleep(0.5)
            
            # 2. Set Payload
            send_at(modem, f'AT+CMQTTPAYLOAD=0,{len(payload)}', wait=0.5, expected_prompt=True)
            modem.write((payload + "\r").encode())
            time.sleep(0.5)
            
            # 3. Publish
            send_at(modem, "AT+CMQTTPUB=0,1,60", wait=1)
            
            # If successful, you would now delete this item from your local SQLite DB here.

        print("Queue flushed successfully. Tearing down connection.")
        send_at(modem, "AT+CMQTTDISC=0,60", wait=1)
        send_at(modem, "AT+CMQTTSTOP", wait=1)
        
        # Turn off the radio to save power until the next queue flush
        send_at(modem, "AT+CFUN=0")

    except Exception as e:
        print(f"Critical error during transmission: {e}")
    finally:
        if 'modem' in locals() and modem.is_open:
            modem.close()

if __name__ == "__main__":
    flush_queue_to_cloud()

```

## What didn't work:

The linux distro used in the UNO Q's set up has been stripped back and does not include the libraries that would allow use of the PPP0 libraries for clean network interaction.

Addendum: The Ideal Linux Gateway (PPP) & Why It Failed
In an always-on Linux environment (like the UNO Q's Qualcomm MPU), relying on manual AT commands within application code is generally a fallback mechanism. The industry standard for routing cellular IoT data is configuring the Point-to-Point Protocol (PPP) daemon.

This section documents what the ideal configuration looks like, and the kernel-level limitations that prevented its deployment on this specific board.

The Objective: Native Network Integration
The goal of PPP is to dial the modem and bind it to the Debian operating system as a native network interface (resulting in a new interface called ppp0 alongside wlan0 or eth0).

The Architectural Advantages:

Native Python Routing: Instead of parsing serial buffers, developers can use standard, highly robust Python libraries (like paho-mqtt or requests) which automatically route traffic over the active ppp0 interface.

OS-Level Queuing: It perfectly supports offline-first architectures. If the cellular connection drops, the local application simply writes to a local SQLite database. When the OS detects ppp0 is restored, the application effortlessly syncs the queue to the cloud.

The Execution (What Should Have Worked)
To establish this connection, two configuration files are required in the Debian OS:

#### 1. The Chatscript (/etc/chatscripts/giffgaff)
This script tells the daemon how to navigate the modem's AT commands, inject the correct APN, and dial the universal cellular data number (*99#).

Plaintext
```
ABORT "BUSY"
ABORT "NO CARRIER"
ABORT "ERROR"
TIMEOUT 30
"" AT
OK ATE0
OK AT+CMEE=2
OK AT+CSQ
OK AT+CREG?
OK AT+CGDCONT=1,"IP","giffgaff.com"
OK ATD*99#
CONNECT ''
```

#### 2. The Peer Configuration (/etc/ppp/peers/giffgaff)
This file links the hardware port to the chatscript and defines the network routing rules.

Plaintext
```
/dev/ttyUSB2 115200
connect 'chat -s -v -f /etc/chatscripts/giffgaff'
hide-password
noauth
defaultroute
usepeerdns
persist
maxfail 0
```

With these files in place, executing sudo pon giffgaff should instruct the OS to dial the modem, authenticate with the tower, and spin up the ppp0 interface.

The Point of Failure: The Stripped Kernel
Upon executing the connection command and manually attempting to create the hardware node (sudo mknod /dev/ppp c 108 0), the connection permanently failed with the following errors:

modprobe: FATAL: Module ppp_generic not found in directory /lib/modules/7.0.0-g122c2c22d838
/usr/sbin/pppd: Please load the ppp_generic kernel module.

The Root Cause:
When compiling the custom embedded Debian image for the UNO Q, the board manufacturer aggressively stripped down the Linux kernel to save storage space and memory. As a result, the ppp_generic kernel module—the core networking code required for the OS to translate cellular serial data into TCP/IP packets—was completely removed from kernel build 7.0.0-g122c2c22d838.

Because the OS lacks the fundamental kernel support to route PPP traffic, the daemon cannot attach to the network stack, rendering native Linux cellular routing impossible on this firmware version.

The Pivot:
To restore functionality without undertaking the massive technical debt of recompiling a custom Linux kernel from source, the architecture was pivoted back to the modem's internal firmware. By using a Python "Flusher" script, we replicated the offline-first queuing logic locally, and pushed the data to the cloud using the modem's built-in AT-command MQTT stack over /dev/ttyUSB2.