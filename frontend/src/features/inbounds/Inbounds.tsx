import { MoreOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { Button, Dropdown, Empty, Space, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../shared/api';
import { QueryState } from '../../shared/QueryState';
import type { Inbound } from '../../shared/types';
import { AdoptionFlow } from './AdoptionFlow';
import { InboundDetails } from './InboundDetails';

const configLabels: Record<Inbound['config_state'], { text: string; color?: string }> = {
  synced: { text: 'Синхронизирован', color: 'success' }, drift: { text: 'Внешние изменения', color: 'warning' },
  pending: { text: 'Применяется', color: 'processing' }, error: { text: 'Ошибка', color: 'error' },
  recovery_required: { text: 'Требуется восстановление', color: 'error' }, unmanaged: { text: 'Не управляется' },
};

export function Inbounds() {
  const [adoption, setAdoption] = useState(false);
  const [detail, setDetail] = useState<string>();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const query = useQuery({ queryKey: ['inbounds'], queryFn: () => api<{ items: Inbound[]; adoption_available: boolean }>('/inbounds'), staleTime: 0, refetchOnMount: 'always' });
  useEffect(() => {
    const inboundId = params.get('inbound');
    if (inboundId && query.data?.items.some(item => item.id === inboundId)) {
      setDetail(inboundId);
      setParams({}, { replace: true });
    }
  }, [params, query.data, setParams]);
  return <>
    <div className="page-heading"><div><Typography.Title level={1}>Входящие</Typography.Title><Typography.Text type="secondary">Точки доступа, конфигурация и операции применения</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} disabled={!query.data?.adoption_available} onClick={() => setAdoption(true)}>{query.data && !query.data.adoption_available ? 'TrustTunnel импортирован' : 'Найти TrustTunnel'}</Button></div>
    <div className="list-controls"><Button icon={<ReloadOutlined />} onClick={() => query.refetch()} loading={query.isFetching}>Обновить состояние</Button></div>
    <QueryState loading={query.isPending} error={query.error} retry={() => query.refetch()} />
    {query.data && <Table<Inbound> size="small" rowKey="id" dataSource={query.data.items} pagination={false} scroll={{ x: 1060 }} columns={[
      { title: 'Статус', width: 115, render: (_, row) => <Tag color={!row.enabled ? undefined : row.service_status === 'running' ? 'success' : row.service_status === 'stopped' ? 'error' : 'warning'}>{!row.enabled ? 'Read-only' : row.service_status === 'running' ? 'Работает' : row.service_status === 'stopped' ? 'Остановлен' : 'Не проверено'}</Tag> },
      { title: 'Название', dataIndex: 'name', width: 180, ellipsis: true },
      { title: 'Тип', width: 130, render: () => <Tag>TrustTunnel</Tag> },
      { title: 'Версия', dataIndex: 'version', width: 90, render: value => value ?? '—' },
      { title: 'Listen', dataIndex: 'listen_address', width: 150, render: value => <span className="technical-value">{value ?? '—'}</span> },
      { title: 'Public address', dataIndex: 'public_address', width: 205, ellipsis: true },
      { title: 'Протоколы', width: 175, render: (_, row) => <Space size={4}>{['http1', 'http2', 'quic'].map(value => <Tag key={value} color={row.protocols.includes(value) ? 'blue' : undefined}>{value === 'quic' ? 'HTTP/3' : value.toUpperCase().replace('HTTP', 'HTTP/')}</Tag>)}</Space> },
      { title: 'Клиенты', dataIndex: 'client_count', width: 90 },
      { title: 'Конфигурация', width: 190, render: (_, row) => { const item = configLabels[row.config_state]; return <Tag color={item.color}>{item.text}</Tag>; } },
      { title: '', width: 56, fixed: 'right', render: (_, row) => <Dropdown trigger={['click']} menu={{ items: [
        { key: 'view', label: 'Просмотреть', onClick: () => setDetail(row.id) },
        { key: 'clients', label: 'Открыть клиентов', onClick: () => navigate('/clients') },
        { key: 'review', label: 'Открыть проверку изменений', onClick: () => setDetail(row.id) },
      ] }}><Button type="text" aria-label={`Действия: ${row.name}`} icon={<MoreOutlined />} /></Dropdown> },
    ]} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<><strong>Нет импортированных входящих</strong><p>Найдите локальную sandbox-конфигурацию, проверьте preview и подтвердите импорт.</p><Button type="primary" onClick={() => setAdoption(true)}>Найти TrustTunnel</Button></>} /> }} />}
    <AdoptionFlow open={adoption} close={() => setAdoption(false)} done={() => { setAdoption(false); query.refetch(); }} />
    <InboundDetails inboundId={detail} close={() => setDetail(undefined)} changed={() => query.refetch()} />
  </>;
}
