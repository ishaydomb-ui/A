import { isConfigured } from "@/lib/google/oauth";

export const dynamic = "force-dynamic";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; next?: string }>;
}) {
  const { error, next } = await searchParams;
  const configured = isConfigured();

  return (
    <div className="flex min-h-[70dvh] items-center justify-center">
      <div className="w-full max-w-sm space-y-5 rounded-2xl border border-[--color-line] bg-[--color-surface] p-7 text-center">
        <div>
          <h1 className="text-2xl font-semibold">Beitenu</h1>
          <p className="mt-1 text-sm text-[--color-muted]">Our household, in one place.</p>
        </div>

        {error && (
          <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
            {error}
          </p>
        )}

        {configured ? (
          <>
            <a
              href={`/api/auth/google/start${next ? `?next=${encodeURIComponent(next)}` : ""}`}
              className="block w-full rounded-xl bg-[--color-accent] px-4 py-2.5 text-sm font-medium text-white"
            >
              Continue with Google
            </a>
            <p className="text-xs text-[--color-muted]">
              Signing in also connects your calendar. Only Ishay and Liran can get in.
            </p>
          </>
        ) : (
          <div className="space-y-2 text-start text-sm text-[--color-muted]">
            <p className="font-medium text-[--color-ink]">Google sign-in isn&rsquo;t set up yet.</p>
            <p>
              Add <code className="text-xs">GOOGLE_CLIENT_ID</code>,{" "}
              <code className="text-xs">GOOGLE_CLIENT_SECRET</code> and{" "}
              <code className="text-xs">GOOGLE_REDIRECT_URI</code> to your environment, then reload.
            </p>
            <p>Until then the dashboard is open and every action is attributed to Ishay.</p>
          </div>
        )}
      </div>
    </div>
  );
}
