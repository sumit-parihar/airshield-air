# AirShield ESP32 firmware scaffold

This is a hardware-neutral Arduino-ESP32 scaffold, not a tested firmware image. The project currently has no confirmed board, sensor models, bus wiring, pins, or VOC measurement semantics. The `readAirShieldSensors()` adapter intentionally returns no measurement until those are established; it never sends fabricated values.

## Local setup

1. Install Arduino IDE 2.x or PlatformIO with Espressif's Arduino-ESP32 core.
2. Select the actual ESP32 board after identifying its exact model.
3. Copy `secrets.h.example` to `secrets.h` and fill Wi-Fi SSID/password and the ThingSpeak **write** API key locally. `secrets.h` is excluded by `.gitignore`; do not commit it or share its contents.
4. Obtain and verify the current root CA certificate chain for `api.thingspeak.com`, then replace the explicit certificate placeholder in `airshield_esp32.ino`. TLS verification remains enabled; do not use `setInsecure()`.
5. Identify sensor model(s), libraries, interfaces (I²C/UART/analog/etc.), supply-voltage requirements, and pinout from the actual hardware. Implement sensor initialization and `readAirShieldSensors()` in the sketch or a local adapter. Pins are intentionally unspecified.
6. Confirm each sensor's units, warm-up behavior, calibration, and valid range. Set `CONFIRMED_VOC_UNIT` only after confirming VOC semantics. Until then, VOC field3 is omitted.
7. Compile for the selected board and test sensor reads locally before connecting writes. The sketch posts to ThingSpeak via HTTPS form body and accepts only a positive entry ID as success.

## ThingSpeak field contract

| Field | Payload | Expected AirShield meaning |
|---|---|---|
| field1 | optional number | CO₂, ppm |
| field2 | optional number | PM2.5, µg/m³ |
| field3 | optional number, withheld until VOC unit confirmation | VOC, sensor-specific units |
| field4 | optional number | Temperature, °C |
| field5 | optional number | Relative humidity, %RH |
| field6 | `online` | Firmware reports it has a valid sample and is publishing |
| field7 | optional number | Battery percentage only when the hardware provides it |

At least one valid environmental parameter is required per update. CO₂/PM2.5/VOC must be finite and nonnegative; temperature is limited to −40…85 °C; humidity to 0…100 %RH; battery to 0…100. Recheck these checks against the confirmed sensor datasheets. The 20-second send interval is conservative for common ThingSpeak channel limits; confirm the current channel/account policy before deployment and do not reduce it below the provider limit.

## Not yet verified

This scaffold has not been compiled for a particular ESP32 board, tested against a sensor, calibrated, or used to send a real feed. Tomorrow, fill the missing board, sensor, pins, libraries, certificate, and local secrets; compile; inspect Serial output without printing secrets; then verify an actual positive ThingSpeak entry ID and that the backend later persists that entry as a fresh `live` reading.
