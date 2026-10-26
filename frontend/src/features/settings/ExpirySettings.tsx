import { Alert, App, Button, Modal, Space, Table, Tag, Typography } from 'antd';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, errorText } from '../../shared/api';
import { QueryState } from '../../shared/QueryState';

interface Affected {
  client_id: string; username: string; inbound_id: string; inbound_name: string;
  expires_at: number; overdue: boolean; revoked: boolean;
}
interface Preview { enabled: boolean; fingerprint: string; affected: Affected[] }
interface Attempt {
  id: string; client_id: string; inbound_id: string; expires_at: number;
  state: string; attempts: number; next_attempt_at: number | null;
  operation_id: string | null; error_code: string | null;
}

const attemptLabels: Record<string, string> = {
  pending: 'Ожидает', running: 'Выполняется', succeeded: 'Отозван',
  error: 'Ошибка применения', blocked: 'Требует вмешательства',
  superseded: 'Срок изменён',
};

export function ExpirySettings() {
  const { message } = App.useApp();
  const cache = useQueryClient();
  const [confirm, setConfirm] = useState<boolean>();
  const preview = useQuery({ queryKey: ['expiry-preview'], queryFn: () => api<Preview>('/expiry/preview') });
  const attempts = useQuery({ queryKey: ['expiry-attempts'], queryFn: () => api<{ items: Attempt[] }>('/expiry/attempts') });
  const change = useMutation({
    mutationFn: (enabled: boolean) => api('/expiry/enabled', { method: 'PUT', body: JSON.stringify({
      enabled, fingerprint: preview.data?.fingerprint,
    }) }),
    onSuccess: () => {
      setConfirm(undefined); message.success('Настройка сроков сохранена');
      cache.invalidateQueries({ queryKey: ['expiry-preview'] });
    },
  });
  const retry = useMutation({
    mutationFn: (id: string) => api(`/expiry/attempts/${id}/retry`, { method: 'POST' }),
    onSuccess: () => {
      message.info('Повтор запланирован для следующего запуска таймера');
      cache.invalidateQueries({ queryKey: ['expiry-attempts'] });
    },
  });
  const overdue = preview.data?.affected.filter(item => item.overdue && !item.revoked) ?? [];
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <Alert type={preview.data?.enabled ? 'success' : 'warning'} showIcon
      title={preview.data?.enabled ? 'Автоматическая обработка сроков включена' : 'Автоматическая обработка сроков выключена'}
      description="Срок — политика TunnelUI, а не функция TrustTunnel v1.1.0. Отзыв требует отдельного запуска CLI по systemd timer, backup, restart и health-check. Настройка выключена после миграции; существующий доступ не отзывается автоматически." />
    <QueryState loading={preview.isPending} error={preview.error} retry={() => preview.refetch()} />
    {preview.data && <>
      <Typography.Text>Просроченных доступов, ожидающих проверки: {overdue.length}. В таблице показаны клиенты, которых затронет включение и последующие проходы.</Typography.Text>
      <Table<Affected> size="small" rowKey={item => `${item.client_id}-${item.inbound_id}`}
        scroll={{ x: 620 }} pagination={{ pageSize: 10 }} dataSource={preview.data.affected}
        columns={[
          { title: 'Username', dataIndex: 'username' },
          { title: 'Входящее', dataIndex: 'inbound_name' },
          { title: 'Срок', render: (_, item) => new Date(item.expires_at * 1000).toLocaleString('ru-RU') },
          { title: 'Состояние', render: (_, item) => <Tag color={item.revoked ? 'success' : item.overdue ? 'warning' : 'processing'}>{item.revoked ? 'Отозван' : item.overdue ? 'Просрочен · доступ может действовать' : 'Срок не наступил'}</Tag> },
        ]} />
      <Space wrap><Button onClick={() => setConfirm(!preview.data.enabled)}>{preview.data.enabled ? 'Выключить автоматическую обработку' : 'Включить после просмотра'}</Button><Button onClick={() => preview.refetch()} loading={preview.isFetching}>Обновить просмотр</Button></Space>
    </>}
    {change.error && <Alert type="error" showIcon title={errorText(change.error)} />}
    <Typography.Title level={3}>Последние попытки</Typography.Title>
    <QueryState loading={attempts.isPending} error={attempts.error} retry={() => attempts.refetch()} />
    {attempts.data && <Table<Attempt> size="small" rowKey="id" scroll={{ x: 850 }} pagination={{ pageSize: 10 }} dataSource={attempts.data.items} columns={[
      { title: 'Клиент', render: (_, item) => preview.data?.affected.find(value => value.client_id === item.client_id)?.username ?? item.client_id },
      { title: 'Входящее', render: (_, item) => preview.data?.affected.find(value => value.inbound_id === item.inbound_id)?.inbound_name ?? item.inbound_id },
      { title: 'Результат', width: 270, render: (_, item) => <><Tag color={item.state === 'succeeded' ? 'success' : item.state === 'running' || item.state === 'pending' ? 'processing' : item.state === 'superseded' ? undefined : 'error'}>{attemptLabels[item.state] ?? 'Неизвестное состояние'}</Tag>{item.error_code && <><br /><Typography.Text type="secondary">{errorText(new ApiError(item.error_code, 409))}</Typography.Text></>}</> },
      { title: 'Следующий проход', width: 160, render: (_, item) => item.next_attempt_at ? new Date(item.next_attempt_at * 1000).toLocaleString('ru-RU') : '—' },
      { title: 'Действие', render: (_, item) => item.state === 'error' || item.state === 'blocked' ? <Button size="small" loading={retry.isPending} onClick={() => retry.mutate(item.id)}>Повторить при следующем запуске</Button> : '—' },
    ]} />}
    {retry.error && <Alert type="error" showIcon title={errorText(retry.error)} />}
    <Modal open={confirm !== undefined} title={confirm ? 'Включить автоматический отзыв?' : 'Выключить автоматический отзыв?'}
      okText={confirm ? 'Включить' : 'Выключить'} cancelText="Отмена" confirmLoading={change.isPending}
      onCancel={() => setConfirm(undefined)} onOk={() => confirm !== undefined && change.mutate(confirm)}>
      <p>{confirm ? `При следующем запуске таймера будут проверены ${overdue.length} просроченных привязок. Последний активный credential нельзя удалить: такая попытка будет заблокирована и показана здесь.` : 'Автоматический проход остановится. Уже отозванный доступ сам не восстановится.'}</p>
    </Modal>
  </Space>;
}
