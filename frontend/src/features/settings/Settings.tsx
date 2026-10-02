import { Alert, Descriptions, Tabs, Typography } from 'antd';

const sections = ['Общие', 'TrustTunnel', 'Параметры клиентов', 'Метрики', 'Сертификаты', '3x-ui', 'Clash/Mihomo', 'Безопасность', 'Резервные копии'];
export function Settings() {
  return <><div className="page-heading"><div><Typography.Title level={1}>Настройки</Typography.Title><Typography.Text type="secondary">Возможности этой версии и состояние интеграций</Typography.Text></div></div>
    <Tabs items={sections.map((label, i) => ({ key: String(i), label, children: i === 0 ? <Descriptions bordered column={1} size="small" items={[
      { key: 'language', label: 'Язык', children: 'Русский' }, { key: 'host', label: 'Системный агент', children: 'Не подключён' },
      { key: 'theme', label: 'Тема', children: 'Переключается в верхней панели; сохраняется только на этом устройстве' },
      { key: 'scope', label: 'Доступно', children: 'Глобальные клиенты, авторизация, аудит' },
    ]} /> : <Alert type="info" showIcon title={`${label}: настройка пока недоступна`} description={i === 7 ? 'В этой версии работают серверные сессии, CSRF и защита входа. Управление политиками безопасности будет добавлено позднее.' : 'Этот раздел предусмотрен архитектурой. Подключение и изменение конфигурации будут доступны на следующем этапе.'} /> }))} />
  </>;
}
