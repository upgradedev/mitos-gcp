import { useEffect, useState } from "react";
import { getConfig, getIdentity, getThread, load } from "./api/client";
import type { Config, Identity, Loaded, Thread } from "./api/types";
import Hero from "./hero/Hero";
import { useRun } from "./hero/useRun";
import Run from "./routes/Run";
// The fleet and thread screens are written by another agent against the same
// typed fetch layer in ./api. They fetch what they need themselves, so the
// router hands them nothing.
import BoundaryView from "./views/BoundaryView";
import RepositoriesView from "./views/RepositoriesView";
import ThreadView from "./views/ThreadView";
import Header from "./shell/Header";
import Sidebar from "./shell/Sidebar";
import { useRoute } from "./ui/router";
import { useTheme } from "./ui/theme";

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const [route, go] = useRoute();
  const [sidebarOpen, setSidebarOpen] = useState(
    () => window.innerWidth >= 1024
  );

  const [identity, setIdentity] = useState<Loaded<Identity>>({
    status: "loading",
  });
  const [config, setConfig] = useState<Loaded<Config>>({ status: "loading" });
  const [thread, setThread] = useState<Loaded<Thread>>({ status: "loading" });

  // The run controller lives here so that navigating between the overview and
  // the run screen does not abandon a stream that is halfway through.
  const run = useRun();

  useEffect(() => {
    load(getIdentity).then(setIdentity);
    load(getConfig).then(setConfig);
    load(() => getThread(120)).then(setThread);
  }, []);

  // A finished run has appended to the thread, so the tile showing the most
  // recent change is stale until it is refetched.
  const runState = run.state;
  useEffect(() => {
    if (runState === "card-produced" || runState === "written" || runState === "halted") {
      load(() => getThread(120)).then(setThread);
    }
  }, [runState]);

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-30 focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm dark:focus:bg-ink-900"
      >
        Skip to content
      </a>

      <Header
        identity={identity}
        theme={theme}
        onToggleTheme={toggleTheme}
        onToggleSidebar={() => setSidebarOpen((v) => !v)}
      />

      <div className="flex min-h-0 flex-1">
        <Sidebar route={route} onNavigate={go} open={sidebarOpen} />
        <main id="main" className="min-w-0 flex-1">
          {route === "overview" && (
            <Hero run={run} thread={thread} identity={identity} />
          )}
          {route === "run" && <Run run={run} config={config} />}
          {route === "boundary" && <BoundaryView />}
          {route === "thread" && <ThreadView />}
          {route === "repositories" && <RepositoriesView />}
        </main>
      </div>
    </div>
  );
}
