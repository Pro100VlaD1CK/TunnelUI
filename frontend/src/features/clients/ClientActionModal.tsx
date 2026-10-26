import { Alert, App, Button, Modal, Space } from 'antd';
import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, errorText } from '../../shared/api';
import type { Client, Operation } from '../../shared/types';

interface ActionResult {
  state?: 'succeeded' | 'partial';
  error_code?: string | null;
  operations?: Operation[];
}

export function ClientActionModal({ client, remove, close }: { client: Client; remove: boolean; close: () => void }) {
  const cache = useQueryClient();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const key = useRef(crypto.randomUUID());
  const [partial, setPartial] = useState<ActionResult>();
  const mutation = useMutation({ mutationFn: (): Promise<ActionResult | undefined> => {
    const { id, created_at: _created, updated_at: _updated,
      expiry_managed: _expiryManaged, attachments: _attachments, ...values } = client;
    if (remove) {
      if (!client.attachments.length) {
        return api<void>(`/clients/${id}?revision=${client.revision}`, { method: 'DELETE' })
          .then(() => ({ state: 'succeeded' as const }));
      }
      return api<ActionResult>(`/clients/${id}/delete`, { method: 'POST', body: JSON.stringify({
        revision: client.revision, idempotency_key: key.current,
      }) });
    }
    if (!remove && client.attachments.length) {
      return api<ActionResult>(`/clients/${id}/access`, { method: 'PUT', body: JSON.stringify({
        enabled: !client.enabled, revision: client.revision, idempotency_key: key.current,
      }) });
    }
    return api(`/clients/${id}`, {
      method: 'PUT', body: JSON.stringify({ ...values, attachments: undefined, enabled: !client.enabled }),
    });
  }, onSuccess: result => {
    if (result?.state === 'partial' || result?.operations?.some(item => item.state !== 'succeeded')) {
      setPartial(result);
      key.current = crypto.randomUUID();
      return;
    }
    message.success(remove ? 'Клиент удалён, доступ отозван' : 'Запись и доступ обновлены');
    close();
  }, onError: error => {
    if (error instanceof ApiError) key.current = crypto.randomUUID();
  }, onSettled: () => {
    cache.invalidateQueries({ queryKey: ['clients'] });
    cache.invalidateQueries({ queryKey: ['inbounds'] });
  } });
  return <Modal open title={remove ? `Удалить клиента «${client.display_name}»?` : `${client.enabled ? 'Отключить' : 'Включить'} запись «${client.display_name}»?`}
    okText={remove ? 'Удалить' : 'Подтвердить'} cancelText="Отмена" confirmLoading={mutation.isPending}
    okButtonProps={{ danger: remove || client.enabled, disabled: Boolean(partial && !remove) }} onOk={() => { setPartial(undefined); mutation.mutate(); }}
    onCancel={() => { if (!mutation.isPending) close(); }}>
    <p>{remove ? `Будет отозван доступ в ${client.attachments.length} входящих. После проверки применения удалятся привязки и глобальная запись. Если одно входящее не применится, запись останется и можно будет продолжить после устранения ошибки.` : client.attachments.length ? `Глобальное состояние изменится для ${client.attachments.length} привязок. Для каждой будет выполнено подтверждённое применение конфигурации.` : 'Изменится только глобальная запись: у клиента нет VPN-привязок.'}</p>
    {mutation.error && <Alert type="error" showIcon title={errorText(mutation.error)} />}
    {partial && <Alert type="warning" showIcon title="Изменение выполнено частично"
      description={partial.error_code ? errorText(new ApiError(partial.error_code, 409)) : 'Проверьте журнал операций и фактическое состояние входящих.'} />}
    {(mutation.error || partial) && !!client.attachments.length && <Space wrap>
      {client.attachments.map(item => <Button key={item.id} size="small" onClick={() => {
        close(); navigate(`/inbounds?inbound=${item.inbound_id}`);
      }}>Открыть {item.inbound_name}</Button>)}
    </Space>}
  </Modal>;
}
