export type ThemeId = 'midnight' | 'daylight' | 'high-contrast';
export type WallpaperId = 'aurora' | 'horizon' | 'blueprint' | 'monolith';
export type ColorScheme = 'dark' | 'light';

export interface ThemeDefinition {
  readonly id: ThemeId;
  readonly label: string;
  readonly colorScheme: ColorScheme;
  readonly description: string;
}

export interface WallpaperDefinition {
  readonly id: WallpaperId;
  readonly label: string;
  readonly description: string;
}

export interface DesktopAppearance {
  readonly themeId: ThemeId;
  readonly wallpaperId: WallpaperId;
}

export interface DesktopStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem?(key: string): void;
}

export type DesktopAppearanceErrorCode =
  | 'INVALID_THEME'
  | 'INVALID_WALLPAPER'
  | 'INVALID_APPEARANCE'
  | 'INVALID_ACTION'
  | 'INVALID_SCOPE'
  | 'CORRUPTED_STORAGE'
  | 'UNSUPPORTED_SCHEMA';

export class DesktopAppearanceError extends Error {
  readonly code: DesktopAppearanceErrorCode;

  constructor(code: DesktopAppearanceErrorCode, message: string) {
    super(message);
    this.name = 'DesktopAppearanceError';
    this.code = code;
  }
}

export const DESKTOP_APPEARANCE_SCHEMA_VERSION = 1 as const;
export const DESKTOP_STORAGE_KEY_PREFIX = 'vibeos.desktop.appearance' as const;
export const MAX_APPEARANCE_PAYLOAD_CHARS = 1024;

export const DEFAULT_THEME_ID: ThemeId = 'midnight';
export const DEFAULT_WALLPAPER_ID: WallpaperId = 'aurora';

export const DEFAULT_DESKTOP_APPEARANCE: DesktopAppearance = Object.freeze({
  themeId: DEFAULT_THEME_ID,
  wallpaperId: DEFAULT_WALLPAPER_ID,
});

export const THEME_LIST: readonly ThemeDefinition[] = Object.freeze([
  Object.freeze({
    id: 'midnight',
    label: 'Midnight',
    colorScheme: 'dark',
    description: 'Deep slate dark desktop surface',
  }),
  Object.freeze({
    id: 'daylight',
    label: 'Daylight',
    colorScheme: 'light',
    description: 'Crisp high-legibility light desktop surface',
  }),
  Object.freeze({
    id: 'high-contrast',
    label: 'High Contrast',
    colorScheme: 'dark',
    description: 'Maximum-contrast accessible dark surface',
  }),
]);

export const WALLPAPER_LIST: readonly WallpaperDefinition[] = Object.freeze([
  Object.freeze({
    id: 'aurora',
    label: 'Aurora',
    description: 'Layered polar atmospheric glow',
  }),
  Object.freeze({
    id: 'horizon',
    label: 'Horizon',
    description: 'Warm twilight radial gradient',
  }),
  Object.freeze({
    id: 'blueprint',
    label: 'Blueprint',
    description: 'Architectural coordinate grid',
  }),
  Object.freeze({
    id: 'monolith',
    label: 'Monolith',
    description: 'Calm low-distraction matte canvas',
  }),
]);

export const THEME_BY_ID: Readonly<Record<ThemeId, ThemeDefinition>> = Object.freeze({
  midnight: THEME_LIST[0],
  daylight: THEME_LIST[1],
  'high-contrast': THEME_LIST[2],
});

export const WALLPAPER_BY_ID: Readonly<Record<WallpaperId, WallpaperDefinition>> = Object.freeze({
  aurora: WALLPAPER_LIST[0],
  horizon: WALLPAPER_LIST[1],
  blueprint: WALLPAPER_LIST[2],
  monolith: WALLPAPER_LIST[3],
});

