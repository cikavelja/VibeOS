import React, { useEffect, useState } from 'react';
import type { HealthState } from '../types';
import {
  DEFAULT_DESKTOP_APPEARANCE,
  parseStoredDesktopAppearance,
  purgeAppearanceStorageKey,
  readDesktopAppearance,
  resolveAppearanceStorageKey,
  saveDesktopAppearance,
  THEME_BY_ID,
  THEME_LIST,
  transitionDesktopAppearance,
  WALLPAPER_BY_ID,
  WALLPAPER_LIST,
  type DesktopAppearance,
  type DesktopStorage,
} from './themes';

export interface DesktopShellProps {
  readonly activeUsername?: string | null;
  readonly healthState: HealthState;
  readonly statusMessage?: string;
  readonly onSignOut?: () => void;
  readonly signOutDisabled?: boolean;
  readonly storage?: DesktopStorage | null;
  readonly children: React.ReactNode;
}

interface ResolvedStorageContext {
  readonly storage: DesktopStorage | null;
  readonly accessError: string | null;
  readonly warnIfUnavailable: boolean;
}

function resolveStorageContext(
  explicitStorage: DesktopStorage | null | undefined,
): ResolvedStorageContext {
  if (explicitStorage !== undefined) {
    return {
      storage: explicitStorage,
      accessError: null,
      warnIfUnavailable: explicitStorage === null,
    };
  }
  if (typeof window === 'undefined') {
    return {
      storage: null,
      accessError: null,
      warnIfUnavailable: false,
    };
  }
  try {
    return {
      storage: window.localStorage,
      accessError: null,
      warnIfUnavailable: true,
    };
  } catch {
    return {
      storage: null,
      accessError: 'Desktop storage is unavailable; using default appearance for this session.',
      warnIfUnavailable: true,
    };
  }
}

interface ShellAppearanceState {
  readonly scopeKey: string | null;
  readonly rawUsername: unknown;
  readonly storageProp: DesktopStorage | null | undefined;
  readonly appearance: DesktopAppearance;
  readonly error: string | null;
  readonly corruptedKey: string | null;
}

function buildScopedState(
  rawUsername: unknown,
  explicitStorage: DesktopStorage | null | undefined,
): ShellAppearanceState {
  const ctx = resolveStorageContext(explicitStorage);
  const loaded = readDesktopAppearance(ctx.storage, {
    activeUsername: rawUsername,
    accessError: ctx.accessError,
    warnIfUnavailable: ctx.warnIfUnavailable,
  });
  let scopeKey: string | null = null;
  try {
    scopeKey = resolveAppearanceStorageKey(rawUsername);
  } catch {
    scopeKey = null;
  }
  return {
    scopeKey,
    rawUsername,
    storageProp: explicitStorage,
    appearance: loaded.appearance,
    error: loaded.error,
    corruptedKey: loaded.corruptedKey,
  };
}

