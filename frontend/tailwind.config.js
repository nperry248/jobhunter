/** @type {import('tailwindcss').Config} */
export default {
  // `content` tells Tailwind which files to scan for class names.
  // It removes unused CSS from the production build (tree-shaking for CSS).
  // If you add a new file location, add it here.
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      // Custom font families for our "Terminal Command Center" aesthetic.
      // font-sans → Syne (geometric, distinctive — used everywhere in the UI)
      // font-mono → JetBrains Mono (used for data: scores, timestamps, status tags)
      fontFamily: {
        sans: ['Syne', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'Consolas', 'monospace'],
      },
      // Custom animations for status indicators and step row reveals.
      // ringPulse: a ring that expands outward from a status dot — more
      // sophisticated than Tailwind's built-in animate-pulse (which just fades).
      // fadeInUp: subtle entrance for log rows as they appear.
      keyframes: {
        ringPulse: {
          '0%':   { transform: 'scale(1)',   opacity: '0.6' },
          '100%': { transform: 'scale(2.2)', opacity: '0' },
        },
        fadeInUp: {
          '0%':   { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'ring-pulse': 'ringPulse 1.8s ease-out infinite',
        'fade-in-up': 'fadeInUp 0.25s ease-out forwards',
      },
    },
  },
  plugins: [],
}
