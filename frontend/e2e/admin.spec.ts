import { expect, test, type Page, type TestInfo } from '@playwright/test';

async function attachDrawerDiagnostics(page: Page, testInfo: TestInfo, name: string) {
  const state = await page.locator('.ant-drawer').evaluateAll(drawers => drawers.map(drawer => {
    const box = drawer.getBoundingClientRect();
    const save = Array.from(drawer.querySelectorAll('button')).find(button => button.textContent?.trim() === 'Сохранить');
    const title = drawer.querySelector('.ant-drawer-title')?.textContent?.trim() ?? null;
    return {
      title,
      ariaHidden: drawer.getAttribute('aria-hidden'),
      display: getComputedStyle(drawer).display,
      visibility: getComputedStyle(drawer).visibility,
      width: Math.round(box.width),
      height: Math.round(box.height),
      save: save ? {
        disabled: (save as HTMLButtonElement).disabled,
        display: getComputedStyle(save).display,
        visibility: getComputedStyle(save).visibility,
        width: Math.round(save.getBoundingClientRect().width),
      } : null,
    };
  }));
  await testInfo.attach(name, { body: Buffer.from(JSON.stringify(state, null, 2)), contentType: 'application/json' });
}

test('admin: login, client CRUD, duplicate, audit, logout and responsive themes', async ({ page }, testInfo) => {
  const errors: string[] = [];
  const consoleErrors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto('/');
  await page.getByLabel('Имя администратора').fill('test-admin');
  await page.getByLabel('Пароль', { exact: true }).fill('test-only-password');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Входящие' })).toBeVisible();
  await expect(page.getByText('Нет импортированных входящих')).toBeVisible();
  await page.screenshot({ path: 'test-results/inbounds-desktop-light.png', fullPage: true, animations: 'disabled' });
  await page.getByRole('link', { name: 'Клиенты', exact: true }).click();
  await page.getByRole('button', { name: 'Добавить клиента' }).click();
  const createDrawer = page.getByRole('dialog', { name: 'Новый клиент' });
  await expect(createDrawer).toBeVisible();
  await createDrawer.getByLabel('Username', { exact: true }).fill('test-ivan');
  await createDrawer.getByLabel('Отображаемое имя').fill('Иван · тестовая запись');
  await createDrawer.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'test-ivan', exact: true })).toBeVisible();
  await expect(createDrawer).toBeHidden();
  await page.getByRole('button', { name: 'Добавить клиента' }).click();
  const duplicateDrawer = page.getByRole('dialog', { name: 'Новый клиент' });
  await expect(duplicateDrawer).toBeVisible();
  await duplicateDrawer.getByLabel('Username', { exact: true }).fill('test-ivan');
  await duplicateDrawer.getByLabel('Отображаемое имя').fill('Дубликат');
  await duplicateDrawer.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(duplicateDrawer.getByText('Такой username уже существует. Укажите другой.')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(duplicateDrawer).toBeHidden();
  await attachDrawerDiagnostics(page, testInfo, 'drawer-state-after-duplicate-close');
  await page.getByRole('button', { name: 'Действия: test-ivan' }).click();
  await page.getByRole('menuitem', { name: 'Редактировать' }).click();
  const editDrawer = page.getByRole('dialog', { name: 'Редактировать клиента' });
  await expect(editDrawer).toBeVisible();
  await attachDrawerDiagnostics(page, testInfo, 'drawer-state-after-editor-open');
  await editDrawer.getByLabel('Отображаемое имя').fill('Очень длинное имя клиента для проверки отображения в таблице и редакторе');
  await attachDrawerDiagnostics(page, testInfo, 'drawer-state-after-editor-fill');
  const saveButton = editDrawer.getByRole('button', { name: 'Сохранить', exact: true });
  await expect(saveButton).toBeVisible();
  await expect(saveButton).toBeEnabled();
  await saveButton.click();
  await expect(editDrawer).toBeHidden();
  await expect(page.getByRole('cell', { name: /Очень длинное/ })).toBeVisible();
  await page.getByRole('button', { name: 'Тёмная тема' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.screenshot({ path: 'test-results/clients-desktop-dark.png', fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('button', { name: 'Открыть меню' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/clients-mobile-dark.png', fullPage: true, animations: 'disabled' });
  await page.getByRole('button', { name: 'Открыть меню' }).click();
  await page.getByRole('link', { name: 'Аудит', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Аудит' })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Создан глобальный клиент', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole('complementary').getByRole('link', { name: 'Клиенты', exact: true }).click();
  await page.getByRole('button', { name: 'Действия: test-ivan' }).click();
  await page.getByRole('menuitem', { name: 'Удалить', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Удалить', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'test-ivan', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Вход в панель' })).toBeVisible();
  expect(errors).toEqual([]);
  expect(consoleErrors.filter(message => /\b(?:React|Warning|Uncaught)\b/i.test(message))).toEqual([]);
});

test('mobile login and visible network failure', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Вход в панель' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/login-mobile-light.png', fullPage: true });
  await page.route('**/api/auth/login', route => route.abort());
  await page.getByLabel('Имя администратора').fill('test-admin');
  await page.getByLabel('Пароль', { exact: true }).fill('test-only-password');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Нет связи с панелью');
});
