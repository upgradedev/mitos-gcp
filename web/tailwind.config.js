/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        ink: {
          50: "#f6f7f9",
          100: "#eceef2",
          200: "#d5d9e2",
          300: "#b0b8c9",
          400: "#8591aa",
          500: "#66738f",
          600: "#515c76",
          700: "#424b60",
          800: "#394051",
          900: "#181b23",
          950: "#0e1015",
        },
      },
      fontFamily: {
        // System stacks only. An external font is a network request the
        // policy refuses, and a self-hosted one is weight for no gain here.
        sans: ["ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
