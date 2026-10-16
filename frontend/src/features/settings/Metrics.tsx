import { useState } from 'react';
import { Alert, Button, Descriptions, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api, errorText } from '../../shared/api';
import { QueryState } from '../../shared/QueryState';
import { InboundPicker, useManagedInbounds } from '../config/useManagedInbound';

interface ClientMetric { username: string; ip: string | null; sessions: number;
  inbound: number; outbound: number; online: boolean }
interface MetricsView { state: 'available' | 'not_configured' | 'per_client_disabled' |
  'unsafe_listener' | 'unavailable' | 'unsupported'; observed_at?: number;
  counter_scope?: 'endpoint_process'; clients: ClientMetric[] }
const states: Record<MetricsView['state'], string> = {
  available: 'Метрики доступны', not_configured: 'Метрики не включены в vpn.toml',
  per_client_disabled: 'per_client_metrics отключён', unsafe_listener: 'Listener метрик не ограничен loopback: запрос заблокирован',
  unavailable: 'Endpoint метрик не ответил', unsupported: 'Версия endpoint не подтверждает /clients',
};
function bytes(value: number) { return new Intl.NumberFormat('ru-RU', { notation: 'compact', maximumFractionDigits: 1 }).format(value) + ' Б'; }

export function Metrics() {
  const inbounds = useManagedInbounds();
  const managed = inbounds.data?.items.filter(item => item.enabled && item.kind === 'trusttunnel') ?? [];
  const [selected, setSelected] = useState<string>();
  const inboundId = selected && managed.some(item => item.id === selected) ? selected : managed[0]?.id;
  const query = useQuery({ queryKey: ['metrics-clients', inboundId], queryFn: () =>
    api<MetricsView>(`/inbounds/${inboundId}/metrics/clients`), enabled: Boolean(inboundId), staleTime: 0 });
  return <div className="configuration-panel">
    <div className="toolbar"><InboundPicker items={managed} value={inboundId} change={setSelected} />
      <Button disabled={!inboundId} loading={query.isFetching} onClick={() => query.refetch()}>Обновить</Button></div>
    <QueryState loading={inbounds.isPending || (Boolean(inboundId) && query.isPending)} error={inbounds.error ?? query.error} retry={() => { inbounds.refetch(); query.refetch(); }} />
    {!inbounds.isPending && !managed.length && <Alert type="info" showIcon title="Нет управляемых входящих" />}
    {query.error && <Alert type="error" showIcon title={errorText(query.error)} />}
    {query.data && <>
      <Alert type={query.data.state === 'available' ? 'success' : query.data.state === 'unsafe_listener' ? 'warning' : 'info'} showIcon
        title={states[query.data.state]} description={query.data.state === 'available' ? 'Данные получены только по локальному адресу, без записи в базу.' : 'Этот раздел только читает /clients. Настройка listener пока выполняется вне панели.'} />
      {query.data.state === 'available' && <>
        <Descriptions size="small" column={1} items={[
          { key: 'scope', label: 'Период счётчиков', children: 'Текущий процесс TrustTunnel; после перезапуска значения могут сброситься' },
          { key: 'time', label: 'Получено', children: query.data.observed_at ? new Date(query.data.observed_at * 1000).toLocaleString('ru-RU') : '—' },
        ]} />
        <Table size="small" rowKey="username" dataSource={query.data.clients} pagination={{ pageSize: 20 }} scroll={{ x: 760 }} columns={[
          { title: 'Клиент', dataIndex: 'username', width: 160, ellipsis: true },
          { title: 'Состояние', width: 125, render: (_, row) => <Tag color={row.online ? 'success' : undefined}>{row.online ? 'Онлайн' : 'Офлайн'}</Tag> },
          { title: 'Сессии', dataIndex: 'sessions', width: 80 },
          { title: 'Входящий трафик', dataIndex: 'inbound', width: 130, render: bytes },
          { title: 'Исходящий трафик', dataIndex: 'outbound', width: 130, render: bytes },
          { title: 'Последний IP', dataIndex: 'ip', width: 150, render: value => <span className="technical-value">{value || '—'}</span> },
        ]} />
        <Typography.Paragraph type="secondary" className="table-note">Показания не являются накопленным трафиком за всё время.</Typography.Paragraph>
      </>}
    </>}
  </div>;
}
