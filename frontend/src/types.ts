export interface SessionState {
  authenticated: boolean;
  username: string;
}

export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface LatestAnalysis {
  final_score: string;
  baseline_score: string;
  trend_score: string;
  boost_score: string;
  reasoning: string;
  provider: string;
  model_name: string;
  calculated_at: string;
}

export interface Product {
  id: number;
  asin: string;
  title: string;
  category: string;
  price: string | null;
  rating: string | null;
  reviews_count: number;
  product_url: string;
  search_keyword: string;
  latest_analysis: LatestAnalysis | null;
}

export type JobType =
  | "product_collection"
  | "trend_collection"
  | "product_analysis";
export type JobStatus = "pending" | "running" | "succeeded" | "failed";

export interface JobRun {
  id: string;
  job_type: JobType;
  status: JobStatus;
  total_items: number;
  processed_items: number;
  failed_items: number;
  error_message: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface SuccessfulProduct {
  id: number;
  title: string;
  category: string;
  keywords: string[];
}

export interface SuccessfulProductPayload {
  title: string;
  category: string;
  keywords: string[];
}

export interface ImportResult {
  created_count: number;
  updated_count: number;
  total_count: number;
}

export type ValidationPayload = Record<string, unknown>;
