// Permanent Linux-to-LoRa Bridge for Uno Q
// Hardware: Shield Inner TXD -> Pin 0, Inner RXD -> Pin 1

void setup() {
  // 1. Force TX (Pin 1) to drive a strong 3.3V idle high signal
  pinMode(1, OUTPUT); 
  digitalWrite(1, HIGH); 

  // 2. Clear the Uno Q Cold Boot Floating RX Bug
  pinMode(0, INPUT_PULLUP); 
  delay(100); 
  pinMode(0, INPUT); 

  // 3. Open the Internal Link to Linux (arduino-router / Port 7500)
  Serial.begin(9600);
  while (!Serial);

  // 4. Open the Physical Link to the LA66 Shield
  Serial1.begin(9600); 
}

void loop() {
  // Pass data from Linux Loopback Socket (Serial) -> Down to the LA66 Shield (Serial1)
  if (Serial.available()) {
    Serial1.write(Serial.read());
  }

  // Pass data from the LA66 Shield (Serial1) -> Up to the Linux Loopback Socket (Serial)
  if (Serial1.available()) {
    Serial.write(Serial1.read());
  }
}