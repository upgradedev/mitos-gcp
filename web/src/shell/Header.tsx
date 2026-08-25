import { Github, LogOut, Menu, Moon, Sun } from "lucide-react";
import type { Identity, Loaded, SessionStatus } from "../api/types";
import type { Theme } from "../ui/theme";

interface Props { identity: Loaded<Identity>; session: Loaded<SessionStatus>; theme: Theme; onToggleTheme: () => void; onToggleSidebar: () => void }

export default function Header({ identity, session, theme, onToggleTheme, onToggleSidebar }: Props) {
  const live = identity.status === "ok";
  return (
    <header className="sticky top-0 z-30 flex h-16 shrink-0 items-center gap-3 border-b border-ink-800 bg-ink-950/90 px-4 backdrop-blur-md md:px-7">
      <button type="button" onClick={onToggleSidebar} aria-label="Toggle navigation" className="icon-button"><Menu size={18} /></button>
      <div className="hidden min-w-0 items-center gap-2 text-xs text-ink-500 sm:flex"><span>Current deployment</span><span>/</span><span className="text-ink-300">{identity.status === "ok" ? identity.value.project || "Unassigned project" : "Connecting"}</span></div>
      <div className="ml-auto" />
      <span className={`hidden items-center gap-2 rounded-full border px-2.5 py-1 text-[11px] sm:inline-flex ${live ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300" : "border-amber-500/20 bg-amber-500/10 text-amber-300"}`}><span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-emerald-400" : "bg-amber-400"}`} />{live ? "Service live" : "Connecting"}</span>
      {session.status === "ok" && session.value.authenticated && session.value.user ? <div className="flex items-center gap-2"><img src={session.value.user.avatar_url} alt="" className="h-7 w-7 rounded-full border border-ink-700" /><span className="hidden text-xs font-medium text-ink-300 lg:inline">{session.value.user.login}</span><form method="post" action="/api/session/logout"><button type="submit" className="icon-button" aria-label="Sign out"><LogOut size={16} /></button></form></div> : <a href="/github/auth/login" className="button-secondary"><Github size={15} /> Sign in</a>}
      <button type="button" onClick={onToggleTheme} aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"} className="icon-button">{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</button>
    </header>
  );
}
