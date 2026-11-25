import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: '0.0.0.0',
    allowedHosts: ['localhost', '127.0.0.1', 'frontend'],
    proxy: {
      '/api': {
        target: process.env.API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
