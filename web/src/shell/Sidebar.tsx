import {
  Activity,
  FolderGit2,
  GitPullRequest,
  LayoutDashboard,
  Settings,
  ShieldCheck,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { RouteId } from "../ui/router";

interface Props { route: RouteId; onNavigate: (id: RouteId) => void; open: boolean }
interface Item { id: RouteId; label: string; icon: LucideIcon }

const ITEMS: Item[] = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "pull-requests", label: "Pull requests", icon: GitPullRequest },
  { id: "repositories", label: "Repositories", icon: FolderGit2 },
  { id: "activity", label: "Activity & audit", icon: Activity },
];

export default function Sidebar({ route, onNavigate, open }: Props) {
  return (
    <aside className={`${open ? "w-64" : "w-0"} sticky top-0 h-screen shrink-0 overflow-hidden border-r border-ink-800 bg-[#0b0d12] transition-[width]`}>
      {!open ? null : <div className="flex h-full w-64 flex-col">
        <div className="flex h-16 items-center gap-3 border-b border-ink-800 px-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-400 text-ink-950"><ShieldCheck size={18} strokeWidth={2.5} /></div>
          <div><p className="text-sm font-semibold tracking-tight text-ink-50">Mitos</p><p className="text-[11px] text-ink-600">Change governance</p></div>
        </div>
        <div className="px-3 py-5">
          <p className="px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-600">Workspace</p>
          <nav aria-label="Primary" className="mt-3 flex flex-col gap-1">{ITEMS.map((item) => { const Icon = item.icon; const active = route === item.id; return <a key={item.id} href={`#/${item.id}`} onClick={(event) => { event.preventDefault(); onNavigate(item.id); }} aria-current={active ? "page" : undefined} className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition ${active ? "bg-ink-800 text-ink-50" : "text-ink-400 hover:bg-ink-900 hover:text-ink-200"}`}><Icon size={17} className={active ? "text-cyan-400" : "text-ink-600"} />{item.label}</a>; })}</nav>
        </div>
        <div className="mt-auto border-t border-ink-800 p-3"><a href="#/settings" onClick={(event) => { event.preventDefault(); onNavigate("settings"); }} className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm ${route === "settings" ? "bg-ink-800 text-ink-50" : "text-ink-400 hover:bg-ink-900"}`}><Settings size={17} /> Settings</a><div className="mt-3 rounded-lg border border-ink-800 px-3 py-3"><p className="text-xs font-medium text-ink-300">Deployment mode</p><p className="mt-1 text-[11px] leading-4 text-ink-600">Team accounts are not configured yet.</p></div></div>
      </div>}
    </aside>
  );
}
