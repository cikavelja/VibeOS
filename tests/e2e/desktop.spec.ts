import { expect, test } from '../../frontend/node_modules/@playwright/test';

test('sign-in workspace is accessible', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Your application workspace' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Register' })).toBeVisible();
});

test('desktop shell occupies full viewport and switches theme and wallpaper with accessible persistence', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');

  const shell = page.getByTestId('desktop-shell');
  await expect(shell).toBeVisible();
  await expect(shell).toHaveAttribute('data-theme', 'midnight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'aurora');
  await expect(shell).toHaveAttribute('data-color-scheme', 'dark');

  const initialBox = await shell.boundingBox();
  expect(initialBox).toEqual({ x: 0, y: 0, width: 1280, height: 720 });

  await page.setViewportSize({ width: 1024, height: 640 });
  const resizedBox = await shell.boundingBox();
  expect(resizedBox).toEqual({ x: 0, y: 0, width: 1024, height: 640 });

  const noHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  );
  expect(noHorizontalOverflow).toBe(true);

  const readShellStyles = () =>
    shell.evaluate((el) => {
      const shellStyle = getComputedStyle(el);
      const topbar = el.querySelector('header');
      const topbarStyle = topbar ? getComputedStyle(topbar) : null;
      return {
        color: shellStyle.color,
        backgroundImage: shellStyle.backgroundImage,
        topbarBg: topbarStyle?.backgroundColor ?? '',
        docTheme: document.documentElement.dataset.theme ?? '',
      };
    });

  const midnightStyles = await readShellStyles();
  expect(midnightStyles.docTheme).toBe('midnight');

  const themeSelect = page.getByLabel('Theme');
  const wallpaperSelect = page.getByLabel('Wallpaper');

  await themeSelect.focus();
  await expect(themeSelect).toBeFocused();
  await themeSelect.selectOption('daylight');
  await expect(shell).toHaveAttribute('data-theme', 'daylight');
  await expect(shell).toHaveAttribute('data-color-scheme', 'light');

  const daylightStyles = await readShellStyles();
  expect(daylightStyles.docTheme).toBe('daylight');
  expect(daylightStyles.color).not.toBe(midnightStyles.color);
  expect(daylightStyles.topbarBg).not.toBe(midnightStyles.topbarBg);

  await themeSelect.selectOption('high-contrast');
  await expect(shell).toHaveAttribute('data-theme', 'high-contrast');
  await expect(shell).toHaveAttribute('data-color-scheme', 'dark');

  const highContrastStyles = await readShellStyles();
  expect(highContrastStyles.docTheme).toBe('high-contrast');
  expect(highContrastStyles.color).not.toBe(daylightStyles.color);
  expect(highContrastStyles.topbarBg).not.toBe(daylightStyles.topbarBg);

  const seenWallpapers = new Set<string>([highContrastStyles.backgroundImage]);
  for (const wallpaperId of ['horizon', 'blueprint', 'monolith'] as const) {
    await wallpaperSelect.focus();
    await expect(wallpaperSelect).toBeFocused();
    await wallpaperSelect.selectOption(wallpaperId);
    await expect(shell).toHaveAttribute('data-wallpaper', wallpaperId);
    const current = await readShellStyles();
    expect(seenWallpapers.has(current.backgroundImage)).toBe(false);
    seenWallpapers.add(current.backgroundImage);
  }

  await page.reload();
  await expect(shell).toHaveAttribute('data-theme', 'high-contrast');
  await expect(shell).toHaveAttribute('data-wallpaper', 'monolith');
});

