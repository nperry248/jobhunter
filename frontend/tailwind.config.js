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
      // Custom design tokens go here in future phases.
      // e.g., brand colors, custom font sizes, etc.
    },
  },
  plugins: [],
}

