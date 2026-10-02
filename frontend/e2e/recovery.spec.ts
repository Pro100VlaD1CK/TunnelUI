import { expect, test, type Page } from '@playwright/test';

async function login(page: Page) {
  await page.goto('/');
  await page.getByLabel('Имя администратора').fill('test-admin');
  await page.getByLabel('Пароль', { exact: true }).fill('test-only-password');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Входящие' })).toBeVisible();
  const csrf = (await (await page.request.get('/api/auth/csrf')).json()).csrf_token;
  return { Origin: 'http://127.0.0.1:8765', 'X-CSRF-Token': csrf };
}

test('confirmation error, concurrent edit recovery and expired session are visible', async ({ page }) => {
  const headers = await login(page);
  const data = { username: 'recovery-user', display_name: 'Проверка восстановления', enabled: true, comment: '', expires_at: null };
  const created = await page.request.post('/api/clients', { headers, data });
  expect(created.status()).toBe(201);
  const client = await created.json();
  await page.getByRole('link', { name: 'Клиенты', exact: true }).click();
  await page.getByRole('button', { name: 'Действия: recovery-user' }).click();
  await page.getByRole('menuitem', { name: 'Удалить', exact: true }).click();
  const path = `**/api/clients/${client.id}?revision=1`;
  await page.route(path, route => route.fulfill({ status: 409, json: { code: 'attached_client_requires_apply' } }));
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Удалить', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('У клиента есть привязки');
  await page.screenshot({ path: 'test-results/confirmation-error.png', fullPage: true, animations: 'disabled' });
  await dialog.getByRole('button', { name: 'Отмена' }).click();
  await page.unroute(path);
  await page.getByRole('button', { name: 'Действия: recovery-user' }).click();
  await page.getByRole('menuitem', { name: 'Редактировать' }).click();
  const changed = await page.request.put(`/api/clients/${client.id}`, { headers, data: { ...data, display_name: 'Изменено другим администратором', revision: 1 } });
  expect(changed.status()).toBe(200);
  await page.getByLabel('Отображаемое имя').fill('Несохранённый вариант');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(dialog.getByRole('alert').filter({ hasText: 'Запись уже изменена' })).toBeVisible();
  await page.getByRole('button', { name: 'Закрыть без сохранения и обновить список' }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole('cell', { name: 'Изменено другим администратором', exact: true })).toBeVisible();
  await page.context().clearCookies();
  await page.getByRole('button', { name: 'Обновить список' }).click();
  await expect(page.getByRole('heading', { name: 'Вход в панель' })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Сессия истекла');
});

test('deleting sole row on page two returns to populated page one', async ({ page }) => {
  const headers = await login(page);
  for (let i = 0; i < 26; i++) {
    const response = await page.request.post('/api/clients', { headers, data: {
      username: `paging-${String(i).padStart(2, '0')}`, display_name: `Клиент ${i}`,
    } });
    expect(response.status()).toBe(201);
  }
  await page.getByRole('link', { name: 'Клиенты', exact: true }).click();
  await page.getByRole('searchbox', { name: 'Поиск клиентов' }).fill('paging-');
  await page.getByRole('searchbox', { name: 'Поиск клиентов' }).press('Enter');
  await expect(page.getByText('Всего: 26', { exact: true })).toBeVisible();
  await page.locator('.ant-pagination-item-2').click();
  await page.getByRole('button', { name: 'Действия: paging-25' }).click();
  await page.getByRole('menuitem', { name: 'Удалить', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Удалить', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'paging-00', exact: true })).toBeVisible();
  await expect(page.getByText('Всего: 25', { exact: true })).toBeVisible();
});

test('primary action contrast and keyboard skip in both themes', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await login(page);
  await page.getByRole('link', { name: 'Клиенты', exact: true }).click();
  for (const dark of [false, true]) {
    if (dark) await page.getByRole('button', { name: 'Тёмная тема' }).click();
    const contrast = await page.getByRole('button', { name: 'Добавить клиента' }).evaluate(element => {
      const style = getComputedStyle(element);
      const luminance = (color: string) => {
        const rgb = color.match(/[\d.]+/g)!.slice(0, 3).map(Number).map(v => {
          const channel = v / 255;
          return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
        });
        return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
      };
      const a = luminance(style.color), b = luminance(style.backgroundColor);
      return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    });
    expect(contrast, `${dark ? 'dark' : 'light'} primary label contrast`).toBeGreaterThanOrEqual(4.5);
  }
  const skip = page.getByRole('link', { name: 'К содержимому' });
  await skip.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main-content')).toBeFocused();
  await expect(page.getByRole('heading', { name: 'Клиенты' })).toBeVisible();
});
