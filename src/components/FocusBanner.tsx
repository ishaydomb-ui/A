import Link from "next/link";
import { activeFocus, focusHref } from "@/lib/focus";
import { formatDay } from "@/components/ui";

/**
 * The one thing that outranks everything else.
 *
 * Rendered above the whole dashboard, at full width, in the accent colour —
 * this is the only element on the page allowed to shout. It renders nothing at
 * all when there's no focus, so the dashboard's normal state stays calm.
 */
export function FocusBanner() {
  const items = activeFocus();
  if (items.length === 0) return null;

  return (
    <div className="space-y-2">
      {items.map((item) => {
        const href = focusHref(item);
        const daysLeft = item.until
          ? Math.ceil(
              (new Date(`${item.until}T00:00:00`).getTime() - Date.now()) / 86_400_000,
            )
          : null;

        const body = (
          <>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[11px] font-medium tracking-wider text-white/70 uppercase">
                  Focus
                </div>
                <h2 className="mt-0.5 text-lg font-semibold" dir="auto">
                  {item.title}
                </h2>
                {item.note && (
                  <p className="mt-1 text-sm text-white/80" dir="auto">
                    {item.note}
                  </p>
                )}
              </div>
              {daysLeft !== null && (
                <span className="shrink-0 rounded-full bg-white/15 px-2.5 py-1 text-xs font-medium">
                  {daysLeft <= 0
                    ? "today"
                    : daysLeft === 1
                      ? "tomorrow"
                      : `${daysLeft} days`}
                </span>
              )}
            </div>
            {item.until && (
              <div className="mt-2 text-xs text-white/70">{formatDay(item.until)}</div>
            )}
          </>
        );

        const className =
          "block rounded-2xl bg-[--color-accent] p-4 text-white transition hover:brightness-110";

        return href ? (
          <Link key={item.id} href={href} className={className}>
            {body}
          </Link>
        ) : (
          <div key={item.id} className={className}>
            {body}
          </div>
        );
      })}
    </div>
  );
}
