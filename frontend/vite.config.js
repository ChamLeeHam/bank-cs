import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/static/react/' : '/',
  build: {
    outDir: '../src/web/react',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        customer: fileURLToPath(new URL('./index.html', import.meta.url)),
        agent: fileURLToPath(new URL('./agent.html', import.meta.url)),
      },
    },
  },
  server: { host: '127.0.0.1', proxy: { '/api': 'http://127.0.0.1:8002' } },
}));
