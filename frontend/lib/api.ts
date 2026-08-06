// 后端 API 客户端 + 认证 token 管理。

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const TOKEN_KEY = "acq_token";

export function getToken(): string | null {
  return typeof window !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;
}
export function setToken(t: string): void {
  localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

// ===== 认证 =====

export type AuthUser = {
  id: string;
  username: string;
  phone: string | null;
  is_super_admin: boolean;
  plan: string; // free | basic | super
  plan_expires_at: string | null;
};

// 卖家获取额度（/me 返回）
export type Quota = {
  plan: string;
  total: number | null;
  used: number;
  remaining: number | null;
  unlimited?: boolean;
};

// 单次对话产出的配额信息（SSE/chat 响应透传，用于展示额度与升级提示）
export type QuotaMeta = {
  plan?: string;
  quota?: number;
  used?: number;
  remaining_after?: number;
  new_granted?: number;
  new_skipped?: number;
  exhausted?: boolean;
  upgrade_available?: boolean;
  unlimited?: boolean;
};

export type AuthMe = AuthUser & { quota: Quota };

export async function sendSms(phone: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/auth/sms/send`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "验证码发送失败");
}

export async function register(
  phone: string,
  code: string,
  password: string,
): Promise<AuthUser> {
  const res = await fetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone, code, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "注册失败");
  setToken(data.token);
  return data.user as AuthUser;
}

export async function login(account: string, password: string): Promise<AuthUser> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "登录失败");
  setToken(data.token);
  return data.user as AuthUser;
}

export async function fetchMe(): Promise<AuthMe | null> {
  const token = getToken();
  if (!token) return null;
  const res = await fetch(`${API_BASE}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    clearToken();
    return null;
  }
  return res.json() as Promise<AuthMe>;
}

// ===== V2 聊天 =====

export type ProgressStep = {
  step: string;
  status: "running" | "done";
  message: string;
};

export type Seller = {
  seller_id: string;
  name: string | null;
  business_country: string | null;
  total_feedback: number | null;
  member_since: string | null;
  seller_score: number | null;
  product_count: number | null;
  analysis?: string;
  contacts?: { source: string; type: string; value: string }[];
};

export type ChatResponse = {
  thread_id: string;
  status: "need_input" | "done";
  interrupt?: {
    question: string;
    current: { marketplace?: string; category?: string };
  };
  result?: { sellers: Seller[]; count: number; reply?: string; quota?: QuotaMeta };
  user?: AuthUser;
};

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token
    ? { "Content-Type": "application/json", Authorization: `Bearer ${token}` }
    : { "Content-Type": "application/json" };
}

export async function chat(
  query: string,
  threadId?: string,
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ query, thread_id: threadId }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `chat failed: ${res.status}`);
  return data as ChatResponse;
}

export async function chatResume(
  threadId: string,
  response: Record<string, unknown>,
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/api/chat/resume`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ thread_id: threadId, response }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `resume failed: ${res.status}`);
  return data as ChatResponse;
}

// ===== 会话历史 =====

export type ChatSession = {
  session_id: string;
  created_at: string;
  total_messages: number;
  title: string;
};

export type ChatMsg = {
  seq: number;
  role: string;
  content: string;
  meta?: { sellers?: Seller[]; progress?: ProgressStep[]; done?: boolean; cancelled?: boolean };
};

export async function listSessions(): Promise<ChatSession[]> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/api/chat/sessions`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("list sessions failed");
  return (await res.json()).sessions as ChatSession[];
}

export async function createChatSession(): Promise<{ session_id: string }> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/api/chat/sessions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "create session failed");
  return data as { session_id: string };
}

export async function deleteSession(sessionId: string): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/api/chat/sessions/${sessionId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "删除失败");
}

export async function renameSession(
  sessionId: string,
  title: string,
): Promise<{ title: string }> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/api/chat/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "重命名失败");
  return data;
}

export async function getSessionMessages(
  sessionId: string,
): Promise<ChatMsg[]> {
  const token = getToken();
  const res = await fetch(
    `${API_BASE}/api/chat/sessions/${sessionId}/messages`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (!res.ok) throw new Error("get messages failed");
  return (await res.json()).messages as ChatMsg[];
}

// ===== 流式 =====

export async function streamChat(
  query: string,
  threadId?: string,
): Promise<{
  status: string;
  thread_id?: string;
  msg_id?: string;
  missing?: string[];
  marketplace?: string;
  category?: string;
}> {
  const res = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ query, thread_id: threadId }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `stream failed: ${res.status}`);
  return data;
}

export function connectStream(
  threadId: string,
  onDelta: (text: string) => void,
  onSellers: (sellers: Seller[]) => void,
  onProgress: (steps: ProgressStep[]) => void,
  onDone: (sellers: Seller[], cancelled: boolean | undefined, quota: QuotaMeta | undefined) => void,
  onError: (err: string) => void,
): AbortController {
  const controller = new AbortController();
  const token = getToken();

  fetch(`${API_BASE}/api/chat/stream/${threadId}/events`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: controller.signal,
  })
    .then(async (res) => {
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const data = JSON.parse(line.slice(6));
            if (data.done) onDone(data.sellers || [], data.cancelled, data as QuotaMeta);
            else if (data.delta) onDelta(data.delta);
            else if (data.sellers) onSellers(data.sellers);
            else if (data.progress) onProgress(data.progress);
          }
        }
      }
    })
    .catch((e) => {
      if (e.name !== "AbortError") onError(String(e));
    });

  return controller;
}

export async function cancelStream(threadId: string): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/api/chat/stream/${threadId}/cancel`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`cancel failed: ${res.status}`);
}
