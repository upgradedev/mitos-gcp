import { useEffect, useState } from "react";
import { getConfig, getGitHubAppStatus, getIdentity, getSession, getThread, load } from "./api/client";
import type { Config, GitHubAppStatus, Identity, Loaded, SessionStatus, Thread } from "./api/types";
import Header from "./shell/Header";
import Sidebar from "./shell/Sidebar";
import { useRoute } from "./ui/router";
import { useTheme } from "./ui/theme";
import {
  ActivityView,
  DashboardView,
  PullRequestsView,
  RepositoriesProductView,
  SettingsView,
} from "./views/ProductViews";

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const [route, go] = useRoute();
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 1024);
  const [identity, setIdentity] = useState<Loaded<Identity>>({ status: "loading" });
  const [config, setConfig] = useState<Loaded<Config>>({ status: "loading" });
  const [githubApp, setGitHubApp] = useState<Loaded<GitHubAppStatus>>({ status: "loading" });
  const [session, setSession] = useState<Loaded<SessionStatus>>({ status: "loading" });
  const [thread, setThread] = useState<Loaded<Thread>>({ status: "loading" });

  useEffect(() => {
    load(getIdentity).then(setIdentity);
    load(getConfig).then(setConfig);
    load(getGitHubAppStatus).then(setGitHubApp);
    load(getSession).then(setSession);
    load(() => getThread(500)).then(setThread);
  }, []);

  const data = { identity, config, githubApp, session, thread, onNavigate: go };

  return (
    <div className="flex min-h-screen bg-ink-950 text-ink-100">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-cyan-400 focus:px-3 focus:py-2 focus:text-sm focus:text-ink-950">Skip to content</a>
      <Sidebar route={route} onNavigate={go} open={sidebarOpen} session={session} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header identity={identity} session={session} theme={theme} onToggleTheme={toggleTheme} onToggleSidebar={() => setSidebarOpen((value) => !value)} />
        <main id="main" className="min-w-0 flex-1">
          {route === "dashboard" && <DashboardView {...data} />}
          {route === "pull-requests" && <PullRequestsView {...data} />}
          {route === "repositories" && <RepositoriesProductView {...data} />}
          {route === "activity" && <ActivityView {...data} />}
          {route === "settings" && <SettingsView {...data} />}
        </main>
      </div>
    </div>
  );
}
