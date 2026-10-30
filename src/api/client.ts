import type {
  Alert,
  AlertParameter,
  AlertPage,
  AlertRule,
  AlertRuleInput,
  AlertRulePatch,
  AnalyticsSummary,
  DevicePage,
  HealthResponse,
  LatestReadingsResponse,
  NotificationPage,
  Parameter,
  Reading,
  TrendResponse,
} from './types';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1').replace(/\/+$/, '');
const REQUEST_TIMEOUT_MS = 10_000;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

type QueryValue = string | number | boolean | null | undefined;
type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH';
  body?: unknown;
  signal?: AbortSignal;
  token?: string;
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function failParse(message: string): never {
  throw new ApiError(message, 502, 'invalid_api_response');
}

function parseReading(value: unknown): value is Reading {
  if (!isObject(value)) return false;
  const nullableNumberFields = ['id', 'provider_entry_id', 'co2', 'pm25', 'voc', 'temperature', 'humidity', 'battery'];
  return typeof value.device_id === 'string'
    && typeof value.timestamp === 'string'
    && nullableNumberFields.every((key) => value[key] === null || typeof value[key] === 'number')
    && (value.voc_unit === null || typeof value.voc_unit === 'string')
    && (value.ingestion_mode === null || value.ingestion_mode === 'live' || value.ingestion_mode === 'historical_backfill')
    && typeof value.source === 'string'
    && isObject(value.source_metadata);
}

function parseLatest(value: unknown): LatestReadingsResponse {
  if (!isObject(value) || !Array.isArray(value.readings) || !value.readings.every(parseReading)
    || typeof value.source !== 'string' || typeof value.fetched_at !== 'string' || typeof value.stale !== 'boolean') {
    return failParse('AirShield returned an invalid latest-readings response.');
  }
  return value as unknown as LatestReadingsResponse;
}

function parseAlert(value: unknown): value is Alert {
  if (!isObject(value)) return false;
  const health = value.category === 'device_health';
  return typeof value.id === 'number'
    && typeof value.device_id === 'string'
    && (value.category === 'environmental' || health)
    && (value.health_state === null || value.health_state === 'stale' || value.health_state === 'offline')
    && (health ? value.parameter === 'device_health' && value.rule_id === null && value.reading_id === null
      && value.observed_value === null && value.threshold === null && value.health_state !== null
      : ['co2', 'pm25', 'voc', 'temperature', 'humidity'].includes(String(value.parameter))
        && typeof value.rule_id === 'string' && typeof value.reading_id === 'number'
        && typeof value.observed_value === 'number' && typeof value.threshold === 'number')
    && ['active', 'acknowledged', 'resolved'].includes(String(value.status))
    && typeof value.unit === 'string'
    && ['info', 'warning', 'critical'].includes(String(value.severity))
    && typeof value.message === 'string'
    && typeof value.recommended_action === 'string';
}

function parseAlertPage(value: unknown): AlertPage {
  if (!isObject(value) || !Array.isArray(value.items) || !value.items.every(parseAlert)
    || typeof value.total !== 'number' || typeof value.offset !== 'number' || typeof value.limit !== 'number') {
    return failParse('AirShield returned an invalid alerts response.');
  }
  return value as unknown as AlertPage;
}

function parseRule(value: unknown): value is AlertRule {
  return isObject(value)
    && typeof value.id === 'string'
    && typeof value.parameter === 'string'
    && typeof value.threshold === 'number'
    && typeof value.unit === 'string'
    && typeof value.severity === 'string'
    && typeof value.enabled === 'boolean'
    && typeof value.persistence_seconds === 'number'
    && typeof value.message === 'string'
    && typeof value.recommended_action === 'string';
}

function parseDevicePage(value: unknown): DevicePage {
  const statuses = ['online', 'stale', 'offline', 'never_seen', 'unknown'];
  if (!isObject(value) || !Array.isArray(value.items) || typeof value.total !== 'number'
    || !value.items.every((item) => isObject(item) && typeof item.id === 'string' && statuses.includes(String(item.status))
      && (item.health_alert === null || parseAlert(item.health_alert)))) {
    return failParse('AirShield returned an invalid devices response.');
  }
  return value as unknown as DevicePage;
}

