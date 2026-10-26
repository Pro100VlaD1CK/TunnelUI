import { useEffect, useState } from 'react';
import { Alert, App, Button, Descriptions, Form, InputNumber, Space, Switch, Tag, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, errorText } from '../../shared/api';
import { QueryState } from '../../shared/QueryState';
import { InboundPicker, useManagedInbounds } from '../config/useManagedInbound';
import type { Operation } from '../../shared/types';

type Key = 'ipv6_available' | 'allow_private_network_connections' |
  'tls_handshake_timeout_secs' | 'client_listener_timeout_secs' |
  'connection_establishment_timeout_secs' | 'tcp_connections_timeout_secs' |
  'udp_connections_timeout_secs';
type Values = Record<Key, boolean | number>;
interface SettingsView { inbound_id: string; version: string; listen_address: string; protocols: string[];
  values: Values; configured: string[]; drift: boolean; restart_required: boolean }
interface Preview { fingerprint: string; changed_files: string[];
  changes: { field: string; before: boolean | number; after: boolean | number }[];
  restart_required: boolean }
const labels: Record<Key, string> = {
  ipv6_available: 'IPv6 доступен', allow_private_network_connections: 'Разрешить частные сети',
  tls_handshake_timeout_secs: 'TLS handshake, с', client_listener_timeout_secs: 'Ожидание клиента, с',
  connection_establishment_timeout_secs: 'Установка соединения, с',
  tcp_connections_timeout_secs: 'TCP соединение, с', udp_connections_timeout_secs: 'UDP соединение, с',
};
const numberKeys = (Object.keys(labels) as Key[]).filter(key => !['ipv6_available', 'allow_private_network_connections'].includes(key));
const limits: Record<string, [number, number]> = {
  tls_handshake_timeout_secs: [1, 300], client_listener_timeout_secs: [1, 86400],
  connection_establishment_timeout_secs: [1, 300], tcp_connections_timeout_secs: [1, 2592000],
  udp_connections_timeout_secs: [1, 3600],
};
const stateLabels: Record<string, string> = { pending: 'Ожидает', preparing: 'Проверка', backed_up: 'Backup создан',
  writing: 'Запись', applying: 'Перезапуск', checking: 'Проверка службы', succeeded: 'Применено',
  rolling_back: 'Откат', rolled_back: 'Откат выполнен', failed: 'Ошибка', needs_recovery: 'Требуется восстановление' };

