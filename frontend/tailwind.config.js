/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Dark background colors
        'navy-950': '#0a0e27',
        'navy-900': '#141b34',
        'navy-800': '#1e2849',
        'navy-700': '#283764',
        'navy-600': '#3d4f7c',
        
        // Neon accent colors
        'neon-cyan': '#00d9ff',
        'neon-purple': '#9d4edd',
        'neon-yellow': '#ffbe0b',
        'neon-green': '#06ffa5',
        'neon-pink': '#ff006e',
        'neon-blue': '#4cc9f0',
        
        // Lightning theme colors
        'electric-blue': '#0066ff',
        'electric-purple': '#7209b7',
      },
      boxShadow: {
        'neon-cyan': '0 0 20px rgba(0, 217, 255, 0.5)',
        'neon-cyan-lg': '0 0 40px rgba(0, 217, 255, 0.6)',
        'neon-purple': '0 0 20px rgba(157, 78, 221, 0.5)',
        'neon-purple-lg': '0 0 40px rgba(157, 78, 221, 0.6)',
        'neon-green': '0 0 20px rgba(6, 255, 165, 0.5)',
        'neon-pink': '0 0 20px rgba(255, 0, 110, 0.5)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'glow': 'glow 2s ease-in-out infinite',
      },
      keyframes: {
        glow: {
          '0%, 100%': { opacity: 1 },
          '50%': { opacity: 0.6 },
        }
      }
    },
  },
  plugins: [],
}
