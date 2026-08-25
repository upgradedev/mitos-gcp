import { Menu, Moon, Sun } from "lucide-react";
import type { Identity, Loaded } from "../api/types";
import type { Theme } from "../ui/theme";

interface Props {
  identity: Loaded<Identity>;
  theme: Theme;
  onToggleTheme: () => void;
  onToggleSidebar: () => void;
}

// Two facts about the running service, taken from /identity and never typed in
// by hand: which build this is, and which model it uses. If the call has not
// answered yet the header says so rather than showing a plausible-looking
// placeholder.
function Fact({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  return (
    <span className="hidden items-center gap-1.5 sm:inline-flex">
      <span className="text-ink-500 dark:text-ink-400">{label}</span>
      <span
        className={
          value === null
            ? "italic text-ink-400 dark:text-ink-500"
            : "font-mono text-ink-800 dark:text-ink-200"
        }
      >
        {value ?? "unknown"}
      </span>
    </span>
  );
}

export default function Header({
  identity,
  theme,
  onToggleTheme,
  onToggleSidebar,
}: Props) {
  const value = identity.status === "ok" ? identity.value : null;

  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b border-ink-200 bg-ink-50/90 px-3 backdrop-blur dark:border-ink-800 dark:bg-ink-950/90 sm:px-5">
      <button
        type="button"
        onClick={onToggleSidebar}
        aria-label="Toggle navigation"
        className="rounded-lg p-2 text-ink-600 hover:bg-ink-200 dark:text-ink-300 dark:hover:bg-ink-800"
      >
        <Menu size={18} aria-hidden="true" />
      </button>

      <a
        href="#/overview"
        className="rounded text-[15px] font-semibold tracking-tight"
      >
        Mitos
      </a>

      <div className="ml-auto flex items-center gap-4 text-xs">
        <Fact label="build" value={value ? value.build_sha : null} />
        <Fact label="model" value={value ? value.model : null} />
        <button
          type="button"
          onClick={onToggleTheme}
          aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"}
          className="rounded-lg p-2 text-ink-600 hover:bg-ink-200 dark:text-ink-300 dark:hover:bg-ink-800"
        >
          {theme === "dark" ? (
            <Sun size={18} aria-hidden="true" />
          ) : (
            <Moon size={18} aria-hidden="true" />
          )}
        </button>
      </div>
    </header>
  );
}