export function TrustTunnelSettings() {
  const { modal, message } = App.useApp();
  const cache = useQueryClient();
  const inbounds = useManagedInbounds();
  const managed = inbounds.data?.items.filter(item => item.enabled && item.kind === 'trusttunnel') ?? [];
  const [selected, setSelected] = useState<string>();
  const inboundId = selected && managed.some(item => item.id === selected) ? selected : managed[0]?.id;
  const view = useQuery({ queryKey: ['configuration', inboundId], queryFn: () =>
    api<SettingsView>(`/inbounds/${inboundId}/settings`), enabled: Boolean(inboundId), staleTime: 0 });
  const [draft, setDraft] = useState<Values>();
  const [lastResult, setLastResult] = useState<Operation>();
  useEffect(() => { setDraft(view.data?.values); }, [view.data]);
  useEffect(() => { setLastResult(undefined); }, [inboundId]);
  const changes = Object.fromEntries((Object.keys(labels) as Key[])
    .filter(key => draft && view.data && draft[key] !== view.data.values[key])
    .map(key => [key, draft![key]]));
  const apply = useMutation({ mutationFn: ({ preview, values }: { preview: Preview; values: Record<string, boolean | number> }) =>
    api<{ operation: Operation }>(`/inbounds/${inboundId}/settings/apply`, {
      method: 'POST', body: JSON.stringify({ values, fingerprint: preview.fingerprint, idempotency_key: crypto.randomUUID() }),
    }), onSuccess: result => {
      setLastResult(result.operation);
      if (result.operation.state !== 'succeeded') {
        message.error(result.operation.state === 'needs_recovery' ? 'Требуется восстановление. Проверьте входящее и журнал операций.' : 'Применение не завершено. Проверьте состояние входящего.');
      } else message.success('Настройки применены; служба проверена');
    }, onSettled: () => { cache.invalidateQueries({ queryKey: ['configuration', inboundId] }); cache.invalidateQueries({ queryKey: ['inbounds'] }); },
  });
  const preview = useMutation({ mutationFn: () => api<Preview>(`/inbounds/${inboundId}/settings/preview`, {
    method: 'POST', body: JSON.stringify({ values: changes }),
  }), onSuccess: result => {
    const proposed = { ...changes };
    modal.confirm({ title: 'Применить настройки TrustTunnel?', okText: 'Создать backup и применить',
      cancelText: 'Отмена', content: <><p>Изменится vpn.toml. Служба будет перезапущена и проверена; при ошибке выполнится откат.</p>
        <ul>{result.changes.map(item => <li key={item.field}>{labels[item.field as Key]}: {String(item.before)} → {String(item.after)}</li>)}</ul></>,
      onOk: async () => { await apply.mutateAsync({ preview: result, values: proposed }); },
    });
  } });
  const status = managed.find(item => item.id === inboundId);
  const port = view.data?.listen_address.match(/:(\d+)$/)?.[1] ?? '—';
  return <div className="configuration-panel">
    <div className="toolbar"><InboundPicker items={managed} value={inboundId} change={setSelected} />
      <Button onClick={() => view.refetch()} disabled={!inboundId} loading={view.isFetching}>Обновить</Button></div>
    <QueryState loading={inbounds.isPending || (Boolean(inboundId) && view.isPending)} error={inbounds.error ?? view.error} retry={() => { inbounds.refetch(); view.refetch(); }} />
    {!inbounds.isPending && !managed.length && <Alert type="info" showIcon title="Нет управляемых входящих" description="Сначала импортируйте TrustTunnel в разделе «Входящие»." />}
    {status?.config_state === 'recovery_required' && <Alert type="error" showIcon title="Требуется восстановление" description="Редактирование заблокировано. Откройте входящее и восстановите операцию." />}
    {(view.data?.drift || status?.config_state === 'drift') && <Alert type="warning" showIcon title="Конфигурация изменена вне панели" description="Применение заблокировано. Проверьте изменения во входящем и выполните явный повторный импорт." />}
    {status?.config_state === 'pending' && <Alert type="warning" showIcon title="Есть ожидающие изменения клиентов" description="Сначала примените или отмените их в разделе «Входящие»." />}
    {(preview.error || apply.error) && <Alert type="error" showIcon title={errorText(preview.error ?? apply.error)} />}
    {lastResult && lastResult.state !== 'succeeded' && <Alert type="error" showIcon title={lastResult.state === 'needs_recovery' ? 'Требуется восстановление' : 'Настройки не применены'} description="Проверьте входящее и журнал операций перед следующей попыткой." />}
    {view.data && draft && <>
      <Descriptions size="small" bordered column={1} items={[
        { key: 'name', label: 'Экземпляр', children: status?.name ?? '—' },
        { key: 'version', label: 'Версия', children: view.data.version },
        { key: 'listen', label: 'Listen (только чтение)', children: <span className="technical-value">{view.data.listen_address}</span> },
        { key: 'port', label: 'Порт (только чтение)', children: port },
        { key: 'protocols', label: 'Протоколы (только чтение)', children: view.data.protocols.join(', ') || 'По умолчанию' },
        { key: 'service', label: 'Служба', children: status?.service_status === 'running' ? 'Работает' : status?.service_status === 'stopped' ? 'Остановлена' : 'Не проверено' },
        { key: 'agent', label: 'Среда управления', children: status?.execution_mode === 'linux' ? 'Linux Agent' : status?.execution_mode === 'sandbox' ? 'Локальный sandbox' : 'Недоступна' },
        { key: 'sync', label: 'Конфигурация', children: status?.config_state === 'synced' ? 'Синхронизирована' : status?.config_state ?? 'Неизвестно' },
        { key: 'operation', label: 'Последняя операция', children: status?.operation ? `${stateLabels[status.operation.state] ?? status.operation.state} · ${new Date(status.operation.updated_at * 1000).toLocaleString('ru-RU')}` : 'Нет операций' },
      ]} />
      <Typography.Paragraph type="secondary" className="table-note">Редактируются только проверенные поля TrustTunnel 1.1.0. Значения остальных полей и комментарии сохраняются.</Typography.Paragraph>
      <Form layout="vertical" className="configuration-form">
        {(['ipv6_available', 'allow_private_network_connections'] as Key[]).map(key => <Form.Item key={key} label={labels[key]}>
          <Switch checked={Boolean(draft[key])} onChange={value => setDraft({ ...draft, [key]: value })} aria-label={labels[key]} />
          {!view.data.configured.includes(key) && <Tag className="default-value-tag">Значение по умолчанию</Tag>}
        </Form.Item>)}
        <div className="configuration-fields">{numberKeys.map(key => <Form.Item key={key} label={labels[key]}>
          <InputNumber value={Number(draft[key])} min={limits[key][0]} max={limits[key][1]} step={1}
            onChange={value => { if (value !== null) setDraft({ ...draft, [key]: value }); }} aria-label={labels[key]} />
          {!view.data.configured.includes(key) && <Tag className="default-value-tag">По умолчанию</Tag>}
        </Form.Item>)}</div>
        <Space wrap><Button type="primary" disabled={!Object.keys(changes).length || view.data.drift || status?.config_state !== 'synced'}
          loading={preview.isPending || apply.isPending} onClick={() => preview.mutate()}>Просмотреть изменения</Button>
          <Button disabled={!Object.keys(changes).length} onClick={() => setDraft(view.data?.values)}>Сбросить форму</Button></Space>
      </Form>
    </>}
  </div>;
}
