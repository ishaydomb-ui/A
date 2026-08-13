import { roomMessages } from "@/lib/room";
import { actorKey } from "@/lib/auth";
import { Room } from "@/components/Room";

export const dynamic = "force-dynamic";

export default async function TogetherPage() {
  const initial = roomMessages();
  const me = await actorKey();

  return (
    <div className="flex h-[calc(100dvh-8rem)] flex-col sm:h-[calc(100dvh-4rem)]">
      <header className="mb-2">
        <h1 className="text-2xl font-semibold">Think together</h1>
        <p className="mt-0.5 text-sm text-[var(--color-muted)]">
          One thread you both see. The assistant reads everything but only answers when you
          address it — say <strong>@ai</strong>, or start with an instruction like
          &ldquo;add&rdquo; or &ldquo;remind&rdquo;.
        </p>
      </header>

      <Room
        initial={initial.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          speaker: m.speaker,
          speaker_key: m.speaker_key,
          color: m.color,
          created_at: m.created_at,
        }))}
        me={me}
      />
    </div>
  );
}
