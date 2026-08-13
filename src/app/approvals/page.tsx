import { listApprovals } from "@/lib/approvals";
import { ApprovalList } from "@/components/ApprovalList";
import { DraftList } from "@/components/DraftList";
import { listDrafts } from "@/lib/drafts";

export const dynamic = "force-dynamic";

export default function ApprovalsPage() {
  const pending = listApprovals("pending");
  const drafts = listDrafts("draft");
  const recent = [...listApprovals("done"), ...listApprovals("rejected")].slice(0, 10);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">Approvals</h1>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          Anything that spends money, goes to someone outside the house, or can&rsquo;t be undone
          waits here. Nothing below has happened yet.
        </p>
      </header>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold tracking-wide text-[var(--color-muted)] uppercase">
          Drafts to send yourself
        </h2>
        <p className="text-sm text-[var(--color-muted)]">
          This system never sends mail. These are written and ready — open one in your own
          mail app and press send.
        </p>
        <DraftList
          drafts={drafts.map((d) => ({
            id: d.id,
            to_addr: d.to_addr,
            cc_addr: d.cc_addr,
            subject: d.subject,
            body: d.body,
            composeUrl: d.composeUrl,
            created_at: d.created_at,
          }))}
        />
      </section>

      <ApprovalList
        approvals={pending.map((a) => ({
          id: a.id,
          kind: a.kind,
          title: a.title,
          summary: a.summary,
          risk: a.risk,
          payload: a.payload,
        }))}
      />

      {recent.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold tracking-wide text-[var(--color-muted)] uppercase">
            Recently decided
          </h2>
          <div className="rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-4">
            {recent.map((a) => (
              <div
                key={a.id}
                className="flex justify-between gap-3 border-b border-[var(--color-line)] py-2 text-sm last:border-0"
              >
                <span dir="auto">{a.title}</span>
                <span className="shrink-0 text-xs text-[var(--color-muted)]">
                  {a.status} · {a.decided_by}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
