/*
 * AirShield ESP32 ThingSpeak publisher scaffold.
 * Board, sensors, pins and sensor libraries are intentionally unassigned.
 * Complete readAirShieldSensors() only after the actual hardware is confirmed.
 */
#include <Arduino.h>
#include <HTTPClient.h>
#include <math.h>
#include <string.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include "secrets.h"

static constexpr char THINGSPEAK_UPDATE_URL[] = "https://api.thingspeak.com/update";
static constexpr uint32_t PUBLISH_INTERVAL_MS = 20000; // Keep within the channel/account write limit.
static constexpr uint32_t HTTP_TIMEOUT_MS = 10000;
static constexpr char CONFIRMED_VOC_UNIT[] = ""; // Fill only after confirming the installed VOC sensor semantics.
static constexpr char THINGSPEAK_ROOT_CA[] = R"CERT(
-----BEGIN CERTIFICATE-----
PASTE THE VERIFIED ROOT CA FOR api.thingspeak.com HERE
-----END CERTIFICATE-----
)CERT";

struct AirMeasurements {
  bool hasCO2 = false;
  bool hasPM25 = false;
  bool hasVOC = false;
  bool hasTemperature = false;
  bool hasHumidity = false;
  bool hasBattery = false;
  float co2 = NAN;
  float pm25 = NAN;
  float voc = NAN;
  float temperature = NAN;
  float humidity = NAN;
  float battery = NAN;
};

// Hardware adapter placeholder. Map confirmed sensor drivers and pins here.
// Return true only after reading at least one supported environmental parameter.
bool readAirShieldSensors(AirMeasurements &out) {
  (void)out;
  // TODO tomorrow: add the confirmed sensor libraries, bus setup, pin definitions,
  // warm-up handling, calibration and unit conversions. Do not return sample values.
  return false;
}

static bool finiteNonNegative(float value) {
  return isfinite(value) && value >= 0.0f;
}

static String formEncode(const String &value) {
  const char hex[] = "0123456789ABCDEF";
  String encoded;
  encoded.reserve(value.length() * 3);
  for (size_t i = 0; i < value.length(); ++i) {
    const uint8_t c = static_cast<uint8_t>(value[i]);
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
        (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '*') {
      encoded += static_cast<char>(c);
    } else if (c == ' ') {
      encoded += '+';
    } else {
      encoded += '%';
      encoded += hex[c >> 4];
      encoded += hex[c & 0x0F];
    }
  }
  return encoded;
}

static void appendField(String &body, const char *name, float value) {
  if (body.length()) body += '&';
  body += name;
  body += '=';
  body += String(value, 2);
}

static bool certificateConfigured() {
  return strstr(THINGSPEAK_ROOT_CA, "PASTE THE VERIFIED") == nullptr;
}

static bool vocUnitsConfigured() {
  return CONFIRMED_VOC_UNIT[0] != '\0';
}

bool publishMeasurements(const AirMeasurements &sample) {
  String body = "api_key=" + formEncode(AIRSHIELD_THINGSPEAK_WRITE_API_KEY);
  bool hasEnvironmentalValue = false;

  if (sample.hasCO2 && finiteNonNegative(sample.co2)) { appendField(body, "field1", sample.co2); hasEnvironmentalValue = true; }
  if (sample.hasPM25 && finiteNonNegative(sample.pm25)) { appendField(body, "field2", sample.pm25); hasEnvironmentalValue = true; }
  // Never send an unlabelled VOC value; confirm the sensor's output units first.
  if (sample.hasVOC && vocUnitsConfigured() && finiteNonNegative(sample.voc)) { appendField(body, "field3", sample.voc); hasEnvironmentalValue = true; }
  if (sample.hasTemperature && isfinite(sample.temperature) && sample.temperature >= -40.0f && sample.temperature <= 85.0f) { appendField(body, "field4", sample.temperature); hasEnvironmentalValue = true; }
  if (sample.hasHumidity && isfinite(sample.humidity) && sample.humidity >= 0.0f && sample.humidity <= 100.0f) { appendField(body, "field5", sample.humidity); hasEnvironmentalValue = true; }
  if (!hasEnvironmentalValue) {
    Serial.println("No valid configured sensor measurements; publish skipped.");
    return false;
  }
  if (sample.hasBattery && finiteNonNegative(sample.battery) && sample.battery <= 100.0f) appendField(body, "field7", sample.battery);
  if (body.length()) body += '&';
  body += "field6=";
  body += formEncode("online");

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi disconnected; publish deferred to the next interval.");
    return false;
  }
  if (!certificateConfigured()) {
    Serial.println("HTTPS root CA is not configured; publish skipped.");
    return false;
  }

  WiFiClientSecure tls;
  tls.setCACert(THINGSPEAK_ROOT_CA);
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  if (!http.begin(tls, THINGSPEAK_UPDATE_URL)) {
    Serial.println("ThingSpeak HTTPS setup failed.");
    return false;
  }
  http.addHeader("Content-Type", "application/x-www-form-urlencoded");
  const int status = http.POST(body);
  const String response = status > 0 ? http.getString() : String();
  http.end();
  body = ""; // Drop the request body containing the write key as soon as possible.

  const long entryId = response.toInt();
  if (status == HTTP_CODE_OK && entryId > 0) {
    Serial.printf("ThingSpeak accepted feed entry %ld.\n", entryId);
    return true;
  }
  Serial.printf("ThingSpeak publish failed (HTTP %d); no credential or response body logged.\n", status);
  return false;
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.begin(AIRSHIELD_WIFI_SSID, AIRSHIELD_WIFI_PASSWORD);
  Serial.println("AirShield ESP32 scaffold starting; waiting for Wi-Fi.");
  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 30000) delay(250);
  Serial.println(WiFi.status() == WL_CONNECTED ? "Wi-Fi connected." : "Wi-Fi not connected; will retry at next publish interval.");
}

void loop() {
  static uint32_t lastPublish = 0;
  if (millis() - lastPublish >= PUBLISH_INTERVAL_MS) {
    lastPublish = millis();
    if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
    AirMeasurements sample;
    if (readAirShieldSensors(sample)) publishMeasurements(sample);
    else Serial.println("Sensor adapter is not configured; no data published.");
  }
  delay(20);
}
