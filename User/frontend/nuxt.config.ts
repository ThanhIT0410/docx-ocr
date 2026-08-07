// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: true },

  // Desktop app packaged with Electron: no server-side rendering, ships as a
  // static SPA that Electron's main process serves over local http://.
  ssr: false,

  modules: ['@pinia/nuxt'],

  // Every template in this app (app.vue, pages/*, components/*) refers to
  // subfolder components by their bare filename (<Dropzone>, <PageGrid>,
  // <PrimarySidebar>...), never Nuxt's default folder-prefixed form
  // (<UploadDropzone>, <SharedPageGrid>...) — pathPrefix: false matches that
  // throughout instead of renaming every usage site. No name collisions
  // across components/{docs,layout,shared,upload}/ (verified: 12 files, 12
  // distinct names) so this is safe app-wide.
  components: [{ path: '~/components', pathPrefix: false }],

  css: ['~/assets/css/main.css'],

  app: {
    head: {
      title: 'DocxOCR',
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
      supabaseUrl: '',
      supabaseAnonKey: '',
      supabaseStorageBucket: 'exam-pages',
      sidecarUrl: 'http://127.0.0.1:8756'
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
