import { Alert, App, Button, Modal } from 'antd';
import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, errorText } from '../../shared/api';
import type { Attachment, Client, Operation } from '../../shared/types';

export function AttachmentActionModal({ client, attachment, detach, close }: { client: Client; attachment: Attachment; detach: boolean; close: () => void }) {
  const cache = useQueryClient();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const key = useRef(crypto.randomUUID());
  const [operation, setOperation] = useState<Operation>();
  const mutation = useMutation({ mutationFn: () => api<{ operation: Operation }>(`/attachments/${attachment.id}`, {
    method: detach ? 'DELETE' : 'PUT', body: JSON.stringify({
      revision: attachment.revision, idempotency_key: key.current,
      ...(detach ? {} : { enabled: !attachment.enabled }),
    }),
  }), onSuccess: result => {
    if (result.operation.state !== 'succeeded') {
      setOperation(result.operation);
      key.current = crypto.randomUUID();
      return;
    }
    message.success(detach ? 'Доступ удалён и применён' : 'Доступ обновлён и применён');
    close();
  }, onError: error => {
    if (error instanceof ApiError) key.current = crypto.randomUUID();
  }, onSettled: () => {
    cache.invalidateQueries({ queryKey: ['clients'] });
    cache.invalidateQueries({ queryKey: ['inbounds'] });
  } });
  const disabling = attachment.enabled;
  return <Modal open title={detach ? `Отвязать ${client.username} от ${attachment.inbound_name}?` : `${disabling ? 'Отключить' : 'Включить'} доступ через ${attachment.inbound_name}?`}
    okText={detach ? 'Отвязать и применить' : 'Изменить и применить'} cancelText="Отмена"
    okButtonProps={{ danger: detach || disabling, disabled: Boolean(operation) }} confirmLoading={mutation.isPending}
    onOk={() => mutation.mutate()} onCancel={() => { if (!mutation.isPending) close(); }}>
    <p>{detach ? 'Клиент останется в TunnelUI, а доступ к этому входящему будет удалён только после успешного применения. Другие входящие не меняются.' : 'Изменится доступ только к выбранному входящему. TunnelUI создаст backup, применит credentials и проверит службу.'}</p>
    {(detach || disabling) && <Alert type="warning" showIcon title="Последний активный клиент защищён" description="Если это последний активный credential, TrustTunnel v1.1.0 отклонит операцию до изменения файла." />}
    {mutation.error && <Alert type="error" showIcon title={errorText(mutation.error)} />}
    {operation && <Alert type="warning" showIcon title="Применение не подтверждено"
      description={errorText(new ApiError(operation.error_code ?? 'operation_failed', 409))}
      action={<Button size="small" onClick={() => { close(); navigate(`/inbounds?inbound=${attachment.inbound_id}`); }}>Открыть входящее</Button>} />}
  </Modal>;
}
