import {
  FolderGit2,
  GitPullRequestArrow,
  LayoutDashboard,
  ListTree,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { RouteId } from "../ui/router";

interface Props {
  route: RouteId;
  onNavigate: (id: RouteId) => void;
  open: boolean;
}

interface Item {
  id: RouteId;
  label: string;
  icon: LucideIcon;
  // Said in the navigation itself, because a stranger reading a list of four
  // words cannot tell what any of them will do.
  hint: string;
}

const ITEMS: Item[] = [
  {
    id: "overview",
    label: "Start here",
    icon: LayoutDashboard,
    hint: "What this is and how to run it",
  },
  {
    id: "run",
    label: "Run a change",
    icon: GitPullRequestArrow,
    hint: "Watch the fleet work, live",
  },
  {
    id: "boundary",
    label: "The boundary",
    icon: Users,
    hint: "Who can write, and who cannot",
  },
  { id: "thread", label: "The thread", icon: ListTree, hint: "Every recorded event" },
  {
    id: "repositories",
    label: "Repositories",
    icon: FolderGit2,
    hint: "Connect one, audit one, see what each has run",
  },
];

export default function Sidebar({ route, onNavigate, open }: Props) {
  return (
    <nav
      aria-label="Sections"
      // Collapsing hides it from the tab order too. A control that is invisible
      // but still focusable is a trap for anyone navigating by keyboard.
      className={`${
        open ? "w-60" : "w-0"
      } shrink-0 overflow-hidden border-r border-ink-200 bg-white transition-[width] dark:border-ink-800 dark:bg-ink-900`}
    >
      {/* Links are unmounted rather than merely hidden when the sidebar is
          collapsed. A control that is invisible but still focusable is a trap
          for anyone travelling by keyboard. */}
      {!open ? null : (
      <ul className="w-60 space-y-1 p-3">
        {ITEMS.map((item) => {
          const active = item.id === route;
          const Icon = item.icon;
          return (
            <li key={item.id}>
              <a
                href={`#/${item.id}`}
                aria-current={active ? "page" : undefined}
                onClick={(event) => {
                  event.preventDefault();
                  onNavigate(item.id);
                }}
                className={`flex items-start gap-3 rounded-lg px-3 py-2.5 ${
                  active
                    ? "bg-sky-50 text-sky-900 dark:bg-sky-950 dark:text-sky-100"
                    : "text-ink-700 hover:bg-ink-100 dark:text-ink-300 dark:hover:bg-ink-800"
                }`}
              >
                <Icon size={17} className="mt-0.5 shrink-0" aria-hidden="true" />
                <span>
                  <span className="block text-sm font-medium">{item.label}</span>
                  <span className="block text-xs text-ink-500 dark:text-ink-400">
                    {item.hint}
                  </span>
                </span>
              </a>
            </li>
          );
        })}
      </ul>
      )}
    </nav>
  );
}