test('fails closed on corrupted storage, invalid inputs, and storage errors while preserving Alice/Bob isolation', async ({
  page,
}) => {
  await page.goto('/');
  await page.evaluate(() => {
    window.localStorage.setItem('unrelated.item', 'keep-intact');
    window.localStorage.setItem(
      'vibeos.desktop.appearance:guest',
      '{"version":1,"themeId":"hacked","wallpaperId":"url(https://evil.invalid)"}',
    );
  });

  await page.reload();
  const shell = page.getByTestId('desktop-shell');
  await expect(shell).toHaveAttribute('data-theme', 'midnight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'aurora');

  const alert = page.getByRole('alert');
  await expect(alert).toContainText(
    'Stored desktop appearance contained unsupported values; restored default appearance.',
  );

  const guestAfterPurge = await page.evaluate(() => ({
    corrupted: window.localStorage.getItem('vibeos.desktop.appearance:guest'),
    unrelated: window.localStorage.getItem('unrelated.item'),
  }));
  expect(guestAfterPurge).toEqual({ corrupted: null, unrelated: 'keep-intact' });

  await alert.getByRole('button', { name: 'Dismiss' }).click();
  await expect(alert).toHaveCount(0);

  await page.getByLabel('Theme').selectOption('daylight');
  await page.getByLabel('Wallpaper').selectOption('horizon');
  await expect(shell).toHaveAttribute('data-theme', 'daylight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'horizon');

  const storedBeforeTamper = await page.evaluate(() =>
    window.localStorage.getItem('vibeos.desktop.appearance:guest'),
  );

  await page.getByLabel('Theme').evaluate((select: HTMLSelectElement) => {
    const rogue = document.createElement('option');
    rogue.value = 'url(https://evil.invalid/theme.css)';
    select.appendChild(rogue);
    select.value = rogue.value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await expect(page.getByRole('alert')).toContainText('Unsupported desktop theme selection.');
  await expect(shell).toHaveAttribute('data-theme', 'daylight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'horizon');

  await page.getByLabel('Wallpaper').evaluate((select: HTMLSelectElement) => {
    const rogue = document.createElement('option');
    rogue.value = '__proto__';
    select.appendChild(rogue);
    select.value = rogue.value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await expect(page.getByRole('alert')).toContainText('Unsupported desktop wallpaper selection.');
  await expect(shell).toHaveAttribute('data-theme', 'daylight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'horizon');

  const storedAfterTamper = await page.evaluate(() =>
    window.localStorage.getItem('vibeos.desktop.appearance:guest'),
  );
  expect(storedAfterTamper).toBe(storedBeforeTamper);

  await page.evaluate(() => {
    window.dispatchEvent(
      new StorageEvent('storage', {
        key: 'vibeos.desktop.appearance:guest',
        newValue: '{"version":99,"themeId":"daylight","wallpaperId":"horizon"}',
      }),
    );
  });
  await expect(page.getByRole('alert')).toContainText(
    'Unsupported desktop appearance version; restored default appearance.',
  );
  await expect(shell).toHaveAttribute('data-theme', 'midnight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'aurora');

  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    (window as unknown as { __origSetItem?: typeof original }).__origSetItem = original;
    Storage.prototype.setItem = () => {
      throw new DOMException('Quota exceeded', 'QuotaExceededError');
    };
  });
  await page.getByLabel('Theme').selectOption('high-contrast');
  await expect(shell).toHaveAttribute('data-theme', 'high-contrast');
  await expect(page.getByRole('alert')).toContainText(
    'Could not save desktop appearance locally; applied for this session only.',
  );
  await page.evaluate(() => {
    const win = window as unknown as { __origSetItem?: typeof Storage.prototype.setItem };
    if (win.__origSetItem) {
      Storage.prototype.setItem = win.__origSetItem;
    }
  });
  await page.getByRole('alert').getByRole('button', { name: 'Dismiss' }).click();

  const suffix = Date.now();
  const alice = `alice_${suffix}`;
  const bob = `bob_${suffix}`;
  const password = 'test-password-123';

  await page.getByLabel('Username').fill(bob);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Register' }).click();
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
  await expect(shell).toHaveAttribute('data-theme', 'midnight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'aurora');
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Your application workspace' })).toBeVisible();

  await page.getByLabel('Username').fill(alice);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Register' }).click();
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();

  await expect(shell).toHaveAttribute('data-theme', 'midnight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'aurora');

  await page.getByLabel('Theme').selectOption('daylight');
  await page.getByLabel('Wallpaper').selectOption('blueprint');
  await expect(shell).toHaveAttribute('data-theme', 'daylight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'blueprint');

  await page.getByRole('button', { name: '+ counter' }).click();
  await page.getByRole('button', { name: 'Save application' }).click();
  await expect(page.getByRole('heading', { name: 'My projects' })).toBeVisible();
  await page.getByLabel('Share with username').fill(bob);
  await page.getByRole('button', { name: 'Send copy' }).click();
  await expect(page.getByText('Application sent')).toBeVisible();

  await page.getByRole('button', { name: 'Install', exact: true }).click();
  await expect(page.getByText('Installed', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Installed', exact: true }).click();
  await page.getByRole('button', { name: 'My application' }).click();
  await page.getByRole('button', { name: 'counter: 0' }).click();
  await expect(page.getByRole('button', { name: 'counter: 1' })).toBeVisible();
  await page.getByRole('button', { name: 'Save state' }).click();
  await expect(page.getByText('State saved')).toBeVisible();

  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Your application workspace' })).toBeVisible();
  await expect(shell).toHaveAttribute('data-theme', 'midnight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'aurora');

  await page.getByLabel('Username').fill(bob);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
  await expect(shell).toHaveAttribute('data-theme', 'midnight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'aurora');

  await page.getByRole('button', { name: 'Inbox', exact: true }).click();
  await page.getByRole('button', { name: 'Accept and install' }).click();
  await page.getByRole('button', { name: 'My application' }).click();
  await expect(page.getByRole('button', { name: 'counter: 0' })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Your application workspace' })).toBeVisible();

  await page.getByLabel('Username').fill(alice);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
  await expect(shell).toHaveAttribute('data-theme', 'daylight');
  await expect(shell).toHaveAttribute('data-wallpaper', 'blueprint');
  await page.getByRole('button', { name: 'Installed', exact: true }).click();
  await page.getByRole('button', { name: 'My application' }).click();
  await expect(page.getByRole('button', { name: 'counter: 1' })).toBeVisible();
});
