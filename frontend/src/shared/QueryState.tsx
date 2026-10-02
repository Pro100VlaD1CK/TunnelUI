import { Alert, Button, Skeleton } from 'antd';
import { errorText } from './api';

export function QueryState({ loading, error, retry }: { loading: boolean; error: unknown; retry: () => void }) {
  if (loading) return <div role="status" aria-busy="true"><span className="sr-only">Загрузка данных…</span><div aria-hidden="true"><Skeleton active paragraph={{ rows: 5 }} /></div></div>;
  if (error) return <Alert type="error" showIcon title={errorText(error)} action={<Button onClick={retry}>Повторить</Button>} />;
  return null;
}
