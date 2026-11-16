#!/bin/bash
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
CPU_LOAD=$(cat /proc/loadavg | awk '{print $1}')
RAM_DISK=$(du -sh /run/shm | awk '{print $1}')

echo "${TIMESTAMP}, ${CPU_LOAD}, ${RAM_DISK}" >> /home/arduino/bioacoustics/data/power_proxy.log