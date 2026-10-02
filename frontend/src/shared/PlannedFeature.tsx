import { Alert, Typography } from 'antd';

export function PlannedFeature({ title, description }: { title: string; description: string }) {
  return <><div className="page-heading"><Typography.Title level={1}>{title}</Typography.Title></div>
    <Alert type="info" showIcon title="Раздел ещё не реализован" description={description} />
  </>;
}
