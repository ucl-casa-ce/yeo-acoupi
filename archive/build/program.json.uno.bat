{
  "timezone": "Europe/London",
  "microphone": {
    "device_name": "UltraMic 192K 16 bit r4",
    "samplerate": 192000,
    "audio_channels": 1
  },
  "recording": {
    "duration": 3,
    "interval": 30,
    "chunksize": 8192,
    "schedule_start": "19:00:00",
    "schedule_end": "07:00:00"
  },
  "paths": {
    "tmp_audio": "/run/shm",
    "recordings": "/home/arduino/bioacoustics/data/recordings",
    "db_metadata": "/home/arduino/bioacoustics/data/metadata.db"
  },
  "messaging": {
    "messages_db": "/home/arduino/bioacoustics/data/messages.db",
    "message_send_interval": 120,
    "heartbeat_interval": 3600,
    "http": null,
    "mqtt": {
      "host": "mqtt.cetools.org",
      "username": "CEDevice",
      "password": "xxxxx",
      "topic": "yeo/unoq-bat/acoupi",
      "port": 1884,
      "timeout": 15
    }
  },
  "detections": {
    "threshold": 0.2
  },
  "model": {
    "detection_threshold": 0.4
  },
  "saving_filters": {
    "starttime": "19:00:00",
    "endtime": "07:00:00",
    "before_dawndusk_duration": 0,
    "after_dawndusk_duration": 0,
    "frequency_duration": 0,
    "frequency_interval": 0,
    "saving_threshold": 0.3
  },
  "saving_managers": {
    "true_dir": "bats",
    "false_dir": "no_bats",
    "timeformat": "%Y%m%d_%H%M%S",
    "bat_threshold": 0.5
  },
  "summariser_config": {
    "interval": 3600.0,
    "low_band_threshold": 0.0,
    "mid_band_threshold": 0.0,
    "high_band_threshold": 0.0
  }
}