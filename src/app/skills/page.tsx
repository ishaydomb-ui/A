import { all } from "@/lib/db";
import { listAutomations } from "@/lib/automations";
import { Badge } from "@/components/ui";

export const dynamic = "force-dynamic";

export default function SkillsPage() {
  const skills = all<{
    key: string;
    name: string;
    description: string;
    body: string;
    version: number;
    autonomy: string;
    enabled: number;
  }>(`SELECT key, name, description, body, version, autonomy, enabled FROM skills ORDER BY key`);

  const automations = listAutomations() as Array<{
    id: number;
    name: string;
    description: string;
    trigger_type: string;
    action_type: string;
    enabled: number;
    last_run_at: string | null;
    last_result: string | null;
  }>;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Skills</h1>
        <p className="mt-1 text-sm text-[--color-muted]">
          A skill is a fixed procedure. When a request matches one, the assistant follows it
          step by step instead of improvising — so the same job gets done the same way, whoever
          asks. Editing a skill here changes behaviour immediately.
        </p>
      </header>

      <div className="space-y-3">
        {skills.map((s) => (
          <details key={s.key} className="rounded-2xl border border-[--color-line] bg-[--color-surface] p-4">
            <summary className="cursor-pointer list-none">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <span className="font-medium">{s.name}</span>
                  <code className="ms-2 text-xs text-[--color-muted]">{s.key}</code>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone={s.autonomy === "auto" ? "ok" : "medium"}>{s.autonomy}</Badge>
                  <span className="text-xs text-[--color-muted]">v{s.version}</span>
                </div>
              </div>
              <p className="mt-1 text-xs text-[--color-muted]">{s.description}</p>
            </summary>
            <pre className="mt-3 overflow-x-auto border-t border-[--color-line] pt-3 text-xs whitespace-pre-wrap">
              {s.body}
            </pre>
          </details>
        ))}
      </div>

      <section>
        <h2 className="mb-2 text-lg font-semibold">Automations</h2>
        <p className="mb-3 text-sm text-[--color-muted]">
          When this happens → run that skill. Rules live in the database, so adding one never
          needs a deploy.
        </p>
        <div className="rounded-2xl border border-[--color-line] bg-[--color-surface] p-4">
          {automations.map((a) => (
            <div key={a.id} className="border-b border-[--color-line] py-2.5 last:border-0">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-medium">{a.name}</span>
                <Badge tone={a.enabled ? "ok" : "low"}>{a.enabled ? "on" : "off"}</Badge>
              </div>
              <p className="mt-0.5 text-xs text-[--color-muted]">{a.description}</p>
              <p className="mt-1 text-[11px] text-[--color-muted]">
                <code>{a.trigger_type}</code> → <code>{a.action_type}</code>
                {a.last_run_at && ` · last ran ${a.last_run_at}`}
              </p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
