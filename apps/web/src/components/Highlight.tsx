import type { HighlightSegment } from '../lib/types.ts';

/**
 * Renders server-computed highlight segments. The browser never re-matches
 * the query itself, so what is emphasised always agrees with what was found,
 * and the displayed text is the original, unmodified string.
 */
export function Highlight({ segments, fallback }: { segments?: HighlightSegment[]; fallback?: string | null }) {
  if (!segments || segments.length === 0) return <>{fallback ?? ''}</>;
  return (
    <>
      {segments.map((segment, i) =>
        segment.match ? <mark key={i}>{segment.text}</mark> : <span key={i}>{segment.text}</span>,
      )}
    </>
  );
}
