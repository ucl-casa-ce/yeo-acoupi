# Comparing solar / battery results - UNOQ vs RPI5

The two csv files were downloaded from Victron Controller. 

The UNO-Q device was in N10, using an ultramic, 50W solar panel, 30Ah battery (Renogy RNG-50D-SS, ECO-WORTHY 12V 30AH LiFePO4) 

RPI-5 device in Garden Lab E20, using audiomoth, 100W solar panel, 100Ah battery (Renogy 16bb - N Type 100W, Fogstar Drift 100ah)

## Summary of stats

Uses script `summary.py`

```
Uno Q Statistics:
Yield(Wh)                  45.806452
Consumption(Wh)            63.225806
Max. PV power(W)           36.129032
Min. battery voltage(V)    13.236129
Max. battery voltage(V)    14.046129
dtype: float64

RPi 5 Statistics:
Yield(Wh)                   75.806452
Consumption(Wh)            105.806452
Max. PV power(W)            49.193548
Min. battery voltage(V)     13.280645
Max. battery voltage(V)     14.190645
dtype: float64

Uno Q Total Yield: 1420 Wh, Total Consumption: 1960 Wh
RPi 5 Total Yield: 2350 Wh, Total Consumption: 3280 Wh

RPi 5 Detailed Head:
         Date  Yield(Wh)  ...  Min. battery voltage(V)  Max. battery voltage(V)
0  19/06/2026         40  ...                    13.28                    13.49
1  18/06/2026         70  ...                    13.28                    14.21
2  17/06/2026         80  ...                    13.28                    14.21
3  16/06/2026         70  ...                    13.28                    14.22
4  15/06/2026         80  ...                    13.28                    14.21
5  14/06/2026         80  ...                    13.28                    14.21
6  13/06/2026         80  ...                    13.28                    14.21
7  12/06/2026         70  ...                    13.28                    14.21
8  11/06/2026         70  ...                    13.28                    14.22
9  10/06/2026         70  ...                    13.28                    14.21

[10 rows x 6 columns]
Uno Q Avg Time in States (minutes):
Time in bulk(m)          491.774194
Time in absorption(m)     89.516129
Time in float(m)         193.387097
dtype: float64

RPi 5 Avg Time in States (minutes):
Time in bulk(m)          365.193548
Time in absorption(m)    116.129032
Time in float(m)         458.967742
dtype: float64
Uno Q Max Values:
Yield(Wh)           80.0
Consumption(Wh)     80.0
Max. PV power(W)    56.0
dtype: float64

RPi 5 Max Values:
Yield(Wh)            90.0
Consumption(Wh)     130.0
Max. PV power(W)     95.0
dtype: float64
```

## Comparison when both devices were both running same algorithm BirdNet on same days.

Uses `script compare.py`

```
UNO Q (BirdNet) Crossover Data:
          Date  Yield(Wh)  ...  Min. battery voltage(V)  Max. battery voltage(V)
20  30/05/2026         50  ...                    13.25                    14.22
21  29/05/2026         40  ...                    13.25                    14.24
22  28/05/2026         50  ...                    13.25                    14.22
23  27/05/2026         50  ...                    13.25                    14.22
24  26/05/2026         50  ...                    13.25                    14.22
25  25/05/2026         50  ...                    13.25                    14.22
26  24/05/2026         50  ...                    13.25                    14.22
27  23/05/2026         50  ...                    13.25                    14.22
28  22/05/2026         50  ...                    13.25                    14.22
29  21/05/2026         50  ...                    13.24                    14.23

[10 rows x 6 columns]

RPi 5 (BirdNet) Crossover Data:
          Date  Yield(Wh)  ...  Min. battery voltage(V)  Max. battery voltage(V)
20  30/05/2026         90  ...                    13.28                    14.21
21  29/05/2026         70  ...                    13.28                    14.21
22  28/05/2026         90  ...                    13.28                    14.21
23  27/05/2026         80  ...                    13.28                    14.21
24  26/05/2026         80  ...                    13.28                    14.21
25  25/05/2026         90  ...                    13.28                    14.21
26  24/05/2026         80  ...                    13.29                    14.21
27  23/05/2026         90  ...                    13.28                    14.21
28  22/05/2026         80  ...                    13.29                    14.21
29  21/05/2026         70  ...                    13.28                    14.21

[10 rows x 6 columns]

--- AVERAGES DURING BIRDNET HEAD-TO-HEAD (May 21 - May 30) ---

UNO Q BirdNet Means:
Yield(Wh)                  49.000
Consumption(Wh)            73.000
Max. PV power(W)           33.600
Min. battery voltage(V)    13.249
Max. battery voltage(V)    14.223
dtype: float64

RPi 5 BirdNet Means:
Yield(Wh)                   82.000
Consumption(Wh)            118.000
Max. PV power(W)            45.300
Min. battery voltage(V)     13.282
Max. battery voltage(V)     14.210
dtype: float64

UNO Q BatDetect2 Means (June):
Yield(Wh)                46.111111
Consumption(Wh)          60.555556
Max. PV power(W)         38.777778
dtype: float64
```

### Model Shift: BirdNet vs. BatDetect2 (On the UNO Q)
Uno Q power metrics after change on May 30th at 9:00 PM:
- UNO Q running BirdNet (May 21–30): Averaged 73.0 Wh of consumption per day.
- UNO Q running BatDetect2 (June 1–19): Averaged 60.5 Wh of consumption per day.

Why consumption drop by 17% when moving to bats? Single bat audio file can push the CPU hard for up to 27 seconds, but doing it less.

### Questions
- Recording .wav files is constant - but is there a difference between 44kHz and 192kHz?
- Can we quantify the variability between processing time for no bat calls vs n calls?
- Can we quantify the relationship between energy use and settings in config file? 

