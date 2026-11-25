import type { HealthResponse, HealthState } from '../types';

const ALLOWED_KEYS = new Set(['status', 'service', 'version']);

export function parseHealthResponse(payload: unknown): HealthResponse {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
    throw new Error('Invalid health response payload');
  }
  const record = payload as Record<string, unknown>;
  for (const key of Object.keys(record)) {
    if (!ALLOWED_KEYS.has(key)) {
      throw new Error(`Unexpected health field: ${key}`);
    }
  }
  if (record.status !== 'ok' || record.service !== 'vibeos') {
    throw new Error('Invalid health status or service');
  }
  if (record.version !== undefined) {
    if (
      typeof record.version !== 'string' ||
      record.version.trim().length === 0 ||
      record.version.length > 100
    ) {
      throw new Error('Invalid health version');
    }
    return { status: 'ok', service: 'vibeos', version: record.version };
  }
  return { status: 'ok', service: 'vibeos' };
}

export async function checkApiHealth(
  fetchImpl: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<HealthState> {
  let response: Response;
  try {
    response = await fetchImpl('/api/v1/health', {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal,
    });
  } catch {
    return { kind: 'unhealthy', error: 'API health endpoint unreachable' };
  }

  if (!response.ok) {
    return { kind: 'unhealthy', error: `Health check failed (HTTP ${response.status})` };
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { kind: 'unhealthy', error: 'Invalid JSON from health endpoint' };
  }

  try {
    const data = parseHealthResponse(body);
    return { kind: 'healthy', data };
  } catch (err) {
    return {
      kind: 'unhealthy',
      error: err instanceof Error ? err.message : 'Invalid health response payload',
    };
  }
}
