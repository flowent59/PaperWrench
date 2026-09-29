import { ApiError, NetworkError } from '@/api/client'

import { messages } from './messages'

export interface CodedDiagnostic {
  code: string
  params?: Record<string, unknown> | null
  details?: Record<string, unknown> | null
}

type ErrorCode = keyof typeof messages.errors.codes

function hasErrorCode(code: string): code is ErrorCode {
  return Object.prototype.hasOwnProperty.call(messages.errors.codes, code)
}

/** Translate a stable backend/durable-history code in the active locale. */
export function errorCodeMessage(code: string, httpStatus?: number | null): string {
  const translated = hasErrorCode(code)
    ? messages.errors.codes[code]
    : messages.errors.unknownCode(code)
  return httpStatus == null
    ? translated
    : messages.errors.withHttpStatus(translated, httpStatus)
}

function issueList(params: Record<string, unknown> | null): string | null {
  const issues = params?.issues
  if (!Array.isArray(issues)) return null
  const translated = issues.flatMap((issue) => {
    if (typeof issue !== 'object' || issue === null || !('code' in issue)) return []
    const code = (issue as { code?: unknown }).code
    return typeof code === 'string' ? [errorCodeMessage(code)] : []
  })
  return translated.length > 0 ? [...new Set(translated)].join(' · ') : null
}

/** Translate an API/transport failure without rendering diagnostic server text. */
export function errorMessage(error: unknown, fallback = messages.errors.generic): string {
  if (error instanceof NetworkError) return messages.errors.network
  if (error instanceof ApiError) {
    return issueList(error.params) ?? errorCodeMessage(error.code)
  }
  return fallback
}

/** Translate filter, transformation, preview, job, or rollback diagnostics. */
export function issueMessage(
  issue: CodedDiagnostic | string,
  httpStatus?: number | null,
): string {
  return errorCodeMessage(typeof issue === 'string' ? issue : issue.code, httpStatus)
}
