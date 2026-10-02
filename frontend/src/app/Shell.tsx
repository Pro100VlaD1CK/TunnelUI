import { useState } from 'react';
import { Button, Drawer, Grid, Layout, Menu, Space, Typography } from 'antd';
import { ApiOutlined, AuditOutlined, FileProtectOutlined, LogoutOutlined, MenuOutlined, MoonOutlined, SettingOutlined, SunOutlined, TeamOutlined, UserOutlined } from '@ant-design/icons';
import { NavLink, Outlet, useLocation } from 'react-router-dom';

const navigation = [
  { key: '/inbounds', icon: <ApiOutlined />, text: 'Входящие' },
  { key: '/clients', icon: <TeamOutlined />, text: 'Клиенты' },
  { key: '/profiles', icon: <UserOutlined />, text: 'Профили' },
  { key: '/rules', icon: <FileProtectOutlined />, text: 'Правила' },
  { key: '/settings', icon: <SettingOutlined />, text: 'Настройки' },
  { key: '/audit', icon: <AuditOutlined />, text: 'Аудит' },
];
export function Shell({ dark, toggleTheme, username, logout, loggingOut }: { dark: boolean; toggleTheme: () => void; username: string; logout: () => void; loggingOut: boolean }) {
  const screens = Grid.useBreakpoint();
  const [drawer, setDrawer] = useState(false);
  const location = useLocation();
  const menu = <nav aria-label="Основная навигация"><Menu theme={dark ? 'dark' : 'light'} mode="inline" selectedKeys={[location.pathname]} onClick={() => setDrawer(false)} items={navigation.map(({ text, ...item }) => ({ ...item, label: <NavLink to={item.key}>{text}</NavLink> }))} /></nav>;
  return <Layout className="app-layout">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus(); }}>К содержимому</a>
    {screens.md && <Layout.Sider width={216} theme={dark ? 'dark' : 'light'}><div className="sidebar-brand brand">TunnelUI</div>{menu}<div className="sidebar-note">Управление VPN-доступом</div></Layout.Sider>}
    {!screens.md && <Drawer title="TunnelUI" placement="left" open={drawer} onClose={() => setDrawer(false)} size={280} destroyOnHidden>{menu}</Drawer>}
    <Layout><Layout.Header className="app-header">
      <Space>{!screens.md && <Button type="text" aria-label="Открыть меню" icon={<MenuOutlined />} onClick={() => setDrawer(true)} />}<Typography.Text>{screens.md ? 'Панель управления' : 'TunnelUI'}</Typography.Text></Space>
      <Space><Button type="text" aria-label={dark ? 'Светлая тема' : 'Тёмная тема'} icon={dark ? <SunOutlined /> : <MoonOutlined />} onClick={toggleTheme} /><Typography.Text className="admin-name">{username}</Typography.Text><Button type="text" aria-label="Выйти" icon={<LogoutOutlined />} onClick={logout} loading={loggingOut} /></Space>
    </Layout.Header><Layout.Content id="main-content" tabIndex={-1} className="app-content"><Outlet /></Layout.Content></Layout>
  </Layout>;
}
