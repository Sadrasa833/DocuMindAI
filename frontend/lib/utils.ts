import type { DocumentStatus } from "./types";

export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatShortDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export const statusMeta: Record<
  DocumentStatus,
  { label: string; className: string; dotClassName: string }
> = {
  uploaded: {
    label: "Uploaded",
    className: "bg-slate-100 text-slate-600",
    dotClassName: "bg-slate-400",
  },
  pending: {
    label: "Queued",
    className: "bg-amber-50 text-amber-700",
    dotClassName: "bg-amber-500",
  },
  processing: {
    label: "Processing",
    className: "bg-blue-50 text-blue-700",
    dotClassName: "bg-blue-500 animate-pulse",
  },
  completed: {
    label: "Ready",
    className: "bg-emerald-50 text-emerald-700",
    dotClassName: "bg-emerald-500",
  },
  failed: {
    label: "Failed",
    className: "bg-rose-50 text-rose-700",
    dotClassName: "bg-rose-500",
  },
};

export function isProcessingStatus(status: DocumentStatus): boolean {
  return status === "pending" || status === "processing" || status === "uploaded";
}

export function truncate(value: string, length: number): string {
  if (value.length <= length) return value;
  return `${value.slice(0, length - 1).trimEnd()}…`;
}
