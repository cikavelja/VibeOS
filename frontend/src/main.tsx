import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Studio } from './designer/Studio';
import { HealthScreen } from './health/HealthScreen';
import { Renderer } from './runtime/Renderer';
import { request } from './services/api';
import { checkApiHealth } from './services/health';
import './style.css';
import type { Definition, HealthState, Installation, Project, Share, Values } from './types';

function App() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState('');
  const [recipient, setRecipient] = useState('');
  const [projects, setProjects] = useState<Project[]>([]);
  const [installs, setInstalls] = useState<Installation[]>([]);
  const [shares, setShares] = useState<Share[]>([]);
  const [message, setMessage] = useState('');
  const [page, setPage] = useState('Studio');
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<HealthState>({ kind: 'checking' });
  const [healthBusy, setHealthBusy] = useState(false);

  const refreshHealth = useCallback(async (signal?: AbortSignal) => {
    setHealthBusy(true);
    try {
      const next = await checkApiHealth(fetch, signal);
      if (!signal?.aborted) {
        setHealth(next);
      }
    } finally {
      if (!signal?.aborted) {
        setHealthBusy(false);
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refreshHealth(controller.signal);
    return () => controller.abort();
  }, [refreshHealth]);

  async function refresh(t = token) {
    const [p, i, s] = await Promise.all([
      request<Project[]>('/projects', t),
      request<Installation[]>('/installations', t),
      request<Share[]>('/shares/incoming', t),
    ]);
    setProjects(p);
    setInstalls(i);
    setShares(s);
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setMessage('');
    try {
      await action();
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function login(register: boolean) {
    await run(async () => {
      if (register) await request('/auth/register', '', 'POST', { username, password });
      const r = await request<{ token: string }>('/auth/login', '', 'POST', { username, password });
      await refresh(r.token);
      setToken(r.token);
      setPassword('');
    });
  }

  async function save(d: Definition) {
    await run(async () => {
      await request('/projects', token, 'POST', d);
      await refresh();
      setMessage('Application saved');
      setPage('Projects');
    });
  }

  const active = installs.find((i) => i.id === selected);
  function updateState(values: Values) {
    setInstalls((old) => old.map((i) => (i.id === selected ? { ...i, state: values } : i)));
  }

  const headerHealthText =
    health.kind === 'healthy'
      ? 'API: Healthy'
      : health.kind === 'checking'
        ? 'API: Checking…'
        : 'API: Unavailable';

  return (
    <main>
      <header>
        <strong>VibeOS</strong>
        <span>Development preview</span>
        <span className={`health-pill health-${health.kind}`}>{headerHealthText}</span>
        {token && (
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await request('/auth/logout', token, 'POST');
                setToken('');
                setProjects([]);
                setInstalls([]);
                setShares([]);
                setSelected('');
                setRecipient('');
                setPage('Studio');
              })
            }
          >
            Sign out
          </button>
        )}
      </header>
      {message && (
        <p className="message" role="status">
          {message}
        </p>
      )}
      {!token ? (
        <>
          <HealthScreen state={health} busy={healthBusy} onRefresh={() => refreshHealth()} />
          <section className="login">
            <h1>Your application workspace</h1>
            <label>
              Username
              <input value={username} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label>
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <button disabled={busy} onClick={() => void login(false)}>
              Sign in
            </button>{' '}
            <button disabled={busy} onClick={() => void login(true)}>
              Register
            </button>
          </section>
        </>
      ) : (
        <>
          <nav>
            {['Studio', 'Projects', 'Installed', 'Inbox', 'Health'].map((p) => (
              <button key={p} className={page === p ? 'active' : ''} onClick={() => setPage(p)}>
                {p}
              </button>
            ))}
            <button disabled={busy} onClick={() => void run(() => refresh())}>
              Refresh
            </button>
          </nav>
          <article>
            {page === 'Studio' && <Studio onSave={save} />}
            {page === 'Projects' && (
              <section>
                <h2>My projects</h2>
                <label>
                  Share with username
                  <input value={recipient} onChange={(e) => setRecipient(e.target.value)} />
                </label>
                {projects.map((p) => (
                  <div className="component" key={p.id}>
                    <strong>{p.definition.name}</strong>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          await request(`/projects/${p.id}/install`, token, 'POST');
                          await refresh();
                          setMessage('Installed');
                        })
                      }
                    >
                      Install
                    </button>
                    <button
                      disabled={busy || !recipient}
                      onClick={() =>
                        void run(async () => {
                          await request(`/projects/${p.id}/share`, token, 'POST', { recipient });
                          setMessage('Application sent');
                        })
                      }
                    >
                      Send copy
                    </button>
                  </div>
                ))}
              </section>
            )}
            {page === 'Inbox' && (
              <section>
                <h2>Incoming applications</h2>
                {shares.map((s) => (
                  <div className="component" key={s.id}>
                    <strong>{s.definition.name}</strong>
                    <p>{s.definition.components.map((c) => c.label).join(', ')}</p>
                    <button
                      disabled={s.accepted || busy}
                      onClick={() =>
                        void run(async () => {
                          await request(`/shares/${s.id}/accept`, token, 'POST');
                          await refresh();
                          setPage('Installed');
                        })
                      }
                    >
                      {s.accepted ? 'Accepted' : 'Accept and install'}
                    </button>
                  </div>
                ))}
              </section>
            )}
            {page === 'Installed' && (
              <section>
                <h2>Installed applications</h2>
                {installs.map((i) => (
                  <button key={i.id} onClick={() => setSelected(i.id)}>
                    {i.definition.name}
                  </button>
                ))}
                {active && (
                  <div className="window" key={active.id}>
                    <h3>{active.definition.name}</h3>
                    <Renderer definition={active.definition} values={active.state} onChange={updateState} />
                    <button
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          await request(`/installations/${active.id}/state`, token, 'PUT', {
                            values: active.state,
                          });
                          setMessage('State saved');
                        })
                      }
                    >
                      Save state
                    </button>
                  </div>
                )}
              </section>
            )}
            {page === 'Health' && (
              <HealthScreen state={health} busy={healthBusy} onRefresh={() => refreshHealth()} />
            )}
          </article>
        </>
      )}
    </main>
  );
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
