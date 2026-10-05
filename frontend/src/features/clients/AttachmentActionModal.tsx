import { Alert, App, Modal } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, errorText } from '../../shared/api';
import type { Attachment, Client } from '../../shared/types';

export function AttachmentActionModal({ client, attachment, detach, close }: { client: Client; attachment: Attachment; detach: boolean; close: () => void }) {
  const cache = useQueryClient();
  const { message } = App.useApp();
  const mutation = useMutation({ mutationFn: () => api(`/attachments/${attachment.id}`, {
    method: detach ? 'DELETE' : 'PUT', body: JSON.stringify({
      revision: attachment.revision, idempotency_key: crypto.randomUUID(),
      ...(detach ? {} : { enabled: !attachment.enabled }),
    }),
  }), onSuccess: () => { cache.invalidateQueries({ queryKey: ['clients'] }); cache.invalidateQueries({ queryKey: ['inbounds'] }); message.success(detach ? 'Привязка удалена' : 'Доступ обновлён и применён'); close(); } });
  const disabling = attachment.enabled;
  return <Modal open title={detach ? `Отвязать ${client.username} от ${attachment.inbound_name}?` : `${disabling ? 'Отключить' : 'Включить'} доступ через ${attachment.inbound_name}?`}
    okText={detach ? 'Отвязать и применить' : 'Изменить и применить'} cancelText="Отмена"
    okButtonProps={{ danger: detach || disabling }} confirmLoading={mutation.isPending}
    onOk={() => mutation.mutate()} onCancel={() => { if (!mutation.isPending) close(); }}>
    <p>{detach ? 'Клиент останется в TunnelUI, но эта привязка будет удалена после успешного apply. Другие входящие не меняются.' : 'Изменится доступ только к выбранному входящему. TunnelUI создаст backup, применит credentials и проверит Fake service.'}</p>
    {(detach || disabling) && <Alert type="warning" showIcon title="Последний активный клиент защищён" description="Если это последний активный credential, TrustTunnel v1.1.0 отклонит операцию до изменения файла." />}
    {mutation.error && <Alert type="error" showIcon title={errorText(mutation.error)} />}
  </Modal>;
}
