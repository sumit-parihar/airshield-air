import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from './App';

const reading = { id: 1, provider_entry_id: 9, device_id: 'sensor-1', timestamp: '2026-10-28T08:00:00Z', co2: 890, pm25: null, voc: 12, voc_unit: 'sensor-index', temperature: 23.4, humidity: 45, device_status: null, battery: null, source: 'thingspeak', source_metadata: {}, ingestion_mode: null };
function response(data: unknown, status = 200) { return new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } }); }

describe('AirShield application', () => {
  it('shows an honest empty state and changes navigation content', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/health/ready')) return Promise.resolve(response({ status: 'ok', database: 'ready' }));
      if (url.includes('/readings/latest')) return Promise.resolve(response({ readings: [], source: 'thingspeak', fetched_at: '2026-10-28T12:00:00Z', stale: true }));
      if (url.includes('/alerts/active')) return Promise.resolve(response({ items: [], total: 0, offset: 0, limit: 100 }));
      if (url.endsWith('/alert-rules')) return Promise.resolve(response([]));
      if (url.includes('/devices?')) return Promise.resolve(response({ items: [], total: 0 }));
      if (url.includes('/notifications/in-app')) return Promise.resolve(response({ items: [], total: 0, offset: 0, limit: 50 }));
      if (url.includes('/analytics/trends')) return Promise.resolve(response({ parameter: 'co2', unit: 'ppm', interval_seconds: 60, samples_in_range: 0, ingestion_modes: [], points: [] }));
      return Promise.resolve(response({ detail: 'not found' }, 404));
    }));
    render(<App />);
    expect(await screen.findByText('No readings recorded yet')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Live monitoring' }));
    expect(await screen.findByRole('heading', { name: 'Measurements in context.' })).toBeInTheDocument();
    expect(screen.getByText('Waiting for the first reading')).toBeInTheDocument();
  });

  it('opens Alert Center and Settings with honest no-alert and no-rule states', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/health/ready')) return Promise.resolve(response({ status: 'ok', database: 'ready' }));
      if (url.includes('/readings/latest')) return Promise.resolve(response({ readings: [], source: 'airshield_database', fetched_at: new Date().toISOString(), stale: true }));
      if (url.includes('/alerts/active')) return Promise.resolve(response({ items: [], total: 0, offset: 0, limit: 100 }));
      if (url.endsWith('/alert-rules')) return Promise.resolve(response([]));
      if (url.includes('/devices?')) return Promise.resolve(response({ items: [], total: 0 }));
      if (url.includes('/notifications/in-app')) return Promise.resolve(response({ items: [], total: 0, offset: 0, limit: 50 }));
      if (url.includes('/analytics/trends')) return Promise.resolve(response({ parameter: 'co2', unit: 'ppm', interval_seconds: 60, samples_in_range: 0, ingestion_modes: [], points: [] }));
      return Promise.resolve(response({}, 404));
    }));
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: 'Alert center' }));
    expect(await screen.findByText('No active alerts are currently recorded')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(await screen.findByText('No alert rules configured')).toBeInTheDocument();
    expect(screen.getByText('Recorded means persisted in the API, not browser or email delivery.')).toBeInTheDocument();
  });

  it('shows persisted device-health alerts in Devices and Alert Center without measurements', async () => {
    const alert = { id: 19, device_id: 'sensor-1', rule_id: null, reading_id: null, category: 'device_health', health_state: 'offline', status: 'active', parameter: 'device_health', observed_value: null, threshold: null, unit: 'state', severity: 'critical', message: 'Device Sensor 1 is offline', recommended_action: 'Check power and network', created_at: new Date().toISOString(), condition_started_at: new Date(Date.now() - 3600000).toISOString(), acknowledged_at: null, acknowledged_by: null, resolved_at: null, resolution: null };
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/health/ready')) return Promise.resolve(response({ status: 'ok', database: 'ready' }));
      if (url.includes('/readings/latest')) return Promise.resolve(response({ readings: [], source: 'airshield_database', fetched_at: new Date().toISOString(), stale: true }));
      if (url.includes('/alerts/active')) return Promise.resolve(response({ items: [alert], total: 1, offset: 0, limit: 100 }));
      if (url.endsWith('/alert-rules')) return Promise.resolve(response([]));
      if (url.includes('/devices?')) return Promise.resolve(response({ items: [{ id: 'sensor-1', name: 'Sensor 1', location: null, status: 'offline', last_seen_at: null, last_successful_ingestion_at: null, last_ingestion_error_at: null, latest_reading: null, health_alert: alert }], total: 1 }));
      if (url.includes('/notifications/in-app')) return Promise.resolve(response({ items: [], total: 0, offset: 0, limit: 50 }));
      if (url.includes('/analytics/trends')) return Promise.resolve(response({ parameter: 'co2', unit: 'ppm', interval_seconds: 60, samples_in_range: 0, ingestion_modes: [], points: [] }));
      return Promise.resolve(response({}, 404));
    }));
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: 'Devices' }));
    expect(await screen.findByText('offline alert · active')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Alert center/ }));
    expect(await screen.findByText('Device health · offline')).toBeInTheDocument();
    expect(screen.queryByText(/Observed.*Threshold/)).not.toBeInTheDocument();
  });

  it('labels recent backfill as not live and never treats configured rows as online', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/health/ready')) return Promise.resolve(response({ status: 'ok', database: 'ready' }));
      if (url.includes('/readings/latest')) return Promise.resolve(response({ readings: [{ ...reading, timestamp: new Date().toISOString(), voc_unit: null, ingestion_mode: 'historical_backfill' }], source: 'airshield_database', fetched_at: new Date().toISOString(), stale: false }));
      if (url.includes('/alerts/active')) return Promise.resolve(response({ items: [], total: 0, offset: 0, limit: 100 }));
      if (url.endsWith('/alert-rules')) return Promise.resolve(response([]));
      if (url.includes('/devices?')) return Promise.resolve(response({ items: [{ id: 'sensor-1', name: 'Sensor 1', location: null, status: 'offline', last_seen_at: null, last_successful_ingestion_at: null, last_ingestion_error_at: null, latest_reading: null, health_alert: null }], total: 1 }));
      if (url.includes('/notifications/in-app')) return Promise.resolve(response({ items: [], total: 0, offset: 0, limit: 50 }));
      if (url.includes('/analytics/trends')) return Promise.resolve(response({ parameter: 'co2', unit: 'ppm', interval_seconds: 60, samples_in_range: 0, ingestion_modes: [], points: [] }));
      return Promise.resolve(response({}, 404));
    }));
    render(<App />);
    expect(await screen.findAllByText('Historical backfill · not live')).toHaveLength(2);
    expect(screen.queryByText('Fresh sensor data')).not.toBeInTheDocument();
    expect(screen.getByText('Unit unconfigured')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Devices' }));
    expect(await screen.findByText('offline')).toBeInTheDocument();
    expect(screen.getByText('Sensor 1')).toBeInTheDocument();
  });

  it('labels backfilled analytics separately and keeps an old latest feed stale', async () => {
    class TestResizeObserver implements ResizeObserver {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe(target: Element) {
        const entry = { target, contentRect: new DOMRect(0, 0, 640, 320) } as ResizeObserverEntry;
        this.callback([entry], this);
      }
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('ResizeObserver', TestResizeObserver);
    const liveTimestamp = new Date().toISOString();
    const backfillTimestamp = new Date(Date.now() - 60_000).toISOString();
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/health/ready')) return Promise.resolve(response({ status: 'ok', database: 'ready' }));
      if (url.includes('/readings/latest')) return Promise.resolve(response({ readings: [{ ...reading, ingestion_mode: 'historical_backfill' }], source: 'airshield_database', fetched_at: '2026-10-28T12:00:00Z', stale: true }));
      if (url.includes('/alerts/active')) return Promise.resolve(response({ items: [], total: 0, offset: 0, limit: 100 }));
      if (url.endsWith('/alert-rules')) return Promise.resolve(response([]));
      if (url.includes('/devices?')) return Promise.resolve(response({ items: [], total: 0 }));
      if (url.includes('/notifications/in-app')) return Promise.resolve(response({ items: [], total: 0, offset: 0, limit: 50 }));
      if (url.includes('/analytics/summary')) return Promise.resolve(response({
        reading_count: 2,
        ingestion_mode_counts: { live: 1, historical_backfill: 1 },
        metrics: {
          co2: { count: 2, minimum: 890, maximum: 950, average: 920, unit: 'ppm' },
          pm25: { count: 0, minimum: null, maximum: null, average: null, unit: 'µg/m³' },
          voc: { count: 0, minimum: null, maximum: null, average: null, unit: null },
          temperature: { count: 0, minimum: null, maximum: null, average: null, unit: '°C' },
          humidity: { count: 0, minimum: null, maximum: null, average: null, unit: '%RH' },
        },
        alert_counts_by_severity: { info: 0, warning: 0, critical: 0 },
        alert_counts_by_parameter: { co2: 0, pm25: 0, voc: 0, temperature: 0, humidity: 0 },
        exceedance_durations: [
          { rule_id: 'co2-rule', parameter: 'co2', operator: 'gt', threshold: 900, unit: 'ppm', ingestion_mode: 'live', observed_seconds: 60, observed_intervals: 1 },
          { rule_id: 'co2-rule', parameter: 'co2', operator: 'gt', threshold: 900, unit: 'ppm', ingestion_mode: 'historical_backfill', observed_seconds: 120, observed_intervals: 1 },
        ],
      }));
      if (url.includes('/analytics/trends')) return Promise.resolve(response({
        parameter: 'co2', unit: 'ppm', interval_seconds: 60, samples_in_range: 2,
        ingestion_modes: ['historical_backfill', 'live'],
        points: [
          { timestamp: backfillTimestamp, value: 890, sample_count: 1 },
          { timestamp: liveTimestamp, value: 950, sample_count: 1 },
        ],
      }));
      return Promise.resolve(response({}, 404));
    }));
    render(<App />);
    expect(await screen.findByText('Stale historical backfill · not live')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Historical analytics' }));
    expect(await screen.findByText(/2 readings · live 1 · backfill 1/)).toBeInTheDocument();
    expect(screen.getByText(/includes historical backfill/)).toBeInTheDocument();
    expect(screen.getByText('2m 0s')).toBeInTheDocument();
    expect(screen.getByText('1m 0s')).toBeInTheDocument();
    expect(screen.getByText('Historical backfill · stale, not live')).toBeInTheDocument();
    expect(screen.queryByText('Fresh sensor data')).not.toBeInTheDocument();
  });

  it('shows backend outage errors without replacing data with demo values', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response({ detail: { code: 'database_unavailable', message: 'Database is unavailable' } }, 503))));
    render(<App />);
    expect(await screen.findByText('Database is unavailable')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('No readings recorded yet')).toBeInTheDocument());
  });

  it('reports persisted-reading API failure while retaining backend and empty states', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/health/ready')) return Promise.resolve(response({ status: 'ok', database: 'ready' }));
      if (url.includes('/readings/latest')) return Promise.resolve(response({ detail: { code: 'database_unavailable', message: 'Persisted readings are unavailable' } }, 503));
      if (url.includes('/alerts/active')) return Promise.resolve(response({ items: [], total: 0, offset: 0, limit: 100 }));
      if (url.endsWith('/alert-rules')) return Promise.resolve(response([]));
      if (url.includes('/devices?')) return Promise.resolve(response({ items: [], total: 0 }));
      if (url.includes('/notifications/in-app')) return Promise.resolve(response({ items: [], total: 0, offset: 0, limit: 50 }));
      if (url.includes('/analytics/trends')) return Promise.resolve(response({ parameter: 'co2', unit: 'ppm', interval_seconds: 60, samples_in_range: 0, ingestion_modes: [], points: [] }));
      return Promise.resolve(response({ detail: 'not found' }, 404));
    }));
    render(<App />);
    expect(await screen.findByText('Persisted readings are unavailable')).toBeInTheDocument();
    expect(await screen.findByText('No readings recorded yet')).toBeInTheDocument();
    expect(screen.getAllByText('Backend connected · data issue').length).toBeGreaterThan(0);
  });
});


