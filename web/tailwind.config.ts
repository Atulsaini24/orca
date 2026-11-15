import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        paper: '#FBFBFA', sky: '#E7EBEF', line: '#DADDE1', ink: { DEFAULT: '#0B0C0E', 2: '#41454C', 3: '#71767E' },
        taxi: { DEFAULT: '#FFC20A', deep: '#C79200' }, slate: '#8B929B', signal: { red: '#E5352B', blue: '#2D5BE3', violet: '#6B3CF0' },
      },
      fontFamily: { sans: ['Archivo', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'] },
      maxWidth: { page: '1200px' },
    },
  },
  plugins: [],
} satisfies Config;
