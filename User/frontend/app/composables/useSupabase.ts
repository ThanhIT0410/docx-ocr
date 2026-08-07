import { createClient, type SupabaseClient } from '@supabase/supabase-js'

let client: SupabaseClient | undefined

/**
 * Singleton Supabase client using the `anon` key. All access control is
 * enforced by RLS policies (see supabase/schema.sql) — this key is safe to
 * ship inside the Electron bundle.
 */
export function useSupabase(): SupabaseClient {
  if (client) return client

  const config = useRuntimeConfig()
  const url = config.public.supabaseUrl
  const key = config.public.supabaseAnonKey

  if (!url || !key) {
    // Loud, actionable failure instead of a confusing runtime crash deep in
    // a query — this is the #1 missing-env mistake on first run.
    // eslint-disable-next-line no-console
    console.error(
      '[useSupabase] Thiếu NUXT_PUBLIC_SUPABASE_URL / NUXT_PUBLIC_SUPABASE_ANON_KEY. ' +
      'Kiểm tra file .env (xem .env.example).'
    )
  }

  client = createClient(url, key, {
    auth: { persistSession: false }
  })
  return client
}

export function storageBucket(): string {
  return useRuntimeConfig().public.supabaseStorageBucket
}
