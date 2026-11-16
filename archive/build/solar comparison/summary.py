import pandas as pd

df_uno = pd.read_csv('SolarHistory-UNO-Q.csv')
df_rpi = pd.read_csv('SolarHistory-RPI5.csv')

print("Uno Q Statistics:")
print(df_uno[['Yield(Wh)', 'Consumption(Wh)', 'Max. PV power(W)', 'Min. battery voltage(V)', 'Max. battery voltage(V)']].mean())

print("\nRPi 5 Statistics:")
print(df_rpi[['Yield(Wh)', 'Consumption(Wh)', 'Max. PV power(W)', 'Min. battery voltage(V)', 'Max. battery voltage(V)']].mean())

# Compare daily differences
df_uno['Net_Wh'] = df_uno['Yield(Wh)'] - df_uno['Consumption(Wh)']
df_rpi['Net_Wh'] = df_rpi['Yield(Wh)'] - df_rpi['Consumption(Wh)']

print(f"\nUno Q Total Yield: {df_uno['Yield(Wh)'].sum()} Wh, Total Consumption: {df_uno['Consumption(Wh)'].sum()} Wh")
print(f"RPi 5 Total Yield: {df_rpi['Yield(Wh)'].sum()} Wh, Total Consumption: {df_rpi['Consumption(Wh)'].sum()} Wh")

print("\nRPi 5 Detailed Head:")
print(df_rpi[['Date', 'Yield(Wh)', 'Consumption(Wh)', 'Max. PV power(W)', 'Min. battery voltage(V)', 'Max. battery voltage(V)']].head(10))

print("Uno Q Avg Time in States (minutes):")
print(df_uno[['Time in bulk(m)', 'Time in absorption(m)', 'Time in float(m)']].mean())

print("\nRPi 5 Avg Time in States (minutes):")
print(df_rpi[['Time in bulk(m)', 'Time in absorption(m)', 'Time in float(m)']].mean())

print("Uno Q Max Values:")
print(df_uno[['Yield(Wh)', 'Consumption(Wh)', 'Max. PV power(W)']].max())

print("\nRPi 5 Max Values:")
print(df_rpi[['Yield(Wh)', 'Consumption(Wh)', 'Max. PV power(W)']].max())

