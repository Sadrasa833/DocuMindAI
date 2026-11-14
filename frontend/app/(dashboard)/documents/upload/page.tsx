"use client";

import { DragEvent, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle2,
  FileText,
  Loader2,
  ShieldCheck,
  UploadCloud,
} from "lucide-react";
import { api, getErrorMessage } from "@/lib/api";
import type { DocumentItem } from "@/lib/types";
import { formatFileSize } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { InlineAlert } from "@/components/states";

const MAX_FILE_BYTES = 20 * 1024 * 1024;

export default function UploadPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploaded, setUploaded] = useState<DocumentItem | null>(null);

  function selectFile(candidate: File | undefined) {
    setError(null);
    setUploaded(null);
    setProgress(0);
    if (!candidate) return;
    const isPdf =
      candidate.type === "application/pdf" || candidate.name.toLowerCase().endsWith(".pdf");
    if (!isPdf) {
      setError("Only PDF documents are supported.");
      return;
    }
    if (candidate.size > MAX_FILE_BYTES) {
      setError("The file is larger than the 20 MB upload limit.");
      return;
    }
    setFile(candidate);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    selectFile(event.dataTransfer.files?.[0]);
  }

  async function handleUpload() {
    if (!file) {
      setError("Choose a PDF to upload first.");
      return;
    }
    setUploading(true);
    setError(null);
    setProgress(1);
    try {
      const document = await api.documents.upload(file, setProgress);
      setProgress(100);
      setUploaded(document);
      setFile(null);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-6">
      <Link
        href="/documents"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 hover:text-slate-800"
      >
        <ArrowLeft className="h-4 w-4" /> Back to documents
      </Link>

      <PageHeader
        eyebrow="Add knowledge"
        title="Upload a document"
        description="DocuMind AI extracts text page by page, builds semantic embeddings, and prepares the PDF for grounded question answering."
      />

      <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        <section className="card p-5 sm:p-7">
          {error && <div className="mb-5"><InlineAlert>{error}</InlineAlert></div>}
          {uploaded ? (
            <div className="flex flex-col items-center py-8 text-center">
              <span className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-50 text-emerald-600">
                <CheckCircle2 className="h-7 w-7" />
              </span>
              <h2 className="mt-4 text-lg font-semibold text-slate-900">Upload complete</h2>
              <p className="mt-1 max-w-md text-sm leading-6 text-slate-500">
                Your document was accepted and queued for background processing. Open it to track
                the status and to generate a summary once it is ready.
              </p>
              <div className="mt-5 flex w-full max-w-md items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3 text-left">
                <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-white text-slate-500 shadow-sm">
                  <FileText className="h-5 w-5" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-slate-800">
                    {uploaded.original_filename}
                  </p>
                  <p className="text-xs text-slate-500">{formatFileSize(uploaded.file_size)}</p>
                </div>
                <StatusBadge status={uploaded.status} />
              </div>
              <div className="mt-6 flex flex-wrap justify-center gap-3">
                <Link href={`/documents/${uploaded.id}`} className="btn-primary">
                  View document
                </Link>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => {
                    setUploaded(null);
                    setProgress(0);
                  }}
                >
                  Upload another
                </button>
              </div>
            </div>
          ) : (
            <>
              <div
                onDragEnter={(event) => {
                  event.preventDefault();
                  setDragActive(true);
                }}
                onDragOver={(event) => event.preventDefault()}
                onDragLeave={(event) => {
                  event.preventDefault();
                  if (event.currentTarget === event.target) setDragActive(false);
                }}
                onDrop={handleDrop}
                className={`flex flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-14 text-center transition ${
                  dragActive
                    ? "border-brand-400 bg-brand-50/70"
                    : "border-slate-200 bg-slate-50/60 hover:border-slate-300"
                }`}
              >
                <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-white text-brand-600 shadow-sm ring-1 ring-slate-200">
                  <UploadCloud className="h-6 w-6" />
                </span>
                <p className="mt-5 text-base font-semibold text-slate-900">
                  Drag and drop your PDF here
                </p>
                <p className="mt-1 text-sm text-slate-500">or choose a file from your computer</p>
                <button
                  type="button"
                  className="btn-secondary mt-5"
                  onClick={() => inputRef.current?.click()}
                  disabled={uploading}
                >
                  Browse files
                </button>
                <input
                  ref={inputRef}
                  type="file"
                  accept=".pdf,application/pdf"
                  className="hidden"
                  onChange={(event) => selectFile(event.target.files?.[0])}
                />
                <p className="mt-4 text-xs text-slate-400">PDF only · 20 MB maximum</p>
              </div>

              {file && (
                <div className="mt-5 rounded-xl border border-slate-200 p-4">
                  <div className="flex items-center gap-3">
                    <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
                      <FileText className="h-5 w-5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-slate-800">{file.name}</p>
                      <p className="text-xs text-slate-500">{formatFileSize(file.size)}</p>
                    </div>
                    {!uploading && (
                      <button
                        type="button"
                        className="text-xs font-semibold text-slate-500 hover:text-slate-800"
                        onClick={() => setFile(null)}
                      >
                        Remove
                      </button>
                    )}
                  </div>
                  {(uploading || progress > 0) && (
                    <div className="mt-4">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-medium text-slate-600">
                          {uploading ? "Uploading…" : "Uploaded"}
                        </span>
                        <span className="text-slate-500">{progress}%</span>
                      </div>
                      <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full bg-brand-600 transition-all duration-300"
                          style={{ width: `${Math.max(progress, 2)}%` }}
                        />
                      </div>
                    </div>
                  )}
                </div>
              )}

              <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                <Link href="/documents" className="btn-secondary">
                  Cancel
                </Link>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => void handleUpload()}
                  disabled={!file || uploading}
                >
                  {uploading ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" /> Uploading
                    </>
                  ) : (
                    <>
                      <UploadCloud className="h-4 w-4" /> Upload and process
                    </>
                  )}
                </button>
              </div>
            </>
          )}
        </section>

        <aside className="space-y-4">
          <div className="card p-5">
            <h2 className="text-sm font-semibold text-slate-900">What happens next?</h2>
            <ol className="mt-4 space-y-4">
              {[
                ["Secure upload", "Your file is validated and stored with a private, generated key."],
                ["Text extraction", "Text is extracted page by page with page references preserved."],
                ["Chunking & embeddings", "Sentences are grouped into overlapping chunks and embedded."],
                ["Ready to query", "Search and grounded chat become available when processing completes."],
              ].map(([title, description], index) => (
                <li key={title} className="flex gap-3">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-[11px] font-bold text-brand-700">
                    {index + 1}
                  </span>
                  <div>
                    <p className="text-sm font-medium text-slate-800">{title}</p>
                    <p className="mt-0.5 text-xs leading-5 text-slate-500">{description}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
          <div className="flex gap-3 rounded-2xl border border-emerald-100 bg-emerald-50/70 p-4">
            <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
            <p className="text-xs leading-5 text-emerald-800">
              Files are scoped to your account. Document contents are treated as untrusted data and
              cannot change the assistant&apos;s instructions.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
