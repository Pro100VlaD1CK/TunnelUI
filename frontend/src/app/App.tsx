import { useEffect, useState } from 'react';
import { App as AntApp, ConfigProvider, theme } from 'antd';
import ruRU from 'antd/locale/ru_RU';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, errorText } from '../shared/api';
import { QueryState } from '../shared/QueryState';
import { PlannedFeature } from '../shared/PlannedFeature';
import { Login } from '../features/auth/Login';
import { Clients } from '../features/clients/Clients';
import { Inbounds } from '../features/inbounds/Inbounds';
import { Audit } from '../features/audit/Audit';
import { Settings } from '../features/settings/Settings';
import { Profiles } from '../features/profiles/Profiles';
import { Shell } from './Shell';

function SessionApp({ dark, toggleTheme }: { dark: boolean; toggleTheme: () => void }) {
  const cache = useQueryClient();
  const [expiredSession, setExpiredSession] = useState(false);
  const { message } = AntApp.useApp();
  const me = useQuery({ queryKey: ['me'], queryFn: () => api<{ username: string }>('/auth/me'), retry: false });
  useEffect(() => {
    const expired = () => { setExpiredSession(true); cache.clear(); cache.setQueryData(['me'], null); };
    window.addEventListener('session-expired', expired);
    return () => window.removeEventListener('session-expired', expired);
  }, [cache]);
  const logout = useMutation({ mutationFn: () => api('/auth/logout', { method: 'POST' }),
    onSuccess: () => { setExpiredSession(false); cache.clear(); cache.setQueryData(['me'], null); },
    onError: error => message.error(errorText(error)),
  });
  if (me.isPending) return <div className="session-loading"><QueryState loading error={null} retry={() => {}} /></div>;
  if (me.error && !(me.error instanceof ApiError && me.error.status === 401)) return <div className="session-loading"><QueryState loading={false} error={me.error} retry={() => me.refetch()} /></div>;
  if (!me.data) return <Login expired={expiredSession} onLogin={() => { setExpiredSession(false); cache.invalidateQueries({ queryKey: ['me'] }); window.location.hash = '/inbounds'; }} />;
  return <Routes><Route element={<Shell dark={dark} toggleTheme={toggleTheme} username={me.data.username} logout={() => logout.mutate()} loggingOut={logout.isPending} />}>
    <Route path="/inbounds" element={<Inbounds />} /><Route path="/clients" element={<Clients />} />
    <Route path="/profiles" element={<Profiles />} />
    <Route path="/rules" element={<PlannedFeature title="Правила" description="Редактирование упорядоченных правил TrustTunnel станет доступно после подключения безопасного применения конфигурации." />} />
    <Route path="/settings" element={<Settings />} /><Route path="/audit" element={<Audit />} />
    <Route path="*" element={<Navigate to="/inbounds" replace />} />
  </Route></Routes>;
}

export default function App() {
  const [dark, setDark] = useState(() => localStorage.getItem('tunnelui-theme') === 'dark');
  const [reducedMotion, setReducedMotion] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const query = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(query.matches);
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; localStorage.setItem('tunnelui-theme', dark ? 'dark' : 'light'); }, [dark]);
  return <ConfigProvider locale={ruRU} theme={{ algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: { motion: !reducedMotion, colorPrimary: dark ? '#54b6cc' : '#176b87', borderRadius: 6, fontFamily: 'Segoe UI, sans-serif', fontSize: 14,
      colorBgLayout: dark ? '#101820' : '#f3f5f7', colorBgContainer: dark ? '#17232d' : '#ffffff', colorText: dark ? '#e6edf3' : '#172b3a' },
    components: {
      Layout: { headerBg: dark ? '#17232d' : '#ffffff', siderBg: dark ? '#17232d' : '#ffffff' },
      Button: { primaryColor: dark ? '#101820' : '#ffffff' },
      Menu: { darkItemSelectedColor: '#101820' },
      Table: { cellPaddingBlockSM: 10 },
    },
  }}><AntApp><SessionApp dark={dark} toggleTheme={() => setDark(v => !v)} /></AntApp></ConfigProvider>;
}
