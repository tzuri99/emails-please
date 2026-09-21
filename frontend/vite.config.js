import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // Dev proxy so the browser never needs CORS or a build-time API host.
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    // `server.proxy` does not apply to `vite preview`; the production build
    // needs its own, mirroring what nginx does in the container.
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
})
