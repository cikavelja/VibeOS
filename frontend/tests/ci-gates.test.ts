import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  cpSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const REPO_ROOT = resolve(FRONTEND_ROOT, '..');

interface PackageJsonScriptsShape {
  scripts?: Record<string, string>;
}

function sha256File(filePath: string): string {
  return createHash('sha256').update(readFileSync(filePath)).digest('hex');
}

function runNodeCli(
  cliRelativePath: string,
  args: string[],
  cwd: string,
): { status: number | null; output: string } {
  const cliPath = join(FRONTEND_ROOT, 'node_modules', cliRelativePath);
  const res = spawnSync(process.execPath, [cliPath, ...args], {
    cwd,
    encoding: 'utf-8',
    env: {
      ...process.env,
      CI: 'true',
      NO_COLOR: '1',
    },
  });
  return {
    status: res.status,
    output: `${res.stdout ?? ''}\n${res.stderr ?? ''}`,
  };
}

function createIsolatedFrontendSandbox(): {
  dir: string;
  cleanup: () => void;
} {
  const dir = mkdtempSync(join(tmpdir(), 'vibeos-fe-ci-gate-'));
  for (const configFile of ['package.json', 'tsconfig.json', 'eslint.config.js']) {
    cpSync(join(FRONTEND_ROOT, configFile), join(dir, configFile));
  }
  const linkedModules = join(dir, 'node_modules');
  symlinkSync(join(FRONTEND_ROOT, 'node_modules'), linkedModules, 'junction');
  return {
    dir,
    cleanup: () => {
      try {
        if (existsSync(linkedModules)) {
          unlinkSync(linkedModules);
        }
      } finally {
        rmSync(dir, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
      }
    },
  };
}

describe('frontend CI quality gates', () => {
  it('declares all required frontend checks in product-preview.yml without error masking', () => {
    const workflowPath = join(REPO_ROOT, '.github', 'workflows', 'product-preview.yml');
    const planningPath = join(REPO_ROOT, '.github', 'workflows', 'planning.yml');
    const workflow = readFileSync(workflowPath, 'utf-8').replace(/\r\n/g, '\n');
    const planning = readFileSync(planningPath, 'utf-8').replace(/\r\n/g, '\n');
    const pkg = JSON.parse(
      readFileSync(join(FRONTEND_ROOT, 'package.json'), 'utf-8'),
    ) as PackageJsonScriptsShape;

    for (const content of [workflow, planning]) {
      expect(content).toContain('permissions:\n  contents: read');
      expect(content).toContain('on: [push, pull_request]');
      expect(content).toContain('timeout-minutes:');
      expect(content).not.toContain('continue-on-error: true');
      expect(content).not.toMatch(/\|\|\s*true\b/);
    }

    expect(planning).toContain('python scripts/verify_ci.py');

    for (const requiredStep of [
      'npm ci',
      'npm run lint',
      'npm run typecheck',
      'npm run build',
      'npm run test',
      'npx playwright install --with-deps chromium',
      'npm run e2e',
    ]) {
      expect(workflow).toContain(requiredStep);
    }

    for (const scriptName of ['lint', 'typecheck', 'build', 'test', 'e2e']) {
      expect(pkg.scripts?.[scriptName]).toBeTruthy();
    }
  });

  it(
    'fails closed with non-zero exit codes on deliberately failing test, typecheck, and lint checks',
    () => {
      const pkgDigestBefore = sha256File(join(FRONTEND_ROOT, 'package.json'));
      const lockDigestBefore = sha256File(join(FRONTEND_ROOT, 'package-lock.json'));

      const sandbox = createIsolatedFrontendSandbox();
      try {
        const srcDir = join(sandbox.dir, 'src');
        cpSync(join(FRONTEND_ROOT, 'src'), srcDir, { recursive: true });

        const passingSpec = join(srcDir, 'ci-passing-probe.test.ts');
        writeFileSync(
          passingSpec,
          "import { expect, test } from 'vitest';\n" +
            "test('passing baseline check', () => {\n" +
            '  expect(2 + 2).toBe(4);\n' +
            '});\n',
          'utf-8',
        );

        const passVitest = runNodeCli(
          'vitest/vitest.mjs',
          ['run', 'src/ci-passing-probe.test.ts'],
          sandbox.dir,
        );
        expect(passVitest.status, passVitest.output).toBe(0);

        const passTsc = runNodeCli('typescript/bin/tsc', ['--noEmit'], sandbox.dir);
        expect(passTsc.status, passTsc.output).toBe(0);

        const passEslint = runNodeCli(
          'eslint/bin/eslint.js',
          ['src/ci-passing-probe.test.ts'],
          sandbox.dir,
        );
        expect(passEslint.status, passEslint.output).toBe(0);

        const failingSpec = join(srcDir, 'ci-deliberate-failure.test.ts');
        writeFileSync(
          failingSpec,
          "import { expect, test } from 'vitest';\n" +
            "test('deliberate frontend failure for AC-01', () => {\n" +
            "  expect('healthy').toBe('broken');\n" +
            '});\n',
          'utf-8',
        );

        const failVitest = runNodeCli(
          'vitest/vitest.mjs',
          ['run', 'src/ci-deliberate-failure.test.ts'],
          sandbox.dir,
        );
        expect(failVitest.status).not.toBe(0);
        expect(failVitest.output).toContain('deliberate frontend failure for AC-01');
        rmSync(failingSpec);

        const brokenTypeFile = join(srcDir, 'broken-type.ts');
        writeFileSync(
          brokenTypeFile,
          "export const brokenPort: number = 'not-a-port';\n",
          'utf-8',
        );
        const failTsc = runNodeCli('typescript/bin/tsc', ['--noEmit'], sandbox.dir);
        expect(failTsc.status).not.toBe(0);
        expect(failTsc.output).toContain('TS2322');
        rmSync(brokenTypeFile);

        const brokenLintFile = join(srcDir, 'broken-lint.ts');
        writeFileSync(
          brokenLintFile,
          "export const unsafeResult: unknown = eval('1 + 1');\n",
          'utf-8',
        );
        const failEslint = runNodeCli(
          'eslint/bin/eslint.js',
          ['src/broken-lint.ts'],
          sandbox.dir,
        );
        expect(failEslint.status).not.toBe(0);
        expect(failEslint.output).toContain('no-eval');
      } finally {
        sandbox.cleanup();
      }

      expect(sha256File(join(FRONTEND_ROOT, 'package.json'))).toBe(pkgDigestBefore);
      expect(sha256File(join(FRONTEND_ROOT, 'package-lock.json'))).toBe(lockDigestBefore);
    },
    60_000,
  );
});
