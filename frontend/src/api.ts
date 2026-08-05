import type {
  ImportResult,
  JobRun,
  JobType,
  PaginatedResponse,
  Product,
  SessionState,
  SuccessfulProduct,
  SuccessfulProductPayload,
  ValidationPayload,
} from "./types";

type UnauthorizedHandler = () => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;

export class ApiError extends Error {
  readonly status: number;
  readonly payload: ValidationPayload | null;

  constructor(message: string, status: number, payload: ValidationPayload | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

function getCookie(name: string): string {
  const prefix = `${name}=`;
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : "";
}

function sameOriginPath(value: string): string {
  if (!/^https?:\/\//i.test(value)) {
    return value;
  }
  const url = new URL(value);
  return `${url.pathname}${url.search}`;
}

function payloadMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object") {
    return fallback;
  }
  const record = payload as Record<string, unknown>;
  if (typeof record.detail === "string") {
    return record.detail;
  }
  const messages: string[] = [];
  for (const [field, value] of Object.entries(record)) {
    if (Array.isArray(value)) {
      messages.push(`${field}: ${value.map(String).join(" ")}`);
    } else if (typeof value === "string") {
      messages.push(`${field}: ${value}`);
    }
  }
  return messages.join(" ") || fallback;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrfToken = getCookie("csrftoken");
    if (csrfToken) {
      headers.set("X-CSRFToken", csrfToken);
    }
  }

  let response: Response;
  try {
    response = await fetch(sameOriginPath(path), {
      ...options,
      headers,
      credentials: "include",
    });
  } catch {
    throw new ApiError("Network request failed. Check that the service is running.", 0, null);
  }

  const raw = await response.text();
  let payload: unknown = null;
  if (raw) {
    try {
      payload = JSON.parse(raw);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const error = new ApiError(
      payloadMessage(payload, `Request failed with status ${response.status}.`),
      response.status,
      payload && typeof payload === "object" ? (payload as ValidationPayload) : null,
    );
    if (
      (response.status === 401 || response.status === 403) &&
      !path.includes("/auth/login/")
    ) {
      unauthorizedHandler?.();
    }
    throw error;
  }
  return payload as T;
}

const jobPaths: Record<JobType, string> = {
  product_collection: "/api/jobs/product-collection/",
  trend_collection: "/api/jobs/trend-collection/",
  product_analysis: "/api/jobs/product-analysis/",
};

export const api = {
  getSession: () => request<SessionState>("/api/auth/session/"),
  login: (username: string, password: string) =>
    request<SessionState>("/api/auth/login/", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<SessionState>("/api/auth/logout/", { method: "POST" }),
  getProducts: (path = "/api/products/") =>
    request<PaginatedResponse<Product>>(path),
  launchJob: (jobType: JobType) =>
    request<JobRun>(jobPaths[jobType], { method: "POST", body: "{}" }),
  getJob: (id: string) => request<JobRun>(`/api/jobs/${id}/`),
  getSuccessfulProducts: (path = "/api/sales-boost/") =>
    request<PaginatedResponse<SuccessfulProduct>>(path),
  createSuccessfulProduct: (payload: SuccessfulProductPayload) =>
    request<SuccessfulProduct>("/api/sales-boost/", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  importSuccessfulProducts: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return request<ImportResult>("/api/sales-boost/import/", {
      method: "POST",
      body: formData,
    });
  },
};
