import { Alert, Button, Empty, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../shared/api';
import { QueryState } from '../../shared/QueryState';

interface Inbound { id: string; name: string; kind: string; enabled: boolean }
export function Inbounds() {
  const query = useQuery({ queryKey: ['inbounds'], queryFn: () => api<{ items: Inbound[] }>('/inbounds') });
  return <>
    <div className="page-heading"><div><Typography.Title level={1}>Входящие</Typography.Title><Typography.Text type="secondary">Точки доступа и состояние управления</Typography.Text></div></div>
    <Alert type="info" showIcon title="Управление сервером ещё не подключено" description="Обнаружение и импорт TrustTunnel будут доступны после подключения безопасного системного агента. Существующая установка не изменяется." />
    <QueryState loading={query.isPending} error={query.error} retry={() => query.refetch()} />
    {query.data && <Table size="small" rowKey="id" dataSource={query.data.items} pagination={false} scroll={{ x: 600 }} columns={[
      { title: 'Название', dataIndex: 'name' }, { title: 'Тип', dataIndex: 'kind', render: v => <Tag>{v === 'trusttunnel' ? 'TrustTunnel' : 'External · 3x-ui'}</Tag> },
      { title: 'Состояние', render: () => <Tag>Не проверено</Tag> },
    ]} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<><strong>Нет импортированных входящих</strong><p>Сначала подключите агент, затем проверьте найденную конфигурацию.</p><Button disabled>Импортировать TrustTunnel — недоступно</Button></>} /> }} />}
  </>;
}
