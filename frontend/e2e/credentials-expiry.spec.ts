import { expect, test, type Page } from '@playwright/test';

// A separate disposable sandbox keeps the Phase 2 adoption scenario independent.
test.use({ baseURL: 'http://127.0.0.1:8766' });

async function loginAndAdopt(page: Page) {
  await page.goto('/');
  await page.getByLabel('Имя администратора').fill('test-admin');
  await page.getByLabel('Пароль', { exact: true }).fill('test-only-password');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await page.getByRole('button', { name: 'Найти TrustTunnel' }).first().click();
  const adoption = page.getByRole('dialog', { name: 'Импорт существующего TrustTunnel' });
  await adoption.getByRole('button', { name: 'Проверить конфигурацию' }).click();
  await expect(adoption.getByText('Проверка завершена')).toBeVisible();
  await adoption.getByRole('button', { name: 'Импортировать в TunnelUI' }).click();
  await expect(adoption).toBeHidden();
}

test('credential edit, rotation, current profile and mobile expiry controls', async ({ page }) => {
  await loginAndAdopt(page);
  await page.getByRole('link', { name: 'Клиенты', exact: true }).click();
  await page.getByRole('button', { name: 'Действия: sandbox-alice' }).click();
  await page.getByRole('menuitem', { name: 'Редактировать' }).click();
  const editor = page.getByRole('dialog', { name: 'Редактировать клиента' });
  await expect(editor).toBeVisible();
  await editor.getByLabel('Username', { exact: true }).fill('alice-renamed');
  await editor.getByRole('button', { name: 'Сохранить', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: 'Применить изменения учётных данных?' });
  await expect(confirmation).toContainText('TrustTunnel');
  const credentialResponse = page.waitForResponse(response => response.url().includes('/credentials'));
  await confirmation.getByRole('button', { name: 'Сохранить и применить' }).click();
  const credentialResult = await credentialResponse;
  expect(credentialResult.status(), JSON.stringify(await credentialResult.json())).toBe(200);
  await expect(confirmation).toBeHidden();
  await expect(editor).toBeHidden();
  await expect(page.getByRole('cell', { name: 'alice-renamed', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Действия: alice-renamed' }).click();
  await page.getByRole('menuitem', { name: 'Сменить пароль · TrustTunnel' }).click();
  const rotation = page.getByRole('dialog', { name: 'Сменить пароль · TrustTunnel' });
  await rotation.getByRole('button', { name: 'Сгенерировать пароль' }).click();
  await rotation.getByRole('button', { name: 'Применить новый пароль' }).click();
  await expect(rotation.getByText('Новый пароль действует')).toBeVisible();
  await rotation.getByRole('button', { name: 'Открыть профиль' }).click();
  await expect(page.getByRole('heading', { name: 'Профили' })).toBeVisible();
  await page.screenshot({ path: 'test-results/credentials-profile-desktop.png', fullPage: true, animations: 'disabled' });
  await page.getByRole('button', { name: /QR$/ }).click();
  const profile = page.getByRole('dialog', { name: 'TrustTunnel · тестовый профиль' });
  await expect(profile).toBeVisible();
  await profile.locator('.ant-modal-footer').getByRole('button', { name: 'Закрыть' }).click();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Открыть меню' }).click();
  await page.getByRole('link', { name: 'Клиенты', exact: true }).click();
  await page.getByRole('button', { name: 'Действия: alice-renamed' }).click();
  await page.getByRole('menuitem', { name: 'Редактировать' }).click();
  const mobileEditor = page.getByRole('dialog', { name: 'Редактировать клиента' });
  await expect(mobileEditor.getByLabel('Username', { exact: true })).toBeEnabled();
  await expect(mobileEditor.getByLabel('Действует до')).toBeEnabled();
  for (const width of [320, 375, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth))
      .toBeLessThanOrEqual(0);
  }
  await page.screenshot({ path: 'test-results/credentials-editor-mobile.png', fullPage: true, animations: 'disabled' });
  await mobileEditor.getByLabel('Действует до').click();
  await mobileEditor.getByLabel('Действует до').fill('20.10.2030 12:00');
  await mobileEditor.getByLabel('Действует до').press('Tab');
  await expect(mobileEditor.getByLabel('Действует до')).toHaveValue('20.10.2030 12:00');
  await mobileEditor.getByRole('button', { name: 'Сохранить', exact: true }).click();
  const expiryConfirmation = page.getByRole('dialog', { name: 'Применить изменения учётных данных?' });
  await expect(expiryConfirmation).toBeVisible();
  await page.screenshot({ path: 'test-results/expiry-confirmation-mobile.png', fullPage: true, animations: 'disabled' });
  await expiryConfirmation.getByRole('button', { name: 'Сохранить и применить' }).click();
  await expect(expiryConfirmation).toBeHidden();
  await expect(mobileEditor).toBeHidden();
  await page.getByRole('button', { name: 'Открыть меню' }).click();
  await page.getByRole('link', { name: 'Настройки', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Настройки' })).toBeVisible();
  await page.getByRole('combobox', { name: 'Раздел настроек' }).selectOption({ label: 'Сроки доступа' });
  await expect(page.getByText('alice-renamed')).toBeVisible();
  await expect(page.getByText('Автоматическая обработка сроков выключена')).toBeVisible();
  await page.screenshot({ path: 'test-results/expiry-settings-mobile.png', fullPage: true, animations: 'disabled' });
});
