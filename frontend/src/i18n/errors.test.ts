import { afterEach, describe, expect, it } from 'vitest'

import { ApiError, NetworkError } from '@/api/client'

import { errorCodeMessage, errorMessage, issueMessage } from './errors'
import { setLocale } from './messages'

afterEach(() => setLocale('en'))

describe('coded error translations', () => {
  it('ignores diagnostic backend text and translates an invalid token', () => {
    const error = new ApiError(401, {
      code: 'AUTH_INVALID_CREDENTIALS',
      message: '<script>server text must never be rendered</script>',
    })

    expect(errorMessage(error)).toBe('The Paperless-ngx token is invalid.')
    expect(errorMessage(error)).not.toContain('server text')
  })

  it('translates Paperless permissions, conflicts, and rollback failures in French', () => {
    setLocale('fr')

    expect(errorCodeMessage('PAPERLESS_FORBIDDEN')).toBe(
      "Paperless-ngx n'autorise pas cette action.",
    )
    expect(errorCodeMessage('CONFLICT')).toContain('Les données ont changé')
    expect(issueMessage({ code: 'ROLLBACK_CONFLICT' })).toContain('Rien ne sera restauré')
  })

  it('translates durable bulk-job failures and keeps the upstream status structured', () => {
    expect(issueMessage('READBACK_DISAGREES', 502)).toBe(
      'The stored document differs from the confirmed write response. (HTTP 502)',
    )
  })

  it('uses structured issue codes nested in API parameters', () => {
    const error = new ApiError(422, {
      code: 'VALIDATION_ERROR',
      message: 'backend prose',
      params: { issues: [{ code: 'UNKNOWN_FIELD', message: 'backend prose' }] },
    })

    expect(errorMessage(error)).toBe('This field is no longer available.')
  })

  it('has localized fallbacks for transport and unknown server codes', () => {
    setLocale('fr')

    expect(errorMessage(new NetworkError(new TypeError('offline')))).toBe(
      'Impossible de joindre le serveur PaperWrench.',
    )
    expect(errorCodeMessage('FUTURE_ERROR')).toBe(
      "Une erreur inattendue s'est produite (code : FUTURE_ERROR).",
    )
  })
})
