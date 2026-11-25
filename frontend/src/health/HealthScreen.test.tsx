import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { HealthScreen } from './HealthScreen';

describe('HealthScreen', () => {
  it('renders healthy service details and enabled refresh control', () => {
    const html = renderToStaticMarkup(
      <HealthScreen
        state={{ kind: 'healthy', data: { status: 'ok', service: 'vibeos', version: '0.1.0' } }}
        onRefresh={() => undefined}
      />,
    );
    expect(html).toContain('System health');
    expect(html).toContain('Healthy');
    expect(html).toContain('API service vibeos (v0.1.0) is online.');
    expect(html).not.toContain('disabled=""');
  });

  it('renders unavailable error details when health check fails', () => {
    const html = renderToStaticMarkup(
      <HealthScreen
        state={{ kind: 'unhealthy', error: 'Health check failed (HTTP 503)' }}
        onRefresh={() => undefined}
      />,
    );
    expect(html).toContain('Unavailable');
    expect(html).toContain('Service unavailable: Health check failed (HTTP 503)');
  });

  it('disables the check button while checking', () => {
    const html = renderToStaticMarkup(
      <HealthScreen state={{ kind: 'checking' }} onRefresh={() => undefined} />,
    );
    expect(html).toContain('Checking API health…');
    expect(html).toContain('disabled=""');
  });
});
