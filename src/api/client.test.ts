import { beforeEach, describe, expect, it, vi } from 'vitest';
import { airshieldApi, ApiError } from './client';

function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }); }

describe('AirShield API client', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));

  it('validates latest persisted readings and distinguishes an empty stale store', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ readings: [], source: 'airshield_database', fetched_at: '2026-10-28T12:00:00Z', stale: true }));
    await expect(airshieldApi.latest()).resolves.toMatchObject({ readings: [], stale: true });
    vi.mocked(fetch).mockResolvedValueOnce(json({ readings: [{ co2: 'bad' }], source: 'thingspeak', fetched_at: '2026-10-28T12:00:00Z', stale: false }));
    await expect(airshieldApi.latest()).rejects.toMatchObject({ status: 502, code: 'invalid_api_response' });
  });

  it('parses conservative analytics exceedance duration with reading origin', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({
      reading_count: 2,
      ingestion_mode_counts: { live: 1, historical_backfill: 1 },
      metrics: {},
      alert_counts_by_severity: {},
      alert_counts_by_parameter: {},
      exceedance_durations: [{ rule_id: 'r1', parameter: 'co2', operator: 'gt', threshold: 900, unit: 'ppm', ingestion_mode: 'historical_backfill', observed_seconds: 60, observed_intervals: 1 }],
    }));
    await expect(airshieldApi.summary('2026-10-28T00:00:00Z', '2026-10-29T00:00:00Z'))
      .resolves.toMatchObject({ exceedance_durations: [{ ingestion_mode: 'historical_backfill', observed_seconds: 60 }] });
  });

  it('uses the alert mutation route, body, and an in-memory bearer token', async () => {
    const alert = { id: 3, device_id: 'sensor-1', rule_id: 'r1', reading_id: 8, category: 'environmental', health_state: null, status: 'acknowledged', parameter: 'co2', observed_value: 1500, threshold: 1000, unit: 'ppm', severity: 'warning', message: 'CO2 high', recommended_action: 'Ventilate', created_at: '2026-10-28T12:00:00Z', condition_started_at: '2026-10-28T11:59:00Z', acknowledged_at: '2026-10-28T12:01:00Z', acknowledged_by: null, resolved_at: null, resolution: null };
    vi.mocked(fetch).mockResolvedValueOnce(json(alert));
    await expect(airshieldApi.acknowledgeAlert(3, 'session-token')).resolves.toMatchObject({ status: 'acknowledged' });
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toContain('/alerts/3/acknowledge');
    expect(init?.method).toBe('POST');
    expect(new Headers(init?.headers).get('authorization')).toBe('Bearer session-token');
    expect(init?.body).toBe('{}');
  });

  it('resolves alerts through the lifecycle API and sends a bounded trend window', async () => {
    const alert = { id: 4, device_id: 'sensor-1', rule_id: 'r1', reading_id: 8, category: 'environmental', health_state: null, status: 'resolved', parameter: 'co2', observed_value: 1500, threshold: 1000, unit: 'ppm', severity: 'warning', message: 'CO2 high', recommended_action: 'Ventilate', created_at: '2026-10-28T12:00:00Z', condition_started_at: '2026-10-28T11:59:00Z', acknowledged_at: null, acknowledged_by: null, resolved_at: '2026-10-28T12:01:00Z', resolution: 'manual' };
    vi.mocked(fetch).mockResolvedValueOnce(json(alert));
    await expect(airshieldApi.resolveAlert(4, '')).resolves.toMatchObject({ status: 'resolved' });
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain('/alerts/4/resolve');
    vi.mocked(fetch).mockResolvedValueOnce(json({ parameter: 'pm25', unit: 'µg/m³', interval_seconds: 60, samples_in_range: 0, ingestion_modes: [], points: [] }));
    await airshieldApi.trend('pm25', '2026-10-28T00:00:00Z', '2026-10-29T00:00:00Z');
    const url = new URL(String(vi.mocked(fetch).mock.calls[1][0]));
    expect(url.searchParams.get('parameter')).toBe('pm25');
    expect(url.searchParams.get('start_at')).toBe('2026-10-28T00:00:00Z');
    expect(url.searchParams.get('end_at')).toBe('2026-10-29T00:00:00Z');
  });

  it('surfaces backend rule validation failures without claiming success', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ detail: { code: 'validation_error', message: 'Rule configuration is invalid' } }, 422));
    await expect(airshieldApi.createRule({ parameter: 'co2', operator: 'gt', threshold: 1, unit: '°C', severity: 'warning', enabled: true, persistence_seconds: 0, recovery_threshold: null, message: 'Rule', recommended_action: 'Check' }, ''))
      .rejects.toMatchObject({ status: 422, message: 'Rule configuration is invalid' });
  });

  it('returns safe API errors when a backend responds with non-JSON content', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('database password hidden', { status: 503, headers: { 'content-type': 'text/plain' } }));
    try { await airshieldApi.ready(); throw new Error('expected failure'); }
    catch (error) { expect(error).toBeInstanceOf(ApiError); expect((error as Error).message).toBe('AirShield API returned HTTP 503.'); expect((error as Error).message).not.toContain('password'); }
  });

  it('parses persisted device-health alerts without inventing measurements', async () => {
    const healthAlert = { id: 12, device_id: 'sensor-1', rule_id: null, reading_id: null, category: 'device_health', health_state: 'offline', status: 'active', parameter: 'device_health', observed_value: null, threshold: null, unit: 'state', severity: 'critical', message: 'Device offline', recommended_action: 'Check power and network', created_at: '2026-10-28T12:00:00Z', condition_started_at: '2026-10-28T11:00:00Z', acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolution: null };
    vi.mocked(fetch).mockResolvedValueOnce(json(healthAlert));
    await expect(airshieldApi.alert(12)).resolves.toMatchObject({ category: 'device_health', health_state: 'offline', observed_value: null, threshold: null });
  });
});
