"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowRight,
  History,
  MessageSquareText,
  Plus,
  Trash2,
} from "lucide-react";
import { api, getErrorMessage } from "@/lib/api";
import type { ConversationSummary, Page } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, InlineAlert } from "@/components/states";
import { RowSkeleton } from "@/components/skeletons";

export default function ConversationsPage() {
  const [result, setResult] = useState<Page<ConversationSummary> | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setResult(await api.conversations.list(page, 12));
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void load();
  }, [load]);

  async function deleteConversation(conversation: ConversationSummary) {
    if (!window.confirm(`Delete "${conversation.title}"? The message history will be removed.`)) {
      return;
    }
    setDeletingId(conversation.id);
    setActionError(null);
    try {
      await api.conversations.delete(conversation.id);
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
        eyebrow="History"
        title="Conversations"
        description="Revisit previous questions, answers, and the source citations behind them."
        actions={
          <Link href="/chat" className="btn-primary">
            <Plus className="h-4 w-4" /> New conversation
          </Link>
        }
      />

      {actionError && <InlineAlert>{actionError}</InlineAlert>}

      <section className="card overflow-hidden">
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
            icon={<History className="h-6 w-6" />}
            title="No conversations yet"
            description="Ask your first question in the AI chat. Conversations and citations will be saved here automatically."
            action={
              <Link href="/chat" className="btn-primary">
                <MessageSquareText className="h-4 w-4" /> Start a conversation
              </Link>
            }
          />
        ) : (
          <>
            <div>
              {result.items.map((conversation) => (
                <div
                  key={conversation.id}
                  className="flex items-center gap-4 border-b border-slate-100 px-5 py-4 transition last:border-0 hover:bg-slate-50/60 sm:px-6"
                >
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand-600">
                    <MessageSquareText className="h-5 w-5" />
                  </span>
                  <Link href={`/chat?conversation=${conversation.id}`} className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-slate-800">
                      {conversation.title}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      {conversation.message_count} messages · Updated{" "}
                      {formatDate(conversation.updated_at)}
                    </p>
                  </Link>
                  <Link
                    href={`/chat?conversation=${conversation.id}`}
                    className="hidden items-center gap-1 text-xs font-semibold text-brand-600 sm:inline-flex"
                  >
                    Open <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                  <button
                    type="button"
                    onClick={() => void deleteConversation(conversation)}
                    disabled={deletingId === conversation.id}
                    className="rounded-lg p-2 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
                    aria-label={`Delete ${conversation.title}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
            <div className="flex flex-col gap-3 border-t border-slate-100 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs text-slate-500">
                {result.total} conversation{result.total === 1 ? "" : "s"}
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
                <span className="px-2 text-xs text-slate-500">
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
      </section>
    </div>
  );
}
