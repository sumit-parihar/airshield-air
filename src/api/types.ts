export type Parameter = 'co2' | 'pm25' | 'voc' | 'temperature' | 'humidity';
export type AlertParameter = Parameter | 'device_health';
export type Severity = 'info' | 'warning' | 'critical';
export type AlertStatus = 'active' | 'acknowledged' | 'resolved';

export interface Reading {
  id: number | null;
  provider_entry_id: number | null;
  device_id: string;
  timestamp: string;
  co2: number | null;
  pm25: number | null;
  voc: number | null;
  voc_unit: string | null;
  temperature: number | null;
  humidity: number | null;
  device_status: string | null;
  battery: number | null;
  source: string;
  source_metadata: Record<string, unknown>;
  ingestion_mode: 'live' | 'historical_backfill' | null;
}

export interface LatestReadingsResponse {
  readings: Reading[];
  source: string;
  fetched_at: string;
  stale: boolean;
}

export interface HealthResponse {
  status: string;
  database: string;
}

export interface Alert {
  id: number;
  device_id: string;
  rule_id: string | null;
  reading_id: number | null;
  category: 'environmental' | 'device_health';
  health_state: 'stale' | 'offline' | null;
  status: AlertStatus;
  parameter: AlertParameter;
  observed_value: number | null;
  threshold: number | null;
  unit: string;
  severity: Severity;
  message: string;
  recommended_action: string;
  created_at: string;
  condition_started_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  resolved_at: string | null;
  resolution: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

export type AlertPage = Page<Alert>;

export interface AlertRule {
  id: string;
  parameter: Parameter;
  operator: 'gt' | 'gte' | 'lt' | 'lte';
  threshold: number;
  unit: string;
  severity: Severity;
  enabled: boolean;
  persistence_seconds: number;
  recovery_threshold: number | null;
  message: string;
  recommended_action: string;
  created_at: string;
  updated_at: string;
}

export type AlertRuleInput = Omit<AlertRule, 'id' | 'created_at' | 'updated_at'>;
export type AlertRulePatch = Partial<AlertRuleInput>;

export interface Device {
  id: string;
  name: string;
  location: string | null;
  status: 'online' | 'stale' | 'offline' | 'never_seen' | 'unknown';
  last_seen_at: string | null;
  last_successful_ingestion_at: string | null;
  last_ingestion_error_at: string | null;
  latest_reading: Reading | null;
  health_alert: Alert | null;
}

export interface DevicePage {
  items: Device[];
  total: number;
}

export interface NotificationRecord {
  id: number;
  alert_id: number;
  channel: 'in_app' | 'browser' | 'email';
  status: 'recorded' | 'pending' | 'delivered' | 'failed' | 'not_configured';
  created_at: string;
  attempted_at: string | null;
  error: string | null;
}

export type NotificationPage = Page<NotificationRecord>;

export interface MetricStatistics {
  count: number;
  minimum: number | null;
  maximum: number | null;
  average: number | null;
  unit: string | null;
}

export interface AnalyticsSummary {
  start_at: string;
  end_at: string;
  device_id: string | null;
  reading_count: number;
  ingestion_mode_counts: Record<'live' | 'historical_backfill', number>;
  metrics: Record<Parameter, MetricStatistics>;
  alert_counts_by_severity: Record<Severity, number>;
  alert_counts_by_parameter: Record<Parameter, number>;
  exceedance_durations: Array<{
    rule_id: string;
    parameter: Parameter;
    operator: 'gt' | 'gte' | 'lt' | 'lte';
    threshold: number;
    unit: string;
    ingestion_mode: 'live' | 'historical_backfill';
    observed_seconds: number;
    observed_intervals: number;
  }>;
}

export interface TrendPoint {
  timestamp: string;
  value: number;
  sample_count: number;
}

export interface TrendResponse {
  parameter: Parameter;
  unit: string | null;
  interval_seconds: number;
  samples_in_range: number;
  ingestion_modes: Array<'live' | 'historical_backfill'>;
  points: TrendPoint[];
}
