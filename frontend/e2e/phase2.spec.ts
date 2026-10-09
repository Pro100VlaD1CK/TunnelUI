import { expect, test, type Page } from '@playwright/test';

async function overflowGeometry(page: Page) {
  return page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    offenders: [...document.querySelectorAll<HTMLElement>('body *')].map(element => {
      const rect = element.getBoundingClientRect();
      let ancestor = element.parentElement;
      let contained = false;
      while (ancestor && ancestor !== document.body) {
        const overflow = getComputedStyle(ancestor).overflowX;
        if (['auto', 'scroll', 'hidden', 'clip'].includes(overflow)) {
          contained = true;
          break;
        }
        ancestor = ancestor.parentElement;
      }
      return {
        tag: element.tagName.toLowerCase(),
        className: typeof element.className === 'string' ? element.className : '',
        left: Math.round(rect.left), right: Math.round(rect.right), width: Math.round(rect.width),
        contained,
      };
    }).filter(item => !item.contained && (item.right > window.innerWidth + 1 || item.left < -1))
      .sort((left, right) => right.right - left.right).slice(0, 12),
  }));
}

async function expectNoPageOverflow(page: Page, width: number) {
  await page.setViewportSize({ width, height: 844 });
  await expect.poll(
    () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
    { message: `Страница не должна переполнять viewport ${width}px` },
  ).toBeLessThanOrEqual(0);
  const geometry = await overflowGeometry(page);
  expect(geometry.documentWidth, JSON.stringify(geometry, null, 2)).toBeLessThanOrEqual(geometry.viewportWidth);
}

async function login(page: Page) {
  await page.goto('/');
  await page.getByLabel('Имя администратора').fill('test-admin');
  await page.getByLabel('Пароль', { exact: true }).fill('test-only-password');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Входящие' })).toBeVisible();
  const csrf = (await (await page.request.get('/api/auth/csrf')).json()).csrf_token;
  return { Origin: 'http://127.0.0.1:8765', 'X-CSRF-Token': csrf };
}

