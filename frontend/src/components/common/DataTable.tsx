import { type ColumnDef, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { QueryError } from "./QueryError"
import { EmptyState } from "./EmptyState"

export interface DataTableProps<T> {
  columns: ColumnDef<T, unknown>[]
  data: T[] | undefined
  isLoading?: boolean
  error?: Error | null
  onRetry?: () => void
  emptyTitle?: string
  emptyDescription?: string
  hasMore?: boolean
  onLoadMore?: () => void
  rowHref?: (row: T) => string
  onRowClick?: (row: T) => void
  caption?: string
  /** Let long column headings wrap onto a second line so a many-column table fits a 1280 px window without scrolling. */
  wrapHeaders?: boolean
}

/** Server-paginated table with loading, empty, error and "load more" states (cursor pagination). */
export function DataTable<T>({ columns, data, isLoading, error, onRetry, emptyTitle, emptyDescription, hasMore, onLoadMore, onRowClick, rowHref, caption, wrapHeaders }: DataTableProps<T>) {
  const { t } = useTranslation()
  // TanStack Table v8 returns unstable functions; this component is intentionally not memoised by React Compiler.
  // oxlint-disable-next-line react/incompatible-library
  const table = useReactTable({ data: data ?? [], columns, getCoreRowModel: getCoreRowModel() })

  if (error) {
    return <QueryError error={error} onRetry={onRetry ?? (() => window.location.reload())} />
  }
  if (isLoading) {
    return (
      <div aria-busy="true" aria-label={t("common.loading")} className="space-y-2">
        {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-10 w-full" />)}
      </div>
    )
  }
  if (!data || data.length === 0) return <EmptyState title={emptyTitle ?? t("common.emptyTitle")} description={emptyDescription} />

  return (
    <div className="min-w-0 space-y-3">
      <div className="rounded-lg border">
        <Table>
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id}>
                {group.headers.map((header) => (
                  <TableHead key={header.id} className={wrapHeaders ? "whitespace-normal leading-tight" : undefined}>{header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}</TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                className={onRowClick ? "cursor-pointer" : undefined}
                onClick={onRowClick ? (event) => {
                  if (!(event.target as HTMLElement).closest("a, button, input")) onRowClick(row.original)
                } : undefined}
              >
                {row.getVisibleCells().map((cell, index) => (
                  <TableCell key={cell.id}>
                    {index === 0 && rowHref ? <a className="record-link" href={rowHref(row.original)} onClick={(event) => {
                      if (onRowClick && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && event.button === 0) { event.preventDefault(); onRowClick(row.original) }
                    }}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</a>
                      : index === 0 && onRowClick ? <button type="button" className="record-link text-left" onClick={() => onRowClick(row.original)}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</button>
                      : flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {hasMore && onLoadMore ? (
        <div className="flex justify-center">
          <Button variant="outline" onClick={onLoadMore}>{t("common.loadMore")}</Button>
        </div>
      ) : null}
    </div>
  )
}
