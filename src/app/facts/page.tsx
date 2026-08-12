import Link from "next/link";
import { recallFacts, expiringFacts } from "@/lib/facts";
import { FactsList } from "@/components/FactsList";
import { Card, Row, Badge, formatDay } from "@/components/ui";

export const dynamic = "force-dynamic";

/**
 * Deliberately absent from the main navigation. The intended way to use this is
 * to ask the assistant; this page exists so nothing is trapped somewhere only
 * the agent can reach, and so you can see what it has been told.
 */
export default function FactsPage() {
  const facts = recallFacts({ limit: 500 });
  const renewals = expiringFacts(120);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">What we know</h1>
        <p className="mt-1 text-sm text-[--color-muted]">
          ID numbers, door codes, where things live, renewal dates, and when things last
          happened. Easiest way to add one is just to tell the assistant — it stores these as
          you mention them.
        </p>
      </header>

      {renewals.length > 0 && (
        <Card title="Coming up for renewal">
          {renewals.map((f) => (
            <Row
              key={f.id}
              left={`${f.subject} — ${f.label}`}
              right={<Badge tone="medium">{formatDay(f.valid_until)}</Badge>}
            />
          ))}
        </Card>
      )}

      <FactsList
        facts={facts.map((f) => ({
          id: f.id,
          subject: f.subject,
          label: f.label,
          value: f.value,
          sensitive: f.sensitive,
          category: f.category,
          occurred_on: f.occurred_on,
          valid_until: f.valid_until,
        }))}
      />

      <p className="text-xs text-[--color-muted]">
        Sensitive values are encrypted on disk and are kept out of digests and emails.{" "}
        <Link href="/settings" className="text-[--color-accent] hover:underline">
          Settings
        </Link>
      </p>
    </div>
  );
}
