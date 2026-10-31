import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isOffline } from "./api/client";
import type { Config, JointPlan, Mode, NegotiationMessage, NegotiationResult, WaveScenario, WaveSummary } from "./api/types";
import { Gantt, GanttLegend, type Rejection } from "./components/Gantt";
import { Log } from "./components/Log";
import { Checks, Outcome, PlanTable, Sources, Weather } from "./components/Panels";
import { clock, dateLabel } from "./format";

type Status = "idle" | "running" | "done" | "error";
type Decision = { kind: "APPROVE" | "OVERRIDE"; at: string; note?: string } | null;
const MODE_LABEL: Record<Mode, string> = { formulas: "Formulas", decision: "Decision models", "decision+claude": "Decision models + Claude brief" };

export function WaveScreen() {
  const [config, setConfig] = useState<Config | null>(null);
  const [waves, setWaves] = useState<WaveSummary[]>([]);
  const [wave, setWave] = useState<WaveScenario | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("decision+claude");
  const [model, setModel] = useState("");
  const [replay, setReplay] = useState(true);

  const [status, setStatus] = useState<Status>("idle");
  const [messages, setMessages] = useState<NegotiationMessage[]>([]);
  const [table, setTable] = useState<JointPlan | null>(null);
  const [rejection, setRejection] = useState<Rejection | null>(null);
  const [result, setResult] = useState<NegotiationResult | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [view, setView] = useState<"plan" | "base">("plan");
  const [decision, setDecision] = useState<Decision>(null);
  const [overriding, setOverriding] = useState(false);
  const [note, setNote] = useState("");
  const [deciding, setDeciding] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const loadWave = useCallback(async (id: string) => {
    abort.current?.abort();
    setStatus("idle"); setMessages([]); setTable(null); setResult(null); setRejection(null); setRunErr(null);
    setDecision(null); setOverriding(false); setNote(""); setView("plan");
    try { setWave(await api.wave(id)); setLoadErr(null); history.replaceState(null, "", `#/wave/${id}`); }
    catch (e) { setLoadErr(`Couldn't load wave ${id}: ${(e as Error).message}`); }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [c, ws] = await Promise.all([api.config(), api.waves()]);
        setConfig(c); setMode(c.default_mode); setModel(c.default_model); setReplay(c.replay_available); setWaves(ws);
        const fromHash = location.hash.match(/#\/wave\/(.+)/)?.[1];
        const first = fromHash ?? ws[0]?.id ?? (await api.randomWave()).id;
        await loadWave(decodeURIComponent(first));
      } catch (e) { setLoadErr(`Couldn't reach the negotiator: ${(e as Error).message}`); }
    })();
  }, [loadWave]);

  const negotiate = async () => {
    if (!wave) return;
    abort.current?.abort();
    const ctl = new AbortController(); abort.current = ctl;
    setStatus("running"); setMessages([]); setTable(null); setResult(null); setRejection(null); setRunErr(null); setDecision(null); setView("plan");
    try {
      await api.negotiate({ wave_id: wave.id, mode, model: mode === "formulas" ? undefined : model, replay }, (ev) => {
        if (ev.event === "message") {
          const m = ev.data;
          setMessages((xs) => [...xs, m]);
          const b = m.body;
          if (b.type === "OPEN" || b.type === "MOVE" || b.type === "COUNTER") setTable(b.plan);
          if (b.type === "MOVE_REJECTED") setRejection({ seq: m.seq, move: b.move, violations: b.violations });
        } else if (ev.event === "result") {
          setResult(ev.data); setTable(ev.data.final_plan); setRejection(null); setStatus("done");
          if (ev.data.log) setMessages(ev.data.log);
        } else if (ev.event === "error") {
          setRunErr(ev.data.message); setStatus("error");
        }
      }, ctl.signal);
      setStatus((s) => (s === "running" ? "error" : s));
      setRunErr((e) => e ?? null);
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setRunErr((e as Error).message); setStatus("error");
    }
  };

  const decide = async (kind: "APPROVE" | "OVERRIDE") => {
    if (!result) return;
    setDeciding(true);
    try {
      const r = await api.decision({ run_id: result.run_id, decision: kind, note: kind === "OVERRIDE" ? note.trim() : undefined });
      setDecision({ kind, at: r.recorded_at, note: note.trim() || undefined }); setOverriding(false);
    } catch (e) { setRunErr(`Decision not recorded: ${(e as Error).message}`); }
    finally { setDeciding(false); }
  };

  // ?autorun starts the negotiation as soon as the wave has loaded (pitch and screenshots)
  const autorun = useRef(new URLSearchParams(location.search).has("autorun"));
  useEffect(() => {
    if (wave && autorun.current) { autorun.current = false; negotiate(); }
  }, [wave]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = view === "base" && result ? result.baseline.plan : table;
  const summary = useMemo(() => finding(wave, status, messages, result), [wave, status, messages, result]);

  if (!wave) {
    return (
      <main className="canvas">
        <div className="titlerow"><div><p className="product">Disruption Negotiator</p><h1>Arrival wave</h1>
          <p className="sub">{loadErr ?? "Loading the wave…"}</p></div></div>
        {loadErr && <button className="btn" onClick={() => location.reload()}>Retry</button>}
      </main>
    );
  }
  const last = messages[messages.length - 1];
  const round = last?.round ?? 0;

  return (
    <main className="canvas">
      <header className="titlerow">
        <div>
          <p className="product">Disruption Negotiator · {wave.airport.name}</p>
          <h1>{wave.airport.iata} arrivals {clock(wave.window_start, 0)}–{clock(wave.window_start, 60)}, {dateLabel(wave.window_start)}</h1>
          <p className="sub">{summary}</p>
        </div>
        <div className="actions">
          {decision ? (
            <span className={`pill ${decision.kind === "APPROVE" ? "pos" : "warn"}`}>
              {decision.kind === "APPROVE" ? "Approved" : "Overridden"} {new Date(decision.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · logged
            </span>
          ) : (
            <>
              <button className="btn" disabled={status !== "done" || deciding} onClick={() => setOverriding((o) => !o)} aria-expanded={overriding}>Override</button>
              <button className="btn primary" disabled={status !== "done" || deciding} onClick={() => decide("APPROVE")}>Approve plan</button>
            </>
          )}
        </div>
      </header>

      {overriding && !decision && (
        <div className="card decide" style={{ marginBottom: "var(--gap)" }}>
          <label htmlFor="note" style={{ fontWeight: 500 }}>Why are you overriding this plan?</label>
          <textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} aria-describedby="note-help"
            placeholder="For example: holding DL2384 at A14, DL1490 is now leaving at 16:30" />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8 }}>
            <span id="note-help" className="muted" style={{ fontSize: 12 }}>The note is required and is logged with the run.</span>
            <div className="actions">
              <button className="btn text" onClick={() => setOverriding(false)}>Cancel</button>
              <button className="btn" disabled={!note.trim() || deciding} onClick={() => decide("OVERRIDE")}>Record override</button>
            </div>
          </div>
        </div>
      )}

      <div className="toolbar">
        <label className="field">Wave
          <select className="select" value={wave.id} onChange={(e) => loadWave(e.target.value)}>
            {waves.map((w) => <option key={w.id} value={w.id}>{w.id} · {w.n_flights} flights</option>)}
            {!waves.some((w) => w.id === wave.id) && <option value={wave.id}>{wave.id}</option>}
          </select>
        </label>
        <button className="btn text" onClick={async () => loadWave((await api.randomWave()).id)}>Random wave</button>
        <div className="seg" role="group" aria-label="Decision mode">
          {(config?.modes ?? ["formulas", "decision", "decision+claude"]).map((m) => (
            <button key={m} aria-pressed={mode === m} onClick={() => setMode(m)}>{MODE_LABEL[m]}</button>
          ))}
        </div>
        {mode !== "formulas" && config && (
          <label className="field">Model
            <select className="select" value={model} onChange={(e) => setModel(e.target.value)}>
              {config.models.filter((m) => m.provider !== "formula").map((m) => (
                <option key={m.id} value={m.id} disabled={!m.available}>{m.label}{m.benchmark_rank === 1 ? " (benchmark winner)" : ""}{m.available ? "" : " (unavailable)"}</option>
              ))}
            </select>
          </label>
        )}
        {config?.replay_available && (
          <label className="field" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={replay} onChange={(e) => setReplay(e.target.checked)} style={{ accentColor: "var(--accent)" }} />
            Replay recorded run
          </label>
        )}
        <button className="btn" onClick={negotiate} disabled={status === "running"}>
          {status === "running" ? `Negotiating, round ${round}…` : status === "idle" ? "Start negotiation" : "Run again"}
        </button>
        {isOffline() && <span className="pill warn">Offline demo: backend not reachable</span>}
      </div>

      <div className="grid" style={{ alignItems: "stretch" }}>
        <div className="span-8 stack">
          <section className="card">
            <div className="card-head">
              <h2>Runways and gates</h2>
              {result ? (
                <div className="seg" role="group" aria-label="Plan shown">
                  <button aria-pressed={view === "plan"} onClick={() => setView("plan")}>Agreed plan</button>
                  <button aria-pressed={view === "base"} onClick={() => setView("base")}>Status quo</button>
                </div>
              ) : <span className="meta">{table ? "Table plan, live" : "Fixed traffic only, no plan yet"}</span>}
            </div>
            <Gantt wave={wave} plan={shown} rejection={view === "plan" ? rejection : null} />
            <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap", marginTop: 10 }}>
              <GanttLegend />
              <span className="reject-note" style={{ marginTop: 0 }} aria-live="assertive">
                {rejection && status === "running" ? `Vetoed: ${rejection.move.label}. ${rejection.violations[0]?.detail ?? ""}` : ""}
              </span>
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>{result ? "Agreed plan against the status quo" : "Flights in this wave"}</h2>
              <span className="meta">{result ? `${result.outcome === "consensus" ? "Consensus" : "Deterministic fallback"} · round ${result.rounds} of 3` : `${wave.flights.length} negotiated · ${wave.queue.length} fixed arrivals`}</span>
            </div>
            <Outcome ev={result?.evaluation ?? null} base={result?.baseline.evaluation ?? null} />
            <div style={{ height: 16 }} />
            <PlanTable wave={wave} plan={result ? result.final_plan : table} baseline={result?.baseline.plan ?? null} />
          </section>
        </div>

        <div className="span-4 stack" style={{ gridTemplateRows: "auto 1fr" }}>
          <section className="card">
            <div className="card-head"><h2>Duty manager brief</h2><span className="meta">{result?.narration ? (result.narration.source === "claude" ? `Written by ${result.narration.model ?? "Claude"} from the log` : "Template from the log") : ""}</span></div>
            {result?.narration ? <p className="brief">{result.narration.brief}</p>
              : <p className="empty">{status === "running" ? "Written once the agents agree." : "A plain-English brief of the agreed plan, written from the log. It decides nothing."}</p>}
            {(() => {
              const t = messages.find((m) => m.body.type === "TRIAGE");
              if (!t || t.body.type !== "TRIAGE") return null;
              const b = t.body, i = b.probabilities.indexOf(Math.max(...b.probabilities));
              return <p className="muted" style={{ marginTop: 10, fontSize: 12 }}>Triage: severity {b.levels[i]}, p {b.probabilities[i].toFixed(2)} · {t.decision?.model ?? "formula"}</p>;
            })()}
            {result?.model_errors.length ? <p style={{ marginTop: 10, fontSize: 12, color: "var(--warning)" }}>{result.model_errors.join(" · ")}</p> : null}
          </section>
        <section className="card" style={{ display: "flex", flexDirection: "column" }}>
          <div className="card-head">
            <h2>Negotiation log</h2>
            <span className="meta">{messages.length} messages{result ? ` · ${result.stats.model_calls} model calls · ${result.stats.fallbacks} fallback${result.stats.fallbacks === 1 ? "" : "s"}` : ""}</span>
          </div>
          {runErr && <p className="empty" style={{ color: "var(--negative)", marginBottom: 10 }}>{runErr} <button className="btn text" onClick={negotiate}>Retry</button></p>}
          <div className="log-outer">
            <div className="log-host">
              <Log messages={messages} running={status === "running"} pilots={wave.flights.map((f) => f.callsign)} />
            </div>
          </div>
        </section>
        </div>

        <div className="span-8"><Checks key={result?.run_id ?? "none"} wave={wave} ev={result?.evaluation ?? null} base={result?.baseline.evaluation ?? null} /></div>
        <div className="span-4 stack">

          <Weather wave={wave} />
          <Sources wave={wave} />
        </div>
      </div>
    </main>
  );
}

