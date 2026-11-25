import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { DesktopShell } from './DesktopShell';
import {
  DESKTOP_APPEARANCE_SCHEMA_VERSION,
  resolveAppearanceStorageKey,
  type DesktopStorage,
} from './themes';

function createSnapshotStorage(entries: Record<string, string> = {}): DesktopStorage & {
  removed: string[];
  written: string[];
} {
  const map = new Map(Object.entries(entries));
  const removed: string[] = [];
  const written: string[] = [];
  return {
    removed,
    written,
    getItem(key: string) {
      return map.get(key) ?? null;
    },
    setItem(key: string, value: string) {
      written.push(`${key}=${value}`);
      map.set(key, value);
    },
    removeItem(key: string) {
      removed.push(key);
      map.delete(key);
    },
  };
}

describe('DesktopShell', () => {
  it('renders viewport shell, accessible theme/wallpaper controls, health pill, and workspace children', () => {
    const html = renderToStaticMarkup(
      <DesktopShell
        healthState={{
          kind: 'healthy',
          data: { status: 'ok', service: 'vibeos', version: '0.1.0' },
        }}
      >
        <section>Workspace content</section>
      </DesktopShell>,
    );

    expect(html).toContain('data-testid="desktop-shell"');
    expect(html).toContain('data-theme="midnight"');
    expect(html).toContain('data-wallpaper="aurora"');
    expect(html).toContain('data-color-scheme="dark"');
    expect(html).toContain('aria-label="Desktop appearance"');
    expect(html).toContain('for="desktop-theme-select"');
    expect(html).toContain('for="desktop-wallpaper-select"');
    expect(html).toContain('API: Healthy');
    expect(html).toContain('aria-label="Desktop workspace"');
    expect(html).toContain('Workspace content');
    expect(html).not.toContain('role="alert"');
  });

  it('hydrates valid user-scoped appearance from storage and renders sign-out action when authenticated', () => {
    const storage = createSnapshotStorage({
      [resolveAppearanceStorageKey('alice')]: JSON.stringify({
        version: DESKTOP_APPEARANCE_SCHEMA_VERSION,
        themeId: 'daylight',
        wallpaperId: 'blueprint',
      }),
    });

    const html = renderToStaticMarkup(
      <DesktopShell
        activeUsername="alice"
        healthState={{ kind: 'checking' }}
        statusMessage="Application saved"
        onSignOut={() => undefined}
        storage={storage}
      >
        <p>Alice workspace</p>
      </DesktopShell>,
    );

    expect(html).toContain('data-theme="daylight"');
    expect(html).toContain('data-wallpaper="blueprint"');
    expect(html).toContain('data-color-scheme="light"');
    expect(html).toContain('API: Checking…');
    expect(html).toContain('Sign out');
    expect(html).toContain('Application saved');
    expect(html).not.toContain('role="alert"');
    expect(storage.removed).toEqual([]);
    expect(storage.written).toEqual([]);
  });

  it('fails closed to default appearance and surfaces accessible alerts without mutating storage during render', () => {
    const corruptedStorage = createSnapshotStorage({
      [resolveAppearanceStorageKey(null)]: '{"version":99,"themeId":"evil","wallpaperId":"evil"}',
    });

    const corruptedHtml = renderToStaticMarkup(
      <DesktopShell healthState={{ kind: 'unhealthy', error: 'Down' }} storage={corruptedStorage}>
        <p>Fallback workspace</p>
      </DesktopShell>,
    );

    expect(corruptedHtml).toContain('data-theme="midnight"');
    expect(corruptedHtml).toContain('data-wallpaper="aurora"');
    expect(corruptedHtml).toContain('role="alert"');
    expect(corruptedHtml).toContain(
      'Unsupported desktop appearance version; restored default appearance.',
    );
    expect(corruptedHtml).toContain('API: Unavailable');
    expect(corruptedStorage.removed).toEqual([]);
    expect(corruptedStorage.written).toEqual([]);

    const unavailableHtml = renderToStaticMarkup(
      <DesktopShell healthState={{ kind: 'checking' }} storage={null}>
        <p>No storage</p>
      </DesktopShell>,
    );
    expect(unavailableHtml).toContain('role="alert"');
    expect(unavailableHtml).toContain(
      'Desktop storage is unavailable; using default appearance for this session.',
    );

    const invalidScopeHtml = renderToStaticMarkup(
      <DesktopShell
        activeUsername="../invalid"
        healthState={{ kind: 'checking' }}
        storage={corruptedStorage}
      >
        <p>Bad scope</p>
      </DesktopShell>,
    );
    expect(invalidScopeHtml).toContain('data-theme="midnight"');
    expect(invalidScopeHtml).toContain('Invalid desktop session scope. Using default appearance.');
  });
});
