export function CardSkeleton() {
  return (
    <div className="card p-6">
      <div className="skeleton h-4 w-24" />
      <div className="skeleton mt-4 h-8 w-16" />
      <div className="skeleton mt-3 h-3 w-32" />
    </div>
  );
}

export function RowSkeleton() {
  return (
    <div className="flex items-center gap-4 border-b border-slate-100 px-5 py-4 last:border-0">
      <div className="skeleton h-10 w-10 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="skeleton h-3.5 w-2/5" />
        <div className="skeleton mt-2 h-3 w-1/4" />
      </div>
      <div className="skeleton h-6 w-20" />
    </div>
  );
}

export function PageLoading() {
  return (
    <div className="space-y-6">
      <div className="skeleton h-9 w-64" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <CardSkeleton key={index} />
        ))}
      </div>
      <div className="card">
        {Array.from({ length: 4 }).map((_, index) => (
          <RowSkeleton key={index} />
        ))}
      </div>
    </div>
  );
}
