import { Bell, Command, Menu, Moon, Search, Sun } from "lucide-react";
import type { Identity, Loaded } from "../api/types";
import type { Theme } from "../ui/theme";

interface Props { identity: Loaded<Identity>; theme: Theme; onToggleTheme: () => void; onToggleSidebar: () => void }

export default function Header({ identity, theme, onToggleTheme, onToggleSidebar }: Props) {
  const live = identity.status === "ok";
  return (
    <header className="sticky top-0 z-30 flex h-16 shrink-0 items-center gap-3 border-b border-ink-800 bg-ink-950/90 px-4 backdrop-blur-md md:px-7">
      <button type="button" onClick={onToggleSidebar} aria-label="Toggle navigation" className="icon-button"><Menu size={18} /></button>
      <div className="hidden min-w-0 items-center gap-2 text-xs text-ink-500 sm:flex"><span>Mitos team</span><span>/</span><span className="text-ink-300">Engineering workspace</span></div>
      <button type="button" className="ml-auto hidden w-full max-w-xs items-center gap-2 rounded-lg border border-ink-800 bg-ink-900 px-3 py-2 text-left text-xs text-ink-600 transition hover:border-ink-700 md:flex"><Search size={15} /><span className="flex-1">Search pull requests</span><span className="flex items-center gap-1 rounded border border-ink-700 px-1.5 py-0.5 font-mono text-[10px]"><Command size={10} />K</span></button>
      <span className={`hidden items-center gap-2 rounded-full border px-2.5 py-1 text-[11px] sm:inline-flex ${live ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300" : "border-amber-500/20 bg-amber-500/10 text-amber-300"}`}><span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-emerald-400" : "bg-amber-400"}`} />{live ? "Service live" : "Connecting"}</span>
      <button type="button" className="icon-button" aria-label="Notifications"><Bell size={17} /></button>
      <button type="button" onClick={onToggleTheme} aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"} className="icon-button">{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</button>
    </header>
  );
}