test('Phase 2 sandbox: adoption, apply, profile, drift, rollback and recovery', async ({ page }) => {
  test.setTimeout(60_000);
  const headers = await login(page);
  await page.getByRole('button', { name: 'Найти TrustTunnel' }).first().click();
  const adoption = page.getByRole('dialog', { name: 'Импорт существующего TrustTunnel' });
  await expect(adoption.getByText('Найден существующий TrustTunnel')).toBeVisible();
  await adoption.getByRole('button', { name: 'Проверить конфигурацию' }).click();
  await expect(adoption.getByText('Проверка завершена')).toBeVisible();
  await expect(adoption.getByText('sandbox-alice')).toHaveCount(0);
  await adoption.getByRole('button', { name: 'Импортировать в TunnelUI' }).click();
  await expect(adoption).not.toBeVisible();
  await expect(page.getByRole('cell', { name: 'TrustTunnel', exact: true }).first()).toBeVisible();

  await page.getByRole('link', { name: 'Клиенты', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'sandbox-alice', exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Действия: sandbox-alice' }).click();
  await page.getByRole('menuitem', { name: 'Отключить доступ · TrustTunnel' }).click();
  let accessDialog = page.getByRole('dialog', { name: /Отключить доступ/ });
  await accessDialog.getByRole('button', { name: 'Изменить и применить' }).click();
  await expect(accessDialog.getByRole('alert').filter({ hasText: 'Нельзя отключить' })).toContainText('последнего активного клиента');
  await accessDialog.getByRole('button', { name: 'Отмена' }).click();
  await page.getByRole('button', { name: 'Добавить клиента' }).click();
  await page.getByLabel('Username', { exact: true }).fill('sandbox-bob');
  await page.getByLabel('Отображаемое имя').fill('Bob sandbox');
  await page.getByLabel('Входящее').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({ hasText: 'TrustTunnel' }).click();
  await page.getByRole('button', { name: 'Сгенерировать пароль' }).click();
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  const apply = page.getByRole('dialog', { name: 'Применить доступ TrustTunnel?' });
  await apply.getByRole('button', { name: 'Создать и применить' }).click();
  await expect(apply).not.toBeVisible();
  await expect(page.getByRole('row', { name: /sandbox-bob/ })).toContainText('Применён');

  await page.route('**/api/attachments/*', route => route.fulfill({ status: 409, json: { code: 'operation_in_progress' } }));
  await page.getByRole('button', { name: 'Действия: sandbox-bob' }).click();
  await page.getByRole('menuitem', { name: 'Отключить доступ · TrustTunnel' }).click();
  accessDialog = page.getByRole('dialog', { name: /Отключить доступ/ });
  await accessDialog.getByRole('button', { name: 'Изменить и применить' }).click();
  await expect(accessDialog.getByRole('alert').filter({ hasText: 'уже выполняется' })).toContainText('уже выполняется операция');
  await accessDialog.getByRole('button', { name: 'Отмена' }).click();
  await page.unroute('**/api/attachments/*');

  await page.getByRole('button', { name: 'Действия: sandbox-bob' }).click();
  await page.getByRole('menuitem', { name: 'QR / профиль' }).click();
  await expect(page.getByRole('heading', { name: 'Профили' })).toBeVisible();
  await page.getByRole('button', { name: /QR$/ }).click();
  const qr = page.getByRole('dialog', { name: 'TrustTunnel · тестовый профиль' });
  await expect(qr.getByText('Тестовый экспортёр')).toBeVisible();
  await qr.locator('.ant-modal-footer').getByRole('button', { name: 'Закрыть' }).click();
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.getByRole('button', { name: /Копировать ссылку$/ }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain('sandbox-profile:sandbox-bob@');
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: /Скачать TOML$/ }).click();
  expect((await downloadPromise).suggestedFilename()).toBe('sandbox-bob.toml');

  let response = await page.request.post('/api/dev/fake-scenario', { headers, data: { scenario: 'drift' } });
  expect(response.status()).toBe(204);
  await page.getByRole('link', { name: 'Входящие', exact: true }).click();
  await page.getByRole('button', { name: 'Действия: TrustTunnel' }).click();
  await page.getByRole('menuitem', { name: 'Просмотреть' }).click();
  const detail = page.getByRole('dialog', { name: 'TrustTunnel' });
  await detail.getByRole('button', { name: 'Проверить изменения' }).click();
  await expect(detail.getByText('Обнаружены внешние изменения')).toBeVisible();
  await detail.getByRole('button', { name: 'Импортировать изменения' }).click();
  await page.getByRole('dialog', { name: 'Импортировать внешнее состояние?' }).getByRole('button', { name: 'Подтвердить' }).click();
  await expect(detail.getByText('Обнаружены внешние изменения')).not.toBeVisible();
  await detail.locator('.ant-drawer-close').click();

  response = await page.request.post('/api/dev/fake-scenario', { headers, data: { scenario: 'health_failure_rollback_success' } });
  expect(response.status()).toBe(204);
  await page.getByRole('link', { name: 'Клиенты', exact: true }).click();
  await page.getByRole('button', { name: 'Добавить клиента' }).click();
  await page.getByLabel('Username', { exact: true }).fill('sandbox-rollback');
  await page.getByLabel('Отображаемое имя').fill('Rollback sandbox');
  await page.getByLabel('Входящее').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({ hasText: 'TrustTunnel' }).click();
  await page.getByRole('button', { name: 'Сгенерировать пароль' }).click();
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await page.getByRole('dialog', { name: 'Применить доступ TrustTunnel?' }).getByRole('button', { name: 'Создать и применить' }).click();
  const failedApply = page.getByRole('dialog', { name: 'Применить доступ TrustTunnel?' });
  await expect(failedApply.getByRole('alert').filter({ hasText: 'Предыдущая конфигурация восстановлена' })).toBeVisible();
  await failedApply.getByRole('button', { name: 'Вернуться к форме' }).click();
  await expect(failedApply).not.toBeVisible();
  await page.getByRole('dialog', { name: 'Новый клиент' }).locator('.ant-drawer-close').click();

  response = await page.request.post('/api/dev/fake-scenario', { headers, data: { scenario: 'rollback_failure' } });
  expect(response.status()).toBe(204);
  const clients = await page.request.get('/api/clients?limit=100');
  const rollbackClient = (await clients.json()).items.find((item: { username: string }) => item.username === 'sandbox-rollback');
  const inbound = (await (await page.request.get('/api/inbounds')).json()).items[0];
  const recoveryClient = await page.request.post('/api/clients', { headers, data: {
    username: 'sandbox-recovery', display_name: 'Recovery sandbox', enabled: true,
    comment: '', expires_at: null,
  } });
  response = await page.request.post(`/api/clients/${(await recoveryClient.json()).id}/attachments`, { headers, data: {
    inbound_id: inbound.id, password: 'recovery-fixture-password',
    max_http2_conns: null, max_http3_conns: null, idempotency_key: crypto.randomUUID(),
  } });
  expect(response.status()).toBe(201);
  expect((await response.json()).operation.state).toBe('needs_recovery');
  expect(rollbackClient.attachments[0].sync_state).not.toBe('active');
  await page.getByRole('link', { name: 'Входящие', exact: true }).click();
  await page.getByRole('button', { name: 'Действия: TrustTunnel' }).click();
  await page.getByRole('menuitem', { name: 'Просмотреть' }).click();
  await expect(detail.getByRole('alert').filter({ hasText: 'Требуется восстановление' })).toBeVisible();
  for (const width of [320, 375, 390, 768]) {
    await expectNoPageOverflow(page, width);
    await expect(detail).toBeVisible();
    await expect(detail.getByRole('alert').filter({ hasText: 'Требуется восстановление' })).toBeVisible();
    if (width === 320) {
      const tableScroll = await detail.locator('.ant-table-content').evaluateAll(elements =>
        elements.map(element => ({
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
          overflowX: getComputedStyle(element).overflowX,
        })),
      );
      expect(tableScroll).toHaveLength(2);
      expect(tableScroll.every(item => item.overflowX === 'auto' && item.scrollWidth > item.clientWidth)).toBe(true);
    }
  }
  await expectNoPageOverflow(page, 390);
  await page.screenshot({ path: 'test-results/phase2-sandbox.png', fullPage: true, animations: 'disabled' });
});