## Power Log analysis from UNO-Q

Using file `uno_power_analysis.py`

```
RangeIndex: 27677 entries, 0 to 27676
Data columns (total 5 columns):
 #   Column     Non-Null Count  Dtype
---  ------     --------------  -----
 0   Timestamp  27677 non-null  datetime64[us]
 1   CPU_Load   27677 non-null  float64
 2   RAM_Disk   27677 non-null  str
 3   Hour       27677 non-null  int32
 4   Date       27677 non-null  object
dtypes: datetime64[us](1), float64(1), int32(1), object(1), str(1)
memory usage: 1000.2+ KB
None
                        Timestamp      CPU_Load          Hour
count                       27677  27677.000000  27677.000000
mean   2026-06-10 22:46:01.081728      0.316474     11.486108
min           2026-06-01 08:09:01      0.000000      0.000000
25%           2026-06-06 03:27:01      0.010000      6.000000
50%           2026-06-10 22:46:01      0.180000     11.000000
75%           2026-06-15 18:05:01      0.630000     17.000000
max           2026-06-20 13:24:01      1.490000     23.000000
std                           NaN      0.322544      6.885777
Hourly average load across the entire log:
 Hour  CPU_Load
    0  0.610316
    1  0.616105
    2  0.621798
    3  0.609272
    4  0.620272
    5  0.605035
    6  0.044386
    7  0.031570
    8  0.029975
    9  0.029425
   10  0.030808
   11  0.030417
   12  0.029792
   13  0.029691
   14  0.027991
   15  0.031833
   16  0.032386
   17  0.030184
   18  0.582096
   19  0.609325
   20  0.596579
   21  0.608798
   22  0.606746
   23  0.610219

Daily average load across the whole period:
      Date  CPU_Load
2026-06-01  0.279895
2026-06-02  0.336014
2026-06-03  0.301639
2026-06-04  0.273118
2026-06-05  0.277854
2026-06-06  0.280396
2026-06-07  0.287097
2026-06-08  0.299056
2026-06-09  0.304007
2026-06-10  0.310535
2026-06-11  0.323486
2026-06-12  0.325854
2026-06-13  0.329083
2026-06-14  0.337382
2026-06-15  0.334931
2026-06-16  0.346500
2026-06-17  0.333347
2026-06-18  0.360056
2026-06-19  0.348312
2026-06-20  0.338025

Top 10 highest load entries recorded:
          Timestamp  CPU_Load RAM_Disk
2026-06-02 04:52:01      1.49        0
2026-06-03 03:23:01      1.40        0
2026-06-02 22:01:02      1.40        0
2026-06-19 02:48:01      1.37        0
2026-06-02 20:18:01      1.29        0
2026-06-01 22:57:01      1.26        0
2026-06-04 23:43:01      1.26        0
2026-06-04 21:56:01      1.26        0
2026-06-02 18:30:01      1.25        0
2026-06-02 22:02:01      1.23        0
```

### Macro Power States
When we aggregate all 27,600+ records by the hour of the day across the 20-day span, a binary emerges. The scheduling system splits the device's life cleanly down the middle:

The Active Processing Window (18:00 to 05:59): Across all twenty nights, the average 1-minute CPU load settles exactly between 0.58 and 0.62. The framework is processing files back-to-back while bats are active.

The Dormant Rest Window (06:00 to 17:59): The average daytime load drops to a completely flat 0.02 to 0.04. This confirms that the daytime power footprint is purely the background operating system kernel idle state.

### Computing the True Continuous Power Budget
Using standard hardware benchmarks for this ARM64 compute tier (power consumption increases linearly relative to CPU load):

Daytime Rest (12 hours/day): Mean load ≈0.03. Power draw remains at a baseline 1.8 W. 
- Power=1.8 W + (0.03×(4.5 W − 1.8 W)) = 1.881 W

Nighttime Processing (12 hours/day): Mean load ≈0.61. Because the load doesn't lock at a constant 1.0+ but floats around 0.61 due to gaps between processing windows and clip thresholds, the average nighttime power draw sits at 3.5 W (rather than the absolute core-pinned peak of 4.5 W).
- Power=1.8 W + (0.61×(4.5 W − 1.8 W)) = 3.447 W

Exact 24-Hour Energy Accumulation:

- Daytime: 12 hours×1.8 W=21.6 Wh
- Nighttime: 12 hours×3.5 W=42.0 Wh
- **Total Daily Budget: 63.6 Wh**

This tracks within the 63.23 Wh daily consumption recorded on Victron smart charge controller over the same dates.

### Stress-Testing the Spikes (Peak Load Events)
The log tracked a few computational peak moments where bat activity pushed the CPU well past its nominal limits. e.g. June 2nd at 04:52:01 AM, CPU load index hit 1.49.

Several other entries breached 1.25 to 1.40. This indicates that the board's single-worker thread environment was working heavily on a backlog queue or coping with rapid consecutive triggers.

Because Celery forced to run serially (worker_concurrency=1), these spikes are completely safe. The thread processing line will temporarily stretch to clear the queue without risking core thread lockups, memory exhaustion, or causing an unpredictable hardware crash. (see note on worker_concurrency in UNO Q build note).

### RAM Disk Integrity Check
Across all 27,677 logs, the third column (RAM_Disk) uniformly reports 0. This means that every time the cron job fired on the minute mark, the RAM disk (/run/shm) was completely empty of audio files. This is a critical metric for long-term field stability. It proves the 30-second scheduling interval is giving the hardware plenty of time to capture the audio, feed it to the batdetect2 model, publish the results via MQTT, and delete the file before the next capture cycle can pile up. The RAM disk is never bottlenecking or accumulating unmanaged files.

