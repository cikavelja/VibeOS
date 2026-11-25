import { expect, test } from '../../frontend/node_modules/@playwright/test';

test('renders live healthy system status from FastAPI backend and authenticated Health view', async ({
  page,
}) => {
  await page.goto('/');
  const panel = page.getByRole('region', { name: 'System health' });
  await expect(panel.getByRole('heading', { name: 'System health' })).toBeVisible();
  await expect(panel).toContainText('Healthy');
  await expect(panel).toContainText('API service vibeos (v0.1.0) is online.');
  await expect(page.locator('header')).toContainText('API: Healthy');

  const uniqueUser = `health_${Date.now()}`;
  await page.getByLabel('Username').fill(uniqueUser);
  await page.getByLabel('Password').fill('test-password-123');
  await page.getByRole('button', { name: 'Register' }).click();

  await page.getByRole('button', { name: 'Health' }).click();
  const authPanel = page.getByRole('region', { name: 'System health' });
  await expect(authPanel).toContainText('API service vibeos (v0.1.0) is online.');
});

test('rejects invalid health inputs and surfaces backend/schema failures without silent success', async ({
  page,
}) => {
  const badQuery = await page.request.get('/api/v1/health?unexpected=1');
  expect(badQuery.status()).toBe(422);

  const badMethod = await page.request.post('/api/v1/health', { data: { status: 'override' } });
  expect(badMethod.status()).toBe(405);

  let mode: '503' | 'tampered' = '503';
  await page.route('**/api/v1/health', async (route) => {
    if (mode === '503') {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Database unavailable' }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', service: 'vibeos', unexpected: 'tampered' }),
    });
  });

  await page.goto('/');
  const panel = page.getByRole('region', { name: 'System health' });
  await expect(panel).toContainText('Service unavailable: Health check failed (HTTP 503)');
  await expect(page.locator('header')).toContainText('API: Unavailable');

  mode = 'tampered';
  await panel.getByRole('button', { name: 'Check health' }).click();
  await expect(panel).toContainText('Service unavailable: Unexpected health field: unexpected');

  await page.unroute('**/api/v1/health');
  await panel.getByRole('button', { name: 'Check health' }).click();
  await expect(panel).toContainText('API service vibeos (v0.1.0) is online.');
  await expect(page.locator('header')).toContainText('API: Healthy');
});