function parseSummary(value: unknown): AnalyticsSummary {
  if (!isObject(value) || typeof value.reading_count !== 'number' || !isObject(value.metrics)
    || !isObject(value.alert_counts_by_severity) || !isObject(value.alert_counts_by_parameter)
    || !isObject(value.ingestion_mode_counts)
    || typeof value.ingestion_mode_counts.live !== 'number'
    || typeof value.ingestion_mode_counts.historical_backfill !== 'number'
    || !Array.isArray(value.exceedance_durations)
    || !value.exceedance_durations.every((item) => isObject(item)
      && typeof item.rule_id === 'string'
      && ['co2', 'pm25', 'voc', 'temperature', 'humidity'].includes(String(item.parameter))
      && ['gt', 'gte', 'lt', 'lte'].includes(String(item.operator))
      && typeof item.threshold === 'number'
      && typeof item.unit === 'string'
      && (item.ingestion_mode === 'live' || item.ingestion_mode === 'historical_backfill')
      && typeof item.observed_seconds === 'number'
      && typeof item.observed_intervals === 'number')) {
    return failParse('AirShield returned an invalid analytics summary.');
  }
  return value as unknown as AnalyticsSummary;
}

function parseTrend(value: unknown): TrendResponse {
  if (!isObject(value) || !Array.isArray(value.points) || typeof value.interval_seconds !== 'number'
    || typeof value.samples_in_range !== 'number'
    || !Array.isArray(value.ingestion_modes)
    || !value.ingestion_modes.every((mode) => mode === 'live' || mode === 'historical_backfill')
    || !value.points.every((point) => isObject(point) && typeof point.timestamp === 'string' && typeof point.value === 'number' && typeof point.sample_count === 'number')) {
    return failParse('AirShield returned an invalid trend response.');
  }
  return value as unknown as TrendResponse;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(new DOMException('Request timed out', 'TimeoutError')), REQUEST_TIMEOUT_MS);
  const abortExternal = () => controller.abort(options.signal?.reason);
  options.signal?.addEventListener('abort', abortExternal, { once: true });
  if (options.signal?.aborted) abortExternal();

  const headers = new Headers({ Accept: 'application/json' });
  if (options.body !== undefined) headers.set('Content-Type', 'application/json');
  if (options.token?.trim()) headers.set('Authorization', `Bearer ${options.token.trim()}`);

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method || 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
    const contentType = response.headers.get('content-type') || '';
    let payload: unknown;
    if (response.status !== 204) {
      if (contentType.includes('application/json')) {
        try {
          payload = await response.json();
        } catch {
          throw new ApiError('AirShield returned malformed JSON.', 502, 'invalid_json');
        }
      } else {
        // Non-JSON bodies may contain infrastructure details. Never reflect them into the UI.
        await response.text();
        payload = null;
      }
    }
    if (!response.ok) {
      const detail = isObject(payload) && isObject(payload.detail) ? payload.detail : null;
      const message = detail && typeof detail.message === 'string'
        ? detail.message
        : isObject(payload) && typeof payload.message === 'string'
          ? payload.message
          : `AirShield API returned HTTP ${response.status}.`;
      const code = detail && typeof detail.code === 'string' ? detail.code : undefined;
      throw new ApiError(message, response.status, code);
    }
    return payload as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) {
      if (options.signal?.aborted) throw new DOMException('Request was cancelled', 'AbortError');
      throw new ApiError('The AirShield request timed out. Check the backend connection and try again.', 408, 'request_timeout');
    }
    throw new ApiError('AirShield could not be reached. Check that the backend is running.', 0, 'network_unavailable');
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abortExternal);
  }
}

function queryString(values: Record<string, QueryValue>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : '';
}

