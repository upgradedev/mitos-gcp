import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const KEY = "mitos-theme";

function preferred(): Theme {
  const saved = localStorage.getItem(KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(preferred);

  useEffect(() => {
    // Tailwind is configured with darkMode: "class", so the class on <html> is
    // the single switch for the whole page.
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem(KEY, theme);
  }, [theme]);

  const toggle = useCallback(
    () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    []
  );

  return [theme, toggle];
}
