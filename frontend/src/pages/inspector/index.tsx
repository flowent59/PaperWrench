import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Link, useParams } from 'react-router'

import { ApiError, documentsApi } from '@/api/client'
import {
  queryKeys,
  useCorrespondents,
  useDocumentTypes,
  useStoragePaths,
  useTags,
} from '@/api/queries'
import type {
  CustomFieldDefinition,
  CustomFieldValueKind,
  DocumentDetail,
  EditRequest,
  EditResponse,
} from '@/api/types'
import { Button } from '@/components/ui/button'
import { messages } from '@/i18n/messages'
import { errorMessage } from '@/i18n/errors'

const m = messages.inspector
const inputClass =
  'rounded-md border border-input bg-background px-3 py-2 text-sm focus-ring'
type Option = { id: string | number; name: string }
type Field = {
  key: string
  label: string
  value: unknown
  type: string
  options?: Option[] | undefined
  definition?: CustomFieldDefinition | undefined
  kind?: CustomFieldValueKind
  editable: boolean
}

function inspectorError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'CONFLICT') return m.conflict
    if (error.code === 'PAPERLESS_UNAUTHORIZED') return m.unauthorized
    if (error.code === 'PAPERLESS_FORBIDDEN') return m.forbidden
    if (error.code === 'NOT_FOUND') return m.notFound
    if (error.code === 'VALIDATION_ERROR') return errorMessage(error)
  }
  return m.uncertain
}

