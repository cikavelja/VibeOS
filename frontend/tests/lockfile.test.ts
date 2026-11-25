import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  cpSync,
  existsSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

interface PackageJsonShape {
  name: string;
  version: string;
  scripts?: Record<string, string>;
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
}

interface LockPackageEntry {
  version?: string;
  resolved?: string;
  integrity?: string;
  link?: boolean;
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
}

interface PackageLockShape {
  name: string;
  version: string;
  lockfileVersion: number;
  packages: Record<string, LockPackageEntry>;
}

const EXACT_SEMVER_RE = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
const SRI_RE = /^sha(?:256|384|512)-[A-Za-z0-9+/=]{32,}$/;

function copyCleanFrontendCheckout(targetDir: string): void {
  const files = [
    'package.json',
    'package-lock.json',
    'eslint.config.js',
    'tsconfig.json',
    'tsconfig.node.json',
    'vite.config.ts',
    'playwright.config.ts',
    'index.html',
  ];
  for (const file of files) {
    cpSync(join(FRONTEND_ROOT, file), join(targetDir, file));
  }
  cpSync(join(FRONTEND_ROOT, 'src'), join(targetDir, 'src'), { recursive: true });
}

function runNpm(args: string[], cwd: string): { status: number | null; output: string } {
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    NODE_ENV: 'development',
    npm_config_audit: 'false',
    npm_config_fund: 'false',
    npm_config_update_notifier: 'false',
  };
  const npmExecPath = process.env.npm_execpath;
  const result =
    npmExecPath && existsSync(npmExecPath)
      ? spawnSync(process.execPath, [npmExecPath, ...args], {
          cwd,
          env,
          encoding: 'utf-8',
        })
      : spawnSync(process.platform === 'win32' ? 'npm.cmd' : 'npm', args, {
          cwd,
          env,
          encoding: 'utf-8',
          shell: process.platform === 'win32',
        });
  return {
    status: result.status,
    output: `${result.stdout ?? ''}\n${result.stderr ?? ''}`,
  };
}

function sha256File(filePath: string): string {
  return createHash('sha256').update(readFileSync(filePath)).digest('hex');
}

describe('frontend reproducible dependency lockfile', () => {
  it('pins exact versions, scripts, cross-platform binaries, and HTTPS SRI hashes', () => {
    const pkg = JSON.parse(
      readFileSync(join(FRONTEND_ROOT, 'package.json'), 'utf-8'),
    ) as PackageJsonShape;
    const lock = JSON.parse(
      readFileSync(join(FRONTEND_ROOT, 'package-lock.json'), 'utf-8'),
    ) as PackageLockShape;

    expect(lock.lockfileVersion).toBe(3);
    expect(lock.name).toBe(pkg.name);
    expect(lock.version).toBe(pkg.version);

    for (const scriptName of ['build', 'typecheck', 'lint', 'test', 'e2e']) {
      expect(pkg.scripts?.[scriptName]).toBeTruthy();
    }

    const rootPkg = lock.packages[''];
    expect(rootPkg).toBeDefined();
    expect(rootPkg.dependencies).toEqual(pkg.dependencies);
    expect(rootPkg.devDependencies).toEqual(pkg.devDependencies);

    const allDirect = {
      ...(pkg.dependencies ?? {}),
      ...(pkg.devDependencies ?? {}),
    };
    for (const [depName, depVersion] of Object.entries(allDirect)) {
      expect(depVersion).toMatch(EXACT_SEMVER_RE);
      expect(lock.packages[`node_modules/${depName}`]?.version).toBe(depVersion);
    }

    for (const platformPkg of [
      'node_modules/@esbuild/linux-x64',
      'node_modules/@esbuild/win32-x64',
      'node_modules/@rollup/rollup-linux-x64-gnu',
      'node_modules/@rollup/rollup-linux-x64-musl',
      'node_modules/@rollup/rollup-win32-x64-msvc',
    ]) {
      expect(lock.packages[platformPkg]).toBeDefined();
    }

    const resolvedEntries = Object.entries(lock.packages).filter(([key]) => key !== '');
    expect(resolvedEntries.length).toBeGreaterThan(20);
    for (const [, entry] of resolvedEntries) {
      expect(entry.link).not.toBe(true);
      expect(entry.resolved?.startsWith('https://')).toBe(true);
      expect(entry.integrity).toMatch(SRI_RE);
    }
  });

  it(
    'installs from committed package-lock.json in a clean checkout and builds dist bundle',
    () => {
      const cleanDir = realpathSync.native(mkdtempSync(join(tmpdir(), 'vibeos-fe-clean-')));
      try {
        copyCleanFrontendCheckout(cleanDir);
        expect(existsSync(join(cleanDir, 'node_modules'))).toBe(false);
        expect(existsSync(join(cleanDir, 'dist'))).toBe(false);

        const installRes = runNpm(['ci', '--prefer-offline'], cleanDir);
        expect(installRes.status, installRes.output).toBe(0);

        const buildRes = runNpm(['run', 'build'], cleanDir);
        expect(buildRes.status, buildRes.output).toBe(0);

        expect(existsSync(join(cleanDir, 'dist', 'index.html'))).toBe(true);
        const assets = readdirSync(join(cleanDir, 'dist', 'assets'));
        expect(assets.some((name) => name.endsWith('.js'))).toBe(true);
        expect(assets.some((name) => name.endsWith('.css'))).toBe(true);
      } finally {
        rmSync(cleanDir, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
      }
    },
    120_000,
  );

  it(
    'fails closed on package.json drift without mutating package-lock.json',
    () => {
      const driftDir = realpathSync.native(mkdtempSync(join(tmpdir(), 'vibeos-fe-drift-')));
      try {
        copyCleanFrontendCheckout(driftDir);
        const lockPath = join(driftDir, 'package-lock.json');
        const beforeHash = sha256File(lockPath);

        const pkgPath = join(driftDir, 'package.json');
        const pkg = JSON.parse(readFileSync(pkgPath, 'utf-8')) as PackageJsonShape;
        pkg.dependencies = { ...(pkg.dependencies ?? {}), react: '19.0.0' };
        writeFileSync(pkgPath, JSON.stringify(pkg, null, 2), 'utf-8');

        const ciRes = runNpm(['ci', '--ignore-scripts', '--prefer-offline'], driftDir);
        expect(ciRes.status).not.toBe(0);
        expect(sha256File(lockPath)).toBe(beforeHash);
      } finally {
        rmSync(driftDir, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
      }
    },
    60_000,
  );
});
