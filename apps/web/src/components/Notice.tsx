import type { ReactNode } from 'react';

export type NoticeTone = 'info' | 'error' | 'warning' | 'success';

/**
 * Errors and important messages are announced to screen readers: `alert` for
 * problems that interrupt, `status` for everything else.
 */
export function Notice({
  tone = 'info',
  title,
  children,
}: {
  tone?: NoticeTone;
  title?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={`notice notice-${tone}`}
      role={tone === 'error' ? 'alert' : 'status'}
    >
      {title && <h2>{title}</h2>}
      {children}
    </div>
  );
}
