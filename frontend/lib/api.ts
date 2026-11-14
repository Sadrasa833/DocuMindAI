import type {
  ApiErrorBody,
  AuthResponse,
  ChatResponse,
  ConversationDetail,
  ConversationSummary,
  DocumentItem,
  DocumentStats,
  DocumentStatus,
  DocumentStatusResponse,
  Page,
  SearchResponse,
  SummaryResponse,
  SummaryStyle,
  User,
} from "./types";

const API_BASE = "/api/proxy/v1";
const AUTH_PATHS = new Set([
  "/auth/login",
  "/auth/register",
  "/auth/refresh",
  "/auth/logout",
]);

export class ApiError extends Error {
  status: number;
  code: string;
  requestId?: string;
  details?: Array<{ field?: string; message?: string; type?: string }>;

  constructor(
    message: string,
    options: {
      status?: number;
      code?: string;
      requestId?: string;
      details?: Array<{ field?: string; message?: string; type?: string }>;
    } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status ?? 0;
    this.code = options.code ?? "request_failed";
    this.requestId = options.requestId;
    this.details = options.details;
  }
}

function errorFromPayload(payload: ApiErrorBody | null, status: number): ApiError {
  const error = payload?.error;
  return new ApiError(
    error?.message ??
      (typeof payload?.detail === "string" ? payload.detail : "Something went wrong. Please try again."),
    {
      status,
      code: error?.code,
      requestId: error?.request_id,
      details: error?.details,
    },
  );
}

async function parseResponseError(response: Response): Promise<ApiError> {
  try {
    const payload = (await response.json()) as ApiErrorBody;
    return errorFromPayload(payload, response.status);
  } catch {
    return new ApiError("The server returned an unexpected response.", {
      status: response.status,
    });
  }
}

let refreshPromise: Promise<boolean> | null = null;

async function clearSessionCookies(): Promise<void> {
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    // The redirect still works even if the backend cannot clear cookies.
  }
}

async function refreshSession(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
    })
      .then(async (response) => {
        if (response.ok) return true;
        await clearSessionCookies();
        return false;
      })
      .catch(async () => {
        await clearSessionCookies();
        return false;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  if (
    window.location.pathname.startsWith("/login") ||
    window.location.pathname.startsWith("/register")
  ) {
    return;
  }
  const next = `${window.location.pathname}${window.location.search}`;
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  optionsOverride: { retryOnUnauthorized?: boolean } = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });

  if (
    response.status === 401 &&
    optionsOverride.retryOnUnauthorized !== false &&
    !AUTH_PATHS.has(path)
  ) {
    const refreshed = await refreshSession();
    if (refreshed) {
      return request<T>(path, options, { retryOnUnauthorized: false });
    }
    redirectToLogin();
  }

  if (!response.ok) {
    throw await parseResponseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const serialized = search.toString();
  return serialized ? `?${serialized}` : "";
}

async function uploadDocument(
  file: File,
  onProgress?: (progress: number) => void,
): Promise<DocumentItem> {
  const send = (allowRefresh: boolean): Promise<DocumentItem> =>
    new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/documents/upload`);
      xhr.withCredentials = true;
      xhr.timeout = 300_000;
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      xhr.onerror = () => reject(new ApiError("Could not reach the server.", { status: 0 }));
      xhr.ontimeout = () =>
        reject(new ApiError("The upload timed out. Please try again.", { status: 0 }));
      xhr.onload = async () => {
        if (xhr.status === 401 && allowRefresh) {
          const refreshed = await refreshSession();
          if (refreshed) {
            send(false).then(resolve).catch(reject);
            return;
          }
          redirectToLogin();
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as DocumentItem);
          } catch {
            reject(new ApiError("The server returned an unexpected response.", { status: xhr.status }));
          }
          return;
        }
        let payload: ApiErrorBody | null = null;
        try {
          payload = JSON.parse(xhr.responseText) as ApiErrorBody;
        } catch {
          payload = null;
        }
        reject(errorFromPayload(payload, xhr.status));
      };
      const formData = new FormData();
      formData.append("file", file);
      xhr.send(formData);
    });
  return send(true);
}

export interface DocumentListParams {
  page?: number;
  pageSize?: number;
  status?: DocumentStatus | "all";
  search?: string;
  sortBy?: "created_at" | "original_filename" | "file_size";
  sortOrder?: "asc" | "desc";
}

export const api = {
  auth: {
    me: () => request<User>("/auth/me"),
    login: (email: string, password: string) =>
      request<AuthResponse>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      }),
    register: (fullName: string, email: string, password: string) =>
      request<AuthResponse>("/auth/register", {
        method: "POST",
        body: JSON.stringify({ full_name: fullName, email, password }),
      }),
    logout: async () => {
      try {
        await request<void>("/auth/logout", { method: "POST" }, { retryOnUnauthorized: false });
      } finally {
        if (typeof window !== "undefined") {
          window.location.assign("/login");
        }
      }
    },
    updateProfile: (fullName: string) =>
      request<User>("/auth/me", {
        method: "PATCH",
        body: JSON.stringify({ full_name: fullName }),
      }),
    changePassword: (currentPassword: string, newPassword: string) =>
      request<AuthResponse>("/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      }),
  },
  documents: {
    list: (params: DocumentListParams = {}) =>
      request<Page<DocumentItem>>(
        `/documents${queryString({
          page: params.page,
          page_size: params.pageSize,
          status: params.status === "all" ? undefined : params.status,
          search: params.search,
          sort_by: params.sortBy,
          sort_order: params.sortOrder,
        })}`,
      ),
    get: (id: string) => request<DocumentItem>(`/documents/${id}`),
    stats: () => request<DocumentStats>("/documents/stats"),
    status: (id: string) => request<DocumentStatusResponse>(`/documents/${id}/status`),
    delete: (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" }),
    summarize: (id: string, style: SummaryStyle, force = false) =>
      request<SummaryResponse>(`/documents/${id}/summarize`, {
        method: "POST",
        body: JSON.stringify({ style, force }),
      }),
    upload: uploadDocument,
  },
  search: (query: string, documentIds: string[] = [], topK = 5) =>
    request<SearchResponse>("/search", {
      method: "POST",
      body: JSON.stringify({ query, document_ids: documentIds, top_k: topK }),
    }),
  chat: (
    question: string,
    documentIds: string[] = [],
    conversationId: string | null = null,
  ) =>
    request<ChatResponse>("/chat", {
      method: "POST",
      body: JSON.stringify({
        question,
        document_ids: documentIds,
        conversation_id: conversationId,
      }),
    }),
  conversations: {
    list: (page = 1, pageSize = 50) =>
      request<Page<ConversationSummary>>(
        `/conversations${queryString({ page, page_size: pageSize })}`,
      ),
    create: (title?: string) =>
      request<ConversationSummary>("/conversations", {
        method: "POST",
        body: JSON.stringify({ title: title || null }),
      }),
    get: (id: string) => request<ConversationDetail>(`/conversations/${id}`),
    delete: (id: string) =>
      request<void>(`/conversations/${id}`, { method: "DELETE" }),
  },
};

export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return "You are sending requests too quickly. Please wait a moment and try again.";
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "An unexpected error occurred. Please try again.";
}
