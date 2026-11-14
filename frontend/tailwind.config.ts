import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          50: "#f6f7fb",
          100: "#eceef6",
          200: "#d9dded",
          300: "#b8c0da",
          400: "#8995bb",
          500: "#6674a2",
          600: "#505b87",
          700: "#414a6e",
          800: "#353c59",
          900: "#22273c",
          950: "#151829",
        },
        brand: {
          50: "#eef4ff",
          100: "#dbe6ff",
          200: "#bed1ff",
          300: "#92b3ff",
          400: "#5f8bff",
          500: "#3a67f5",
          600: "#254bea",
          700: "#1d3ad7",
          800: "#1e32ae",
          900: "#1e3189",
          950: "#172054",
        },
      },
      boxShadow: {
        soft: "0 10px 35px -18px rgba(22, 30, 55, 0.25)",
        lift: "0 18px 50px -24px rgba(22, 30, 55, 0.35)",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};

export default config;
