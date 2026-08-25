import { useEffect, useState } from "react";

// Five routes, hash based. A router library would add weight for one thing this
// build needs: knowing which of five panes to render. `form-action 'self'` and
// `base-uri 'none'` are untouched by hash navigation, and a hash route needs no
// server rewrite rule, which matters because the service serving these files
// answers API paths on the same origin.
//
// One consequence worth knowing before adding a link: the hash is the route, so
// a same-page anchor such as href="#audit" would be read as an unknown route
// and land the reader on the overview. Views jump between sections with
// scrolling or with a real route, never with an anchor.

export type RouteId =
  | "overview"
  | "run"
  | "boundary"
  | "thread"
  | "repositories";

export const ROUTES: RouteId[] = [
  "overview",
  "run",
  "boundary",
  "thread",
  "repositories",
];

function parse(): RouteId {
  const raw = window.location.hash.replace(/^#\/?/, "");
  return (ROUTES as string[]).includes(raw) ? (raw as RouteId) : "overview";
}

export function useRoute(): [RouteId, (id: RouteId) => void] {
  const [route, setRoute] = useState<RouteId>(parse);

  useEffect(() => {
    const onChange = () => setRoute(parse());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const go = (id: RouteId) => {
    window.location.hash = `#/${id}`;
  };

  return [route, go];
}
