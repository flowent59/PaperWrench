import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch, authApi, clearBrowserSession, NetworkError, systemApi } from './client'

function mockFetch(response: Response | Error) {
  const spy = vi.fn(
    (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> =>
      response instanceof Error
        ? Promise.reject(response)
        : Promise.resolve(response.clone()),
  )
  vi.stubGlobal('fetch', spy)
  return spy
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  clearBrowserSession()
  vi.unstubAllGlobals()
})

describe('apiFetch', () => {
  it('prefixes requests with the versioned API path', async () => {
    const spy = mockFetch(jsonResponse({ status: 'ok' }))

    await apiFetch('/system/health')

    expect(spy).toHaveBeenCalledOnce()
    expect(spy.mock.calls[0]?.[0]).toBe('/api/v1/system/health')
  })

  it('parses the uniform error envelope into an ApiError', async () => {
    mockFetch(
      jsonResponse(
        { error: { code: 'PAPERLESS_UNAUTHORIZED', message: 'Bad token' } },
        401,
      ),
    )

    await expect(apiFetch('/system/info')).rejects.toMatchObject({
      name: 'ApiError',
      code: 'PAPERLESS_UNAUTHORIZED',
      status: 401,
      message: 'Bad token',
    })
  })

  it('falls back to a status-based error when the body is not our envelope', async () => {
    mockFetch(new Response('<html>502</html>', { status: 502 }))

    const error = await apiFetch('/system/info').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(502)
  })

  it('wraps transport failures in a NetworkError', async () => {
    mockFetch(new TypeError('Failed to fetch'))

    await expect(apiFetch('/system/health')).rejects.toBeInstanceOf(NetworkError)
  })

  it('sends only the opaque session cookie, never an authorization header', async () => {
    const spy = mockFetch(jsonResponse({}))

    await apiFetch('/system/health')

    const init = spy.mock.calls[0]?.[1] as RequestInit | undefined
    const headers = JSON.stringify(init?.headers ?? {}).toLowerCase()
    expect(headers).not.toContain('authorization')
    expect(headers).not.toContain('token')
    expect(init?.credentials).toBe('include')
  })

  it('keeps the Paperless token in the login body and uses CSRF afterwards', async () => {
    const spy = mockFetch(jsonResponse({
      user_id: 1, username: 'alice', display_name: 'Alice',
      expires_at: '2030-01-01T00:00:00Z', csrf_token: 'csrf-value', locale: 'fr',
    }))

    await authApi.login('paperless-secret', 'fr')
    await apiFetch('/collections', { method: 'POST', body: '{}' })

    const loginInit = spy.mock.calls[0]?.[1] as RequestInit
    const requestInit = spy.mock.calls[1]?.[1] as RequestInit
    expect(loginInit.body).toBe(JSON.stringify({ token: 'paperless-secret', locale: 'fr' }))
    expect(JSON.stringify(loginInit.headers)).not.toContain('paperless-secret')
    expect(requestInit.body).not.toContain('paperless-secret')
    expect(requestInit.headers).toMatchObject({ 'X-CSRF-Token': 'csrf-value' })
  })

  it('persists a locale preference with the in-memory CSRF token', async () => {
    const spy = mockFetch(jsonResponse({
      user_id: 1, username: 'alice', display_name: 'Alice',
      expires_at: '2030-01-01T00:00:00Z', csrf_token: 'csrf-value', locale: 'en',
    }))
    await authApi.login('paperless-secret', 'en')
    await authApi.updateLocale('fr')

    const request = spy.mock.calls[1]?.[1] as RequestInit
    expect(request.method).toBe('PATCH')
    expect(request.body).toBe(JSON.stringify({ locale: 'fr' }))
    expect(request.headers).toMatchObject({ 'X-CSRF-Token': 'csrf-value' })
  })
})

describe('systemApi.paperless', () => {
  it('calls the probe endpoint', async () => {
    const spy = mockFetch(
      jsonResponse({ configured: true, connected: true, compatible: true }),
    )

    await systemApi.paperless()

    expect(spy.mock.calls[0]?.[0]).toBe('/api/v1/system/paperless')
  })

  it('returns a failure payload rather than throwing when Paperless is down', async () => {
    // The endpoint answers 200 even on failure, so the UI can explain *why*
    // instead of rendering a generic network error.
    mockFetch(
      jsonResponse({
        configured: true,
        connected: false,
        compatible: false,
        url: 'http://paperless.invalid',
        error_code: 'PAPERLESS_UNREACHABLE',
        error_message: 'Could not reach Paperless.',
      }),
    )

    const status = await systemApi.paperless()

    expect(status.connected).toBe(false)
    expect(status.error_code).toBe('PAPERLESS_UNREACHABLE')
  })

  it('never exposes a token field, however the backend evolves', async () => {
    // Structural guard: this payload is rendered in the browser, so a token
    // appearing here would be a disclosure. Mirrors the backend-side test.
    mockFetch(
      jsonResponse({
        configured: true,
        connected: true,
        compatible: true,
        url: 'http://paperless.example',
        api_version: '10',
        paperless_version: '3.1.2',
      }),
    )

    const status = await systemApi.paperless()

    const keys = Object.keys(status).join(' ').toLowerCase()
    expect(keys).not.toContain('token')
    expect(keys).not.toContain('secret')
    expect(keys).not.toContain('password')
  })
})
