// LA66 Bridge sketch for UNO Q  (STM32 side)
// Exposes two RPC methods to the Linux side via Arduino_RouterBridge:
//   la66_send(cmd)  -> writes one AT line to the LA66 on Serial1 (D0/D1)
//   la66_drain(_)   -> returns + clears everything the LA66 has said since last call
// This is the *reliable* IPC path (no raw :7500 contention). The Linux main.py
// drives the LA66 entirely through these RPC calls.
//
// Shield wiring: LA66 Inner TXD -> D0 (RX), Inner RXD -> D1 (TX); Serial1 @ 9600 8N1.
#include "Arduino_RouterBridge.h"

String rxbuf = "";

String la66_drain(int _unused) {   // return + clear buffered LA66 output
  String s = rxbuf;
  rxbuf = "";
  return s;
}

int la66_send(String cmd) {        // write one AT line to the LA66
  Serial1.print(cmd);
  Serial1.print("\r\n");
  return cmd.length();
}

void setup() {
  pinMode(1, OUTPUT); digitalWrite(1, HIGH);              // hold TX idle-high during boot
  pinMode(0, INPUT_PULLUP); delay(100); pinMode(0, INPUT); // clear floating RX
  Bridge.begin();                  // internal link to arduino-router (do NOT wait on Serial)
  Serial1.begin(9600);             // LA66 AT interface = 9600 8N1
  Bridge.provide("la66_drain", la66_drain);
  Bridge.provide("la66_send", la66_send);
}

void loop() {
  while (Serial1.available()) {
    char c = Serial1.read();
    if (rxbuf.length() < 2000) rxbuf += c;
  }
}
