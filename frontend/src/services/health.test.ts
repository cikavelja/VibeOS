import { describe, expect, it } from 'vitest';
import { checkApiHealth, parseHealthResponse } from './health';

describe('parseHealthResponse', () => {
  it('accepts valid health payloads with or without version', () => {
    expect(parseHealthResponse({ status: 'ok', service: 'vibeos', version: '0.1.0' })).toEqual({
      status: 'ok',
      service: 'vibeos',
      version: '0.1.0',
    });
    expect(parseHealthResponse({ status: 'ok', service: 'vibeos' })).toEqual({
      status: 'ok',
      service: 'vibeos',
    });
  });

  it('rejects non-objects, invalid status/service, blank version, and unknown fields', () => {
    expect(() => parseHealthResponse(null)).toThrow('Invalid health response payload');
    expect(() => parseHealthResponse(['ok'])).toThrow('Invalid health response payload');
    expect(() => parseHealthResponse({ status: 'degraded', service: 'vibeos' })).toThrow(
      'Invalid health status or service',
    );
    expect(() => parseHealthResponse({ status: 'ok', service: 'other' })).toThrow(
      'Invalid health status or service',
    );
    expect(() => parseHealthResponse({ status: 'ok', service: 'vibeos', version: '   ' })).toThrow(
      'Invalid health version',
    );
    expect(() =>
      parseHealthResponse({ status: 'ok', service: 'vibeos', unexpected: true }),
    ).toThrow('Unexpected health field: unexpected');
  });
});

describe('checkApiHealth', () => {
  it('returns healthy state on valid 200 JSON response', async () => {
    const fakeFetch: typeof fetch = async () =>
      new Response(JSON.stringify({ status: 'ok', service: 'vibeos', version: '0.1.0' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });

    const result = await checkApiHealth(fakeFetch);
    expect(result).toEqual({
      kind: 'healthy',
      data: { status: 'ok', service: 'vibeos', version: '0.1.0' },
    });
  });

  it('returns unhealthy state without silent success on HTTP error, bad JSON, invalid schema, or network error', async () => {
    const http503: typeof fetch = async () =>
      new Response(JSON.stringify({ detail: 'Database unavailable' }), { status: 503 });
    expect(await checkApiHealth(http503)).toEqual({
      kind: 'unhealthy',
      error: 'Health check failed (HTTP 503)',
    });

    const badJson: typeof fetch = async () => new Response('<html>bad gateway</html>', { status: 200 });
    expect(await checkApiHealth(badJson)).toEqual({
      kind: 'unhealthy',
      error: 'Invalid JSON from health endpoint',
    });

    const badSchema: typeof fetch = async () =>
      new Response(JSON.stringify({ status: 'ok', service: 'vibeos', injected: 'x' }), {
        status: 200,
      });
    expect(await checkApiHealth(badSchema)).toEqual({
      kind: 'unhealthy',
      error: 'Unexpected health field: injected',
    });

    const networkDown: typeof fetch = async () => {
      throw new TypeError('Failed to fetch');
    };
    expect(await checkApiHealth(networkDown)).toEqual({
      kind: 'unhealthy',
      error: 'API health endpoint unreachable',
    });
  });
});
