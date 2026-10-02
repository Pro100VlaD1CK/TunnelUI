import { Alert, App, Button, DatePicker, Drawer, Form, Input, Switch } from 'antd';
import dayjs from 'dayjs';
import { useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, errorText } from '../../shared/api';
import type { Client } from '../../shared/types';

export function ClientEditor({ open, client, close }: { open: boolean; client?: Client; close: () => void }) {
  const [form] = Form.useForm();
  const cache = useQueryClient();
  const { message } = App.useApp();
  const mutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => api(client ? `/clients/${client.id}` : '/clients', {
      method: client ? 'PUT' : 'POST', body: JSON.stringify({ ...values,
        comment: values.comment ?? '', expires_at: values.expires_at ? (values.expires_at as dayjs.Dayjs).unix() : null,
        ...(client ? { revision: client.revision } : {}),
      }),
    }), onSuccess: () => { cache.invalidateQueries({ queryKey: ['clients'] }); message.success('Клиент сохранён'); close(); },
  });
  useEffect(() => {
    if (open) { form.resetFields(); mutation.reset(); form.setFieldsValue(client ? { ...client, expires_at: client.expires_at ? dayjs.unix(client.expires_at) : null } : { enabled: true }); }
    // The editor resets on opening or changing its record, never on background query updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, client, form]);
  return <Drawer title={client ? 'Редактировать клиента' : 'Новый клиент'} open={open} onClose={() => { if (!mutation.isPending) close(); }} size={480} className="client-drawer" destroyOnHidden>
    <Alert type="info" title="Глобальная запись клиента" description="VPN-доступ появится после привязки к входящему. Привязки ещё не реализованы." />
    {mutation.error && <Alert role="alert" type="error" showIcon title={errorText(mutation.error)} />}
    {mutation.error instanceof ApiError && mutation.error.code === 'revision_conflict' && <Button onClick={() => { cache.invalidateQueries({ queryKey: ['clients'] }); close(); }}>Закрыть без сохранения и обновить список</Button>}
    <Form form={form} layout="vertical" requiredMark={false} onFinish={v => mutation.mutate(v)}>
      <Form.Item name="username" label="Username" rules={[{ required: true, message: 'Укажите username' }, { pattern: /^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,63}$/, message: 'До 64 латинских букв, цифр и _.@-; начало — буква или цифра' }]}><Input maxLength={64} autoComplete="off" /></Form.Item>
      <Form.Item name="display_name" label="Отображаемое имя" rules={[{ required: true, whitespace: true, message: 'Укажите имя' }]}><Input maxLength={160} /></Form.Item>
      <Form.Item name="comment" label="Комментарий"><Input.TextArea rows={3} maxLength={4000} showCount /></Form.Item>
      <Form.Item name="expires_at" label="Действует до" extra="Время вашего браузера. Запись без привязок не выдаёт VPN-доступ."><DatePicker showTime format="DD.MM.YYYY HH:mm" /></Form.Item>
      {!client && <Form.Item name="enabled" label="Запись включена" valuePropName="checked"><Switch /></Form.Item>}
      {client && <Form.Item name="enabled" hidden><Input /></Form.Item>}
      <Button type="primary" htmlType="submit" loading={mutation.isPending}>Сохранить</Button>
    </Form>
  </Drawer>;
}
