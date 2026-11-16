import pandas as pd

# Load files
df_uno = pd.read_csv('SolarHistory-UNO-Q.csv')
df_rpi = pd.read_csv('SolarHistory-RPI5.csv')

# Ensure standard date parsing or clean filtering since they look like DD/MM/YYYY strings
# Let's see the rows for dates between 21/05/2026 and 30/05/2026
dates_of_interest = [
    '21/05/2026', '22/05/2026', '23/05/2026', '24/05/2026', '25/05/2026',
    '26/05/2026', '27/05/2026', '28/05/2026', '29/05/2026', '30/05/2026'
]

df_uno_bird = df_uno[df_uno['Date'].isin(dates_of_interest)].copy()
df_rpi_bird = df_rpi[df_rpi['Date'].isin(dates_of_interest)].copy()

print("UNO Q (BirdNet) Crossover Data:")
print(df_uno_bird[['Date', 'Yield(Wh)', 'Consumption(Wh)', 'Max. PV power(W)', 'Min. battery voltage(V)', 'Max. battery voltage(V)']])

print("\nRPi 5 (BirdNet) Crossover Data:")
print(df_rpi_bird[['Date', 'Yield(Wh)', 'Consumption(Wh)', 'Max. PV power(W)', 'Min. battery voltage(V)', 'Max. battery voltage(V)']])

print("\n--- AVERAGES DURING BIRDNET HEAD-TO-HEAD (May 21 - May 30) ---")
print("\nUNO Q BirdNet Means:")
print(df_uno_bird[['Yield(Wh)', 'Consumption(Wh)', 'Max. PV power(W)', 'Min. battery voltage(V)', 'Max. battery voltage(V)']].mean())

print("\nRPi 5 BirdNet Means:")
print(df_rpi_bird[['Yield(Wh)', 'Consumption(Wh)', 'Max. PV power(W)', 'Min. battery voltage(V)', 'Max. battery voltage(V)']].mean())


# Let's check UNO Q performance when running BatDetect2 (June 1st to June 19th)
june_dates = [d for d in df_uno['Date'].unique() if '/06/2026' in d and d != '19/06/2026'] # exclude day 0 if partial
df_uno_bat = df_uno[df_uno['Date'].isin(june_dates)]

print("UNO Q BatDetect2 Means (June):")
print(df_uno_bat[['Yield(Wh)', 'Consumption(Wh)', 'Max. PV power(W)']].mean())