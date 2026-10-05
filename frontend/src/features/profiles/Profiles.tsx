import { CopyOutlined, DownloadOutlined, QrcodeOutlined } from '@ant-design/icons';
import { Alert, App, Button, Empty, Modal, QRCode, Select, Space, Table, Tag, Typography } from 'antd';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, errorText } from '../../shared/api';
import { QueryState } from '../../shared/QueryState';
import type { Attachment, Client, Page } from '../../shared/types';

interface Exported { format: 'deeplink' | 'toml'; content: string; media_type: string; sandbox: boolean }
interface ExportRequest { format: 'deeplink' | 'toml'; attachment: Attachment }

export function Profiles() {
  const [params] = useSearchParams();
  const [selected, setSelected] = useState<string | undefined>(params.get('client') ?? undefined);
  const [profile, setProfile] = useState<Exported>();
  const { message } = App.useApp();
  const clients = useQuery({ queryKey: ['clients', 'profiles'], queryFn: () => api<Page<Client>>('/clients?limit=100') });
  useEffect(() => { if (!selected && clients.data?.items.length) setSelected(clients.data.items[0].id); }, [clients.data, selected]);
  const chosen = clients.data?.items.find(item => item.id === selected);
  const active = chosen?.attachments.filter(item => item.applied_state === 'active') ?? [];
  const exporter = useMutation({ mutationFn: ({ format, attachment }: ExportRequest) => api<Exported>(`/clients/${selected}/profiles/trusttunnel`, { method: 'POST', body: JSON.stringify({ format, inbound_id: attachment.inbound_id }) }), onSuccess: async data => {
    if (data.format === 'toml') {
      const url = URL.createObjectURL(new Blob([data.content], { type: data.media_type }));
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${chosen?.username ?? 'client'}.toml`; anchor.click(); URL.revokeObjectURL(url);
      message.success('TOML подготовлен тестовым экспортёром');
    } else setProfile(data);
  } });
  const copy = async (attachment: Attachment) => {
    let data: Exported | undefined;
    try {
      data = await exporter.mutateAsync({ format: 'deeplink', attachment });
      await navigator.clipboard.writeText(data.content);
      setProfile(undefined); message.success('Тестовая ссылка скопирована');
    } catch {
      if (data) {
        setProfile(data);
        message.error('Не удалось записать ссылку в буфер. Скопируйте значение из открытого профиля.');
      }
    }
  };
  return <>
    <div className="page-heading"><div><Typography.Title level={1}>Профили</Typography.Title><Typography.Text type="secondary">Секретные данные создаются только по явному действию и не попадают в списки</Typography.Text></div></div>
    <div className="toolbar"><Select aria-label="Клиент профиля" loading={clients.isPending} value={selected} onChange={setSelected} placeholder="Выберите клиента" showSearch optionFilterProp="label" options={clients.data?.items.map(item => ({ value: item.id, label: `${item.display_name} · ${item.username}` }))} /></div>
    <QueryState loading={clients.isPending} error={clients.error} retry={() => clients.refetch()} />
    {exporter.error && <Alert type="error" showIcon title={errorText(exporter.error)} />}
    {chosen && <Table size="small" rowKey="id" pagination={false} dataSource={active} columns={[
      { title: 'Профиль', render: () => <><strong>TrustTunnel</strong><br /><Typography.Text type="secondary">Мобильный профиль · интерфейс официального CLI</Typography.Text></> },
      { title: 'Источник', dataIndex: 'inbound_name' },
      { title: 'Статус', render: () => <Tag color="success">Активен</Tag> },
      { title: 'Действия', width: 390, render: (_, attachment) => <Space wrap><Button icon={<QrcodeOutlined />} loading={exporter.isPending} onClick={() => exporter.mutate({ format: 'deeplink', attachment })}>QR</Button><Button icon={<CopyOutlined />} loading={exporter.isPending} onClick={() => copy(attachment)}>Копировать ссылку</Button><Button icon={<DownloadOutlined />} loading={exporter.isPending} onClick={() => exporter.mutate({ format: 'toml', attachment })}>Скачать TOML</Button></Space> },
    ]} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="У клиента нет активного применённого профиля TrustTunnel" /> }} />}
    <Modal open={Boolean(profile)} title="TrustTunnel · тестовый профиль" footer={<Button onClick={() => setProfile(undefined)}>Закрыть</Button>} onCancel={() => setProfile(undefined)} destroyOnHidden>
      {profile && <div className="qr-panel"><Alert type="info" showIcon title="Тестовый экспортёр" description="Это маркер локальной среды, а не реальный tt://. Production-адаптер будет вызывать официальный TrustTunnel CLI." /><QRCode value={profile.content} size={240} /><Typography.Text type="secondary">QR содержит секретный результат и очищается при закрытии окна.</Typography.Text></div>}
    </Modal>
  </>;
}
