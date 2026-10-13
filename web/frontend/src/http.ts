export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// JSON reads and profile downloads use the same session and cache policy.
export function fetchResponse(
  path: string,
  init: RequestInit = {},
  csrfToken?: string,
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  return fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  csrfToken?: string,
): Promise<T> {
  const response = await fetchResponse(path, init, csrfToken);
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch {
      // Error bodies are deliberately optional.
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function listAll<T>(path: string, signal?: AbortSignal): Promise<T[]> {
  const items: T[] = [];
  for (let offset = 0; ; offset += 100) {
    const page = await request<T[]>(`${path}?limit=100&offset=${offset}`, { signal });
    items.push(...page);
    if (page.length < 100) return items;
  }
}
