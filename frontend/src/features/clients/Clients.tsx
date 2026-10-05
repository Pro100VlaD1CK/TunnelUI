import { useEffect, useState } from 'react';
import { Button, Dropdown, Empty, Input, Select, Space, Table, Tag, Typography } from 'antd';
import { MoreOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../../shared/api';
import type { Client, Page } from '../../shared/types';
import { QueryState } from '../../shared/QueryState';
import { ClientEditor } from './ClientEditor';
import { ClientActionModal } from './ClientActionModal';
import { AttachmentActionModal } from './AttachmentActionModal';

const syncLabels: Record<Client['attachments'][number]['sync_state'], string> = {
  active: 'Применён', pending: 'Ожидает применения', conflict: 'Конфликт',
  error: 'Ошибка применения', disabled: 'Отключён',
};

export function Clients() {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<string>('all');
  const [page, setPage] = useState(1);
  const [editor, setEditor] = useState<{ open: boolean; client?: Client }>({ open: false });
  const [action, setAction] = useState<{ client: Client; remove: boolean }>();
  const [attachmentAction, setAttachmentAction] = useState<{ client: Client; attachment: Client['attachments'][number]; detach: boolean }>();
  const navigate = useNavigate();
  const query = useQuery({ queryKey: ['clients', search, filter, page], queryFn: () => api<Page<Client>>(`/clients?q=${encodeURIComponent(search)}&offset=${(page - 1) * 25}${filter !== 'all' ? `&enabled=${filter}` : ''}`) });
  useEffect(() => {
    if (query.data) setPage(current => Math.min(current, Math.max(1, Math.ceil(query.data.total / 25))));
  }, [query.data]);
  function confirm(client: Client, remove: boolean) {
    setAction({ client, remove });
  }
  return <>
    <div className="page-heading"><div><Typography.Title level={1}>Клиенты</Typography.Title><Typography.Text type="secondary">Глобальные записи пользователей · {query.data?.total ?? '—'}</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => setEditor({ open: true })}>Добавить клиента</Button></div>
    <div className="toolbar"><Input.Search aria-label="Поиск клиентов" placeholder="Username или имя" prefix={<SearchOutlined />} allowClear onSearch={v => { setSearch(v); setPage(1); }} /><Select aria-label="Состояние записи" value={filter} onChange={v => { setFilter(v); setPage(1); }} options={[{ value: 'all', label: 'Все записи' }, { value: 'true', label: 'Включённые' }, { value: 'false', label: 'Отключённые' }]} /></div>
    <Button className="refresh-list" icon={<ReloadOutlined />} onClick={() => query.refetch()} loading={query.isFetching}>Обновить список</Button>
    <Typography.Paragraph type="secondary">Глобальное состояние клиента и фактически применённое состояние каждой привязки показаны отдельно. Метрики появятся после Phase 3.</Typography.Paragraph>
    <QueryState loading={query.isPending} error={query.error} retry={() => query.refetch()} />
    {query.data && <Table<Client> rowKey="id" size="small" scroll={{ x: 820 }} dataSource={query.data.items} pagination={{ current: page, total: query.data.total, pageSize: 25, showSizeChanger: false, onChange: setPage, showTotal: total => `Всего: ${total}` }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search || filter !== 'all' ? 'По заданным условиям клиентов нет' : 'Добавьте первого клиента. Привязать VPN-доступ можно будет после импорта входящего.'} /> }} columns={[
      { title: 'Статус', width: 120, render: (_, r) => <Tag color={!r.enabled ? undefined : r.expires_at && r.expires_at <= Date.now() / 1000 ? 'warning' : 'success'}>{!r.enabled ? 'Отключён' : r.expires_at && r.expires_at <= Date.now() / 1000 ? 'Истёк' : 'Включён'}</Tag> },
      { title: 'Username', dataIndex: 'username', width: 160, ellipsis: true },
      { title: 'Имя', dataIndex: 'display_name', width: 180, ellipsis: true },
      { title: 'Входящие', width: 230, render: (_, row) => row.attachments.length ? <Space size={[4, 4]} wrap>{row.attachments.map(item => <Tag key={item.id} color={item.sync_state === 'active' ? 'success' : item.sync_state === 'pending' ? 'processing' : item.sync_state === 'error' || item.sync_state === 'conflict' ? 'error' : undefined}>{item.inbound_name} · {syncLabels[item.sync_state]}</Tag>)}</Space> : <Typography.Text type="secondary">Нет доступа</Typography.Text> },
      { title: 'Истекает', width: 160, render: (_, r) => r.expires_at ? new Date(r.expires_at * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : 'Без срока' },
      { title: '', width: 56, fixed: 'right', render: (_, r) => <Dropdown trigger={['click']} menu={{ items: [
        { key: 'edit', label: 'Редактировать', onClick: () => setEditor({ open: true, client: r }) },
        { key: 'toggle', label: r.enabled ? 'Отключить глобальную запись' : 'Включить глобальную запись', onClick: () => confirm(r, false) },
        { type: 'divider' }, { key: 'profile', label: 'QR / профиль', disabled: !r.attachments.some(item => item.applied_state === 'active'), onClick: () => navigate(`/profiles?client=${r.id}`) },
        ...r.attachments.flatMap(item => [{ key: `toggle-${item.id}`, label: `${item.enabled ? 'Отключить' : 'Включить'} доступ · ${item.inbound_name}`, onClick: () => setAttachmentAction({ client: r, attachment: item, detach: false }) }, { key: `detach-${item.id}`, danger: true, label: `Отвязать · ${item.inbound_name}`, onClick: () => setAttachmentAction({ client: r, attachment: item, detach: true }) }]),
        { type: 'divider' }, { key: 'delete', danger: true, label: 'Удалить', onClick: () => confirm(r, true) },
      ] }}><Button type="text" aria-label={`Действия: ${r.username}`} icon={<MoreOutlined />} /></Dropdown> },
    ]} />}
    <Space className="table-note"><Typography.Text type="secondary">Online, сессии, трафик и IP появятся после подключения метрик. Трафик — с запуска endpoint.</Typography.Text></Space>
    <ClientEditor open={editor.open} client={editor.client} close={() => setEditor({ open: false })} />
    {action && <ClientActionModal {...action} close={() => setAction(undefined)} />}
    {attachmentAction && <AttachmentActionModal {...attachmentAction} close={() => setAttachmentAction(undefined)} />}
  </>;
}
