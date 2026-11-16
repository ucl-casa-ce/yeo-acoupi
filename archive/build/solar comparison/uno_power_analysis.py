import pandas as pd

# Load the file and parse it properly
data = []
with open('power_proxy.log', 'r') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('$'): # skip empty or corrupted text
            continue
        parts = line.split(',')
        if len(parts) == 3:
            data.append([parts[0].strip(), float(parts[1].strip()), parts[2].strip()])

df = pd.DataFrame(data, columns=['Timestamp', 'CPU_Load', 'RAM_Disk'])
df['Timestamp'] = pd.to_datetime(df['Timestamp'])
df['Hour'] = df['Timestamp'].dt.hour
df['Date'] = df['Timestamp'].dt.date

print(df.info())
print(df.describe())

# the average load by hour to chart day vs night differences across the whole 20-day log
hourly_load = df.groupby('Hour')['CPU_Load'].mean().reset_index()
print("Hourly average load across the entire log:")
print(hourly_load.to_string(index=False))

# Identify when BatDetect2 kicked off compared to BirdNet
# May 30 21:00 was the crossover point. Let's see the load profile by date
daily_load = df.groupby('Date')['CPU_Load'].mean().reset_index()
print("\nDaily average load across the whole period:")
print(daily_load.to_string(index=False))

# Find the maximum load spiked during this window
max_spikes = df.sort_values(by='CPU_Load', ascending=False).head(10)
print("Top 10 highest load entries recorded:")
print(max_spikes[['Timestamp', 'CPU_Load', 'RAM_Disk']].to_string(index=False))