import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/admin/assets/',
  plugins: [vue()],
  build: {
    outDir: '../voice_platform/admin_ui',
    emptyOutDir: true,
    license: { fileName: 'THIRD_PARTY_LICENSES.md' },
    modulePreload: { polyfill: false },
    rollupOptions: {
      output: {
        entryFileNames: 'admin.js',
        assetFileNames: 'admin.[ext]',
      },
    },
  },
})
