"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  Check,
  ChevronDown,
  FileText,
  History,
  Loader2,
  Plus,
  Send,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";
import { api, getErrorMessage } from "@/lib/api";
import type {
  Citation,
  ConversationSummary,
  DocumentItem,
  Message,
} from "@/lib/types";
import { formatShortDate } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { InlineAlert } from "@/components/states";

const exampleQuestions = [
  "What are the main risks described in these documents?",
  "Summarize the key obligations and deadlines.",
  "What payment terms are specified?",
];

const STREAMING_STORAGE_KEY = "documind.chat.streaming";
const PENDING_ASSISTANT_PREFIX = "pending-assistant-";

function CitationCard({ citation }: { citation: Citation }) {
  const content = (
    <>
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-slate-100 text-slate-500 group-hover:bg-brand-100 group-hover:text-brand-600">
        <FileText className="h-3.5 w-3.5" />
      </span>
      <span className="min-w-0">
        <span className="block max-w-[190px] truncate text-[11px] font-semibold text-slate-700">
          {citation.document_name}
        </span>
        <span className="block text-[10px] text-slate-500">
          {citation.available
            ? `Page ${citation.page_number}`
            : `Page ${citation.page_number} · document unavailable`}
        </span>
      </span>
    </>
  );

  if (!citation.available) {
    return (
      <div
        className="flex cursor-not-allowed items-center gap-2.5 rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-2 opacity-75"
        title="This source document has been deleted. The citation metadata is preserved for history."
      >
        {content}
      </div>
    );
  }

  return (
    <Link
      href={`/documents/${citation.document_id}?page=${citation.page_number}`}
      className="group flex items-center gap-2.5 rounded-lg border border-slate-200 bg-white px-3 py-2 transition hover:border-brand-200 hover:bg-brand-50/50"
    >
      {content}
    </Link>
  );
}

