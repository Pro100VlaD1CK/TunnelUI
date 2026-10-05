import { Alert, Button, Descriptions, Modal, Space, Steps, Tag, Typography } from 'antd';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api, errorText } from '../../shared/api';
import { QueryState } from '../../shared/QueryState';

interface Discovery { found: boolean; service_name: string; version: string; working_directory: string; listen_address: string; service_status: string }
interface Preview extends Discovery {
  preview_id: string; binary_path: string; vpn_config_path: string; hosts_config_path: string;
  credentials_path: string; rules_path: string | null; public_address: string;
  protocols: string[]; tls_hosts: string[]; client_count: number;
  capabilities: Record<string, boolean>; warnings: string[];
}

export function AdoptionFlow({ open, close, done }: { open: boolean; close: () => void; done: () => void }) {
  const [preview, setPreview] = useState<Preview>();
  const discovery = useQuery({ queryKey: ['trusttunnel-discovery'], queryFn: () => api<Discovery>('/trusttunnel/discovery'), enabled: open, retry: false });
  const previewMutation = useMutation({ mutationFn: () => api<Preview>('/trusttunnel/adoption/preview', { method: 'POST' }), onSuccess: setPreview });
  const confirm = useMutation({ mutationFn: () => api('/trusttunnel/adoption/confirm', { method: 'POST', body: JSON.stringify({ preview_id: preview!.preview_id }) }), onSuccess: done });
  useEffect(() => { if (!open) { setPreview(undefined); previewMutation.reset(); confirm.reset(); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  return <Modal width={760} open={open} title="Импорт существующего TrustTunnel" onCancel={close} footer={null} destroyOnHidden>
    <Steps size="small" current={preview ? 1 : 0} items={[{ title: 'Обнаружение' }, { title: 'Проверка и импорт' }]} />
    <div className="flow-content">
      <QueryState loading={discovery.isPending} error={discovery.error} retry={() => discovery.refetch()} />
      {discovery.data && !preview && <>
        <Alert type="info" showIcon title="Найден существующий TrustTunnel" description="Импорт создаст metadata и зашифрованные привязки. Исходные файлы и service unit не изменятся." />
        <Descriptions bordered size="small" column={1} items={[
          { key: 'service', label: 'Сервис', children: discovery.data.service_name },
          { key: 'status', label: 'Состояние', children: <Tag color={discovery.data.service_status === 'running' ? 'success' : 'warning'}>{discovery.data.service_status}</Tag> },
          { key: 'version', label: 'Версия', children: discovery.data.version },
          { key: 'workdir', label: 'Рабочий каталог', children: <span className="technical-value">{discovery.data.working_directory}</span> },
          { key: 'listen', label: 'Адрес прослушивания', children: discovery.data.listen_address },
        ]} />
        {previewMutation.error && <Alert type="error" showIcon title={errorText(previewMutation.error)} />}
        <div className="flow-actions"><Button onClick={close}>Отмена</Button><Button type="primary" loading={previewMutation.isPending} onClick={() => previewMutation.mutate()}>Проверить конфигурацию</Button></div>
      </>}
      {preview && <>
        <Alert type="success" showIcon title="Проверка завершена" description="При импорте файлы будут прочитаны повторно, а их контрольные суммы проверены. Данные из браузера не используются как источник истины." />
        {preview.warnings.map(item => <Alert key={item} type="warning" showIcon title={item} />)}
        <Descriptions bordered size="small" column={1} items={[
          { key: 'service', label: 'Сервис', children: preview.service_name },
          { key: 'version', label: 'Версия', children: preview.version },
          { key: 'binary', label: 'Исполняемый файл', children: <span className="technical-value">{preview.binary_path}</span> },
          { key: 'configs', label: 'Конфигурация', children: <Typography.Text className="technical-value">{preview.vpn_config_path}<br />{preview.hosts_config_path}<br />{preview.credentials_path}<br />{preview.rules_path}</Typography.Text> },
          { key: 'listen', label: 'Адреса', children: `${preview.listen_address} · ${preview.public_address}` },
          { key: 'protocols', label: 'Протоколы', children: <Space size={4}>{preview.protocols.map(item => <Tag color="blue" key={item}>{item}</Tag>)}</Space> },
          { key: 'hosts', label: 'TLS-хосты', children: preview.tls_hosts.join(', ') },
          { key: 'clients', label: 'Клиенты', children: preview.client_count },
          { key: 'capabilities', label: 'Возможности', children: Object.entries(preview.capabilities).filter(([, enabled]) => enabled).map(([name]) => <Tag key={name}>{name}</Tag>) },
        ]} />
        {confirm.error && <Alert type="error" showIcon title={errorText(confirm.error)} />}
        <div className="flow-actions"><Button onClick={() => setPreview(undefined)} disabled={confirm.isPending}>Назад</Button><Button type="primary" loading={confirm.isPending} onClick={() => confirm.mutate()}>Импортировать в TunnelUI</Button></div>
      </>}
    </div>
  </Modal>;
}
