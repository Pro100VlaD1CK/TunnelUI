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
}
export interface Page<T> { items: T[]; total: number }
