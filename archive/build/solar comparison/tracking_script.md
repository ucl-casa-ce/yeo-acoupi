# Power Tracking Script for UNO Q

Set-up:

`nano ~/bioacoustics/track_power.sh`

copy in:

```
#!/bin/bash
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
CPU_LOAD=$(cat /proc/loadavg | awk '{print $1}')
RAM_DISK=$(du -sh /run/shm | awk '{print $1}')

echo "${TIMESTAMP}, ${CPU_LOAD}, ${RAM_DISK}" >> /home/arduino/bioacoustics/data/power_proxy.log
```

then make executable:

`chmod +x ~/bioacoustics/track_power.sh`

Then add to crontab -e

`* * * * * /home/arduino/bioacoustics/track_power.sh`

Verify using:

`tail -f ~/bioacoustics/data/power_proxy.log`
