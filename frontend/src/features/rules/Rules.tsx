import { useEffect, useState } from 'react';
import { Alert, App, Button, Form, Input, Modal, Select, Space, Table, Tag, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, errorText } from '../../shared/api';
import { QueryState } from '../../shared/QueryState';
import { InboundPicker, useManagedInbounds } from '../config/useManagedInbound';
import type { Operation } from '../../shared/types';

interface Rule { cidr?: string | null; client_random_prefix?: string | null; action: 'allow' | 'deny' }
interface RuleView { rules: Rule[]; drift: boolean; restart_required: boolean }
interface Preview { fingerprint: string; before_count: number; after_count: number; restart_required: boolean }
function normalized(rule: Rule): Rule {
  return { action: rule.action,
    ...(rule.cidr?.trim() ? { cidr: rule.cidr.trim() } : {}),
    ...(rule.client_random_prefix?.trim() ? { client_random_prefix: rule.client_random_prefix.trim() } : {}),
  };
}

export function Rules() {
  const { modal, message } = App.useApp();
  const cache = useQueryClient();
  const inbounds = useManagedInbounds();
  const managed = inbounds.data?.items.filter(item => item.enabled && item.kind === 'trusttunnel') ?? [];
  const [selected, setSelected] = useState<string>();
  const inboundId = selected && managed.some(item => item.id === selected) ? selected : managed[0]?.id;
  const view = useQuery({ queryKey: ['rules', inboundId], queryFn: () => api<RuleView>(`/inbounds/${inboundId}/rules`),
    enabled: Boolean(inboundId), staleTime: 0 });
  const [draft, setDraft] = useState<Rule[]>([]);
  const [lastResult, setLastResult] = useState<Operation>();
  useEffect(() => { setLastResult(undefined); }, [inboundId]);
  const [editing, setEditing] = useState<number | 'new' | null>(null);
  const [form] = Form.useForm<Rule>();
  useEffect(() => { if (view.data) setDraft(view.data.rules.map(normalized)); }, [view.data]);
  const dirty = Boolean(view.data && JSON.stringify(draft) !== JSON.stringify(view.data.rules.map(normalized)));
  const status = managed.find(item => item.id === inboundId);
  const blocked = Boolean(view.data?.drift || status?.config_state !== 'synced');
  const apply = useMutation({ mutationFn: ({ preview, rules }: { preview: Preview; rules: Rule[] }) =>
    api<{ operation: Operation }>(`/inbounds/${inboundId}/rules/apply`, {
      method: 'POST', body: JSON.stringify({ rules, fingerprint: preview.fingerprint, idempotency_key: crypto.randomUUID() }),
    }), onSuccess: result => {
      setLastResult(result.operation);
      if (result.operation.state === 'succeeded') message.success('Правила применены; служба проверена');
      else message.error(result.operation.state === 'needs_recovery' ? 'Требуется восстановление. Проверьте входящее.' : 'Применение не завершено. Проверьте журнал операций.');
    }, onSettled: () => { cache.invalidateQueries({ queryKey: ['rules', inboundId] }); cache.invalidateQueries({ queryKey: ['inbounds'] }); },
  });
  const preview = useMutation({ mutationFn: () => api<Preview>(`/inbounds/${inboundId}/rules/preview`, {
    method: 'POST', body: JSON.stringify({ rules: draft }),
  }), onSuccess: result => {
    const proposed = draft.map(rule => ({ ...rule }));
    modal.confirm({ title: 'Применить правила TrustTunnel?', okText: 'Создать backup и применить',
      cancelText: 'Отмена', content: <><p>Порядок правил определяет приоритет: срабатывает первое совпадение. Изменится rules.toml, затем служба будет перезапущена и проверена.</p>
        <p>Правил: {result.before_count} → {result.after_count}. При ошибке будет выполнен откат.</p></>,
      onOk: async () => { await apply.mutateAsync({ preview: result, rules: proposed }); },
    });
  } });
  const openEditor = (index: number | 'new') => {
    setEditing(index);
    const rule: Rule = index === 'new' ? { action: 'allow' } : draft[index];
    form.setFieldsValue({ cidr: rule.cidr ?? '', client_random_prefix: rule.client_random_prefix ?? '', action: rule.action });
  };
  const saveRule = async () => {
    const values = await form.validateFields();
    const next = normalized(values);
    setDraft(previous => editing === 'new' ? [...previous, next] : previous.map((item, index) => index === editing ? next : item));
    setEditing(null);
  };
  const move = (index: number, offset: number) => setDraft(previous => {
    const next = [...previous];
    [next[index], next[index + offset]] = [next[index + offset], next[index]];
    return next;
  });
  return <>
    <div className="page-heading"><div><Typography.Title level={1}>Правила</Typography.Title><Typography.Text type="secondary">Упорядоченная фильтрация TrustTunnel</Typography.Text></div></div>
    <div className="toolbar"><InboundPicker items={managed} value={inboundId} change={setSelected} />
      <Button disabled={!inboundId} loading={view.isFetching} onClick={() => view.refetch()}>Обновить</Button></div>
    <QueryState loading={inbounds.isPending || (Boolean(inboundId) && view.isPending)} error={inbounds.error ?? view.error} retry={() => { inbounds.refetch(); view.refetch(); }} />
    {!inbounds.isPending && !managed.length && <Alert type="info" showIcon title="Нет управляемых входящих" description="Сначала импортируйте TrustTunnel в разделе «Входящие»." />}
    {status?.config_state === 'recovery_required' && <Alert type="error" showIcon title="Требуется восстановление" description="Новые изменения заблокированы до восстановления операции во входящем." />}
    {(view.data?.drift || status?.config_state === 'drift') && <Alert type="warning" showIcon title="Обнаружены внешние изменения" description="Применение заблокировано. Проверьте и импортируйте текущее состояние во входящем." />}
    {status?.config_state === 'pending' && <Alert type="warning" showIcon title="Есть ожидающие изменения клиентов" description="Сначала примените или отмените их во входящем." />}
    {(preview.error || apply.error) && <Alert type="error" showIcon title={errorText(preview.error ?? apply.error)} />}
    {lastResult && lastResult.state !== 'succeeded' && <Alert type="error" showIcon title={lastResult.state === 'needs_recovery' ? 'Требуется восстановление' : 'Правила не применены'} description="Проверьте входящее и журнал операций перед следующей попыткой." />}
    {view.data && <>
      <div className="list-controls"><Button type="primary" disabled={blocked} onClick={() => openEditor('new')}>Добавить правило</Button></div>
      <Table size="small" rowKey={(_, index) => String(index)} dataSource={draft} pagination={false} scroll={{ x: 780 }}
        locale={{ emptyText: 'Правил нет. TrustTunnel не применяет дополнительную фильтрацию.' }} columns={[
          { title: '№', width: 48, render: (_, _rule, index) => index + 1 },
          { title: 'CIDR', dataIndex: 'cidr', width: 190, render: value => <span className="technical-value">{value || '—'}</span> },
          { title: 'Префикс клиента / маска', dataIndex: 'client_random_prefix', width: 220, render: value => <span className="technical-value">{value || '—'}</span> },
          { title: 'Действие', dataIndex: 'action', width: 115, render: value => <Tag color={value === 'deny' ? 'error' : 'success'}>{value === 'deny' ? 'Запретить' : 'Разрешить'}</Tag> },
          { title: 'Порядок и правка', width: 310, render: (_, _rule, index) => <Space size={4} wrap>
            <Button size="small" disabled={blocked || index === 0} onClick={() => move(index, -1)} aria-label={`Правило ${index + 1}: выше`}>↑</Button>
            <Button size="small" disabled={blocked || index === draft.length - 1} onClick={() => move(index, 1)} aria-label={`Правило ${index + 1}: ниже`}>↓</Button>
            <Button size="small" disabled={blocked} onClick={() => openEditor(index)}>Изменить</Button>
            <Button size="small" danger disabled={blocked} onClick={() => setDraft(previous => previous.filter((_, i) => i !== index))}>Удалить</Button>
          </Space> },
        ]} />
      <Typography.Paragraph type="secondary" className="table-note">Правила применяются сверху вниз. CIDR и префикс — поля TrustTunnel 1.1.0; произвольные условия по username не поддерживаются.</Typography.Paragraph>
      <Space wrap><Button type="primary" disabled={!dirty || blocked} loading={preview.isPending || apply.isPending} onClick={() => preview.mutate()}>Просмотреть изменения</Button>
        <Button disabled={!dirty} onClick={() => setDraft(view.data!.rules.map(normalized))}>Сбросить изменения</Button></Space>
    </>}
    <Modal title={editing === 'new' ? 'Добавить правило' : 'Изменить правило'} open={editing !== null}
      onCancel={() => setEditing(null)} onOk={saveRule} okText="Сохранить в черновик" cancelText="Отмена" destroyOnHidden>
      <Form form={form} layout="vertical"><Form.Item name="cidr" label="CIDR" extra="Например, 192.0.2.0/24"><Input autoComplete="off" /></Form.Item>
        <Form.Item name="client_random_prefix" label="Префикс клиента / маска" extra="Hex, например aabb/ffff"><Input autoComplete="off" /></Form.Item>
        <Form.Item name="action" label="Действие" rules={[{ required: true }]}><Select options={[
          { value: 'allow', label: 'Разрешить' }, { value: 'deny', label: 'Запретить' },
        ]} /></Form.Item></Form>
      <Alert type="info" showIcon title="Сохранение создаёт только черновик" description="Применение к службе требует отдельного предварительного просмотра и подтверждения." />
    </Modal>
  </>;
}
