import { Check, FileSearch, Quote, ShieldCheck, Sparkles } from "lucide-react";
import { Logo } from "@/components/logo";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50 lg:grid lg:grid-cols-[1.05fr_1fr]">
      <aside className="relative hidden overflow-hidden bg-ink-950 p-12 text-white lg:flex lg:flex-col">
        <div className="absolute -left-24 top-1/3 h-96 w-96 rounded-full bg-brand-600/20 blur-3xl" />
        <div className="absolute -right-32 bottom-0 h-80 w-80 rounded-full bg-cyan-400/10 blur-3xl" />
        <div className="relative z-10">
          <Logo light href="/login" />
        </div>

        <div className="relative z-10 my-auto max-w-xl py-16">
          <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-medium text-brand-200">
            <Sparkles className="h-3.5 w-3.5" />
            Retrieval-augmented intelligence
          </span>
          <h1 className="mt-6 text-4xl font-semibold leading-tight tracking-tight xl:text-5xl">
            Turn long documents into clear, cited answers.
          </h1>
          <p className="mt-5 max-w-lg text-base leading-7 text-slate-400">
            DocuMind AI reads your PDFs, finds the passages that matter, and answers
            questions using only what your documents actually say.
          </p>

          <div className="mt-9 grid gap-3">
            {[
              "Semantic search across every page",
              "Answers with document and page citations",
              "Private, ownership-scoped workspaces",
            ].map((feature) => (
              <div key={feature} className="flex items-center gap-3 text-sm text-slate-300">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-400/10 text-emerald-300">
                  <Check className="h-3.5 w-3.5" />
                </span>
                {feature}
              </div>
            ))}
          </div>

          <div className="mt-12 rounded-2xl border border-white/10 bg-white/[0.04] p-5 backdrop-blur">
            <Quote className="h-5 w-5 text-brand-300" />
            <p className="mt-3 text-sm leading-6 text-slate-300">
              “What are the payment terms and renewal risks in this agreement?”
            </p>
            <div className="mt-4 flex items-center gap-3 rounded-xl border border-white/10 bg-ink-900/60 px-3 py-2.5">
              <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-500/20">
                <FileSearch className="h-3.5 w-3.5 text-brand-300" />
              </span>
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-slate-200">msa_agreement.pdf</p>
                <p className="text-[10px] text-slate-500">Page 4 · Source citation</p>
              </div>
            </div>
          </div>
        </div>

        <div className="relative z-10 flex items-center gap-2 text-xs text-slate-500">
          <ShieldCheck className="h-4 w-4" />
          Your documents are never shared across accounts.
        </div>
      </aside>

      <main className="flex min-h-screen flex-col px-6 py-8 sm:px-10 lg:px-12">
        <div className="lg:hidden">
          <Logo href="/login" />
        </div>
        <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center py-12">
          {children}
        </div>
        <p className="mx-auto w-full max-w-md text-xs text-slate-400">
          © {new Date().getFullYear()} DocuMind AI · Secure document intelligence
        </p>
      </main>
    </div>
  );
}
