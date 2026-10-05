export interface ClientInput {
  username: string;
  display_name: string;
  enabled: boolean;
  comment: string;
  expires_at: number | null;
}
export interface Client extends ClientInput {
  id: string;
  created_at: number;
  updated_at: number;
  revision: number;
  attachments: Attachment[];
}
export interface Page<T> { items: T[]; total: number }

export interface Attachment {
  id: string;
  inbound_id: string;
  inbound_name: string;
  enabled: boolean;
  desired_state: 'active' | 'disabled' | 'detached' | 'pending';
  applied_state: 'active' | 'disabled' | 'pending';
  sync_state: 'active' | 'pending' | 'conflict' | 'error' | 'disabled';
  revision: number;
}

export interface Operation {
  id: string;
  inbound_id: string;
  kind: string;
  state: 'pending' | 'preparing' | 'backed_up' | 'writing' | 'applying' | 'checking' |
    'succeeded' | 'rolling_back' | 'rolled_back' | 'failed' | 'needs_recovery';
  error_code: string | null;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
}

export interface Inbound {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  version: string | null;
  listen_address: string | null;
  public_address: string | null;
  protocols: string[];
  client_count: number;
  config_state: 'synced' | 'drift' | 'pending' | 'error' | 'recovery_required' | 'unmanaged';
  service_status: 'running' | 'stopped' | 'unknown';
  operation: Operation | null;
}