function finding(wave: WaveScenario | null, status: Status, msgs: NegotiationMessage[], r: NegotiationResult | null): React.ReactNode {
  if (!wave) return null;
  if (r) {
    const e = r.evaluation.totals, b = r.baseline.evaluation.totals;
    const broken = r.baseline.evaluation.checks.filter((c) => !c.passed);
    const worse = r.final_plan.assignments.flatMap((a) => {
      const base = r.baseline.plan.assignments.find((x) => x.callsign === a.callsign);
      if (!base) return [];
      const d = a.metrics.delay_min - base.metrics.delay_min, p = base.metrics.pax_protected - a.metrics.pax_protected;
      return d > 0 || p > 0 ? [`${a.callsign} ${d > 0 ? `${d} min later` : ""}${d > 0 && p > 0 ? ", " : ""}${p > 0 ? `${p} pax miss connections` : ""}`] : [];
    });
    return (
      <>
        <b>{r.outcome === "consensus" ? `Agreed in ${r.rounds} round${r.rounds === 1 ? "" : "s"}` : "No consensus; deterministic fallback plan"}</b>, every rule passed:
        {" "}gate delay {e.delay_min} min against {b.delay_min} for the status quo{broken.length ? `, which breaks ${broken.map((c) => `${c.rule} on ${c.callsign}`).join(", ")}` : ""}.
        {worse.length ? <> <span className="neg">Worse than status quo: {worse.join("; ")}.</span></> : " No flight is worse off."}
      </>
    );
  }
  if (status === "running" && msgs.length) return `Round ${msgs[msgs.length - 1].round} of 3 · ${msgs.length} messages so far.`;
  const dis = wave.flights.filter((f) => f.disrupted).map((f) => f.callsign);
  return `${wave.flights.length} flights to negotiate, ${dis.length} disrupted (${dis.join(", ")}); ${wave.queue.length} other arrivals fixed. ${wave.metar ? `${wave.metar.raw.split(" ").slice(2, 5).join(" ")}, ${wave.metar.surface} runways.` : ""}`;
}