export default function ChatPage() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [documentPanelOpen, setDocumentPanelOpen] = useState(false);
  const [streamingEnabled, setStreamingEnabled] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const lastMessageContent = messages.length > 0 ? messages[messages.length - 1].content : "";

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior, block: "end" });
    });
  }, []);

  const loadConversations = useCallback(async () => {
    try {
      const response = await api.conversations.list(1, 50);
      setConversations(response.items);
    } catch {
      // A sidebar failure should not block the active conversation.
    }
  }, []);

  const openConversation = useCallback(
    async (conversationId: string, updateUrl = true) => {
      abortRef.current?.abort();
      abortRef.current = null;
      setSending(false);
      setLoadingConversation(true);
      setError(null);
      setDocumentPanelOpen(false);
      try {
        const conversation = await api.conversations.get(conversationId);
        setActiveConversationId(conversation.id);
        setMessages(conversation.messages);
        if (updateUrl && typeof window !== "undefined") {
          window.history.replaceState({}, "", `/chat?conversation=${conversation.id}`);
        }
        scrollToBottom("auto");
      } catch (err) {
        setError(getErrorMessage(err));
        setActiveConversationId(null);
        setMessages([]);
      } finally {
        setLoadingConversation(false);
      }
    },
    [scrollToBottom],
  );

  useEffect(() => {
    let cancelled = false;
    async function initialize() {
      setInitializing(true);
      try {
        const [conversationPage, documentPage] = await Promise.all([
          api.conversations.list(1, 50),
          api.documents.list({ pageSize: 100, status: "completed" }),
        ]);
        if (cancelled) return;
        setConversations(conversationPage.items);
        setDocuments(documentPage.items);

        const searchParams = new URLSearchParams(window.location.search);
        const conversationId = searchParams.get("conversation");
        const documentId = searchParams.get("document");
        if (documentId && documentPage.items.some((document) => document.id === documentId)) {
          setSelectedDocumentIds([documentId]);
        }
        if (conversationId) {
          await openConversation(conversationId, false);
        }
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      } finally {
        if (!cancelled) setInitializing(false);
      }
    }
    void initialize();
    return () => {
      cancelled = true;
    };
  }, [openConversation]);

  useEffect(() => {
    if (messages.length > 0) scrollToBottom("auto");
  }, [lastMessageContent, messages.length, scrollToBottom]);

  useEffect(() => {
    const stored = window.localStorage.getItem(STREAMING_STORAGE_KEY);
    if (stored !== null) setStreamingEnabled(stored === "true");
  }, []);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  function startNewChat() {
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
    setActiveConversationId(null);
    setMessages([]);
    setError(null);
    setQuestion("");
    if (typeof window !== "undefined") {
      window.history.replaceState({}, "", "/chat");
    }
    textareaRef.current?.focus();
  }

  function toggleStreaming() {
    const next = !streamingEnabled;
    setStreamingEnabled(next);
    window.localStorage.setItem(STREAMING_STORAGE_KEY, String(next));
  }

  async function handleSend(event?: FormEvent) {
    event?.preventDefault();
    const text = question.trim();
    if (!text || sending) return;
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    const optimisticId = `pending-user-${Date.now()}`;
    const assistantId = `${PENDING_ASSISTANT_PREFIX}${Date.now()}`;
    const optimisticMessage: Message = {
      id: optimisticId,
      role: "user",
      content: text,
      citations: null,
      created_at: new Date().toISOString(),
    };
    const placeholderMessage: Message = {
      id: assistantId,
      role: "assistant",
      content: "",
      citations: null,
      created_at: new Date().toISOString(),
    };
    setMessages((current) =>
      streamingEnabled
        ? [...current, optimisticMessage, placeholderMessage]
        : [...current, optimisticMessage],
    );
    setQuestion("");
    setSending(true);
    setError(null);
    scrollToBottom();
    let streamCompleted = false;
    try {
      if (streamingEnabled) {
        await api.chatStream(
          text,
          selectedDocumentIds,
          activeConversationId,
          (streamEvent) => {
            if (streamEvent.type === "delta") {
              setMessages((current) =>
                current.map((message) =>
                  message.id === assistantId
                    ? { ...message, content: message.content + streamEvent.content }
                    : message,
                ),
              );
            } else if (streamEvent.type === "done") {
              streamCompleted = true;
              setMessages((current) =>
                current.map((message) =>
                  message.id === assistantId
                    ? {
                        ...message,
                        id: streamEvent.message_id || assistantId,
                        content: streamEvent.answer,
                        citations: streamEvent.citations,
                      }
                    : message,
                ),
              );
              setActiveConversationId(streamEvent.conversation_id);
              if (typeof window !== "undefined") {
                window.history.replaceState(
                  {},
                  "",
                  `/chat?conversation=${streamEvent.conversation_id}`,
                );
              }
              void loadConversations();
            }
          },
          controller.signal,
        );
        if (!streamCompleted) {
          throw new Error("The response stream ended unexpectedly. Please try again.");
        }
      } else {
        const response = await api.chat(text, selectedDocumentIds, activeConversationId);
        const assistantMessage: Message = {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          content: response.answer,
          citations: response.citations,
          created_at: new Date().toISOString(),
        };
        setMessages((current) => [...current, assistantMessage]);
        if (!activeConversationId) {
          setActiveConversationId(response.conversation_id);
          if (typeof window !== "undefined") {
            window.history.replaceState(
              {},
              "",
              `/chat?conversation=${response.conversation_id}`,
            );
          }
        }
        void loadConversations();
      }
    } catch (err) {
      if (controller.signal.aborted || streamCompleted) return;
      setMessages((current) =>
        current.filter(
          (message) => message.id !== optimisticId && message.id !== assistantId,
        ),
      );
      setQuestion(text);
      setError(getErrorMessage(err));
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setSending(false);
      }
    }
  }

  function toggleDocument(documentId: string) {
    setSelectedDocumentIds((current) =>
      current.includes(documentId)
        ? current.filter((id) => id !== documentId)
        : [...current, documentId],
    );
  }

  const activeTitle =
    conversations.find((conversation) => conversation.id === activeConversationId)?.title ??
    (activeConversationId ? "Conversation" : "New conversation");

  if (initializing) {
    return (
      <div className="space-y-6">
        <div className="skeleton h-9 w-52" />
        <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
          <div className="skeleton hidden h-[600px] lg:block" />
          <div className="skeleton h-[600px]" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="AI assistant"
        title="Chat with your documents"
        description="Ask questions and receive answers grounded in retrieved passages with page-level citations."
        actions={
          <>
            <Link href="/conversations" className="btn-secondary lg:hidden">
              <History className="h-4 w-4" /> History
            </Link>
            <button type="button" className="btn-secondary" onClick={startNewChat}>
              <Plus className="h-4 w-4" /> New chat
            </button>
          </>
        }
      />

      {error && (
        <InlineAlert>
          {error}
          <button type="button" className="ml-2 font-semibold underline" onClick={() => setError(null)}>
            Dismiss
          </button>
        </InlineAlert>
      )}

      <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
        <aside className="card hidden max-h-[calc(100vh-230px)] min-h-[560px] flex-col overflow-hidden lg:flex">
          <div className="border-b border-slate-100 px-4 py-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-900">Recent chats</h2>
              <button
                type="button"
                onClick={startNewChat}
                className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                aria-label="New conversation"
              >
                <Plus className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="scrollbar-thin flex-1 overflow-y-auto p-2">
            {conversations.length === 0 ? (
              <p className="px-3 py-8 text-center text-xs leading-5 text-slate-400">
                Your saved conversations will appear here.
              </p>
            ) : (
              conversations.map((conversation) => (
                <button
                  key={conversation.id}
                  type="button"
                  onClick={() => void openConversation(conversation.id)}
                  className={`mb-1 w-full rounded-xl px-3 py-3 text-left transition ${
                    activeConversationId === conversation.id
                      ? "bg-brand-50 text-brand-900 ring-1 ring-brand-100"
                      : "hover:bg-slate-50"
                  }`}
                >
                  <p className="truncate text-xs font-semibold text-slate-800">
                    {conversation.title}
                  </p>
                  <p className="mt-1 text-[10px] text-slate-400">
                    {conversation.message_count} messages · {formatShortDate(conversation.updated_at)}
                  </p>
                </button>
              ))
            )}
          </div>
        </aside>

        <section className="card flex h-[calc(100vh-230px)] min-h-[560px] flex-col overflow-hidden">
          <header className="flex flex-col gap-3 border-b border-slate-100 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-slate-900">{activeTitle}</h2>
              <p className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-500">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                Grounded with document citations
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={toggleStreaming}
                disabled={sending}
                aria-pressed={streamingEnabled}
                className={`btn-ghost !px-2.5 !py-2 text-[11px] font-semibold ${
                  streamingEnabled ? "text-brand-700" : "text-slate-500"
                }`}
                title="Toggle progressive response streaming"
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    streamingEnabled ? "bg-emerald-500" : "bg-slate-300"
                  }`}
                />
                Streaming {streamingEnabled ? "on" : "off"}
              </button>
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setDocumentPanelOpen((current) => !current)}
                  className="btn-secondary !px-3 !py-2 text-xs"
                  aria-expanded={documentPanelOpen}
                >
                <FileText className="h-3.5 w-3.5" />
                {selectedDocumentIds.length === 0
                  ? "All documents"
                  : `${selectedDocumentIds.length} document${selectedDocumentIds.length === 1 ? "" : "s"}`}
                <ChevronDown className="h-3.5 w-3.5" />
              </button>
              {documentPanelOpen && (
                <div className="absolute right-0 z-20 mt-2 w-80 max-w-[85vw] rounded-xl border border-slate-200 bg-white p-3 shadow-lift">
                  <div className="flex items-center justify-between px-1">
                    <p className="text-xs font-semibold text-slate-700">Limit to documents</p>
                    <button
                      type="button"
                      onClick={() => setDocumentPanelOpen(false)}
                      className="rounded p-1 text-slate-400 hover:bg-slate-100"
                      aria-label="Close document selector"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  {documents.length === 0 ? (
                    <div className="mt-2 rounded-lg bg-slate-50 p-3 text-xs leading-5 text-slate-500">
                      No processed documents yet.{" "}
                      <Link href="/documents/upload" className="font-semibold text-brand-600">
                        Upload a PDF
                      </Link>
                      .
                    </div>
                  ) : (
                    <>
                      <div className="scrollbar-thin mt-2 max-h-64 space-y-1 overflow-y-auto">
                        {documents.map((document) => {
                          const selected = selectedDocumentIds.includes(document.id);
                          return (
                            <button
                              key={document.id}
                              type="button"
                              onClick={() => toggleDocument(document.id)}
                              className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-slate-50"
                            >
                              <span
                                className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                                  selected
                                    ? "border-brand-600 bg-brand-600 text-white"
                                    : "border-slate-300"
                                }`}
                              >
                                {selected && <Check className="h-3 w-3" />}
                              </span>
                              <span className="min-w-0">
                                <span className="block truncate text-xs font-medium text-slate-700">
                                  {document.original_filename}
                                </span>
                                <span className="text-[10px] text-slate-400">
                                  {document.page_count ?? "?"} pages
                                </span>
                              </span>
                            </button>
                          );
                        })}
                      </div>
                      {selectedDocumentIds.length > 0 && (
                        <button
                          type="button"
                          onClick={() => setSelectedDocumentIds([])}
                          className="mt-2 w-full rounded-lg px-2 py-1.5 text-left text-[11px] font-semibold text-brand-600 hover:bg-brand-50"
                        >
                          Use all documents
                        </button>
                      )}
                    </>
                  )}
                </div>
              )}
              </div>
            </div>
          </header>

          <div className="scrollbar-thin flex-1 overflow-y-auto bg-slate-50/50">
            {loadingConversation ? (
              <div className="flex h-full items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-brand-600" />
              </div>
            ) : messages.length === 0 ? (
              <div className="flex h-full items-center justify-center p-6">
                <div className="max-w-lg text-center">
                  <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-indigo-600 text-white shadow-lg shadow-brand-200">
                    <Sparkles className="h-6 w-6" />
                  </span>
                  <h3 className="mt-5 text-xl font-semibold tracking-tight text-slate-900">
                    What would you like to know?
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-slate-500">
                    Ask about obligations, risks, dates, figures, or any detail from your processed
                    documents. Answers use only retrieved source material.
                  </p>
                  {documents.length === 0 && (
                    <Link
                      href="/documents/upload"
                      className="mt-5 inline-flex items-center gap-2 text-sm font-semibold text-brand-600"
                    >
                      <UploadCloud className="h-4 w-4" /> Upload your first PDF
                    </Link>
                  )}
                  <div className="mt-6 grid gap-2 text-left">
                    {exampleQuestions.map((example) => (
                      <button
                        key={example}
                        type="button"
                        onClick={() => {
                          setQuestion(example);
                          textareaRef.current?.focus();
                        }}
                        className="group flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-3 text-xs text-slate-600 transition hover:border-brand-200 hover:text-slate-900"
                      >
                        {example}
                        <ArrowRight className="h-3.5 w-3.5 shrink-0 text-slate-300 group-hover:text-brand-500" />
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-6">
                {messages.map((message) =>
                  message.role === "user" ? (
                    <div key={message.id} className="flex justify-end">
                      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-600 px-4 py-3 text-sm leading-6 text-white shadow-sm">
                        {message.content}
                      </div>
                    </div>
                  ) : (
                    <div key={message.id} className="flex gap-3">
                      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-ink-950 text-brand-300">
                        <Sparkles className="h-4 w-4" />
                      </span>
                      <div className="min-w-0 max-w-[88%]">
                        <div className="rounded-2xl rounded-tl-md border border-slate-200 bg-white px-4 py-3 text-sm leading-7 text-slate-700 shadow-sm">
                          {message.content ? (
                            <p className="whitespace-pre-wrap">
                              {message.content}
                              {message.id.startsWith(PENDING_ASSISTANT_PREFIX) && (
                                <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-brand-500 align-middle" />
                              )}
                            </p>
                          ) : (
                            <div className="flex items-center gap-2 text-xs text-slate-500">
                              <span className="flex gap-1">
                                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400 [animation-delay:-0.3s]" />
                                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400 [animation-delay:-0.15s]" />
                                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400" />
                              </span>
                              Searching your documents…
                            </div>
                          )}
                        </div>
                        {message.citations && message.citations.length > 0 && (
                          <div className="mt-2.5">
                            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                              Sources
                            </p>
                            <div className="flex flex-wrap gap-2">
                              {message.citations.map((citation) => (
                                <CitationCard
                                  key={`${message.id}-${citation.chunk_id}`}
                                  citation={citation}
                                />
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  ),
                )}
                {sending && !streamingEnabled && (
                  <div className="flex gap-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-ink-950 text-brand-300">
                      <Sparkles className="h-4 w-4" />
                    </span>
                    <div className="rounded-2xl rounded-tl-md border border-slate-200 bg-white px-4 py-3 shadow-sm">
                      <div className="flex items-center gap-2 text-xs text-slate-500">
                        <span className="flex gap-1">
                          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400 [animation-delay:-0.3s]" />
                          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400 [animation-delay:-0.15s]" />
                          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-400" />
                        </span>
                        Searching your documents…
                      </div>
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>
            )}
          </div>

          <form onSubmit={(event) => void handleSend(event)} className="border-t border-slate-100 bg-white p-3 sm:p-4">
            <div className="flex items-end gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm transition focus-within:border-brand-300 focus-within:ring-4 focus-within:ring-brand-50">
              <textarea
                ref={textareaRef}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    void handleSend();
                  }
                }}
                rows={1}
                maxLength={4000}
                placeholder="Ask a question about your documents…"
                className="max-h-32 min-h-[38px] flex-1 resize-none border-0 bg-transparent px-2 py-2 text-sm text-slate-800 outline-none placeholder:text-slate-400"
                disabled={sending || loadingConversation}
                aria-label="Question"
              />
              <button
                type="submit"
                disabled={!question.trim() || sending || loadingConversation}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-600 text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
                aria-label="Send question"
              >
                {sending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
              </button>
            </div>
            <p className="mt-2 text-center text-[10px] text-slate-400">
              DocuMind AI answers only from retrieved document context. Verify important information.
            </p>
          </form>
        </section>
      </div>
    </div>
  );
}
