import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch, NetworkError } from './client'

function mockFetch(response: Response | Error) {
  const spy = vi.fn(
    (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> =>
      response instanceof Error
        ? Promise.reject(response)
        : Promise.resolve(response),
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

  it('never sends credentials or a Paperless token', async () => {
    const spy = mockFetch(jsonResponse({}))

    await apiFetch('/system/health')

    const init = spy.mock.calls[0]?.[1] as RequestInit | undefined
    const headers = JSON.stringify(init?.headers ?? {}).toLowerCase()
    expect(headers).not.toContain('authorization')
    expect(headers).not.toContain('token')
    expect(init?.credentials).toBeUndefined()
  })
})
