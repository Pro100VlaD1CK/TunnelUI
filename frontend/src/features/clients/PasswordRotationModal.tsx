import { Alert, App, Button, Form, Input, Modal, Space } from 'antd';
import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, errorText } from '../../shared/api';
import type { Attachment, Client, Operation } from '../../shared/types';

function generatedPassword() {
  return Array.from(crypto.getRandomValues(new Uint8Array(24)), value =>
    value.toString(16).padStart(2, '0')).join('');
}

export function PasswordRotationModal({ client, attachment, close }: {
  client: Client; attachment: Attachment; close: () => void;
}) {
  const [form] = Form.useForm<{ password: string }>();
  const key = useRef(crypto.randomUUID());
  const [operation, setOperation] = useState<Operation>();
  const [applied, setApplied] = useState(false);
  const { message } = App.useApp();
  const cache = useQueryClient();
  const navigate = useNavigate();
  const mutation = useMutation({
    mutationFn: (password: string) => api<{ operation: Operation }>(`/attachments/${attachment.id}/rotate-password`, {
      method: 'POST', body: JSON.stringify({
        password, revision: attachment.revision, idempotency_key: key.current,
      }),
    }),
    onSuccess: ({ operation: result }) => {
      form.resetFields();
      if (result.state === 'succeeded') {
        setApplied(true);
        message.success('Новый пароль применён');
      } else setOperation(result);
    },
    onError: error => {
      if (error instanceof ApiError && error.code === 'idempotency_conflict') key.current = crypto.randomUUID();
    },
    onSettled: () => {
      cache.invalidateQueries({ queryKey: ['clients'] });
      cache.invalidateQueries({ queryKey: ['inbounds'] });
    },
  });
  return <Modal open title={`Сменить пароль · ${attachment.inbound_name}`} okText="Применить новый пароль"
    cancelText="Закрыть" confirmLoading={mutation.isPending}
    okButtonProps={{ disabled: applied || Boolean(operation) }}
    onOk={() => form.submit()} onCancel={() => { if (!mutation.isPending) close(); }}>
    <p>Изменится только доступ клиента «{client.username}» в этом входящем. После backup и restart старый профиль может перестать подключаться. Получите новый QR или TOML после подтверждённого применения.</p>
    {!applied && !operation && <Form form={form} layout="vertical" onFinish={values => mutation.mutate(values.password)}>
      <Form.Item name="password" label="Новый пароль" rules={[{ required: true, min: 12, message: 'Укажите не менее 12 символов' }]}>
        <Input.Password autoComplete="new-password" maxLength={1024} />
      </Form.Item>
      <Button onClick={() => form.setFieldValue('password', generatedPassword())}>Сгенерировать пароль</Button>
    </Form>}
    {mutation.error && <Alert type="error" showIcon title={errorText(mutation.error)} />}
    {operation && <Alert type="warning" showIcon title="Пароль не подтверждён на сервере"
      description={errorText(new ApiError(operation.error_code ?? 'operation_failed', 409))} />}
    {applied && <Alert type="success" showIcon title="Новый пароль действует"
      description="Ранее выданный профиль может перестать подключаться. Откройте обновлённый профиль и передайте его клиенту безопасным способом." />}
    {(applied || operation) && <Space wrap style={{ marginTop: 16 }}>
      {applied && <Button onClick={() => { close(); navigate(`/profiles?client=${client.id}`); }}>Открыть профиль</Button>}
      {operation && <Button onClick={() => { close(); navigate(`/inbounds?inbound=${attachment.inbound_id}`); }}>Открыть операцию</Button>}
    </Space>}
  </Modal>;
}
