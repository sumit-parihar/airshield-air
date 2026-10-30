import { useCallback, useEffect, useRef, useState } from 'react';
import { airshieldApi, ApiError } from '../api/client';
import type { AlertPage, DevicePage, HealthResponse, LatestReadingsResponse, NotificationPage, AlertRule } from '../api/types';

export interface DashboardData {
  health: HealthResponse | null;
  latest: LatestReadingsResponse | null;
  alerts: AlertPage | null;
  rules: AlertRule[] | null;
  devices: DevicePage | null;
  notifications: NotificationPage | null;
}

const empty: DashboardData = { health: null, latest: null, alerts: null, rules: null, devices: null, notifications: null };

export function useDashboard() {
  const [data, setData] = useState<DashboardData>(empty);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const inFlight = useRef(false);
  const hasLoaded = useRef(false);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    if (inFlight.current) return;
    inFlight.current = true;
    if (!hasLoaded.current) setLoading(true);
    try {
      const results = await Promise.allSettled([
        airshieldApi.ready(signal), airshieldApi.latest(signal), airshieldApi.activeAlerts({ limit: 100 }, signal),
        airshieldApi.rules({}, signal), airshieldApi.devices(signal), airshieldApi.notifications(signal),
      ]);
      if (signal?.aborted) return;
      const failed = results.find((result) => result.status === 'rejected');
      if (failed?.status === 'rejected') setError(failed.reason instanceof ApiError ? failed.reason : new ApiError('Backend request failed.', 0));
      else setError(null);
      setData({
        health: results[0].status === 'fulfilled' ? results[0].value : null,
        latest: results[1].status === 'fulfilled' ? results[1].value : null,
        alerts: results[2].status === 'fulfilled' ? results[2].value : null,
        rules: results[3].status === 'fulfilled' ? results[3].value : null,
        devices: results[4].status === 'fulfilled' ? results[4].value : null,
        notifications: results[5].status === 'fulfilled' ? results[5].value : null,
      });
      hasLoaded.current = true;
    } catch (reason) {
      if (!signal?.aborted) setError(reason instanceof ApiError ? reason : new ApiError('Backend request failed.', 0));
    } finally {
      inFlight.current = false;
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const interval = Math.max(15_000, Number(import.meta.env.VITE_POLL_INTERVAL_MS) || 30_000);
    // Defer the first poll one task so React StrictMode's development cleanup can cancel
    // its probe mount before any network request is started.
    const initial = window.setTimeout(() => void refresh(controller.signal), 0);
    const timer = window.setInterval(() => void refresh(controller.signal), interval);
    return () => { controller.abort(); window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh]);

  return { ...data, loading, error, refresh };
}
