import {test,expect} from '../../frontend/node_modules/@playwright/test';
test('sign-in workspace is accessible',async({page})=>{await page.goto('/');await expect(page.getByRole('heading',{name:'Your application workspace'})).toBeVisible();await expect(page.getByRole('button',{name:'Register'})).toBeVisible();});
