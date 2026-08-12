import { all } from "@/lib/db";
import { currentPerson } from "@/lib/auth";
import { isConfigured } from "@/lib/google/oauth";
import { syncStatus, connectedPeople } from "@/lib/google/calendar";
import { Card, Row, Badge } from "@/components/ui";
import { SyncButton } from "@/components/SyncButton";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  const me = await currentPerson();
  const configured = isConfigured();
  const connected = configured ? connectedPeople() : [];
  const calendars = configured
    ? (syncStatus() as Array<{
        person: string;
        calendar_id: string;
        summary: string;
        enabled: number;
        last_synced: string | null;
        last_result: string | null;
      }>)
    : [];

  const adults = all<{ key: string; name: string; email: string }>(
    `SELECT key, name, email FROM people WHERE role = 'adult' ORDER BY key`,
  );
  const eventCount = all<{ n: number; classified: number }>(
    `SELECT COUNT(*) AS n, SUM(CASE WHEN kind IS NOT NULL THEN 1 ELSE 0 END) AS classified FROM events`,
  )[0];

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="mt-1 text-sm text-[--color-muted]">
          {me ? `Signed in as ${me.name}.` : "Not signed in — sign-in isn't configured yet."}
        </p>
      </header>

      <Card title="Household access">
        {adults.map((p) => {
          const isConnected = connected.some((c) => c.key === p.key);
          return (
            <Row
              key={p.key}
              left={p.name}
              sub={p.email}
              right={
                <Badge tone={isConnected ? "ok" : "medium"}>
                  {isConnected ? "connected" : "not connected"}
                </Badge>
              }
            />
          );
        })}
        {!configured && (
          <p className="mt-3 text-xs text-[--color-muted]">
            Google sign-in isn&rsquo;t configured, so the dashboard is currently open and every
            action is attributed to Ishay. Set the Google environment variables to close this.
          </p>
        )}
      </Card>

      <Card title="Calendar sync">
        {calendars.length === 0 ? (
          <p className="text-sm text-[--color-muted]">
            No calendars yet. They appear once someone signs in with Google.
          </p>
        ) : (
          calendars.map((c) => (
            <Row
              key={`${c.person}-${c.calendar_id}`}
              left={c.summary}
              sub={`${c.person}${c.last_result ? ` · ${c.last_result}` : ""}`}
              right={
                <Badge tone={c.enabled ? (c.last_synced ? "ok" : "medium") : "low"}>
                  {c.enabled ? (c.last_synced ? "synced" : "pending") : "off"}
                </Badge>
              }
            />
          ))
        )}
        <div className="mt-3 flex items-center justify-between gap-3">
          <span className="text-xs text-[--color-muted]">
            {eventCount.n} events stored · {eventCount.classified ?? 0} classified
          </span>
          {me && <SyncButton />}
        </div>
      </Card>

      <Card title="How classification works">
        <p className="text-sm text-[--color-muted]">
          Every synced event is labelled once — pickup, class, on-call, appointment, travel — along
          with which child it concerns and which parent is responsible. That&rsquo;s what makes
          &ldquo;which days am I picking up the kids&rdquo; an exact query rather than a guess. If
          something is labelled wrongly, tell the assistant and it can correct the event.
        </p>
      </Card>

      {me && (
        <form action="/api/auth/logout" method="post">
          <button
            type="submit"
            className="rounded-xl border border-[--color-line] px-3 py-1.5 text-sm"
          >
            Sign out
          </button>
        </form>
      )}
    </div>
  );
}