const ALLOWED_THEME_IDS: ReadonlySet<string> = new Set(THEME_LIST.map((item) => item.id));
const ALLOWED_WALLPAPER_IDS: ReadonlySet<string> = new Set(WALLPAPER_LIST.map((item) => item.id));
const ALLOWED_APPEARANCE_KEYS: ReadonlySet<string> = new Set(['themeId', 'wallpaperId']);
const ALLOWED_STORED_KEYS: ReadonlySet<string> = new Set(['version', 'themeId', 'wallpaperId']);
const CANONICAL_USERNAME_RE = /^[a-z0-9_]{3,40}$/;

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return false;
  }
  const proto: unknown = Object.getPrototypeOf(value);
  if (proto !== Object.prototype && proto !== null) {
    return false;
  }
  if (
    Object.prototype.hasOwnProperty.call(value, '__proto__') ||
    Object.prototype.hasOwnProperty.call(value, 'constructor') ||
    Object.prototype.hasOwnProperty.call(value, 'prototype')
  ) {
    return false;
  }
  return true;
}

export function parseThemeId(value: unknown): ThemeId {
  if (typeof value !== 'string' || !ALLOWED_THEME_IDS.has(value)) {
    throw new DesktopAppearanceError('INVALID_THEME', 'Unsupported desktop theme selection.');
  }
  return value as ThemeId;
}

export function parseWallpaperId(value: unknown): WallpaperId {
  if (typeof value !== 'string' || !ALLOWED_WALLPAPER_IDS.has(value)) {
    throw new DesktopAppearanceError(
      'INVALID_WALLPAPER',
      'Unsupported desktop wallpaper selection.',
    );
  }
  return value as WallpaperId;
}

export function validateDesktopAppearance(value: unknown): DesktopAppearance {
  if (!isPlainRecord(value)) {
    throw new DesktopAppearanceError('INVALID_APPEARANCE', 'Invalid desktop appearance state.');
  }
  const keys = Object.keys(value);
  for (const key of keys) {
    if (!ALLOWED_APPEARANCE_KEYS.has(key)) {
      throw new DesktopAppearanceError(
        'INVALID_APPEARANCE',
        'Unexpected field in desktop appearance state.',
      );
    }
  }
  if (!Object.hasOwn(value, 'themeId') || !Object.hasOwn(value, 'wallpaperId')) {
    throw new DesktopAppearanceError('INVALID_APPEARANCE', 'Incomplete desktop appearance state.');
  }
  const themeId = parseThemeId(value.themeId);
  const wallpaperId = parseWallpaperId(value.wallpaperId);
  return { themeId, wallpaperId };
}

export function resolveAppearanceStorageKey(activeUsername: unknown = null): string {
  if (activeUsername === null || activeUsername === undefined || activeUsername === '') {
    return `${DESKTOP_STORAGE_KEY_PREFIX}:guest`;
  }
  if (typeof activeUsername !== 'string' || !CANONICAL_USERNAME_RE.test(activeUsername)) {
    throw new DesktopAppearanceError('INVALID_SCOPE', 'Invalid desktop session scope.');
  }
  return `${DESKTOP_STORAGE_KEY_PREFIX}:user:${activeUsername}`;
}

