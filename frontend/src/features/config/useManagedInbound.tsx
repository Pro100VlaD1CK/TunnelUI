import { useQuery } from '@tanstack/react-query';
import { Select } from 'antd';
import { api } from '../../shared/api';
import type { Inbound } from '../../shared/types';

export function useManagedInbounds() {
  return useQuery({ queryKey: ['inbounds'], queryFn: () =>
    api<{ items: Inbound[]; adoption_available: boolean }>('/inbounds'),
    staleTime: 0, refetchOnMount: 'always',
  });
}

export function InboundPicker({ items, value, change }: {
  items: Inbound[]; value?: string; change: (value: string) => void;
}) {
  return <Select className="managed-inbound-picker" aria-label="Входящее TrustTunnel"
    placeholder="Выберите входящее" value={value} onChange={change}
    options={items.map(item => ({ value: item.id, label: item.name }))} />;
}
