import { describe, expect, it } from 'vitest';
import {
  DEFAULT_DESKTOP_APPEARANCE,
  DESKTOP_APPEARANCE_SCHEMA_VERSION,
  DesktopAppearanceError,
  loadDesktopAppearance,
  MAX_APPEARANCE_PAYLOAD_CHARS,
  parseStoredDesktopAppearance,
  parseThemeId,
  parseWallpaperId,
  readDesktopAppearance,
  resolveAppearanceStorageKey,
  saveDesktopAppearance,
  THEME_LIST,
  transitionDesktopAppearance,
  validateDesktopAppearance,
  WALLPAPER_LIST,
  type DesktopStorage,
} from './themes';

class MemoryStorage implements DesktopStorage {
  readonly data = new Map<string, string>();
  setCalls = 0;
  removeCalls = 0;

  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.setCalls += 1;
    this.data.set(key, value);
  }

  removeItem(key: string): void {
    this.removeCalls += 1;
    this.data.delete(key);
  }
}

describe('desktop theme and wallpaper registries (AC-01)', () => {
  it('exposes immutable allowlisted themes and wallpapers with midnight/aurora default', () => {
    expect(Object.isFrozen(THEME_LIST)).toBe(true);
    expect(Object.isFrozen(WALLPAPER_LIST)).toBe(true);
    expect(Object.isFrozen(DEFAULT_DESKTOP_APPEARANCE)).toBe(true);

    expect(THEME_LIST.map((item) => item.id)).toEqual(['midnight', 'daylight', 'high-contrast']);
    expect(WALLPAPER_LIST.map((item) => item.id)).toEqual([
      'aurora',
      'horizon',
      'blueprint',
      'monolith',
    ]);
    expect(DEFAULT_DESKTOP_APPEARANCE).toEqual({
      themeId: 'midnight',
      wallpaperId: 'aurora',
    });
  });

  it('transitions theme, wallpaper, and reset without mutating previous state', () => {
    const initial = { themeId: 'midnight' as const, wallpaperId: 'aurora' as const };

    const afterTheme = transitionDesktopAppearance(initial, { kind: 'theme', value: 'daylight' });
    expect(afterTheme).toEqual({
      ok: true,
      appearance: { themeId: 'daylight', wallpaperId: 'aurora' },
      error: null,
    });
    expect(initial).toEqual({ themeId: 'midnight', wallpaperId: 'aurora' });

    const afterWallpaper = transitionDesktopAppearance(afterTheme.appearance, {
      kind: 'wallpaper',
      value: 'blueprint',
    });
    expect(afterWallpaper).toEqual({
      ok: true,
      appearance: { themeId: 'daylight', wallpaperId: 'blueprint' },
      error: null,
    });

    const afterReset = transitionDesktopAppearance(afterWallpaper.appearance, { kind: 'reset' });
    expect(afterReset).toEqual({
      ok: true,
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      error: null,
    });
  });

  it('persists and isolates appearance preferences across guest and authenticated user scopes', () => {
    const storage = new MemoryStorage();

    expect(loadDesktopAppearance(storage, null)).toEqual({
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      error: null,
    });

    const savedAlice = saveDesktopAppearance(
      storage,
      { themeId: 'daylight', wallpaperId: 'blueprint' },
      'alice',
    );
    expect(savedAlice).toEqual({
      ok: true,
      appearance: { themeId: 'daylight', wallpaperId: 'blueprint' },
      persisted: true,
      error: null,
    });

    const savedBob = saveDesktopAppearance(
      storage,
      { themeId: 'high-contrast', wallpaperId: 'monolith' },
      'bob',
    );
    expect(savedBob.ok).toBe(true);
    expect(savedBob.persisted).toBe(true);

    expect(loadDesktopAppearance(storage, null)).toEqual({
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      error: null,
    });
    expect(loadDesktopAppearance(storage, 'alice')).toEqual({
      appearance: { themeId: 'daylight', wallpaperId: 'blueprint' },
      error: null,
    });
    expect(loadDesktopAppearance(storage, 'bob')).toEqual({
      appearance: { themeId: 'high-contrast', wallpaperId: 'monolith' },
      error: null,
    });
  });
});

