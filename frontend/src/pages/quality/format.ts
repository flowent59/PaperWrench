import type { SchemaRuleResult } from '@/api/types'
import { messages } from '@/i18n/messages'

const m = messages.quality

export function observed(rule: SchemaRuleResult): string {
  if (rule.value_kind === 'absent') return m.absent
  if (rule.value_kind === 'null') return m.null
  if (typeof rule.actual === 'string') return JSON.stringify(rule.actual)
  if (rule.actual === false) return 'false'
  if (rule.actual === 0) return '0'
  return JSON.stringify(rule.actual) ?? String(rule.actual)
}

export function expected(rule: SchemaRuleResult): string {
  if (rule.kind === 'required') return m.requiredExpected
  if (typeof rule.expected === 'string') return JSON.stringify(rule.expected)
  return JSON.stringify(rule.expected) ?? String(rule.expected)
}
