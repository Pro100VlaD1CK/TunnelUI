import { Alert, App, Button, Descriptions, Drawer, Space, Table, Tag, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, errorText } from '../../shared/api';
import { QueryState } from '../../shared/QueryState';
import type { Operation } from '../../shared/types';

interface Detail {
  id: string; name: string; enabled: boolean; public_address: string; config_state: string;
  service_status: 'running' | 'stopped' | 'unknown';
  execution_mode: 'sandbox' | 'linux' | 'unavailable';
  metadata: Record<string, unknown>;
  attachments: { id: string; username: string; display_name: string; sync_state: string; desired_state: string; applied_state: string }[];
  operations: Operation[];
}

interface PendingPreview {
  fingerprint: string;
  changed_files: string[];
  affected_inbounds: { client_id: string; inbound_id: string; inbound_name: string; applied_global_enabled: boolean | null }[];
  changes: {
    attachment_id: string; username: string; desired_state: string; applied_state: string;
    desired_limits: [number | null, number | null];
    applied_limits: [number | null, number | null];
    desired_global_enabled: boolean; applied_global_enabled: boolean | null;
  }[];
}

const operationLabels: Record<string, string> = {
  pending: 'Ожидает', preparing: 'Проверка', backed_up: 'Backup создан', writing: 'Запись',
  applying: 'Restart', checking: 'Health check', succeeded: 'Применено', rolling_back: 'Откат',
  rolled_back: 'Откат выполнен', failed: 'Ошибка', needs_recovery: 'Требуется восстановление',
};
const accessLabels: Record<string, string> = { active: 'Активен', disabled: 'Отключён', detached: 'Отвязан', pending: 'Ожидает' };
const syncLabels: Record<string, string> = { active: 'Применён', pending: 'Ожидает применения', conflict: 'Конфликт', error: 'Ошибка', disabled: 'Отключён' };
const operationKinds: Record<string, string> = { attachment_create: 'Добавление доступа', attachment_update: 'Изменение доступа', attachment_detach: 'Отвязка клиента', client_access: 'Глобальный доступ клиента', restart: 'Перезапуск' };
const errorLabels: Record<string, string> = { health_failed: 'Проверка здоровья не пройдена', restart_failed: 'Перезапуск не выполнен', rollback_failed: 'Откат не завершён', drift_conflict: 'Конфигурация изменена извне' };
function pendingSummary(item: PendingPreview['changes'][number]) {
  const parts = [];
  if (item.desired_state !== item.applied_state) {
    parts.push(`доступ ${accessLabels[item.applied_state] ?? item.applied_state} → ${accessLabels[item.desired_state] ?? item.desired_state}`);
  }
  if (item.desired_limits.some((value, index) => value !== item.applied_limits[index])) {
    parts.push('ограничения подключений');
  }
  if (item.applied_global_enabled !== null && item.desired_global_enabled !== item.applied_global_enabled) {
    parts.push('глобальное состояние');
  }
  return parts.join(', ');
}

