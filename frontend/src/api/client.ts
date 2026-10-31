import type {
  BenchmarkReport, Config, DecisionRequest, FlightsResponse, NegotiateRequest, NegotiationEvent, NegotiationResult,
  WaveScenario, WaveSummary,
} from "./types";

// ?mock=1 forces the offline demo (the backend's recorded sample run); otherwise the mock is used only when the API is unreachable.
const params = new URLSearchParams(location.search);
const forceMock = params.has("mock");
const fast = params.has("fast");                      // replay without delays (screenshots, tests)
let offline = forceMock;
export const isOffline = () => offline;

const mock = () => import("../mock/samples");

export class ApiError extends Error {
  constructor(public code: string, message: string, public status = 0) { super(message); }
}

class NotDeployed extends Error {}

async function get<T>(path: string, fallback: () => Promise<T>, valid: (x: any) => boolean = () => true): Promise<T> {
  if (offline) return fallback();
  try {
    const r = await fetch(path);
    const ct = r.headers.get("content-type") ?? "";
    if (!ct.includes("json")) throw new TypeError("no API");          // dev server returned index.html
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      // FastAPI's own {"detail": "Not Found"} means the route does not exist: wave mode is not deployed
      if (r.status === 404 && !e?.error) throw new NotDeployed(path);
      throw new ApiError(e?.error?.code ?? String(r.status), e?.error?.message ?? r.statusText, r.status);
    }
    const body = await r.json();
    if (!valid(body)) throw new NotDeployed(path);              // e.g. the v1 /api/config shape
    return body as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    offline = true;
    return fallback();
  }
}

export const api = {
  config: () => get<Config>("/api/config", async () => (await mock()).mockConfig, (c) => Array.isArray(c?.modes) && Array.isArray(c?.models)),
  waves: (q = "") => get<WaveSummary[]>(`/api/waves?limit=50${q ? `&q=${encodeURIComponent(q)}` : ""}`, async () => (await mock()).mockWaves),
  randomWave: () => get<WaveSummary>("/api/waves/random", async () => (await mock()).mockWaves[0]),
  flights: (q = "", airport = "") => get<FlightsResponse>(`/api/flights?limit=30${q ? `&q=${encodeURIComponent(q)}` : ""}${airport ? `&airport=${encodeURIComponent(airport)}` : ""}`, async () => {
    const f = (await mock()).mockFlights, t = q.trim().toLowerCase();
    const rows = f.flights.filter((r) => !t || [r.flight, r.origin, r.dest, r.wave_id].some((x) => x.toLowerCase().includes(t)));
    return { flights: rows, total: rows.length };
  }),
  wave: (id: string) => get<WaveScenario>(`/api/wave/${encodeURIComponent(id)}`, async () => (await mock()).wave),
  benchmark: () => get<BenchmarkReport>("/api/benchmark", async () => {
    return (await mock()).mockBenchmark;
  }),

  async decision(req: DecisionRequest): Promise<{ ok: true; recorded_at: string }> {
    if (offline) return { ok: true, recorded_at: new Date().toISOString() };
    const r = await fetch("/api/decision", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(req) });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new ApiError(e?.error?.code ?? String(r.status), e?.error?.message ?? r.statusText, r.status);
    }
    return r.json();
  },

  /** Streams a negotiation. Resolves when the stream ends; call the returned abort to stop early. */
  negotiate(req: NegotiateRequest, onEvent: (e: NegotiationEvent) => void, signal: AbortSignal): Promise<void> {
    return offline ? replayMock(onEvent, signal) : streamSSE(req, onEvent, signal);
  },
};

async function streamSSE(req: NegotiateRequest, onEvent: (e: NegotiationEvent) => void, signal: AbortSignal) {
  const r = await fetch("/api/negotiate-wave", {
    method: "POST", signal,
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify({ ...req, stream: true }),
  });
  if (!r.ok || !r.body) {
    const e = await r.json().catch(() => ({}));
    const retry = e?.error?.retry_after_s ?? e?.retry_after_s;
    throw new ApiError(e?.error?.code ?? String(r.status),
      (e?.error?.message ?? r.statusText) + (retry ? ` Try again in ${retry} s.` : ""), r.status);
  }
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value.replace(/\r\n/g, "\n");
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) onEvent({ event, data: JSON.parse(data.join("\n")) } as NegotiationEvent);
    }
  }
}

async function replayMock(onEvent: (e: NegotiationEvent) => void, signal: AbortSignal) {
  const { mockLog, mockResult, wave } = await mock();
  const wait = (ms: number) => new Promise<void>((res, rej) => {
    if (fast) ms = 0;
    const t = setTimeout(res, ms);
    signal.addEventListener("abort", () => { clearTimeout(t); rej(new DOMException("aborted", "AbortError")); }, { once: true });
  });
  onEvent({ event: "start", data: { run_id: mockResult.run_id, wave_id: wave.id, mode: mockResult.mode, model_chain: [mockResult.model], started_at: new Date().toISOString() } });
  for (const m of mockLog) {
    await wait(m.type === "AGENT_TURN" ? 900 : 260);
    onEvent({ event: "message", data: m });
  }
  await wait(400);
  const { log: _omit, ...result } = mockResult;
  onEvent({ event: "result", data: result as NegotiationResult });
}
