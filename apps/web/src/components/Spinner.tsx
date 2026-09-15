import { useI18n } from '../i18n.ts';

export function Spinner({ label }: { label?: string }) {
  const { t } = useI18n();
  return (
    <p className="row" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{label ?? t.loading}</span>
    </p>
  );
}