export function parseStoredDesktopAppearance(raw: unknown): DesktopAppearance {
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > MAX_APPEARANCE_PAYLOAD_CHARS) {
    throw new DesktopAppearanceError(
      'CORRUPTED_STORAGE',
      'Stored desktop appearance data is corrupted; restored default appearance.',
    );
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new DesktopAppearanceError(
      'CORRUPTED_STORAGE',
      'Stored desktop appearance data is corrupted; restored default appearance.',
    );
  }

  if (!isPlainRecord(parsed)) {
    throw new DesktopAppearanceError(
      'CORRUPTED_STORAGE',
      'Stored desktop appearance data is corrupted; restored default appearance.',
    );
  }

  const keys = Object.keys(parsed);
  for (const key of keys) {
    if (!ALLOWED_STORED_KEYS.has(key)) {
      throw new DesktopAppearanceError(
        'CORRUPTED_STORAGE',
        'Stored desktop appearance contained unexpected fields; restored default appearance.',
      );
    }
  }

  if (
    !Object.hasOwn(parsed, 'version') ||
    !Object.hasOwn(parsed, 'themeId') ||
    !Object.hasOwn(parsed, 'wallpaperId')
  ) {
    throw new DesktopAppearanceError(
      'CORRUPTED_STORAGE',
      'Stored desktop appearance data is incomplete; restored default appearance.',
    );
  }

  if (parsed.version !== DESKTOP_APPEARANCE_SCHEMA_VERSION) {
    throw new DesktopAppearanceError(
      'UNSUPPORTED_SCHEMA',
      'Unsupported desktop appearance version; restored default appearance.',
    );
  }

  try {
    const themeId = parseThemeId(parsed.themeId);
    const wallpaperId = parseWallpaperId(parsed.wallpaperId);
    return { themeId, wallpaperId };
  } catch {
    throw new DesktopAppearanceError(
      'CORRUPTED_STORAGE',
      'Stored desktop appearance contained unsupported values; restored default appearance.',
    );
  }
}

export interface ReadAppearanceOptions {
  readonly activeUsername?: unknown;
  readonly accessError?: string | null;
  readonly warnIfUnavailable?: boolean;
}

export interface ReadAppearanceResult {
  readonly appearance: DesktopAppearance;
  readonly error: string | null;
  readonly corruptedKey: string | null;
}

export function readDesktopAppearance(
  storage: DesktopStorage | null | undefined,
  options: ReadAppearanceOptions = {},
): ReadAppearanceResult {
  const { activeUsername = null, accessError = null, warnIfUnavailable = true } = options;

  let storageKey: string;
  try {
    storageKey = resolveAppearanceStorageKey(activeUsername);
  } catch (err) {
    return {
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      error:
        err instanceof DesktopAppearanceError
          ? `${err.message} Using default appearance.`
          : 'Invalid desktop session scope. Using default appearance.',
      corruptedKey: null,
    };
  }

  if (accessError) {
    return {
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      error: accessError,
      corruptedKey: null,
    };
  }

  if (!storage) {
    return {
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      error: warnIfUnavailable
        ? 'Desktop storage is unavailable; using default appearance for this session.'
        : null,
      corruptedKey: null,
    };
  }

  let raw: string | null;
  try {
    raw = storage.getItem(storageKey);
  } catch {
    return {
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      error: 'Desktop storage could not be read; using default appearance for this session.',
      corruptedKey: null,
    };
  }

  if (raw === null) {
    return {
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      error: null,
      corruptedKey: null,
    };
  }

  try {
    const appearance = parseStoredDesktopAppearance(raw);
    return { appearance, error: null, corruptedKey: null };
  } catch (err) {
    return {
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      error:
        err instanceof DesktopAppearanceError
          ? err.message
          : 'Stored desktop appearance data is corrupted; restored default appearance.',
      corruptedKey: storageKey,
    };
  }
}

export function purgeAppearanceStorageKey(
  storage: DesktopStorage | null | undefined,
  storageKey: string,
): void {
  if (!storage || typeof storage.removeItem !== 'function') {
    return;
  }
  if (!storageKey.startsWith(`${DESKTOP_STORAGE_KEY_PREFIX}:`)) {
    return;
  }
  try {
    storage.removeItem(storageKey);
  } catch {
    // Storage deletion may also be blocked in restricted contexts; fail safely.
  }
}

export interface LoadAppearanceResult {
  readonly appearance: DesktopAppearance;
  readonly error: string | null;
}

export function loadDesktopAppearance(
  storage: DesktopStorage | null | undefined,
  activeUsername: unknown = null,
): LoadAppearanceResult {
  const result = readDesktopAppearance(storage, { activeUsername, warnIfUnavailable: true });
  if (result.corruptedKey !== null) {
    purgeAppearanceStorageKey(storage, result.corruptedKey);
  }
  return {
    appearance: result.appearance,
    error: result.error,
  };
}

