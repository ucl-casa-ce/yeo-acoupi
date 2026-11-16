# Acoupi BatDetect2 Setup Blueprint
Assumptions
* Board is pre-configured with password and Wi-Fi via Arduino App Lab.
* You can SSH into the device.

### System-Level Dependencies & Math Libraries
Before touching Python, we must install the native hardware math packages. Installing numpy and scipythrough apt prevents the board from failing complex local legacy Fortran compilations.

```sudo apt update 
sudo apt upgrade -y
sudo apt install -y log2ram python3-pip python3.13-venv libsndfile1 build-essential portaudio19-dev
```

CRITICAL: Native ARM64 system libraries for optimization and compiling audio dependencies
```
sudo apt install -y python3-numpy python3-scipy gfortran libopenblas-dev liblapack-dev pkg-config rabbitmq-server jq
```

Enable and verify the RabbitMQ queue broker right away

```
sudo systemctl enable rabbitmq-server 
sudo systemctl start rabbitmq-server
```

### Environment Setup (Isolated Standalone Python 3.11)
Since Debian Trixie pushes Python 3.13 and Acoupi needs Python 3.11, we use uv.
Install UV Python manager

```
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

Create Workspace

```
mkdir ~/bioacoustics && cd ~/bioacoustics 
```

CRITICAL FIX: Build the venv using system-site-packages so it bridges directly to the native system math libraries installed above

```
uv venv --python 3.11 --system-site-packages
source .venv/bin/activate
```

### Audio & Storage Verification
Check Mic

```
arecord -l
arecord -D hw:0,0 -d 5 -f S16_LE -r 48000 test.wav
```
*(Verify download to your laptop via desktop terminal):* 
```
scp arduino@<device_ip>:~/bioacoustics/test.wav ~/Desktop/
```

### Format & Auto-Mount SD Card

```
lsblk # Identify card name (usually /dev/sda1)
sudo mkfs.ext4 /dev/sda1
sudo blkid /dev/sda1 # Copy the generated UUID="..." string
```

Open fstab:
```
sudo nano /etc/fstab
```
Add line matching your file system layout (**ext4** is highly recommended over **vfat** for long-term field stability):

For EXT4 formatted cards:
```
UUID=your-uuid-here /home/arduino/bioacoustics/data ext4 defaults,noatime 0 2
```

For VFAT/exFAT formatted cards:
```
UUID=your-uuid-here /home/arduino/bioacoustics/data vfat defaults,noatime,uid=1000,gid=1000,umask=000 0 2
```

Create mount directory and apply paths:
```
mkdir -p ~/bioacoustics/data
sudo mount -a
```

### Native Application & Pure CPU Math Installation
This is the critical adjustment area for UNO Q. We must strictly prevent Python from downloading generic internet binaries packed with conflicting CPU vector calculations or unexecutable hidden NVIDIA GPU/CUDA binaries.

Force the installation of a stable CPU-only version of PyTorch 
```
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu --no-cache-dir
```

Force the installation of a generic math fallback stream of ONNX Runtime
```
uv pip install onnxruntime==1.16.3 --no-cache-dir
```

Force-compile the underlying bioacoustic tools directly on your local target metal 
```
uv pip install acoupi
uv pip install acoupi-batdetect2 --no-binary batdetect2,acoupi-batdetect2
```

### System Configuration (program.json & celery.json)
Run standard setup using the proper pipeline target:
```
acoupi setup --program acoupi_batdetect2.program
```

Use the following strict values during interactive setup or inline configuration:
* **paths.tmp_audio**: /run/shm *(Keeps heavy reading/writing isolated to RAM, saving SD card lifespans)*
* **paths.recordings**: /home/arduino/bioacoustics/data/recordings
* **paths.metadata_db**: /home/arduino/bioacoustics/data/metadata.db
* **paths.messages_db**: /home/arduino/bioacoustics/data/messages.db
* **topic**: yeo/unoq-bat/acoupi

Critical Infrastructure Updates via jq
Run these explicit commands to inject your custom network configurations and processing concurrency overrides directly into your runtime configs:
Force MQTT to use Port 1884 and lengthen the TLS handshake timeout budget to 15 seconds

```
cat ~/.acoupi/config/program.json | jq '.messaging.mqtt.port=1884 | .messaging.mqtt.timeout=15' > ~/.acoupi/config/program_new.json && mv ~/.acoupi/config/program_new.json ~/.acoupi/config/program.json
```

BatDetect2 calculations take between 8 to 27 seconds on this processor, change the capture scheduling interval from 10 seconds to 30 or 45 seconds to keep the RAM disk stable.
```
cat ~/.acoupi/config/program.json | jq '.scheduler.interval=30' > ~/.acoupi/config/program_new.json && mv ~/.acoupi/config/program_new.json ~/.acoupi/config/program.json
```

Force Celery to execute tasks strictly one-at-a-time (Serially) to prevent internal ONNX thread deadlocking
```
cat ~/.acoupi/config/celery.json | jq '.worker_concurrency=1 | .task_acks_late=false' > ~/.acoupi/config/celery_new.json && mv ~/.acoupi/config/celery_new.json ~/.acoupi/config/celery.json
```

### Process Persistence & Deployment
Ensure background user tasks survive SSH disconnects:
```
sudo loginctl enable-linger arduino
```
### Clean Flush and Launch Sequence
Always wipe stale cache backlogs before starting a fresh run:
Wipe out lingering RAM disk files from previous boots
```
rm -f /run/shm/*.wav
rm -f ~/.acoupi/celerybeat-schedule.db
```

Purge any backlogged tracking states stuck in the RabbitMQ broker
```
sudo rabbitmqctl purge_queue celery
sudo rabbitmqctl purge_queue recording
```

### Start
```
acoupi check
acoupi deployment start
```

### Diagnostics
Monitor the Audio Engine loop
```
tail -f ~/.acoupi/log/recording.log
```

Monitor the BatDetect2 AI Processing layer 
```
tail -f ~/.acoupi/log/default.log
```

Watch Memory Processing Health. Ensure files are being cleanly evaluated and removed without creating pileups
```
watch -n 1 "ls -lh /run/shm"
```

### Safe Config Editing Loop
```
acoupi deployment stop
cat ~/.acoupi/config/program.json | jq . > ~/.acoupi/config/program_pretty.json && mv ~/.acoupi/config/program_pretty.json ~/.acoupi/config/program.json
nano ~/.acoupi/config/program.json
acoupi deployment start
```

### Copy bat files across to local machine
```
scp -r arduino@192.168.1.30:~/bioacoustics/data/recordings/bats/ ~/Desktop/bats/
```

### SW load monitoring of CPU and RAM disk size

```
nano ~/bioacoustics/track_power.sh
```

```
#!/bin/bash
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
CPU_LOAD=$(cat /proc/loadavg | awk '{print $1}')
RAM_DISK=$(du -sh /run/shm | awk '{print $1}')

echo "${TIMESTAMP}, ${CPU_LOAD}, ${RAM_DISK}" >> /home/arduino/bioacoustics/data/power_proxy.log
```

```
chmod +x ~/bioacoustics/track_power.sh
```

test script:
```
~/bioacoustics/track_power.sh
cat ~/bioacoustics/data/power_proxy.log
```
crontab -e
```
* * * * * /home/arduino/bioacoustics/track_power.sh
```

```
tail -f ~/bioacoustics/data/power_proxy.log
```

```
sort -t, -k2 -nr ~/bioacoustics/data/power_proxy.log | head -n 10
```

