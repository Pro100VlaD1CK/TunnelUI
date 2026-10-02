import { useState } from 'react';
import { Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../shared/api';
import type { Page } from '../../shared/types';
import { QueryState } from '../../shared/QueryState';

interface AuditRow { id: string; timestamp: number; admin: string; action: string; summary: string; result: string }
export function Audit() {
  const [page, setPage] = useState(1);
  const query = useQuery({ queryKey: ['audit', page], queryFn: () => api<Page<AuditRow>>(`/audit?offset=${(page - 1) * 25}`) });
  return <><div className="page-heading"><div><Typography.Title level={1}>Аудит</Typography.Title><Typography.Text type="secondary">Действия администраторов без секретных данных</Typography.Text></div></div>
    <QueryState loading={query.isPending} error={query.error} retry={() => query.refetch()} />
    {query.data && <Table<AuditRow> rowKey="id" size="small" dataSource={query.data.items} scroll={{ x: 720 }} pagination={{ current: page, total: query.data.total, pageSize: 25, showSizeChanger: false, onChange: setPage }} columns={[
      { title: 'Время', dataIndex: 'timestamp', width: 180, render: v => new Date(v * 1000).toLocaleString('ru-RU') },
      { title: 'Администратор', dataIndex: 'admin', width: 160 },
      { title: 'Действие', dataIndex: 'summary' },
      { title: 'Результат', dataIndex: 'result', width: 120, render: v => <Tag color={v === 'success' ? 'success' : 'error'}>{v === 'success' ? 'Успешно' : 'Ошибка'}</Tag> },
    ]} />}</>;
}
