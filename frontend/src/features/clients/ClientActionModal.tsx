import { Alert, App, Modal } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, errorText } from '../../shared/api';
import type { Client } from '../../shared/types';

export function ClientActionModal({ client, remove, close }: { client: Client; remove: boolean; close: () => void }) {
  const cache = useQueryClient();
  const { message } = App.useApp();
  const mutation = useMutation({ mutationFn: () => {
    const { id, created_at: _created, updated_at: _updated, ...values } = client;
    return api(`/clients/${id}${remove ? `?revision=${client.revision}` : ''}`, {
      method: remove ? 'DELETE' : 'PUT', ...(remove ? {} : { body: JSON.stringify({ ...values, enabled: !client.enabled }) }),
    });
  }, onSuccess: () => { cache.invalidateQueries({ queryKey: ['clients'] }); message.success('Запись обновлена'); close(); } });
  return <Modal open title={remove ? `Удалить клиента «${client.display_name}»?` : `${client.enabled ? 'Отключить' : 'Включить'} запись «${client.display_name}»?`}
    okText={remove ? 'Удалить' : 'Подтвердить'} cancelText="Отмена" confirmLoading={mutation.isPending}
    okButtonProps={{ danger: remove || client.enabled }} onOk={() => mutation.mutate()}
    onCancel={() => { if (!mutation.isPending) close(); }}>
    <p>Действие доступно только для клиента без привязок к VPN. Записи с привязками требуют отдельного применения конфигурации.</p>
    {mutation.error && <Alert type="error" showIcon title={errorText(mutation.error)} />}
  </Modal>;
}
