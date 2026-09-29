import { createContext, useContext } from 'react'

import type { AuthSession } from '@/api/types'
import type { Locale } from '@/i18n/messages'

export interface AuthContextValue {
  session: AuthSession
  locale: Locale
  changeLocale: (locale: Locale) => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthGate')
  return value
}