describe('desktop appearance fail-closed validation and error handling (AC-02)', () => {
  it('rejects invalid theme and wallpaper identifiers including Object.prototype keys and CSS payloads', () => {
    const invalidInputs: unknown[] = [
      '',
      ' midnight',
      'MIDNIGHT',
      'solarized',
      'url(https://evil.example/bg.png)',
      'expression(alert(1))',
      '__proto__',
      'constructor',
      'toString',
      null,
      undefined,
      42,
      ['midnight'],
      { id: 'midnight' },
    ];

    for (const candidate of invalidInputs) {
      expect(() => parseThemeId(candidate)).toThrow(DesktopAppearanceError);
      expect(() => parseWallpaperId(candidate)).toThrow(DesktopAppearanceError);
    }
  });

  it('preserves active state without silent success when transitions receive invalid inputs or actions', () => {
    const active = { themeId: 'daylight' as const, wallpaperId: 'horizon' as const };

    const badTheme = transitionDesktopAppearance(active, {
      kind: 'theme',
      value: 'javascript:alert(1)',
    });
    expect(badTheme).toEqual({
      ok: false,
      appearance: active,
      error: 'Unsupported desktop theme selection.',
    });

    const badWallpaper = transitionDesktopAppearance(active, {
      kind: 'wallpaper',
      value: '__proto__',
    });
    expect(badWallpaper).toEqual({
      ok: false,
      appearance: active,
      error: 'Unsupported desktop wallpaper selection.',
    });

    for (const badAction of [
      null,
      'theme',
      { kind: 'unknown', value: 'midnight' },
      { kind: 'theme' },
      { kind: 'theme', value: 'midnight', extra: true },
      { kind: 'reset', extra: true },
    ]) {
      const res = transitionDesktopAppearance(active, badAction);
      expect(res.ok).toBe(false);
      expect(res.appearance).toEqual(active);
      expect(res.error).toBe('Unsupported desktop appearance action.');
    }

    const recovered = transitionDesktopAppearance(
      { themeId: 'corrupted', wallpaperId: 'corrupted' },
      { kind: 'theme', value: 'high-contrast' },
    );
    expect(recovered).toEqual({
      ok: true,
      appearance: { themeId: 'high-contrast', wallpaperId: 'aurora' },
      error: 'Invalid prior desktop state recovered to safe defaults before applying theme.',
    });
  });

  it('enforces closed schema on validateDesktopAppearance and parseStoredDesktopAppearance', () => {
    expect(() => validateDesktopAppearance(null)).toThrow(DesktopAppearanceError);
    expect(() => validateDesktopAppearance({ themeId: 'midnight' })).toThrow(DesktopAppearanceError);
    expect(() =>
      validateDesktopAppearance({
        themeId: 'midnight',
        wallpaperId: 'aurora',
        injected: 'nope',
      }),
    ).toThrow(DesktopAppearanceError);

    const validRaw = JSON.stringify({
      version: DESKTOP_APPEARANCE_SCHEMA_VERSION,
      themeId: 'high-contrast',
      wallpaperId: 'monolith',
    });
    expect(parseStoredDesktopAppearance(validRaw)).toEqual({
      themeId: 'high-contrast',
      wallpaperId: 'monolith',
    });

    const invalidPayloads: unknown[] = [
      '',
      '{not-json',
      'null',
      '[]',
      '"midnight"',
      'x'.repeat(MAX_APPEARANCE_PAYLOAD_CHARS + 1),
      JSON.stringify({ version: 2, themeId: 'midnight', wallpaperId: 'aurora' }),
      JSON.stringify({ version: 0, themeId: 'midnight', wallpaperId: 'aurora' }),
      JSON.stringify({ themeId: 'midnight', wallpaperId: 'aurora' }),
      JSON.stringify({
        version: 1,
        themeId: 'midnight',
        wallpaperId: 'aurora',
        extra: 'unexpected',
      }),
      '{"__proto__":{"polluted":true},"version":1,"themeId":"midnight","wallpaperId":"aurora"}',
      JSON.stringify({ version: 1, themeId: 'unknown', wallpaperId: 'aurora' }),
      JSON.stringify({ version: 1, themeId: 'midnight', wallpaperId: 'unknown' }),
    ];

    for (const raw of invalidPayloads) {
      expect(() => parseStoredDesktopAppearance(raw)).toThrow(DesktopAppearanceError);
    }
  });

  it('rejects invalid session scopes and refuses unauthorized or malformed storage writes', () => {
    const storage = new MemoryStorage();

    for (const badScope of ['../admin', 'Alice', 'ab', 'alice:bob', 'a'.repeat(41), 123, {}]) {
      expect(() => resolveAppearanceStorageKey(badScope)).toThrow(DesktopAppearanceError);

      const loadRes = loadDesktopAppearance(storage, badScope);
      expect(loadRes.appearance).toEqual(DEFAULT_DESKTOP_APPEARANCE);
      expect(loadRes.error).toContain('Invalid desktop session scope.');

      const saveRes = saveDesktopAppearance(
        storage,
        { themeId: 'daylight', wallpaperId: 'horizon' },
        badScope,
      );
      expect(saveRes.ok).toBe(false);
      expect(saveRes.persisted).toBe(false);
      expect(saveRes.error).toBe('Invalid desktop session scope.');
    }

    const badAppearanceSave = saveDesktopAppearance(
      storage,
      { themeId: 'evil', wallpaperId: 'aurora' },
      'alice',
    );
    expect(badAppearanceSave.ok).toBe(false);
    expect(badAppearanceSave.persisted).toBe(false);
    expect(storage.setCalls).toBe(0);
    expect(storage.data.size).toBe(0);
  });

  it('purges only the corrupted scoped entry on load and handles storage read/write failures explicitly', () => {
    const storage = new MemoryStorage();
    storage.data.set('unrelated.key', 'keep-me');
    storage.data.set(resolveAppearanceStorageKey('alice'), '{corrupted-json');

    const pureRead = readDesktopAppearance(storage, { activeUsername: 'alice' });
    expect(pureRead.appearance).toEqual(DEFAULT_DESKTOP_APPEARANCE);
    expect(pureRead.corruptedKey).toBe('vibeos.desktop.appearance:user:alice');
    expect(storage.removeCalls).toBe(0);

    const loaded = loadDesktopAppearance(storage, 'alice');
    expect(loaded.appearance).toEqual(DEFAULT_DESKTOP_APPEARANCE);
    expect(loaded.error).toContain('restored default appearance');
    expect(storage.data.has('vibeos.desktop.appearance:user:alice')).toBe(false);
    expect(storage.data.get('unrelated.key')).toBe('keep-me');

    const missingStorageLoad = loadDesktopAppearance(null, null);
    expect(missingStorageLoad.appearance).toEqual(DEFAULT_DESKTOP_APPEARANCE);
    expect(missingStorageLoad.error).toContain('Desktop storage is unavailable');

    const missingStorageSave = saveDesktopAppearance(
      null,
      { themeId: 'daylight', wallpaperId: 'horizon' },
      null,
    );
    expect(missingStorageSave).toEqual({
      ok: true,
      appearance: { themeId: 'daylight', wallpaperId: 'horizon' },
      persisted: false,
      error: 'Desktop storage is unavailable; appearance applied for this session only.',
    });

    const throwingStorage: DesktopStorage = {
      getItem() {
        throw new Error('SecurityError: storage disabled');
      },
      setItem() {
        throw new Error('QuotaExceededError');
      },
      removeItem() {
        throw new Error('SecurityError');
      },
    };

    const failedRead = loadDesktopAppearance(throwingStorage, 'alice');
    expect(failedRead).toEqual({
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      error: 'Desktop storage could not be read; using default appearance for this session.',
    });

    const failedWrite = saveDesktopAppearance(
      throwingStorage,
      { themeId: 'high-contrast', wallpaperId: 'blueprint' },
      'alice',
    );
    expect(failedWrite).toEqual({
      ok: true,
      appearance: { themeId: 'high-contrast', wallpaperId: 'blueprint' },
      persisted: false,
      error: 'Could not save desktop appearance locally; applied for this session only.',
    });
  });
});
