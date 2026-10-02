import { Alert, Button, Form, Input, Typography } from 'antd';
import { useMutation } from '@tanstack/react-query';
import { api, errorText, refreshCsrf } from '../../shared/api';

export function Login({ onLogin, expired }: { onLogin: () => void; expired?: boolean }) {
  const mutation = useMutation({
    mutationFn: async (values: { username: string; password: string }) => {
      await refreshCsrf();
      return api('/auth/login', { method: 'POST', body: JSON.stringify(values) });
    }, onSuccess: onLogin,
  });
  return <main className="login-page"><section className="login-panel" aria-labelledby="login-title">
    <div className="brand">TunnelUI</div>
    <Typography.Title id="login-title" level={1}>Вход в панель</Typography.Title>
    <Typography.Paragraph type="secondary">Управление клиентами и VPN-доступом</Typography.Paragraph>
    {expired && <Alert type="warning" showIcon title="Сессия истекла. Войдите снова." />}
    {mutation.error && <Alert role="alert" type="error" showIcon title={errorText(mutation.error)} />}
    <Form layout="vertical" onFinish={v => mutation.mutate(v)} requiredMark={false}>
      <Form.Item name="username" label="Имя администратора" rules={[{ required: true, message: 'Укажите имя' }]}>
        <Input autoComplete="username" autoFocus maxLength={64} />
      </Form.Item>
      <Form.Item name="password" label="Пароль" rules={[{ required: true, message: 'Укажите пароль' }]}>
        <Input.Password autoComplete="current-password" maxLength={1024} />
      </Form.Item>
      <Button type="primary" htmlType="submit" block loading={mutation.isPending}>Войти</Button>
    </Form>
    <Typography.Paragraph type="secondary">Первый администратор создаётся локально через команду <code>tunnelui create-admin</code>.</Typography.Paragraph>
  </section></main>;
}
