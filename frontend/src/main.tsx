import React from 'react';
import ReactDOM from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import dayjs from 'dayjs';
import 'dayjs/locale/ru';
import App from './app/App';
import './app/styles.css';

dayjs.locale('ru');
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 15_000, refetchOnWindowFocus: false } } });
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={queryClient}><HashRouter><App /></HashRouter></QueryClientProvider></React.StrictMode>);
