import { Alert, App, Modal } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, errorText } from '../../shared/api';
import type { Client } from '../../shared/types';

export function ClientActionModal({ client, remove, close }: { client: Client; remove: boolean; close: () => void }) {
  const cache = useQueryClient();
  const { message } = App.useApp();
  const mutation = useMutation({ mutationFn: () => {
    const { id, created_at: _created, updated_at: _updated, ...values } = client;
    if (!remove && client.attachments.length) {
      return api(`/clients/${id}/access`, { method: 'PUT', body: JSON.stringify({
        enabled: !client.enabled, revision: client.revision, idempotency_key: crypto.randomUUID(),
      }) });
    }
    return api(`/clients/${id}${remove ? `?revision=${client.revision}` : ''}`, {
      method: remove ? 'DELETE' : 'PUT', ...(remove ? {} : { body: JSON.stringify({ ...values, attachments: undefined, enabled: !client.enabled }) }),
    });
  }, onSuccess: () => { cache.invalidateQueries({ queryKey: ['clients'] }); message.success('Запись обновлена'); close(); } });
  return <Modal open title={remove ? `Удалить клиента «${client.display_name}»?` : `${client.enabled ? 'Отключить' : 'Включить'} запись «${client.display_name}»?`}
    okText={remove ? 'Удалить' : 'Подтвердить'} cancelText="Отмена" confirmLoading={mutation.isPending}
    okButtonProps={{ danger: remove || client.enabled }} onOk={() => mutation.mutate()}
    onCancel={() => { if (!mutation.isPending) close(); }}>
    <p>{remove ? 'Глобальный клиент удаляется только после явного detach всех привязок. Файлы TrustTunnel этим действием не меняются.' : client.attachments.length ? `Глобальное состояние изменится для ${client.attachments.length} привязок. Для каждой будет выполнено подтверждённое применение конфигурации.` : 'Изменится только глобальная запись: у клиента нет VPN-привязок.'}</p>
    {mutation.error && <Alert type="error" showIcon title={errorText(mutation.error)} />}
  </Modal>;
}
