"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  FileText,
  Layers,
  MessageSquareText,
  Plus,
  Sparkles,
  UploadCloud,
  TriangleAlert,
} from "lucide-react";
import { api, getErrorMessage } from "@/lib/api";
import type { DocumentItem, DocumentStats } from "@/lib/types";
import { formatFileSize, formatShortDate } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { EmptyState, ErrorState } from "@/components/states";
import { PageLoading } from "@/components/skeletons";

export default function DashboardPage() {
  const [stats, setStats] = useState<DocumentStats | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statsResponse, documentsResponse] = await Promise.all([
        api.documents.stats(),
        api.documents.list({ pageSize: 5 }),
      ]);
      setStats(statsResponse);
      setDocuments(documentsResponse.items);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <PageLoading />;
  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!stats) return null;

  const cards = [
    {
      label: "Documents",
      value: stats.total_documents,
      detail: `${stats.completed_documents} ready to query`,
      icon: FileText,
      accent: "bg-brand-50 text-brand-600",
    },
    {
      label: "Pages indexed",
      value: stats.total_pages,
      detail: "Extracted with page references",
      icon: Layers,
      accent: "bg-cyan-50 text-cyan-600",
    },
    {
      label: "Knowledge chunks",
      value: stats.total_chunks,
      detail: "Embedded with pgvector",
      icon: Sparkles,
      accent: "bg-violet-50 text-violet-600",
    },
    {
      label: "In progress",
      value: stats.processing_documents,
      detail:
        stats.failed_documents > 0
          ? `${stats.failed_documents} failed upload${stats.failed_documents === 1 ? "" : "s"}`
          : "All processing healthy",
      icon: stats.failed_documents > 0 ? TriangleAlert : UploadCloud,
      accent:
        stats.failed_documents > 0
          ? "bg-amber-50 text-amber-600"
          : "bg-emerald-50 text-emerald-600",
    },
  ];

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Workspace overview"
        title="Good to see you"
        description="Track your document library, jump into a conversation, or upload new material to explore."
        actions={
          <>
            <Link href="/chat" className="btn-secondary">
              <MessageSquareText className="h-4 w-4" /> Ask a question
            </Link>
            <Link href="/documents/upload" className="btn-primary">
              <Plus className="h-4 w-4" /> Upload PDF
            </Link>
          </>
        }
      />

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <div key={card.label} className="card p-5">
              <div className="flex items-start justify-between">
                <p className="text-sm font-medium text-slate-500">{card.label}</p>
                <span className={`flex h-9 w-9 items-center justify-center rounded-xl ${card.accent}`}>
                  <Icon className="h-4 w-4" />
                </span>
              </div>
              <p className="mt-4 text-3xl font-semibold tracking-tight text-slate-900">
                {card.value.toLocaleString()}
              </p>
              <p className="mt-1 text-xs text-slate-500">{card.detail}</p>
            </div>
          );
        })}
      </section>

      <div className="grid gap-6 xl:grid-cols-[1.6fr_1fr]">
        <section className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 sm:px-6">
            <div>
              <h2 className="text-base font-semibold text-slate-900">Recent documents</h2>
              <p className="mt-0.5 text-xs text-slate-500">Your latest uploads and their processing state.</p>
            </div>
            <Link
              href="/documents"
              className="inline-flex items-center gap-1 text-xs font-semibold text-brand-600 hover:text-brand-700"
            >
              View all <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>

          {documents.length === 0 ? (
            <EmptyState
              icon={<FileText className="h-6 w-6" />}
              title="No documents yet"
              description="Upload a PDF to start extracting, indexing, and asking questions about its content."
              action={
                <Link href="/documents/upload" className="btn-primary">
                  <UploadCloud className="h-4 w-4" /> Upload your first PDF
                </Link>
              }
            />
          ) : (
            <div>
              {documents.map((document) => (
                <Link
                  key={document.id}
                  href={`/documents/${document.id}`}
                  className="flex items-center gap-4 border-b border-slate-100 px-5 py-4 transition last:border-0 hover:bg-slate-50/70 sm:px-6"
                >
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-500">
                    <FileText className="h-5 w-5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-slate-800">
                      {document.original_filename}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      {formatFileSize(document.file_size)}
                      {document.page_count ? ` · ${document.page_count} pages` : ""}
                      {` · ${formatShortDate(document.created_at)}`}
                    </p>
                  </div>
                  <StatusBadge status={document.status} />
                  <ArrowRight className="hidden h-4 w-4 text-slate-300 sm:block" />
                </Link>
              ))}
            </div>
          )}
        </section>

        <section className="overflow-hidden rounded-2xl bg-ink-950 p-6 text-white shadow-lift">
          <span className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1.5 text-[11px] font-medium text-brand-200">
            <Sparkles className="h-3.5 w-3.5" /> AI assistant
          </span>
          <h2 className="mt-5 text-2xl font-semibold tracking-tight">
            Ask better questions of your documents.
          </h2>
          <p className="mt-3 text-sm leading-6 text-slate-400">
            Every answer is grounded in retrieved passages and includes the document and page
            where the evidence was found.
          </p>
          <ul className="mt-6 space-y-3 text-sm text-slate-300">
            {[
              "Source-backed answers",
              "Conversation history saved",
              "Search across selected PDFs",
            ].map((item) => (
              <li key={item} className="flex items-center gap-2.5">
                <span className="h-1.5 w-1.5 rounded-full bg-brand-300" />
                {item}
              </li>
            ))}
          </ul>
          <Link
            href="/chat"
            className="mt-7 inline-flex items-center gap-2 rounded-xl bg-white px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-slate-100"
          >
            Open AI chat <ArrowRight className="h-4 w-4" />
          </Link>
        </section>
      </div>
    </div>
  );
}
