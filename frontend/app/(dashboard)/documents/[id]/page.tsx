"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Calendar,
  FileText,
  Layers,
  Loader2,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { api, getErrorMessage } from "@/lib/api";
import type { DocumentItem, SummaryResponse, SummaryStyle } from "@/lib/types";
import { formatDate, formatFileSize, isProcessingStatus } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { ErrorState, InlineAlert } from "@/components/states";

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const documentId = params.id;
  const [document, setDocument] = useState<DocumentItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [style, setStyle] = useState<SummaryStyle>("short");
  const [summarizing, setSummarizing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [citationPage, setCitationPage] = useState<number | null>(null);
  const summaryPollToken = useRef(0);

  useEffect(() => {
    const page = Number(new URLSearchParams(window.location.search).get("page"));
    if (Number.isFinite(page) && page > 0) setCitationPage(page);
  }, []);

  useEffect(() => {
    return () => {
      summaryPollToken.current += 1;
    };
  }, []);

  const applyDocument = useCallback((next: DocumentItem) => {
    setDocument(next);
    if (next.summary_style) setStyle(next.summary_style);
  }, []);

  const load = useCallback(
    async (showLoading = false) => {
      if (showLoading) setLoading(true);
      try {
        const next = await api.documents.get(documentId);
        applyDocument(next);
        setError(null);
        return next;
      } catch (err) {
        setError(getErrorMessage(err));
        return null;
      } finally {
        if (showLoading) setLoading(false);
      }
    },
    [applyDocument, documentId],
  );

  useEffect(() => {
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;

    async function poll(initial = false) {
      if (initial) setLoading(true);
      try {
        const next = await api.documents.get(documentId);
        if (cancelled) return;
        applyDocument(next);
        setError(null);
        if (isProcessingStatus(next.status)) {
          timeout = setTimeout(() => void poll(), 3000);
        }
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      } finally {
        if (initial) setLoading(false);
      }
    }

    void poll(true);
    return () => {
      cancelled = true;
      if (timeout) clearTimeout(timeout);
    };
  }, [applyDocument, documentId]);

  function applySummary(result: SummaryResponse) {
    setDocument((current) =>
      current
        ? {
            ...current,
            summary: result.status === "completed" ? result.summary : current.summary,
            summary_style: result.style ?? current.summary_style,
            summary_status: result.status,
            summary_error: result.error_message,
            summarized_at: result.created_at ?? current.summarized_at,
          }
        : current,
    );
  }

  async function generateSummary() {
    if (!document) return;
    const token = ++summaryPollToken.current;
    setSummarizing(true);
    setActionError(null);
    try {
      const force = document.summary_style === style && Boolean(document.summary);
      const result = await api.documents.summarize(document.id, style, force);
      if (token !== summaryPollToken.current) return;
      applySummary(result);
      if (result.status === "completed") return;
      if (result.status === "failed") {
        throw new Error(result.error_message ?? "Summary generation failed.");
      }

      for (let attempt = 0; attempt < 60; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        if (token !== summaryPollToken.current) return;
        const state = await api.documents.summary(document.id);
        if (token !== summaryPollToken.current) return;
        applySummary(state);
        if (state.status === "completed") return;
        if (state.status === "failed") {
          throw new Error(state.error_message ?? "Summary generation failed.");
        }
      }
      throw new Error("Summary generation is still running. Refresh to check again.");
    } catch (err) {
      if (token === summaryPollToken.current) setActionError(getErrorMessage(err));
    } finally {
      if (token === summaryPollToken.current) setSummarizing(false);
    }
  }

  async function handleDelete() {
    if (!document) return;
    if (
      !window.confirm(
        `Delete "${document.original_filename}"? This permanently removes the uploaded file, extracted text, and search index. Prior chat citations may remain as historical references.`,
      )
    ) {
      return;
    }
    setDeleting(true);
    setActionError(null);
    try {
      await api.documents.delete(document.id);
      router.replace("/documents");
    } catch (err) {
      setActionError(getErrorMessage(err));
      setDeleting(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="skeleton h-4 w-36" />
        <div className="skeleton h-10 w-2/3 max-w-lg" />
        <div className="grid gap-4 md:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="card p-5">
              <div className="skeleton h-3 w-20" />
              <div className="skeleton mt-3 h-5 w-28" />
            </div>
          ))}
        </div>
        <div className="card p-6">
          <div className="skeleton h-5 w-40" />
          <div className="skeleton mt-4 h-28 w-full" />
        </div>
      </div>
    );
  }

  if (error && !document) {
    return (
      <div className="space-y-5">
        <Link href="/documents" className="inline-flex items-center gap-1.5 text-sm text-slate-500">
          <ArrowLeft className="h-4 w-4" /> Back to documents
        </Link>
        <ErrorState message={error} onRetry={() => void load(true)} />
      </div>
    );
  }

  if (!document) return null;
  const processing = isProcessingStatus(document.status);
  const summaryBusy =
    summarizing ||
    document.summary_status === "pending" ||
    document.summary_status === "processing";
  const hasMatchingSummary = document.summary_style === style && Boolean(document.summary);

  return (
    <div className="space-y-6">
      <Link
        href="/documents"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 hover:text-slate-800"
      >
        <ArrowLeft className="h-4 w-4" /> Back to documents
      </Link>

      <PageHeader
        eyebrow="Document details"
        title={document.original_filename}
        description="Review processing metadata, generate a summary, or ask questions about this document."
        actions={
          <>
            <Link href={`/chat?document=${document.id}`} className="btn-secondary">
              <Sparkles className="h-4 w-4" /> Ask this document
            </Link>
            <button
              type="button"
              className="btn-ghost !text-rose-600 hover:!bg-rose-50"
              onClick={() => void handleDelete()}
              disabled={deleting}
            >
              {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              Delete
            </button>
          </>
        }
      />

      {citationPage && (
        <div className="flex items-center gap-2 rounded-xl border border-brand-100 bg-brand-50 px-4 py-3 text-sm text-brand-800">
          <BookOpen className="h-4 w-4" />
          Citation reference: the answer used page {citationPage} of this document.
        </div>
      )}

      {actionError && <InlineAlert>{actionError}</InlineAlert>}

      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge status={document.status} />
        {processing && (
          <span className="flex items-center gap-1.5 text-xs text-slate-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Processing in the background · this
            page refreshes automatically
          </span>
        )}
        {document.status === "failed" && document.error_message && (
          <span className="flex items-center gap-1.5 text-xs text-rose-600">
            <AlertTriangle className="h-3.5 w-3.5" /> {document.error_message}
          </span>
        )}
      </div>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label: "Pages", value: document.page_count ?? "—", icon: BookOpen },
          { label: "Indexed chunks", value: document.chunk_count.toLocaleString(), icon: Layers },
          { label: "File size", value: formatFileSize(document.file_size), icon: FileText },
          { label: "Uploaded", value: formatDate(document.created_at), icon: Calendar },
        ].map((item) => {
          const Icon = item.icon;
          return (
            <div key={item.label} className="card p-5">
              <div className="flex items-center gap-2 text-xs font-medium text-slate-500">
                <Icon className="h-3.5 w-3.5" /> {item.label}
              </div>
              <p className="mt-2 text-lg font-semibold text-slate-900">{item.value}</p>
            </div>
          );
        })}
      </section>

      <div className="grid gap-6 xl:grid-cols-[1.55fr_1fr]">
        <section className="card overflow-hidden">
          <div className="flex flex-col gap-4 border-b border-slate-100 p-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <div>
              <h2 className="flex items-center gap-2 text-base font-semibold text-slate-900">
                <Sparkles className="h-4 w-4 text-brand-600" /> AI summary
              </h2>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                Generate a concise overview or a detailed breakdown from the extracted content.
              </p>
            </div>
            <div className="inline-flex rounded-lg bg-slate-100 p-1">
              {(["short", "detailed"] as SummaryStyle[]).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setStyle(option)}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold capitalize transition ${
                    style === option
                      ? "bg-white text-slate-900 shadow-sm"
                      : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {option}
                </button>
              ))}
            </div>
          </div>

          <div className="p-5 sm:p-6">
            {hasMatchingSummary ? (
              <>
                <div className="prose-summary">{document.summary}</div>
                <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4">
                  <p className="text-xs text-slate-400">
                    Generated {formatDate(document.summarized_at)} · {document.summary_style} summary
                  </p>
                  <button
                    type="button"
                    className="btn-secondary !py-2"
                    onClick={() => void generateSummary()}
                    disabled={summaryBusy || processing}
                  >
                    {summaryBusy ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <RefreshCw className="h-4 w-4" />
                    )}
                    {summaryBusy ? "Regenerating" : "Regenerate"}
                  </button>
                </div>
              </>
            ) : (
              <div className="flex flex-col items-center py-10 text-center">
                <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-50 text-brand-600">
                  {summaryBusy ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    <Sparkles className="h-5 w-5" />
                  )}
                </span>
                <p className="mt-4 text-sm font-semibold text-slate-900">
                  {processing
                    ? "Summary will be available shortly"
                    : summaryBusy
                      ? "Generating summary"
                      : document.summary_status === "failed"
                        ? "Summary generation failed"
                        : "No summary yet"}
                </p>
                <p className="mt-1 max-w-sm text-sm leading-6 text-slate-500">
                  {processing
                    ? "We are still extracting and indexing this document. You can generate a summary once processing completes."
                    : summaryBusy
                      ? "The summary is being generated in the background. This page will update automatically."
                      : document.summary_status === "failed"
                        ? (document.summary_error ??
                          "The summary could not be generated. Try again.")
                        : `Create a ${style} summary grounded entirely in this PDF.`}
                </p>
                <button
                  type="button"
                  className="btn-primary mt-5"
                  disabled={summaryBusy || processing}
                  onClick={() => void generateSummary()}
                >
                  {summaryBusy ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" /> Generating summary
                    </>
                  ) : (
                    <>
                      <Sparkles className="h-4 w-4" />{" "}
                      {document.summary_status === "failed"
                        ? "Try again"
                        : `Generate ${style} summary`}
                    </>
                  )}
                </button>
              </div>
            )}
          </div>
        </section>

        <aside className="space-y-5">
          <div className="card p-5">
            <h2 className="text-sm font-semibold text-slate-900">Processing details</h2>
            <dl className="mt-4 space-y-3 text-sm">
              <div className="flex items-center justify-between gap-4">
                <dt className="text-slate-500">Status</dt>
                <dd><StatusBadge status={document.status} /></dd>
              </div>
              <div className="flex items-center justify-between gap-4">
                <dt className="text-slate-500">Document type</dt>
                <dd className="font-medium text-slate-700">{document.mime_type}</dd>
              </div>
              <div className="flex items-center justify-between gap-4">
                <dt className="text-slate-500">Processed at</dt>
                <dd className="text-right font-medium text-slate-700">
                  {formatDate(document.processed_at)}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-4">
                <dt className="text-slate-500">Last updated</dt>
                <dd className="text-right font-medium text-slate-700">
                  {formatDate(document.updated_at)}
                </dd>
              </div>
            </dl>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-5">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Grounded by design
            </p>
            <p className="mt-2 text-xs leading-5 text-slate-500">
              Answers and summaries rely only on extracted text from this document. Citations point
              to the exact page and chunk used.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
