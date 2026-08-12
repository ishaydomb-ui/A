import type { Metadata, Viewport } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Beitenu",
  description: "Our household, in one place",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#2563eb",
};

const nav = [
  { href: "/", label: "Today", icon: "🏠" },
  { href: "/chat", label: "Ask", icon: "💬" },
  { href: "/trackers", label: "Trackers", icon: "🎟️" },
  { href: "/food", label: "Food", icon: "🛒" },
  { href: "/approvals", label: "Approvals", icon: "✅" },
  { href: "/skills", label: "Skills", icon: "📘" },
];

// Reachable from the desktop rail; kept off the phone tab bar, which only has
// room for the screens used mid-errand.
const secondaryNav = [{ href: "/settings", label: "Settings", icon: "⚙️" }];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-dvh pb-20 sm:pb-0">
        <div className="mx-auto flex max-w-5xl flex-col sm:flex-row">
          {/* Desktop rail */}
          <aside className="hidden shrink-0 border-e border-[--color-line] p-4 sm:block sm:w-48">
            <div className="mb-6 px-2 text-lg font-semibold">Beitenu</div>
            <nav className="flex flex-col gap-1">
              {[...nav, ...secondaryNav].map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="rounded-lg px-3 py-2 text-sm hover:bg-black/5 dark:hover:bg-white/5"
                >
                  <span className="me-2">{item.icon}</span>
                  {item.label}
                </Link>
              ))}
            </nav>
          </aside>

          <main className="min-w-0 flex-1 p-4 sm:p-6">{children}</main>
        </div>

        {/* Phone tab bar - this is mostly used one-handed, mid-errand. */}
        <nav className="fixed inset-x-0 bottom-0 z-10 flex border-t border-[--color-line] bg-[--color-surface] sm:hidden">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]"
            >
              <span className="text-lg">{item.icon}</span>
              {item.label}
            </Link>
          ))}
        </nav>
      </body>
    </html>
  );
}