export interface SaveAppearanceResult {
  readonly ok: boolean;
  readonly appearance: DesktopAppearance;
  readonly persisted: boolean;
  readonly error: string | null;
}

export function saveDesktopAppearance(
  storage: DesktopStorage | null | undefined,
  appearance: unknown,
  activeUsername: unknown = null,
): SaveAppearanceResult {
  let validated: DesktopAppearance;
  let storageKey: string;
  try {
    validated = validateDesktopAppearance(appearance);
    storageKey = resolveAppearanceStorageKey(activeUsername);
  } catch (err) {
    return {
      ok: false,
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      persisted: false,
      error:
        err instanceof DesktopAppearanceError
          ? err.message
          : 'Invalid desktop appearance preferences.',
    };
  }

  if (!storage) {
    return {
      ok: true,
      appearance: validated,
      persisted: false,
      error: 'Desktop storage is unavailable; appearance applied for this session only.',
    };
  }

  const serialized = JSON.stringify({
    version: DESKTOP_APPEARANCE_SCHEMA_VERSION,
    themeId: validated.themeId,
    wallpaperId: validated.wallpaperId,
  });

  try {
    storage.setItem(storageKey, serialized);
    return {
      ok: true,
      appearance: validated,
      persisted: true,
      error: null,
    };
  } catch {
    return {
      ok: true,
      appearance: validated,
      persisted: false,
      error: 'Could not save desktop appearance locally; applied for this session only.',
    };
  }
}

export type AppearanceTransitionAction =
  | { readonly kind: 'theme'; readonly value: unknown }
  | { readonly kind: 'wallpaper'; readonly value: unknown }
  | { readonly kind: 'reset' };

export interface TransitionAppearanceResult {
  readonly ok: boolean;
  readonly appearance: DesktopAppearance;
  readonly error: string | null;
}

export function transitionDesktopAppearance(
  current: unknown,
  action: unknown,
): TransitionAppearanceResult {
  let base: DesktopAppearance = DEFAULT_DESKTOP_APPEARANCE;
  let corruptedStateRecovered = false;
  try {
    base = validateDesktopAppearance(current);
  } catch {
    corruptedStateRecovered = true;
  }

  if (!isPlainRecord(action) || typeof action.kind !== 'string') {
    return {
      ok: false,
      appearance: base,
      error: 'Unsupported desktop appearance action.',
    };
  }

  const keys = Object.keys(action);
  if (action.kind === 'reset') {
    if (keys.length !== 1) {
      return {
        ok: false,
        appearance: base,
        error: 'Unsupported desktop appearance action.',
      };
    }
    return {
      ok: true,
      appearance: DEFAULT_DESKTOP_APPEARANCE,
      error: null,
    };
  }

  if (
    (action.kind !== 'theme' && action.kind !== 'wallpaper') ||
    keys.length !== 2 ||
    !Object.hasOwn(action, 'value')
  ) {
    return {
      ok: false,
      appearance: base,
      error: 'Unsupported desktop appearance action.',
    };
  }

  try {
    if (action.kind === 'theme') {
      const themeId = parseThemeId(action.value);
      return {
        ok: true,
        appearance: { themeId, wallpaperId: base.wallpaperId },
        error: corruptedStateRecovered
          ? 'Invalid prior desktop state recovered to safe defaults before applying theme.'
          : null,
      };
    }
    const wallpaperId = parseWallpaperId(action.value);
    return {
      ok: true,
      appearance: { themeId: base.themeId, wallpaperId },
      error: corruptedStateRecovered
        ? 'Invalid prior desktop state recovered to safe defaults before applying wallpaper.'
        : null,
    };
  } catch (err) {
    return {
      ok: false,
      appearance: base,
      error:
        err instanceof DesktopAppearanceError
          ? err.message
          : 'Unsupported desktop appearance selection.',
    };
  }
}
