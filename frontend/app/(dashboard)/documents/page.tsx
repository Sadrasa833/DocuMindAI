"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowRight,
  FileText,
  Plus,
  Search,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { api, getErrorMessage } from "@/lib/api";
import type { DocumentItem, DocumentStatus, Page } from "@/lib/types";
import { formatFileSize, formatShortDate } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { EmptyState, ErrorState, InlineAlert } from "@/components/states";
import { RowSkeleton } from "@/components/skeletons";

const statusOptions: Array<{ value: DocumentStatus | "all"; label: string }> = [
  { value: "all", label: "All statuses" },
  { value: "completed", label: "Ready" },
  { value: "pending", label: "Queued" },
  { value: "processing", label: "Processing" },
  { value: "failed", label: "Failed" },
];

export default function DocumentsPage() {
  const [result, setResult] = useState<Page<DocumentItem> | null>(null);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<DocumentStatus | "all">("all");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [sortOrder, setSortOrder] = useState<"desc" | "asc">("desc");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    const timeout = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(timeout);
  }, [searchInput]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.documents.list({
        page,
        pageSize: 10,
        status: statusFilter,
        search: search || undefined,
        sortOrder,
      });
      setResult(response);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter, search, sortOrder]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleDelete(document: DocumentItem) {
    const confirmed = window.confirm(
      `Delete "${document.original_filename}"? This removes the file, extracted chunks, and summaries permanently.`,
    );
    if (!confirmed) return;
    setDeletingId(document.id);
    setActionError(null);
    try {
      await api.documents.delete(document.id);
      if (result?.items.length === 1 && page > 1) {
        setPage((current) => current - 1);
      } else {
        await load();
      }
    } catch (err) {
      setActionError(getErrorMessage(err));
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Library"
        title="Documents"
        description="Manage your PDFs, track processing, and open a document to generate summaries."
        actions={
          <Link href="/documents/upload" className="btn-primary">
            <Plus className="h-4 w-4" /> Upload PDF
          </Link>
        }
      />

      <div className="card overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-slate-100 p-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <div className="relative w-full sm:max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              className="input pl-9"
              placeholder="Search by filename"
              aria-label="Search documents"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value as DocumentStatus | "all");
                setPage(1);
              }}
              className="input w-auto py-2"
              aria-label="Filter by status"
            >
              {statusOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select
              value={sortOrder}
              onChange={(event) => {
                setSortOrder(event.target.value as "desc" | "asc");
                setPage(1);
              }}
              className="input w-auto py-2"
              aria-label="Sort order"
            >
              <option value="desc">Newest first</option>
              <option value="asc">Oldest first</option>
            </select>
          </div>
        </div>

        {actionError && (
          <div className="p-4">
            <InlineAlert>{actionError}</InlineAlert>
          </div>
        )}

        {loading ? (
          <div>
            {Array.from({ length: 5 }).map((_, index) => (
              <RowSkeleton key={index} />
            ))}
          </div>
        ) : error ? (
          <div className="p-5">
            <ErrorState message={error} onRetry={load} />
          </div>
        ) : !result || result.items.length === 0 ? (
          <EmptyState
            icon={<FileText className="h-6 w-6" />}
            title={search || statusFilter !== "all" ? "No matching documents" : "Your library is empty"}
            description={
              search || statusFilter !== "all"
                ? "Adjust your search or status filter to see more results."
                : "Upload a PDF and DocuMind AI will extract, index, and prepare it for questions."
            }
            action={
              search || statusFilter !== "all" ? (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => {
                    setSearchInput("");
                    setStatusFilter("all");
                    setPage(1);
                  }}
                >
                  Clear filters
                </button>
              ) : (
                <Link href="/documents/upload" className="btn-primary">
                  <UploadCloud className="h-4 w-4" /> Upload a PDF
                </Link>
              )
            }
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full text-left">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50/70 text-[11px] uppercase tracking-wider text-slate-500">
                    <th className="px-5 py-3 font-semibold">Document</th>
                    <th className="px-4 py-3 font-semibold">Status</th>
                    <th className="px-4 py-3 font-semibold">Pages</th>
                    <th className="px-4 py-3 font-semibold">Size</th>
                    <th className="px-4 py-3 font-semibold">Uploaded</th>
                    <th className="px-5 py-3 text-right font-semibold">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {result.items.map((document) => (
                    <tr
                      key={document.id}
                      className="border-b border-slate-100 last:border-0 hover:bg-slate-50/60"
                    >
                      <td className="max-w-[340px] px-5 py-4">
                        <Link href={`/documents/${document.id}`} className="flex items-center gap-3">
                          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-500">
                            <FileText className="h-5 w-5" />
                          </span>
                          <span className="min-w-0">
                            <span className="block truncate text-sm font-semibold text-slate-800">
                              {document.original_filename}
                            </span>
                            <span className="mt-0.5 block text-xs text-slate-500">
                              {document.chunk_count.toLocaleString()} indexed chunks
                            </span>
                          </span>
                        </Link>
                      </td>
                      <td className="px-4 py-4">
                        <StatusBadge status={document.status} />
                      </td>
                      <td className="px-4 py-4 text-sm text-slate-600">
                        {document.page_count ?? "—"}
                      </td>
                      <td className="px-4 py-4 text-sm text-slate-600">
                        {formatFileSize(document.file_size)}
                      </td>
                      <td className="px-4 py-4 text-sm text-slate-600">
                        {formatShortDate(document.created_at)}
                      </td>
                      <td className="px-5 py-4">
                        <div className="flex items-center justify-end gap-1">
                          <Link
                            href={`/documents/${document.id}`}
                            className="rounded-lg px-2.5 py-1.5 text-xs font-semibold text-brand-600 hover:bg-brand-50"
                          >
                            Open
                          </Link>
                          <button
                            type="button"
                            onClick={() => void handleDelete(document)}
                            disabled={deletingId === document.id}
                            className="rounded-lg p-2 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
                            aria-label={`Delete ${document.original_filename}`}
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex flex-col gap-3 border-t border-slate-100 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs text-slate-500">
                Showing {(result.page - 1) * result.page_size + 1}–
                {Math.min(result.page * result.page_size, result.total)} of {result.total}
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="btn-secondary !px-3 !py-2"
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                  disabled={page <= 1}
                >
                  <ArrowLeft className="h-4 w-4" /> Previous
                </button>
                <span className="px-2 text-xs font-medium text-slate-500">
                  Page {result.page} of {Math.max(result.pages, 1)}
                </span>
                <button
                  type="button"
                  className="btn-secondary !px-3 !py-2"
                  onClick={() => setPage((current) => current + 1)}
                  disabled={page >= result.pages}
                >
                  Next <ArrowRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