export const airshieldApi = {
  baseUrl: API_BASE_URL,
  async ready(signal?: AbortSignal) {
    const value = await request<unknown>('/health/ready', { signal });
    if (!isObject(value) || typeof value.status !== 'string' || typeof value.database !== 'string') return failParse('AirShield returned an invalid health response.');
    return value as unknown as HealthResponse;
  },
  async latest(signal?: AbortSignal) {
    return parseLatest(await request<unknown>('/readings/latest', { signal }));
  },
  async activeAlerts(filters: { severity?: string; parameter?: AlertParameter; device_id?: string; offset?: number; limit?: number } = {}, signal?: AbortSignal) {
    return parseAlertPage(await request<unknown>(`/alerts/active${queryString(filters)}`, { signal }));
  },
  async alerts(filters: { status?: string; severity?: string; parameter?: AlertParameter; device_id?: string; offset?: number; limit?: number } = {}, signal?: AbortSignal) {
    return parseAlertPage(await request<unknown>(`/alerts${queryString(filters)}`, { signal }));
  },
  async alert(id: number, signal?: AbortSignal) {
    const value = await request<unknown>(`/alerts/${id}`, { signal });
    if (!parseAlert(value)) return failParse('AirShield returned an invalid alert.');
    return value;
  },
  async acknowledgeAlert(id: number, token: string, signal?: AbortSignal) {
    const value = await request<unknown>(`/alerts/${id}/acknowledge`, { method: 'POST', body: {}, token, signal });
    if (!parseAlert(value)) return failParse('AirShield returned an invalid alert.');
    return value;
  },
  async resolveAlert(id: number, token: string, signal?: AbortSignal) {
    const value = await request<unknown>(`/alerts/${id}/resolve`, { method: 'POST', body: {}, token, signal });
    if (!parseAlert(value)) return failParse('AirShield returned an invalid alert.');
    return value;
  },
  async rules(filters: { parameter?: Parameter; enabled?: boolean } = {}, signal?: AbortSignal) {
    const value = await request<unknown>(`/alert-rules${queryString(filters)}`, { signal });
    if (!Array.isArray(value) || !value.every(parseRule)) return failParse('AirShield returned invalid alert rules.');
    return value as AlertRule[];
  },
  async createRule(input: AlertRuleInput, token: string, signal?: AbortSignal) {
    const value = await request<unknown>('/alert-rules', { method: 'POST', body: input, token, signal });
    if (!parseRule(value)) return failParse('AirShield returned an invalid alert rule.');
    return value;
  },
  async updateRule(id: string, input: AlertRulePatch, token: string, signal?: AbortSignal) {
    const value = await request<unknown>(`/alert-rules/${encodeURIComponent(id)}`, { method: 'PATCH', body: input, token, signal });
    if (!parseRule(value)) return failParse('AirShield returned an invalid alert rule.');
    return value;
  },
  async devices(signal?: AbortSignal) {
    return parseDevicePage(await request<unknown>('/devices?offset=0&limit=200', { signal }));
  },
  async notifications(signal?: AbortSignal) {
    const value = await request<unknown>('/notifications/in-app?offset=0&limit=50', { signal });
    if (!isObject(value) || !Array.isArray(value.items) || typeof value.total !== 'number'
      || typeof value.offset !== 'number' || typeof value.limit !== 'number'
      || !value.items.every((item) => isObject(item) && typeof item.id === 'number'
        && typeof item.alert_id === 'number' && ['in_app', 'browser', 'email'].includes(String(item.channel))
        && ['recorded', 'pending', 'delivered', 'failed', 'not_configured'].includes(String(item.status))
        && typeof item.created_at === 'string')) return failParse('AirShield returned invalid notifications.');
    return value as unknown as NotificationPage;
  },
  async summary(startAt: string, endAt: string, deviceId?: string, signal?: AbortSignal) {
    const value = await request<unknown>(`/analytics/summary${queryString({ start_at: startAt, end_at: endAt, device_id: deviceId })}`, { signal });
    return parseSummary(value);
  },
  async trend(parameter: Parameter, startAt: string, endAt: string, deviceId?: string, signal?: AbortSignal) {
    const value = await request<unknown>(`/analytics/trends${queryString({ parameter, start_at: startAt, end_at: endAt, device_id: deviceId, points: 500 })}`, { signal });
    return parseTrend(value);
  },
};
