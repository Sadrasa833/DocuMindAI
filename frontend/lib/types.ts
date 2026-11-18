export type DocumentStatus =
  | "uploaded"
  | "pending"
  | "processing"
  | "completed"
  | "failed";

export type SummaryStyle = "short" | "detailed";

export type SummaryStatus = "none" | "pending" | "processing" | "completed" | "failed";

export interface User {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  created_at: string;
}

export interface AuthResponse {
  user: User;
}

export interface DocumentItem {
  id: string;
  original_filename: string;
  mime_type: string;
  file_size: number;
  status: DocumentStatus;
  page_count: number | null;
  chunk_count: number;
  error_message: string | null;
  summary: string | null;
  summary_style: SummaryStyle | null;
  summary_status: SummaryStatus;
  summary_error: string | null;
  summarized_at: string | null;
  processed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentStats {
  total_documents: number;
  completed_documents: number;
  processing_documents: number;
  failed_documents: number;
  total_chunks: number;
  total_pages: number;
}

export interface DocumentStatusResponse {
  id: string;
  status: DocumentStatus;
  error_message: string | null;
  summary_status: SummaryStatus;
  summary_error: string | null;
  page_count: number | null;
  chunk_count: number;
  updated_at: string;
}

export interface SummaryResponse {
  document_id: string;
  style: SummaryStyle | null;
  status: SummaryStatus;
  summary: string | null;
  error_message: string | null;
  started_at: string | null;
  created_at: string | null;
  updated_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface Citation {
  document_id: string;
  document_name: string;
  page_number: number;
  chunk_id: string;
  available: boolean;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[] | null;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: Message[];
}

export interface CitationValidationSummary {
  enabled: boolean;
  supported_claims: number;
  unsupported_claims: number;
  refused: boolean;
}

export interface ChatResponse {
  answer: string;
  conversation_id: string;
  citations: Citation[];
  citation_validation?: CitationValidationSummary | null;
}

export interface ChatStreamMeta {
  type: "meta";
  conversation_id: string | null;
}

export interface ChatStreamDelta {
  type: "delta";
  content: string;
}

export interface ChatStreamDone {
  type: "done";
  conversation_id: string;
  message_id: string;
  answer: string;
  citations: Citation[];
  replaced: boolean;
  citation_validation?: CitationValidationSummary | null;
}

export interface ChatStreamError {
  type: "error";
  message: string;
}

export type ChatStreamEvent = ChatStreamMeta | ChatStreamDelta | ChatStreamDone | ChatStreamError;

export interface SearchResult {
  chunk_id: string;
  document_id: string;
  document_name: string;
  page_number: number;
  chunk_index: number;
  content: string;
  similarity: number;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
}

export interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
    details?: Array<{ field?: string; message?: string; type?: string }>;
  };
  detail?: string;
}
