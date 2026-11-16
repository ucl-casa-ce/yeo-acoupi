# Acoupi BirdNet Build on RPi5

Assumptions: 
* Using latest build of 64-bit RPi and connected to wifi
* You can SSH into the device.
* User account is pi (and you have a note of login password)
* Device was setup to use Raspberry Pi Connect

## Check for audio
Set up audio moth as USB mic:
- audiomoth flash app, select usb from firmware menu
- set to frequency required (44kHz for BirdNet, 192kHz for BatDetect)
- flip switch to default

Install Audacity so that we can check audio recordings
`sudo apt install audacity`

`sudo apt update` and `sudo apt upgrade`

Run `arecord -l` to test mic and then `arecord -D plughw:1,0 -c 1 -r 192000 -f S16_LE -t wav test_audiomoth.wav` to create test wav file to look at in Audacity

To listen to audio recording I downloaded locally using:
`scp pi@192.168.1.31:/home/pi/test_audiomoth.wav ~/Desktop`

## Setup virtual environment for acoupi

Note: need to use Python < 3.12 

Download and install miniforge

`wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh`

`bash Miniforge3-Linux-aarch64.sh`

Close and re-open shell 

`conda create -n bird_env python=3.11`

`conda activate bird_env`

## Install Acoupi

Notes base on repo here [https://acoupi.github.io/acoupi/](https://acoupi.github.io/acoupi/)

`curl -sSL https://github.com/acoupi/acoupi/raw/main/scripts/setup.sh | bash`

`pip install acoupi_birdnet`

Run the program set-up

`acoupi setup --program acoupi_birdnet.program`

I like to edit config file in Nano so use jq to prettify the JSON

`sudo apt-get install jq`

`python3 -m json.tool ~/.acoupi/config/program.json > temp.json && mv temp.json ~/.acoupi/config/program.json` 

## Helpers for debugging

Keeping an eye on how quickly .wav files are cleared out of temporary memory by the detection algo

`watch -n 0.5 "ls -lh /run/shm/*.wav"`


Keeping an eye on live logs to see what acoupi is doing:

`tail -f ~/.acoupi/log/recording.log ~/.acoupi/log/default.log`