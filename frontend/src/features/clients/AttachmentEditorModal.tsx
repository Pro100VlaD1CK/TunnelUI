import { Alert, App, Button, Form, Input, InputNumber, Modal, Select, Space } from 'antd';
import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, errorText } from '../../shared/api';
import type { Attachment, Client, Inbound, Operation } from '../../shared/types';

interface Values {
  inbound_id?: string;
  password?: string;
  max_http2_conns?: number | null;
  max_http3_conns?: number | null;
}

function generatedPassword() {
  return Array.from(crypto.getRandomValues(new Uint8Array(24)), value =>
    value.toString(16).padStart(2, '0')).join('');
}

export function AttachmentEditorModal({ client, attachment, close }: {
  client: Client; attachment?: Attachment; close: () => void;
}) {
  const [form] = Form.useForm<Values>();
  const key = useRef(crypto.randomUUID());
  const [operation, setOperation] = useState<Operation>();
  const cache = useQueryClient();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const inbounds = useQuery({
    queryKey: ['inbounds'], queryFn: () => api<{ items: Inbound[] }>('/inbounds'),
    enabled: !attachment,
  });
  const available = inbounds.data?.items.filter(item =>
    item.enabled && item.kind === 'trusttunnel'
    && item.execution_mode !== 'unavailable'
    && !client.attachments.some(existing => existing.inbound_id === item.id)
  ) ?? [];
  const mutation = useMutation({
    mutationFn: async (values: Values) => {
      const result = attachment
        ? await api<{ operation: Operation }>(`/attachments/${attachment.id}`, {
          method: 'PUT', body: JSON.stringify({
            enabled: attachment.enabled, revision: attachment.revision,
            max_http2_conns: values.max_http2_conns ?? null,
            max_http3_conns: values.max_http3_conns ?? null,
            idempotency_key: key.current,
          }),
        })
        : await api<{ operation: Operation }>(`/clients/${client.id}/attachments`, {
          method: 'POST', body: JSON.stringify({
            inbound_id: values.inbound_id, password: values.password,
            max_http2_conns: values.max_http2_conns ?? null,
            max_http3_conns: values.max_http3_conns ?? null,
            idempotency_key: key.current,
          }),
        });
      return result.operation;
    },
    onSuccess: result => {
      if (result.state !== 'succeeded') {
        setOperation(result);
        key.current = crypto.randomUUID();
        return;
      }
      message.success(attachment ? 'Ограничения применены' : 'Доступ создан и применён');
      close();
    },
    onError: error => {
      if (error instanceof ApiError) key.current = crypto.randomUUID();
    },
    onSettled: () => {
      cache.invalidateQueries({ queryKey: ['clients'] });
      cache.invalidateQueries({ queryKey: ['inbounds'] });
    },
  });
  return <Modal open title={attachment ? `Ограничения · ${attachment.inbound_name}` : `Добавить доступ · ${client.username}`}
    okText={attachment ? 'Сохранить и применить' : 'Добавить и применить'}
    cancelText="Закрыть" confirmLoading={mutation.isPending}
    okButtonProps={{ disabled: Boolean(operation) }}
    onOk={() => form.submit()} onCancel={() => { if (!mutation.isPending) close(); }}>
    {!attachment && <p>Доступ будет добавлен к выбранному TrustTunnel после backup, restart и проверки здоровья.</p>}
    <Form form={form} layout="vertical" onFinish={values => mutation.mutate(values)}
      initialValues={attachment ? {
        max_http2_conns: attachment.max_http2_conns,
        max_http3_conns: attachment.max_http3_conns,
      } : {}}>
      {!attachment && <>
        <Form.Item name="inbound_id" label="Входящее" rules={[{ required: true, message: 'Выберите входящее' }]}>
          <Select aria-label="Входящее" loading={inbounds.isPending} options={available.map(item => ({
            value: item.id, label: `${item.name} · ${item.public_address}`,
          }))} />
        </Form.Item>
        <Form.Item name="password" label="Пароль" rules={[{ required: true, min: 12, message: 'Укажите пароль от 12 символов' }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Button onClick={() => form.setFieldValue('password', generatedPassword())}>Сгенерировать пароль</Button>
      </>}
      <Space className="connection-limits" align="start">
        <Form.Item name="max_http2_conns" label="HTTP/2 connections"><InputNumber min={0} max={4294967295} /></Form.Item>
        <Form.Item name="max_http3_conns" label="HTTP/3 connections"><InputNumber min={0} max={4294967295} /></Form.Item>
      </Space>
    </Form>
    {!attachment && !inbounds.isPending && !available.length && <Alert type="info" title="Доступных входящих нет" />}
    {mutation.error && <Alert type="error" showIcon title={errorText(mutation.error)} />}
    {operation && <Alert type="warning" showIcon title="Доступ сохранён, применение не подтверждено"
      description={errorText(new ApiError(operation.error_code ?? 'operation_failed', 409))}
      action={<Button size="small" onClick={() => {
        close(); navigate(`/inbounds?inbound=${operation.inbound_id}`);
      }}>Открыть входящее</Button>} />}
  </Modal>;
}
