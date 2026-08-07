// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: true },

  // Desktop app packaged with Electron, same reasoning as User/frontend:
  // ships as a static SPA that Electron's main process serves over local
  // http:// (see electron/staticServer.js).
  ssr: false,

  modules: ['@pinia/nuxt'],

  // Every template here refers to subfolder components by their bare
  // filename (<PrimarySidebar>, <UsageBar>...), never Nuxt's default
  // folder-prefixed form (<LayoutPrimarySidebar>, <SharedUsageBar>...) —
  // pathPrefix: false matches that instead of renaming every usage site.
  // Same fix as User/frontend/nuxt.config.ts (same bug, same cause).
  components: [{ path: '~/components', pathPrefix: false }],

  css: ['~/assets/css/main.css'],

  app: {
    head: {
      title: 'DocxOCR Processor',
      htmlAttrs: { lang: 'vi' },
      link: [
        { rel: 'preconnect', href: 'https://fonts.googleapis.com' },
        {
          rel: 'stylesheet',
          href: 'https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap'
        }
      ]
    }
  },

  runtimeConfig: {
    public: {
      // Fallback only for `nuxt dev` in a plain browser tab (no Electron) —
      // see .env.example. The packaged/dev Electron app ignores these
      // entirely: electron/sidecar.js spawns the admin API itself and
      // injects its real (randomly-chosen-port) base URL + freshly-generated
      // API keys at runtime via preload.js (app/composables/useProcessorApi.ts),
      // since baking a fixed URL/keys in at build time can't work once the
      // backend's port and keys are chosen fresh on every launch.
      processorApiUrl: 'http://127.0.0.1:8800',
      processorApiKey: '',
      processorAdminApiKey: ''
    }
  },

  typescript: {
    strict: true,
    typeCheck: false
  },

  vite: {
    server: {
      // Allow the Electron main process's wait-on check to hit this reliably.
      strictPort: true
    }
  }
})
