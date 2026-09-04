/**
 * Column definitions for the Explorer grid.
 *
 * Base columns are fixed (selection, title, document type, correspondent,
 * created, modified, tags, ASN). Custom-field columns are entirely dynamic,
 * built from the Metadata Registry's custom field definitions - never
 * hardcoded (M3 brief).
 */

import type { ColumnDef } from '@tanstack/react-table'

import { Badge } from '@/components/ui/badge'
import { messages } from '@/i18n/messages'
import type { CustomFieldDefinition, DocumentListItem, MetadataRef } from '@/api/types'

import { formatCustomFieldValue, formatDate, formatUnknownReference } from './format'

/** Renders a resolved (or unresolved) metadata reference. */
export function MetadataRefCell({ value }: { value: MetadataRef | null }) {
  if (value === null) {
    return <span className="text-muted-foreground">{messages.explorer.nullValue}</span>
  }
  if (value.name === null) {
    return <span className="text-muted-foreground">{formatUnknownReference(value.id)}</span>
  }
  return <span>{value.name}</span>
}

/** Base (non-custom-field) columns, in the order the M3 brief lists them. */
export function buildBaseColumns(): ColumnDef<DocumentListItem>[] {
  return [
    {
      accessorKey: 'title',
      header: messages.explorer.columnTitle,
      cell: ({ row }) => <span className="font-medium">{row.original.title}</span>,
      enableSorting: true,
    },
    {
      id: 'document_type',
      header: messages.explorer.columnDocumentType,
      cell: ({ row }) => <MetadataRefCell value={row.original.document_type} />,
      enableSorting: true,
    },
    {
      id: 'correspondent',
      header: messages.explorer.columnCorrespondent,
      cell: ({ row }) => <MetadataRefCell value={row.original.correspondent} />,
      enableSorting: true,
    },
    {
      accessorKey: 'created',
      header: messages.explorer.columnCreated,
      cell: ({ row }) => <span className="tabular">{formatDate(row.original.created)}</span>,
      enableSorting: true,
    },
    {
      accessorKey: 'modified',
      header: messages.explorer.columnModified,
      cell: ({ row }) => <span className="tabular">{formatDate(row.original.modified)}</span>,
      enableSorting: true,
    },
    {
      id: 'tags',
      header: messages.explorer.columnTags,
      enableSorting: false,
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1">
          {row.original.tags.map((tag) =>
            tag.name === null ? (
              <Badge key={tag.id} variant="outline">
                {formatUnknownReference(tag.id)}
              </Badge>
            ) : (
              <Badge key={tag.id} variant="default">
                {tag.name}
              </Badge>
            ),
          )}
        </div>
      ),
    },
    {
      accessorKey: 'archive_serial_number',
      header: messages.explorer.columnArchiveSerialNumber,
      enableSorting: true,
      cell: ({ row }) => (
        <span className="tabular text-muted-foreground">
          {row.original.archive_serial_number ?? messages.explorer.nullValue}
        </span>
      ),
    },
  ]
}

/**
 * One dynamic column per custom field definition known to the Metadata
 * Registry - resolved live from `/api/v1/metadata/custom-fields`, never
 * hardcoded. A field with no value on a given document renders as ABSENT
 * (the "—" placeholder), not as a missing column.
 */
export function buildCustomFieldColumns(
  definitions: CustomFieldDefinition[],
): ColumnDef<DocumentListItem>[] {
  return definitions.map((definition) => ({
    id: `custom_field_${definition.id}`,
    header: definition.name,
    enableSorting:
      definition.data_type === 'string' ||
      definition.data_type === 'longtext' ||
      definition.data_type === 'monetary' ||
      definition.data_type === 'date',
    cell: ({ row }: { row: { original: DocumentListItem } }) => {
      const value = row.original.custom_fields.find((v) => v.field_id === definition.id)
      return <span>{formatCustomFieldValue(value, definition)}</span>
    },
  }))
}