function display(value: unknown): string {
  if (value === '') return m.empty
  if (value === null || value === undefined) return m.none
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function InspectorPage() {
  const { documentId } = useParams()
  const id = Number(documentId)
  const [generation, setGeneration] = useState(0)
  const query = useQuery({
    queryKey: queryKeys.document(id),
    queryFn: () => documentsApi.detail(id),
    enabled: Number.isSafeInteger(id) && id > 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      <Link className="underline focus-ring" to="/documents">
        {m.back}
      </Link>
      <h1 className="text-xl font-semibold">{m.title}</h1>
      {!Number.isSafeInteger(id) || id <= 0 ? (
        <p role="alert">{m.notFound}</p>
      ) : query.isError ? (
        <p role="alert">{inspectorError(query.error)}</p>
      ) : query.isFetching ? (
        <p>{m.loading}</p>
      ) : query.data ? (
        <Inspector
          key={`${id}:${query.data.revision}:${generation}`}
          document={query.data}
          reload={async () => {
            const result = await query.refetch()
            if (result.isSuccess) setGeneration((g) => g + 1)
          }}
        />
      ) : (
        <p>{m.loading}</p>
      )}
    </div>
  )
}

function Inspector({
  document: initialDocument,
  reload,
}: {
  document: DocumentDetail
  reload: () => unknown
}) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<string | null>(null)
  const [receipt, setReceipt] = useState<{
    field: Field
    response: EditResponse
    intended: unknown
  } | null>(null)
  const document = receipt?.response.document ?? initialDocument
  const submitting = useRef(false)
  const tags = useTags()
  const correspondents = useCorrespondents()
  const types = useDocumentTypes()
  const paths = useStoragePaths()
  const mutation = useMutation({
    mutationFn: (request: EditRequest) => documentsApi.edit(document.id, request),
    retry: false,
  })
  // After an ambiguous outcome/conflict, a new decision requires an explicit reload.
  const blocked =
    mutation.isError &&
    !(mutation.error instanceof ApiError && mutation.error.status === 422)
  const editable = document.user_can_change === true && !blocked
  const core: Field[] = [
    {
      key: 'title',
      label: messages.explorer.columnTitle,
      value: document.title,
      type: 'string',
      editable,
    },
    {
      key: 'correspondent',
      label: messages.explorer.columnCorrespondent,
      value: document.correspondent?.id ?? null,
      type: 'reference',
      options: correspondents.data,
      editable: editable && correspondents.isSuccess,
    },
    {
      key: 'document_type',
      label: messages.explorer.columnDocumentType,
      value: document.document_type?.id ?? null,
      type: 'reference',
      options: types.data,
      editable: editable && types.isSuccess,
    },
    {
      key: 'storage_path',
      label: m.storagePath,
      value: document.storage_path?.id ?? null,
      type: 'reference',
      options: paths.data,
      editable: editable && paths.isSuccess,
    },
    {
      key: 'tags',
      label: messages.explorer.columnTags,
      value: document.tags.map((t) => t.id),
      type: 'tags',
      options: tags.data,
      editable: editable && tags.isSuccess,
    },
    {
      key: 'created',
      label: messages.explorer.columnCreated,
      value: document.created,
      type: 'date',
      editable,
    },
    {
      key: 'archive_serial_number',
      label: messages.explorer.columnArchiveSerialNumber,
      value: document.archive_serial_number,
      type: 'integer',
      editable,
    },
    {
      key: 'modified',
      label: messages.explorer.columnModified,
      value: document.modified,
      type: 'string',
      editable: false,
    },
    {
      key: 'added',
      label: messages.explorer.columnAdded,
      value: document.added,
      type: 'string',
      editable: false,
    },
    {
      key: 'filename',
      label: m.filename,
      value: document.original_file_name,
      type: 'string',
      editable: false,
    },
    {
      key: 'owner',
      label: m.owner,
      value: document.owner,
      type: 'integer',
      editable: false,
    },
  ].map((field) => ({
    ...field,
    editable: field.editable && document.editable_core_fields.includes(field.key),
  }))
  const custom: Field[] = document.custom_fields.map((value) => {
    const definition = document.definitions.find((d) => d.id === value.field_id)
    const options = definition?.extra_data.select_options as
      | { id: string; label: string }[]
      | undefined
    return {
      key: `custom:${value.field_id}`,
      label: definition?.name ?? `${m.unknown} #${value.field_id}`,
      value: value.raw,
      type: definition?.data_type ?? 'unknown',
      kind: value.kind,
      definition,
      options: options?.map((o) => ({ id: o.id, name: o.label })),
      editable:
        editable &&
        !!definition &&
        document.editable_custom_types.includes(definition.data_type),
    }
  })

  async function save(
    field: Field,
    value: unknown,
    kind: CustomFieldValueKind,
    ack: boolean,
  ) {
    if (submitting.current) return
    submitting.current = true
    const request: EditRequest = {
      expected_revision: document.revision,
      catalog_revision: document.catalog_revision,
      core: field.definition ? {} : { [field.key]: value },
      custom_changes: field.definition
        ? [
            {
              field_id: field.definition.id,
              kind,
              ...(kind === 'present' ? { value } : {}),
            },
          ]
        : [],
      acknowledge_external_race: ack,
    }
    try {
      const response = await mutation.mutateAsync(request)
      setReceipt({
        field,
        response,
        intended: field.definition
          ? { kind, value: kind === 'present' ? value : null }
          : value,
      })
      setEditing(null)
      // Keep the receipt visible; detail is invalidated without replacing this snapshot.
      // A fresh snapshot is explicitly loaded before the next edit below.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['documents'] }),
        queryClient.invalidateQueries({ queryKey: ['filters', 'count'] }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.document(document.id),
          refetchType: 'none',
        }),
      ])
    } catch {
      // Preserve the draft; the common error envelope is rendered below.
    } finally {
      submitting.current = false
    }
  }
  function receiptValue(detail: DocumentDetail, field: Field): unknown {
    if (field.definition) {
      const v = detail.custom_fields.find((v) => v.field_id === field.definition?.id)
      return { kind: v?.kind ?? 'absent', value: v?.raw ?? null }
    }
    return detail[field.key as keyof DocumentDetail]
  }
  return (
    <>
      <h2 className="text-lg font-medium">
        {receipt?.response.document.title ?? document.title}
      </h2>
      <p className="text-sm text-muted-foreground">{m.noRollback}</p>
      <p className="rounded border border-amber-500/40 p-3 text-sm">{m.risk}</p>
      {document.user_can_change !== true && <p role="status">{m.readOnly}</p>}
      {mutation.isError && (
        <div role="alert">
          <p>{inspectorError(mutation.error)}</p>
          <Button onClick={() => reload()}>{m.reload}</Button>
        </div>
      )}
      {receipt && (
        <section role="status" className="rounded border border-primary p-4">
          <h3>{receipt.field.label}</h3>
          <dl className="grid gap-2">
            <dt>{m.before}</dt>
            <dd>{display(receiptValue(receipt.response.before, receipt.field))}</dd>
            <dt>{m.intended}</dt>
            <dd>{display(receipt.intended)}</dd>
            <dt>{m.stored}</dt>
            <dd>{display(receiptValue(receipt.response.document, receipt.field))}</dd>
          </dl>
          <Button onClick={() => reload()}>{m.reload}</Button>
        </section>
      )}
      {[
        { label: m.core, fields: core },
        { label: m.custom, fields: custom },
      ].map((group) => (
        <section key={group.label} className="rounded-lg border border-border p-4">
          <h2 className="mb-3 font-semibold">{group.label}</h2>
          <dl className="flex flex-col gap-3">
            {group.fields.map((field) => (
              <div key={field.key} className="border-b border-border pb-3">
                <dt className="font-medium">{field.label}</dt>
                <dd>
                  {editing === field.key ? (
                    <InlineEditor
                      field={field}
                      busy={mutation.isPending}
                      blocked={blocked}
                      save={(value, kind, ack) => save(field, value, kind, ack)}
                      cancel={() => {
                        setEditing(null)
                        if (!blocked) mutation.reset()
                      }}
                    />
                  ) : (
                    <div className="flex items-center justify-between gap-3">
                      <span className="whitespace-pre-wrap break-all">
                        {field.kind === 'absent'
                          ? m.absent
                          : field.kind === 'null'
                            ? m.null
                            : field.options && field.type !== 'tags'
                              ? (field.options.find((o) => o.id === field.value)?.name ??
                                display(field.value))
                              : field.type === 'tags'
                                ? document.tags
                                    .map((t) => t.name ?? `#${t.id}`)
                                    .join(', ')
                                : display(field.value)}
                      </span>
                      {field.editable && !receipt ? (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={editing !== null || mutation.isPending}
                          aria-label={`${m.edit} ${field.label}`}
                          onClick={() => {
                            mutation.reset()
                            setEditing(field.key)
                          }}
                        >
                          {m.edit}
                        </Button>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          {m.unsupported}
                        </span>
                      )}
                    </div>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </>
  )
}

function InlineEditor({
  field,
  busy,
  blocked,
  save,
  cancel,
}: {
  field: Field
  busy: boolean
  blocked: boolean
  save(value: unknown, kind: CustomFieldValueKind, ack: boolean): Promise<void>
  cancel: () => void
}) {
  const [kind, setKind] = useState<CustomFieldValueKind>(field.kind ?? 'present')
  const [value, setValue] = useState<unknown>(
    field.type === 'date' && typeof field.value === 'string'
      ? field.value.slice(0, 10)
      : field.value ?? (field.type === 'boolean' ? false : field.type === 'reference' ? null : ''),
  )
  const [ack, setAck] = useState(false)
  const label = field.label
  const input =
    field.type === 'reference' || field.type === 'select' ? (
      <select
        aria-label={label}
        className={inputClass}
        value={String(value ?? '')}
        onChange={(e) =>
          setValue(
            field.type === 'reference'
              ? e.target.value === ''
                ? null
                : Number(e.target.value)
              : e.target.value,
          )
        }
      >
        <option value="">{field.type === 'reference' ? m.none : m.choose}</option>
        {field.options?.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    ) : field.type === 'tags' ? (
      <select
        multiple
        aria-label={label}
        className={inputClass}
        value={(value as number[]).map(String)}
        onChange={(e) =>
          setValue(Array.from(e.target.selectedOptions, (o) => Number(o.value)))
        }
      >
        {field.options?.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    ) : field.type === 'boolean' ? (
      <select
        aria-label={label}
        className={inputClass}
        value={String(value)}
        onChange={(e) => setValue(e.target.value === 'true')}
      >
        <option value="false">{m.false}</option>
        <option value="true">{m.true}</option>
      </select>
    ) : field.type === 'longtext' ? (
      <textarea
        aria-label={label}
        className={inputClass}
        value={String(value)}
        onChange={(e) => setValue(e.target.value)}
      />
    ) : (
      <input
        aria-label={label}
        className={inputClass}
        type={
          field.type === 'date' ? 'date' : field.type === 'integer' ? 'number' : 'text'
        }
        value={String(value ?? '')}
        onChange={(e) => setValue(e.target.value)}
      />
    )
  return (
    <form
      className="mt-2 flex flex-col items-start gap-3"
      onSubmit={(e) => {
        e.preventDefault()
        const actual =
          field.type === 'integer' ? (value === '' ? null : Number(value)) : value
        void save(actual, kind, ack)
      }}
    >
      <fieldset disabled={busy || blocked} className="flex flex-col items-start gap-3">
        {field.definition && (
          <label>
            {m.state}
            <select
              className={inputClass}
              aria-label={m.state}
              value={kind}
              onChange={(e) => setKind(e.target.value as CustomFieldValueKind)}
            >
              <option value="absent">{m.absent}</option>
              <option value="null">{m.null}</option>
              <option value="present">{m.present}</option>
            </select>
          </label>
        )}
        {(!field.definition || kind === 'present') && input}
        {field.type === 'monetary' && <p className="text-sm">{m.monetary}</p>}
        {field.definition && (
          <label className="text-sm">
            <input
              type="checkbox"
              checked={ack}
              onChange={(e) => setAck(e.target.checked)}
            />{' '}
            {m.acknowledge}
          </label>
        )}
        <Button type="submit" disabled={!!field.definition && !ack}>
          {m.save}
        </Button>
      </fieldset>
      <Button type="button" variant="outline" disabled={busy} onClick={cancel}>
        {m.cancel}
      </Button>
    </form>
  )
}
