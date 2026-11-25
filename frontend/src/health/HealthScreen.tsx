import type { HealthState } from '../types';

export interface HealthScreenProps {
  state: HealthState;
  busy?: boolean;
  onRefresh: () => void | Promise<void>;
}

export function HealthScreen({ state, busy = false, onRefresh }: HealthScreenProps) {
  const isChecking = busy || state.kind === 'checking';
  const badgeLabel =
    state.kind === 'healthy'
      ? 'Healthy'
      : state.kind === 'checking'
        ? 'Checking'
        : 'Unavailable';

  const summary =
    state.kind === 'healthy'
      ? `API service ${state.data.service}${state.data.version ? ` (v${state.data.version})` : ''} is online.`
      : state.kind === 'checking'
        ? 'Checking API health…'
        : `Service unavailable: ${state.error}`;

  return (
    <section className="health-panel" aria-labelledby="system-health-heading">
      <div className="health-header">
        <h2 id="system-health-heading">System health</h2>
        <span className={`health-badge health-${state.kind}`}>{badgeLabel}</span>
      </div>
      <p role="status" aria-live="polite">
        {summary}
      </p>
      {state.kind === 'healthy' && (
        <dl className="health-meta">
          <div>
            <dt>Service</dt>
            <dd>{state.data.service}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{state.data.status}</dd>
          </div>
          <div>
            <dt>Version</dt>
            <dd>{state.data.version ?? 'unversioned'}</dd>
          </div>
        </dl>
      )}
      <button type="button" disabled={isChecking} onClick={() => void onRefresh()}>
        Check health
      </button>
    </section>
  );
}
