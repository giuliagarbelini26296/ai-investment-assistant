import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
export default defineConfig({
  base: '/static/react_portal/',
  plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', port: 5174 },
  build: {
    outDir: '../apps/react_portal/static/react_portal', emptyOutDir: true, chunkSizeWarningLimit: 750,
    // Bibliotecas em arquivos separados: mudam pouco, então o navegador reaproveita o cache entre publicações.
    rolldownOptions: { output: { codeSplitting: { groups: [
      { name: 'vendor-react', test: /node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/ },
      { name: 'vendor-charts', test: /node_modules[\\/](recharts|d3-[^\\/]+|victory-vendor|@reduxjs|redux|immer|reselect|es-toolkit|decimal\.js-light)[\\/]/ },
      { name: 'vendor-motion', test: /node_modules[\\/](framer-motion|motion-dom|motion-utils)[\\/]/ },
    ] } } },
  },
});
