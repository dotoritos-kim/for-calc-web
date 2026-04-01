/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#f7f0e4",
        canvas: "#efe4d1",
        ink: "#1e1713",
        ember: "#c36337",
        emberDeep: "#8c3a26",
        moss: "#5d6745",
        fog: "#d5c7b4",
        shell: "#fffaf2",
      },
      fontFamily: {
        sans: ['"Trebuchet MS"', '"Segoe UI Variable Text"', '"Segoe UI"', "sans-serif"],
        display: ['"Iowan Old Style"', '"Palatino Linotype"', '"Book Antiqua"', "serif"],
        mono: ['"Cascadia Code"', '"JetBrains Mono"', '"Consolas"', "monospace"],
      },
      boxShadow: {
        panel: "0 24px 80px rgba(60, 36, 24, 0.14)",
        bevel: "inset 0 1px 0 rgba(255,255,255,0.7), 0 12px 32px rgba(60, 36, 24, 0.10)",
      },
      backgroundImage: {
        "paper-grid":
          "linear-gradient(rgba(96,72,55,0.07) 1px, transparent 1px), linear-gradient(90deg, rgba(96,72,55,0.07) 1px, transparent 1px)",
      },
    },
  },
  plugins: [],
};