export function InboundDetails({ inboundId, close, changed }: { inboundId?: string; close: () => void; changed: () => void }) {
  const cache = useQueryClient();
  const { modal, message } = App.useApp();
  const query = useQuery({ queryKey: ['inbound', inboundId], queryFn: () => api<Detail>(`/inbounds/${inboundId}`), enabled: Boolean(inboundId), staleTime: 0, refetchOnMount: 'always' });
  const pending = useQuery({ queryKey: ['inbound-pending', inboundId], queryFn: () => api<PendingPreview>(`/inbounds/${inboundId}/pending`), enabled: Boolean(inboundId), staleTime: 0, refetchOnMount: 'always' });
  const action = useMutation({ mutationFn: async ({ path, method = 'POST', body }: { path: string; method?: string; body?: unknown }) => {
    const result = await api<{ operation?: Operation }>(path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
    if (result?.operation && result.operation.state !== 'succeeded') {
      throw new ApiError(result.operation.state === 'needs_recovery' ? 'recovery_required' : result.operation.error_code ?? 'operation_failed', 409);
    }
    return result;
  }, onSuccess: () => { changed(); message.success('Состояние обновлено'); }, onSettled: () => {
    cache.invalidateQueries({ queryKey: ['inbound', inboundId] });
    cache.invalidateQueries({ queryKey: ['inbound-pending', inboundId] });
    cache.invalidateQueries({ queryKey: ['inbounds'] });
    cache.invalidateQueries({ queryKey: ['clients'] });
  } });
  const confirm = (title: string, description: string, path: string, body?: unknown, method = 'POST') => modal.confirm({ title, content: description, okText: 'Подтвердить', cancelText: 'Отмена', okButtonProps: { danger: title.includes('Удалить') }, onOk: async () => { await action.mutateAsync({ path, method, body }); } });
  const confirmCancel = async () => {
    const current = await pending.refetch();
    if (!current.data || !current.data.changes.length) return;
    const preview = current.data;
    modal.confirm({
      title: 'Отменить ожидающие изменения?',
      content: <div><p>Будут отменены перечисленные изменения. Файлы и служба не изменятся.</p>
        <ul>{preview.changes.map(item => <li key={item.attachment_id}>{item.username}: {pendingSummary(item)}</li>)}</ul>
        {preview.affected_inbounds.some(item => item.inbound_id !== inboundId) && <p>Глобальный статус клиента изменится во всех его входящих. После отмены проверьте и примените новое ожидающее состояние: {preview.affected_inbounds.filter(item => item.inbound_id !== inboundId).map(item => item.inbound_name).join(', ')}.</p>}
        <p>Изменённые файлы: {preview.changed_files.join(', ') || 'не обнаружены'}.</p>
      </div>,
      okText: 'Отменить перечисленные', cancelText: 'Вернуться',
      onOk: async () => action.mutateAsync({
        path: `/inbounds/${inboundId}/cancel-pending`,
        body: { fingerprint: preview.fingerprint },
      }),
    });
  };
  const recoverable = query.data?.operations.find(item => item.state === 'needs_recovery');
  return <Drawer title={query.data?.name ?? 'Входящее'} open={Boolean(inboundId)} onClose={close} size={760} rootClassName="inbound-details-drawer" destroyOnHidden>
    <QueryState loading={query.isPending} error={query.error} retry={() => query.refetch()} />
    {pending.error && <Alert type="error" showIcon title={errorText(pending.error)} action={<Button size="small" onClick={() => pending.refetch()}>Повторить</Button>} />}
    {action.error && <Alert type="error" showIcon title={errorText(action.error)} />}
    {query.data && <>
      {(query.data.config_state === 'drift' || Boolean(pending.data?.changes.length)) && <Alert type="warning" showIcon
        title={query.data.config_state === 'drift' ? 'Обнаружены внешние изменения' : 'Есть ожидающие изменения'}
        description={<div>
          <p>Изменённые файлы: {pending.data?.changed_files.join(', ') || 'не обнаружены'}.{query.data.config_state === 'recovery_required' ? ' Сначала восстановите службу; действия с ожидающими изменениями пока заблокированы.' : query.data.config_state === 'drift' ? ' Применение поверх неизвестного состояния заблокировано.' : ' Ожидающее изменение можно применить после проверки или явно отменить.'}</p>
          {!!pending.data?.changes.length && <p>Ожидают применения: {pending.data.changes.map(item => item.username).join(', ')}. Перед импортом можно явно отменить перечисленные изменения.</p>}
          <Space wrap>
          {query.data.config_state === 'drift' && <Button size="small" disabled={pending.isPending || Boolean(pending.data?.changes.length)} onClick={() => confirm('Импортировать внешнее состояние?', 'Только при точном совпадении username. Пароли обновятся в зашифрованном хранилище, metadata — из файлов. Файлы и служба не изменятся.', `/inbounds/${inboundId}/reimport`)}>Импортировать изменения</Button>}
          {!!pending.data?.changes.length && query.data.config_state !== 'drift' && <Button size="small" disabled={query.data.config_state === 'recovery_required'} onClick={() => confirm('Применить ожидающие изменения?', 'TunnelUI проверит файлы, создаст backup, применит credentials, перезапустит службу и проверит результат.', `/inbounds/${inboundId}/apply-pending`, { idempotency_key: crypto.randomUUID() })}>Применить ожидающие</Button>}
          {!!pending.data?.changes.length && <Button size="small" disabled={query.data.config_state === 'recovery_required'} onClick={confirmCancel}>Просмотреть и отменить ожидающие</Button>}
          </Space>
        </div>} />}
      {query.data.config_state === 'recovery_required' && <Alert type="error" showIcon title="Требуется восстановление" description={<div><p>Откат не подтвердил здоровое состояние сервиса. Новые применения заблокированы.</p>{recoverable && <Button danger size="small" onClick={() => confirm('Повторить безопасное восстановление?', 'Будет восстановлена резервная копия этой операции, затем выполнены перезапуск и проверка здоровья.', `/operations/${recoverable.id}/recover`)}>Восстановить</Button>}</div>} />}
      <Descriptions bordered size="small" column={1} items={[
        { key: 'status', label: 'Управление', children: <Tag color={query.data.enabled ? 'success' : undefined}>{query.data.enabled ? 'Под управлением' : 'Только чтение'}</Tag> },
        { key: 'service', label: 'Сервис', children: <Tag color={query.data.service_status === 'running' ? 'success' : query.data.service_status === 'stopped' ? 'error' : 'warning'}>{query.data.service_status === 'running' ? 'Работает' : query.data.service_status === 'stopped' ? 'Остановлен' : 'Не проверено'}</Tag> },
        { key: 'version', label: 'Версия', children: String(query.data.metadata.version ?? '—') },
        { key: 'listen', label: 'Listen', children: String(query.data.metadata.listen_address ?? '—') },
        { key: 'public', label: 'Public address', children: query.data.public_address },
        { key: 'paths', label: 'Working directory', children: <span className="technical-value">{String(query.data.metadata.working_directory ?? '—')}</span> },
      ]} />
      <div className="section-heading"><Typography.Title level={2}>Действия</Typography.Title><Space wrap><Button onClick={() => action.mutate({ path: `/inbounds/${inboundId}/drift` })} loading={action.isPending}>Проверить изменения</Button><Button disabled={!query.data.enabled || query.data.execution_mode === 'unavailable'} onClick={() => confirm(query.data.execution_mode === 'sandbox' ? 'Перезапустить тестовый сервис?' : 'Перезапустить службу TrustTunnel?', query.data.execution_mode === 'sandbox' ? 'Будут выполнены тестовые перезапуск и проверка здоровья. Файлы не меняются.' : 'Агент перезапустит выбранную службу TrustTunnel через systemd и проверит её состояние. Файлы не меняются.', `/inbounds/${inboundId}/restart`, { idempotency_key: crypto.randomUUID() })}>{query.data.execution_mode === 'sandbox' ? 'Перезапустить (тест)' : 'Перезапустить службу'}</Button><Button danger onClick={() => confirm('Удалить из управления?', 'Запись станет доступна только для чтения. Файлы TrustTunnel не удаляются и не изменяются.', `/inbounds/${inboundId}/management`, undefined, 'DELETE')}>Удалить из управления</Button></Space></div>
      <div className="section-heading"><Typography.Title level={2}>Клиенты</Typography.Title></div>
      <Table size="small" rowKey="id" pagination={false} scroll={{ x: 700 }} dataSource={query.data.attachments} columns={[
        { title: 'Username', dataIndex: 'username', width: 150 }, { title: 'Имя', dataIndex: 'display_name', width: 170 },
        { title: 'Желаемое', dataIndex: 'desired_state', width: 115, render: value => accessLabels[value] ?? value }, { title: 'Применённое', dataIndex: 'applied_state', width: 125, render: value => accessLabels[value] ?? value },
        { title: 'Синхронизация', dataIndex: 'sync_state', width: 140, render: value => <Tag>{syncLabels[value] ?? value}</Tag> },
      ]} />
      <div className="section-heading"><Typography.Title level={2}>Последние операции</Typography.Title></div>
      <Table<Operation> size="small" rowKey="id" pagination={false} scroll={{ x: 720 }} dataSource={query.data.operations} columns={[
        { title: 'Время', width: 160, render: (_, row) => new Date(row.created_at * 1000).toLocaleString('ru-RU') },
        { title: 'Операция', dataIndex: 'kind', width: 180, render: value => operationKinds[value] ?? value },
        { title: 'Состояние', width: 175, render: (_, row) => <Tag color={row.state === 'succeeded' ? 'success' : row.state === 'needs_recovery' || row.state === 'failed' ? 'error' : row.state === 'rolled_back' ? 'warning' : 'processing'}>{operationLabels[row.state] ?? row.state}</Tag> },
        { title: 'Результат', dataIndex: 'error_code', width: 205, render: value => value ? (errorLabels[value] ?? 'Операция не завершена') : '—' },
      ]} />
    </>}
  </Drawer>;
}