export function DesktopShell({
  activeUsername = null,
  healthState,
  statusMessage = '',
  onSignOut,
  signOutDisabled = false,
  storage: explicitStorage,
  children,
}: DesktopShellProps) {
  const [state, setState] = useState<ShellAppearanceState>(() =>
    buildScopedState(activeUsername, explicitStorage),
  );

  const currentState =
    state.rawUsername !== activeUsername || state.storageProp !== explicitStorage
      ? buildScopedState(activeUsername, explicitStorage)
      : state;

  if (currentState !== state) {
    setState(currentState);
  }

  const activeTheme = THEME_BY_ID[currentState.appearance.themeId];
  const activeWallpaper = WALLPAPER_BY_ID[currentState.appearance.wallpaperId];

  useEffect(() => {
    if (currentState.corruptedKey === null) {
      return;
    }
    const ctx = resolveStorageContext(explicitStorage);
    purgeAppearanceStorageKey(ctx.storage, currentState.corruptedKey);
  }, [explicitStorage, currentState.corruptedKey]);

  useEffect(() => {
    if (typeof document === 'undefined') {
      return;
    }
    document.documentElement.dataset.theme = currentState.appearance.themeId;
    document.documentElement.dataset.wallpaper = currentState.appearance.wallpaperId;
    document.documentElement.style.colorScheme = activeTheme.colorScheme;
  }, [
    activeTheme.colorScheme,
    currentState.appearance.themeId,
    currentState.appearance.wallpaperId,
  ]);

  useEffect(() => {
    if (typeof window === 'undefined' || currentState.scopeKey === null) {
      return;
    }
    const expectedKey = currentState.scopeKey;
    function onStorage(event: StorageEvent) {
      const ctx = resolveStorageContext(explicitStorage);
      if (event.storageArea !== null && ctx.storage !== null && event.storageArea !== ctx.storage) {
        return;
      }
      if (event.key !== null && event.key !== expectedKey) {
        return;
      }
      if (event.newValue === null) {
        setState((prev) => ({
          ...prev,
          appearance: DEFAULT_DESKTOP_APPEARANCE,
          error: null,
          corruptedKey: null,
        }));
        return;
      }
      try {
        const parsed = parseStoredDesktopAppearance(event.newValue);
        setState((prev) => ({
          ...prev,
          appearance: parsed,
          error: null,
          corruptedKey: null,
        }));
      } catch (err) {
        purgeAppearanceStorageKey(ctx.storage, expectedKey);
        setState((prev) => ({
          ...prev,
          appearance: DEFAULT_DESKTOP_APPEARANCE,
          error:
            err instanceof Error
              ? err.message
              : 'Stored desktop appearance data is corrupted; restored default appearance.',
          corruptedKey: null,
        }));
      }
    }
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [explicitStorage, state.scopeKey]);

  function applyChange(kind: 'theme' | 'wallpaper', rawValue: unknown) {
    const transition = transitionDesktopAppearance(currentState.appearance, {
      kind,
      value: rawValue,
    });
    if (!transition.ok) {
      setState((prev) => ({
        ...prev,
        error: transition.error,
      }));
      return;
    }

    const ctx = resolveStorageContext(explicitStorage);
    if (ctx.accessError) {
      setState((prev) => ({
        ...prev,
        appearance: transition.appearance,
        error: ctx.accessError,
        corruptedKey: null,
      }));
      return;
    }

    const saved = saveDesktopAppearance(ctx.storage, transition.appearance, activeUsername);
    if (!saved.ok) {
      setState((prev) => ({
        ...prev,
        error: saved.error,
      }));
      return;
    }

    setState((prev) => ({
      ...prev,
      appearance: saved.appearance,
      error: saved.error ?? transition.error,
      corruptedKey: null,
    }));
  }

  const headerHealthText =
    healthState.kind === 'healthy'
      ? 'API: Healthy'
      : healthState.kind === 'checking'
        ? 'API: Checking…'
        : 'API: Unavailable';

  return (
    <div
      className="desktop-shell"
      data-testid="desktop-shell"
      data-theme={currentState.appearance.themeId}
      data-wallpaper={currentState.appearance.wallpaperId}
      data-color-scheme={activeTheme.colorScheme}
    >
      <header className="desktop-topbar">
        <div className="desktop-brand">
          <strong>VibeOS</strong>
          <span className="desktop-subtitle">Development preview</span>
        </div>

        <div className="desktop-appearance-bar" role="group" aria-label="Desktop appearance">
          <div className="desktop-control-field">
            <label htmlFor="desktop-theme-select">Theme</label>
            <select
              id="desktop-theme-select"
              value={currentState.appearance.themeId}
              title={activeTheme.description}
              onChange={(e) => applyChange('theme', e.target.value)}
            >
              {THEME_LIST.map((theme) => (
                <option key={theme.id} value={theme.id}>
                  {theme.label}
                </option>
              ))}
            </select>
          </div>

          <div className="desktop-control-field">
            <label htmlFor="desktop-wallpaper-select">Wallpaper</label>
            <select
              id="desktop-wallpaper-select"
              value={currentState.appearance.wallpaperId}
              title={activeWallpaper.description}
              onChange={(e) => applyChange('wallpaper', e.target.value)}
            >
              {WALLPAPER_LIST.map((wallpaper) => (
                <option key={wallpaper.id} value={wallpaper.id}>
                  {wallpaper.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="desktop-status-cluster">
          <span className={`health-pill health-${healthState.kind}`}>{headerHealthText}</span>
          {onSignOut && (
            <button type="button" disabled={signOutDisabled} onClick={onSignOut}>
              Sign out
            </button>
          )}
        </div>
      </header>

      {currentState.error && (
        <div className="desktop-alert" role="alert">
          <span>{currentState.error}</span>
          <button
            type="button"
            className="desktop-alert-dismiss"
            onClick={() => setState((prev) => ({ ...prev, error: null }))}
          >
            Dismiss
          </button>
        </div>
      )}

      {statusMessage && (
        <p className="message" role="status">
          {statusMessage}
        </p>
      )}

      <main className="desktop-workspace" aria-label="Desktop workspace">
        {children}
      </main>
    </div>
  );
}
