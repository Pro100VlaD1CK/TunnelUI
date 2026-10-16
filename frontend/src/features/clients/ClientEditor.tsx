import { Alert, App, Button, DatePicker, Drawer, Form, Input, InputNumber, Modal, Select, Space, Switch, Typography } from 'antd';
import dayjs from 'dayjs';
import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, errorText } from '../../shared/api';
import type { Client, Inbound, Operation } from '../../shared/types';

interface Values {
  username: string; display_name: string; enabled: boolean; comment?: string;
  expires_at?: dayjs.Dayjs | null; inbound_id?: string; password?: string;
  max_http2_conns?: number; max_http3_conns?: number;
}

function generatedPassword() {
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
}

export function ClientEditor({ open, client, close }: { open: boolean; client?: Client; close: () => void }) {
  const [form] = Form.useForm<Values>();
  const [confirmation, setConfirmation] = useState<Values>();
  const [partial, setPartial] = useState(false);
  const [failedOperations, setFailedOperations] = useState<Operation[]>([]);
  const requestKey = useRef(crypto.randomUUID());
  const attached = Boolean(client?.attachments.length);
  const cache = useQueryClient();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const inbounds = useQuery({ queryKey: ['inbounds'], queryFn: () => api<{ items: Inbound[] }>('/inbounds'), enabled: open && !client });
  const mutation = useMutation({
    mutationFn: async (values: Values) => {
      const body = {
        username: values.username, display_name: values.display_name,
        enabled: values.enabled ?? true, comment: values.comment ?? '',
        expires_at: values.expires_at ? values.expires_at.unix() : null,
        ...(client ? { revision: client.revision } : {}),
      };
      const credentialChange = Boolean(client && attached && (
        values.username !== client.username
        || (values.expires_at?.unix() ?? null) !== client.expires_at
      ));
      if (client && credentialChange) {
        const result = await api<{ state: 'succeeded' | 'partial'; operations: Operation[]; error_code: string | null }>(`/clients/${client.id}/credentials`, {
          method: 'PUT', body: JSON.stringify({
            username: body.username, display_name: body.display_name,
            comment: body.comment, expires_at: body.expires_at,
            revision: client.revision, idempotency_key: requestKey.current,
          }),
        });
        if (result.state !== 'succeeded') {
          setPartial(true); setFailedOperations(result.operations);
          throw new ApiError(result.error_code ?? 'operation_failed', 409);
        }
        return client;
      }
      const saved = await api<Client>(client ? `/clients/${client.id}` : '/clients', {
        method: client ? 'PUT' : 'POST', body: JSON.stringify(body),
      });
      if (!client && values.inbound_id && values.password) {
        setPartial(true);
        const result = await api<{ attachment_id: string; operation: Operation }>(`/clients/${saved.id}/attachments`, {
          method: 'POST', body: JSON.stringify({
            inbound_id: values.inbound_id, password: values.password,
            max_http2_conns: values.max_http2_conns ?? null,
            max_http3_conns: values.max_http3_conns ?? null,
            idempotency_key: crypto.randomUUID(),
          }),
        });
        if (result.operation.state !== 'succeeded') {
          throw new ApiError(result.operation.state === 'needs_recovery' ? 'recovery_required' : 'apply_failed', 409);
        }
      }
      return saved;
    },
    onSuccess: (_result, values) => {
      cache.invalidateQueries({ queryKey: ['clients'] });
      cache.invalidateQueries({ queryKey: ['inbounds'] });
      const createdMessage = !values.inbound_id ? 'Клиент создан'
        : values.expires_at && values.expires_at.unix() <= Math.floor(Date.now() / 1000)
          ? 'Клиент создан; срок истёк, доступ не выдан'
          : 'Клиент создан, доступ применён';
      message.success(client ? (confirmation ? 'Учётные данные применены' : 'Клиент сохранён') : createdMessage);
      setConfirmation(undefined); close();
    },
    onSettled: () => {
      cache.invalidateQueries({ queryKey: ['clients'] });
      cache.invalidateQueries({ queryKey: ['inbounds'] });
    },
  });
  useEffect(() => {
    if (open) {
      form.resetFields(); mutation.reset(); setConfirmation(undefined); setPartial(false);
      setFailedOperations([]); requestKey.current = crypto.randomUUID();
      form.setFieldsValue(client ? { ...client, expires_at: client.expires_at ? dayjs.unix(client.expires_at) : null } : { enabled: true });
    }
    // The editor resets only when it opens or changes record.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, client, form]);
  const submit = (values: Values) => {
    if ((!client && values.inbound_id) || (client && attached && (
      values.username !== client.username
      || (values.expires_at?.unix() ?? null) !== client.expires_at
    ))) setConfirmation(values);
    else mutation.mutate(values);
  };
  return <>
    <Drawer title={client ? 'Редактировать клиента' : 'Новый клиент'} open={open} onClose={() => { if (!mutation.isPending) close(); }} size={480} className="client-drawer">
      <Alert type="info" title={client ? 'Глобальная запись клиента' : 'Клиент и доступ'} description={client ? (attached ? 'Изменение username или срока затронет все привязанные входящие. После подтверждения TunnelUI применит их по очереди и покажет частичный результат, если одно из входящих не обновится.' : 'Изменения этой записи не затрагивают конфигурацию TrustTunnel.') : 'Можно создать только глобальную запись либо сразу привязать её к управляемому TrustTunnel и применить конфигурацию.'} />
      {mutation.error && !confirmation && <Alert role="alert" type="error" showIcon title={errorText(mutation.error)} />}
      {mutation.error instanceof ApiError && mutation.error.code === 'revision_conflict' && <Button onClick={() => { cache.invalidateQueries({ queryKey: ['clients'] }); close(); }}>Закрыть без сохранения и обновить список</Button>}
      <Form form={form} layout="vertical" requiredMark={false} onFinish={submit}>
        <Form.Item name="username" label="Username" rules={[{ required: true, message: 'Укажите username' }, { pattern: /^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,63}$/, message: 'До 64 латинских букв, цифр и _.@-; начало — буква или цифра' }]}><Input maxLength={64} autoComplete="off" disabled={partial} /></Form.Item>
        <Form.Item name="display_name" label="Отображаемое имя" rules={[{ required: true, whitespace: true, message: 'Укажите имя' }]}><Input maxLength={160} disabled={partial} /></Form.Item>
        <Form.Item name="comment" label="Комментарий"><Input.TextArea rows={3} maxLength={4000} showCount disabled={partial} /></Form.Item>
        <Form.Item name="expires_at" label="Действует до" extra="Пусто — без срока. Введите местное время; сервер хранит и сравнивает UTC."><DatePicker showTime format="DD.MM.YYYY HH:mm" disabled={partial} /></Form.Item>
        {!client && <>
          <Form.Item name="enabled" label="Глобальная запись включена" valuePropName="checked"><Switch disabled={partial} /></Form.Item>
          <Typography.Title level={2}>Доступ TrustTunnel</Typography.Title>
          <Form.Item name="inbound_id" label="Входящее" extra="Оставьте пустым, чтобы создать клиента без VPN-доступа."><Select allowClear loading={inbounds.isPending} disabled={partial} placeholder="Без привязки" options={inbounds.data?.items.filter(item => item.enabled && item.kind === 'trusttunnel').map(item => ({ value: item.id, label: `${item.name} · ${item.public_address}` }))} /></Form.Item>
          <Form.Item noStyle dependencies={['inbound_id']}>{({ getFieldValue }) => getFieldValue('inbound_id') ? <>
            <Form.Item name="password" label="Пароль" rules={[{ required: true, min: 12, message: 'Сгенерируйте или укажите пароль от 12 символов' }]}><Input.Password autoComplete="new-password" disabled={partial} /></Form.Item>
            <Button className="generate-secret" onClick={() => form.setFieldValue('password', generatedPassword())} disabled={partial}>Сгенерировать пароль</Button>
            <Space className="connection-limits" align="start"><Form.Item name="max_http2_conns" label="HTTP/2 connections"><InputNumber min={0} max={4294967295} /></Form.Item><Form.Item name="max_http3_conns" label="HTTP/3 connections"><InputNumber min={0} max={4294967295} /></Form.Item></Space>
          </> : null}</Form.Item>
        </>}
        {client && <Form.Item name="enabled" hidden><Input /></Form.Item>}
        <Button type="primary" htmlType="submit" loading={mutation.isPending} disabled={partial && Boolean(mutation.error)}>Сохранить</Button>
      </Form>
    </Drawer>
    <Modal open={Boolean(confirmation)} title={client ? 'Применить изменения учётных данных?' : 'Применить доступ TrustTunnel?'} okText={client ? 'Сохранить и применить' : 'Создать и применить'} okButtonProps={{ disabled: partial }} cancelText="Вернуться к форме" confirmLoading={mutation.isPending} onCancel={() => { if (!mutation.isPending) setConfirmation(undefined); }} onOk={() => confirmation && mutation.mutate(confirmation)}>
      <p>{client ? `Будут затронуты ${client.attachments.length} входящих: ${client.attachments.map(item => item.inbound_name).join(', ')}. Применение выполняется по очереди; до подтверждения возможен старый доступ.` : 'Будут созданы клиент и привязка. Затем TunnelUI сохранит резервную копию credentials.toml, атомарно применит изменения, перезапустит выбранную службу и проверит её состояние. В локальном sandbox эти действия выполняются тестовым провайдером.'}</p>
      <p>При ошибке после записи файлов TunnelUI восстановит предыдущую конфигурацию. Учётные данные не попадут в журнал операций или аудит.</p>
      {partial && <Alert type="warning" showIcon title="Применение не подтверждено полностью" description="Проверьте фактическое состояние каждого входящего и журнал операций. Сохранённое и применённое состояние могут различаться." />}
      {partial && (client?.attachments.map(item => ({ inbound_id: item.inbound_id, inbound_name: item.inbound_name })) ?? (confirmation?.inbound_id ? [{ inbound_id: confirmation.inbound_id, inbound_name: 'входящее' }] : [])).map(item => <Button key={item.inbound_id} size="small" onClick={() => { setConfirmation(undefined); close(); navigate(`/inbounds?inbound=${item.inbound_id}`); }}>Открыть {item.inbound_name}{failedOperations.some(op => op.inbound_id === item.inbound_id && op.state !== 'succeeded') ? ' · ошибка' : ''}</Button>)}
      {mutation.error && <Alert role="alert" type="error" showIcon title={errorText(mutation.error)} />}
    </Modal>
  </>;
}
