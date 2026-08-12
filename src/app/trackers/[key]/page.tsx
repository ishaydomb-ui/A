import { notFound } from "next/navigation";
import { getTracker, queryItems } from "@/lib/trackers";
import { TrackerTable } from "@/components/TrackerTable";

export const dynamic = "force-dynamic";

export default async function TrackerPage({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const tracker = getTracker(key);
  if (!tracker) notFound();

  const items = queryItems(key, { status: "any" });

  return (
    <div className="space-y-4">
      <header>
        <h1 className="flex items-center gap-2 text-2xl font-semibold">
          <span>{tracker.icon}</span>
          {tracker.name}
        </h1>
        {tracker.description && (
          <p className="mt-1 text-sm text-[--color-muted]">{tracker.description}</p>
        )}
      </header>

      <TrackerTable
        trackerKey={tracker.key}
        fields={tracker.fields}
        items={items.map((i) => ({
          id: i.id,
          status: i.status,
          expires_at: i.expires_at,
          data: i.data,
        }))}
      />
    </div>
  );
}
