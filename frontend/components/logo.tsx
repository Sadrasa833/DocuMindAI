import Link from "next/link";
import { FileSearch } from "lucide-react";
import { cn } from "@/lib/utils";

export function Logo({
  compact = false,
  light = false,
  href = "/dashboard",
}: {
  compact?: boolean;
  light?: boolean;
  href?: string;
}) {
  return (
    <Link href={href} className="inline-flex items-center gap-3">
      <span
        className={cn(
          "flex h-10 w-10 items-center justify-center rounded-xl",
          light ? "bg-white/10 text-white ring-1 ring-white/20" : "bg-brand-600 text-white shadow-sm",
        )}
      >
        <FileSearch className="h-5 w-5" strokeWidth={2.2} />
      </span>
      {!compact && (
        <span className="leading-none">
          <span
            className={cn(
              "block text-base font-bold tracking-tight",
              light ? "text-white" : "text-slate-900",
            )}
          >
            DocuMind <span className={light ? "text-brand-300" : "text-brand-600"}>AI</span>
          </span>
          <span
            className={cn(
              "mt-1 block text-[10px] font-medium uppercase tracking-[0.18em]",
              light ? "text-slate-400" : "text-slate-400",
            )}
          >
            Document intelligence
          </span>
        </span>
      )}
    </Link>
  );
}
