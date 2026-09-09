import { Link } from 'react-router-dom';
import { useI18n } from '../i18n.ts';

export function NotFoundPage() {
  const { t } = useI18n();
  return (
    <>
      <h1>{t.errorNotFound}</h1>
      <p>
        <Link to="/">{t.navSearch}</Link>
      </p>
    </>
  );
}
