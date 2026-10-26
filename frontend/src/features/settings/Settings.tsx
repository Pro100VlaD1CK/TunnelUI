import { Alert, Descriptions, Tabs, Typography } from 'antd';
import { useState } from 'react';
import { ExpirySettings } from './ExpirySettings';

const sections = ['Общие', 'TrustTunnel', 'Параметры клиентов', 'Сроки доступа', 'Метрики', 'Сертификаты', 'Безопасность', 'Резервные копии'];
export function Settings() {
  const [section, setSection] = useState('0');
  return <><div className="page-heading"><div><Typography.Title level={1}>Настройки</Typography.Title><Typography.Text type="secondary">Параметры управления TrustTunnel</Typography.Text></div></div>
    <select className="settings-mobile-select" aria-label="Раздел настроек" value={section}
      onChange={event => setSection(event.target.value)}>
      {sections.map((label, i) => <option key={label} value={String(i)}>{label}</option>)}
    </select>
    <Tabs className="settings-tabs" activeKey={section} onChange={setSection} items={sections.map((label, i) => ({ key: String(i), label, children: i === 0 ? <Descriptions bordered column={1} size="small" items={[
      { key: 'language', label: 'Язык', children: 'Русский' }, { key: 'host', label: 'Системный агент', children: 'Состояние выбранного входящего показано в разделе «Входящие»' },
      { key: 'theme', label: 'Тема', children: 'Переключается в верхней панели; сохраняется только на этом устройстве' },
      { key: 'scope', label: 'Доступно', children: 'Клиенты, входящие TrustTunnel, профили, операции и аудит' },
    ]} /> : i === 3 ? <ExpirySettings /> : <Alert type="info" showIcon title={`${label}: настройка пока недоступна`} description={i === 6 ? 'В этой версии работают серверные сессии, CSRF и защита входа. Управление политиками безопасности будет добавлено позднее.' : 'Раздел запланирован для TrustTunnel; изменение параметров пока недоступно.'} /> }))} />
  </>;
}
