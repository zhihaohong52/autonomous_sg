import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isOffline } from "./api/client";
import type { Config, FlightRow, JointPlan, Mode, NegotiationMessage, NegotiationResult, WaveScenario, WaveSummary } from "./api/types";
import { Gantt, GanttLegend, type Rejection } from "./components/Gantt";
import { Log } from "./components/Log";
import { Checks, Limits, Outcome, PlanTable, RevisionLine, Sources, Weather } from "./components/Panels";
import { clock, dateLabel } from "./format";

type Status = "idle" | "running" | "done" | "error";
type Decision = { kind: "APPROVE" | "OVERRIDE"; at: string; note?: string; offline: boolean } | null;
const MODE_LABEL: Record<Mode, string> = { formulas: "Formulas", decision: "Decision models", "decision+claude": "Decision models + Claude brief" };
type Tab = "flights" | "rules" | "log" | "data";
const TABS: { id: Tab; label: string }[] = [
  { id: "flights", label: "Flights" }, { id: "rules", label: "Safety rules" }, { id: "log", label: "Negotiation log" }, { id: "data", label: "Weather and data" },
];

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
  const [tab, setTab] = useState<Tab>("flights");
  const abort = useRef<AbortController | null>(null);
  const tableRef = useRef<JointPlan | null>(null);        // the table plan at the moment a message arrives
  const showTable = (p: JointPlan | null) => { tableRef.current = p; setTable(p); };
  useEffect(() => () => abort.current?.abort(), []);

  const loadWave = useCallback(async (id: string) => {
    abort.current?.abort();
    setStatus("idle"); setMessages([]); showTable(null); setResult(null); setRejection(null); setRunErr(null);
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
    setStatus("running"); setMessages([]); showTable(null); setResult(null); setRejection(null); setRunErr(null); setDecision(null); setView("plan"); setTab("log");
    let ended = false;
    try {
      await api.negotiate({ wave_id: wave.id, mode, model: mode === "formulas" ? undefined : model, replay }, (ev) => {
        if (ev.event === "message") {
          const m = ev.data;
          setMessages((xs) => [...xs, m]);
          const b = m.body;
          if (b.type === "AGENT_TURN") {
            const r = b.rejected[b.rejected.length - 1];
            if (r) {
              const cs = r.move.callsigns[0];
              setRejection({ seq: m.seq, move: r.move, violations: r.violations, runway: tableRef.current?.assignments.find((a) => a.callsign === cs)?.runway });
            }
            showTable(b.proposal);
          }
          if (b.type === "OPEN" || b.type === "MOVE" || b.type === "COUNTER") showTable(b.plan);   // MOVE, COUNTER: v1 runs
          if (b.type === "MOVE_REJECTED") {
            const cs = b.move.callsigns[0];
            setRejection({ seq: m.seq, move: b.move, violations: b.violations, runway: tableRef.current?.assignments.find((a) => a.callsign === cs)?.runway });
          }
        } else if (ev.event === "result") {
          ended = true;
          setResult(ev.data); showTable(ev.data.final_plan); setRejection(null); setStatus("done"); setTab("flights");
          if (ev.data.log) setMessages(ev.data.log);
        } else if (ev.event === "error") {
          ended = true;
          setRunErr(ev.data.message); setStatus("error");
        }
      }, ctl.signal);
      if (!ended && !ctl.signal.aborted) {               // the stream closed without a result or an error event
        setRunErr("The negotiation stream ended before a result arrived."); setStatus("error");
      }
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
      setDecision({ kind, at: r.recorded_at, note: note.trim() || undefined, offline: isOffline() }); setOverriding(false);
    } catch (e) { setRunErr(`Decision not recorded: ${(e as Error).message}`); }
    finally { setDeciding(false); }
  };

  // ?autorun starts the negotiation as soon as the wave has loaded (pitch and screenshots)
  const autorun = useRef(new URLSearchParams(location.search).has("autorun"));
  useEffect(() => {
    if (wave && autorun.current) { autorun.current = false; negotiate(); }
  }, [wave]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = view === "base" && result ? result.baseline.plan : table;
  const modelLabel = config?.models.find((m) => m.id === model)?.label;
  const triage = (() => {
    const t = messages.find((m) => m.body.type === "TRIAGE");
    if (!t || t.body.type !== "TRIAGE") return null;
    const b = t.body, i = b.probabilities.indexOf(Math.max(...b.probabilities));
    return `${b.levels[i]}, p ${b.probabilities[i].toFixed(2)} (${t.decision?.model ?? "formula"})`;
  })();
  const onTabKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = TABS.findIndex((t) => t.id === tab), n = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length].id;
    setTab(n); document.getElementById(`tab-${n}`)?.focus();
  };
  const summary = useMemo(() => finding(wave, status, messages, result), [wave, status, messages, result]);

  if (!wave) {
    return (
      <main className="canvas">
        <div className="titlerow"><div><h1>Arrival wave</h1>
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
          <h1>{wave.airport.iata} arrivals {clock(wave.window_start, 0)}–{clock(wave.window_start, 60)}, {dateLabel(wave.window_start)}</h1>
          <p className="sub">{summary}</p>
          <p className="sr-only" aria-live="polite">{status === "running" ? `Negotiating, turn ${round} of 3` : status === "done" ? "Negotiation finished. The plan is ready to approve or override." : status === "error" ? `Negotiation failed. ${runErr ?? ""}` : ""}</p>
        </div>
        <div className="actions">
          {decision ? (
            <span className={`pill ${decision.kind === "APPROVE" ? "pos" : "warn"}`}>
              {decision.kind === "APPROVE" ? "Approved" : "Overridden"} {new Date(decision.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · {decision.offline ? "offline demo, not logged" : "logged"}
            </span>
          ) : status === "done" ? (
            <>
              <button className="btn" disabled={deciding} onClick={() => setOverriding((o) => !o)} aria-expanded={overriding}>Override</button>
              <button className="btn primary" disabled={deciding} onClick={() => decide("APPROVE")}>Approve plan</button>
            </>
          ) : (
            <button className="btn primary" onClick={negotiate} disabled={status === "running"}>
              {status === "running" ? `Negotiating, turn ${round}…` : status === "error" ? "Run again" : "Start negotiation"}
            </button>
          )}
        </div>
      </header>

      {overriding && !decision && (
        <div className="card decide" style={{ marginBottom: "var(--gap)" }}>
          <label htmlFor="note" style={{ fontWeight: 500 }}>Why are you overriding this plan?</label>
          <textarea id="note" required aria-required="true" value={note} onChange={(e) => setNote(e.target.value)} aria-describedby="note-help"
            placeholder="For example: holding DL2384 at A14, DL1490 is now leaving at 16:30" />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginTop: 8 }}>
            <span id="note-help" className="muted" style={{ fontSize: 12 }}>The note is required and is logged with the run.</span>
            <div className="actions">
              <button className="btn text" onClick={() => setOverriding(false)}>Cancel</button>
              <button className="btn" disabled={!note.trim() || deciding} onClick={() => decide("OVERRIDE")}>Record override</button>
            </div>
          </div>
        </div>
      )}

      <div className="toolbar">
        <label className="field"><span className="sr-only">Wave</span>
          <select className="select" value={wave.id} onChange={(e) => loadWave(e.target.value)} aria-label="Wave">
            {waves.map((w) => <option key={w.id} value={w.id}>{w.id} · {w.n_flights} flights</option>)}
            {!waves.some((w) => w.id === wave.id) && <option value={wave.id}>{wave.id}</option>}
          </select>
        </label>
        <FlightPicker airport={wave.airport.iata} onPick={(id) => loadWave(id)} />
        <button className="btn text" onClick={async () => loadWave((await api.randomWave()).id)}>Random wave</button>
        <details className="settings">
          <summary className="btn text"><span className="lbl">{MODE_LABEL[mode]}{mode !== "formulas" && modelLabel ? ` · ${modelLabel}` : ""}{replay ? " · replay" : ""}</span></summary>
          <div className="menu">
            <div className="field">Decision mode
              <div className="seg" role="group" aria-label="Decision mode">
                {(config?.modes ?? ["formulas", "decision", "decision+claude"]).map((m) => (
                  <button key={m} aria-pressed={mode === m} onClick={() => setMode(m)}>{MODE_LABEL[m]}</button>
                ))}
              </div>
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
              <label style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer" }}>
                <input type="checkbox" checked={replay} onChange={(e) => setReplay(e.target.checked)} style={{ accentColor: "var(--accent)" }} />
                Replay the recorded run
              </label>
            )}
            {mode === "decision+claude" && config && !config.llm_available && <p className="muted" style={{ fontSize: 12 }}>Claude is not configured, so the brief uses a template.</p>}
          </div>
        </details>
        {status === "done" && <button className="btn text" onClick={negotiate}>Run again</button>}
        {isOffline() && <span className="pill warn end">Offline demo: backend not reachable</span>}
      </div>

      <div className="stack">
        <section className="card" aria-label="Outcome against the status quo">
          <RevisionLine r={result?.revision ?? null} />
          <Outcome ev={result?.evaluation ?? null} base={result?.baseline.evaluation ?? null} />
        </section>

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
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap", marginTop: 12 }}>
            <GanttLegend />
            <span className="reject-note" aria-live="polite">
              {rejection && status === "running" ? `Vetoed: ${rejection.move.label}. ${rejection.violations[0]?.detail ?? ""}` : ""}
            </span>
          </div>
        </section>

        <section className="card">
          <div className="tabs" role="tablist" aria-label="Details" onKeyDown={onTabKey}>
            {TABS.map((t) => (
              <button key={t.id} id={`tab-${t.id}`} role="tab" aria-selected={tab === t.id} aria-controls={`panel-${t.id}`} tabIndex={tab === t.id ? 0 : -1} onClick={() => setTab(t.id)}>
                {t.label}{t.id === "log" && messages.length ? <span className="count">{messages.length}</span> : null}
              </button>
            ))}
          </div>
          <div className="tabpanel" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
            {tab === "flights" && <PlanTable wave={wave} plan={result ? result.final_plan : table} baseline={result?.baseline.plan ?? null} />}
            {tab === "rules" && <Checks key={result?.run_id ?? "none"} wave={wave} ev={result?.evaluation ?? null} base={result?.baseline.evaluation ?? null} />}
            {tab === "log" && (
              <>
                {(result?.narration || triage) && (
                  <section style={{ marginBottom: 20 }}>
                    {result?.narration && <p className="brief">{result.narration.brief}</p>}
                    <p className="muted" style={{ marginTop: 8, fontSize: 12 }}>
                      {result?.narration ? (result.narration.source === "claude" ? `Brief written by ${result.narration.model ?? "Claude"} from the log; it decides nothing.` : "Brief from a template over the log; it decides nothing.") : ""}
                      {triage ? ` Triage: severity ${triage}.` : ""}
                      {result ? ` ${result.stats.model_calls} model calls, ${result.stats.fallbacks} fallback${result.stats.fallbacks === 1 ? "" : "s"}.` : ""}
                    </p>
                    {result?.model_errors.length ? <p style={{ marginTop: 6, fontSize: 12, color: "var(--warning)" }}>{result.model_errors.join(" · ")}</p> : null}
                  </section>
                )}
                {runErr && <p style={{ color: "var(--negative)", marginBottom: 10 }}>{runErr} <button className="btn text" onClick={negotiate}>Retry</button></p>}
                <Log messages={messages} running={status === "running"} focus={result?.focus ?? wave.focus} />
              </>
            )}
            {tab === "data" && <><Limits limits={result?.limits ?? null} /><Weather wave={wave} /><Sources wave={wave} /></>}
          </div>
        </section>
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
        <b>{r.outcome === "consensus" ? `Agreed in turn ${r.turns ?? r.rounds} of 3` : "No consensus after 3 turns; the deterministic solver decided"}</b>, every rule passed:
        {" "}gate delay {e.delay_min} min against {b.delay_min} for the status quo{broken.length ? `, which breaks ${broken.map((c) => `${c.rule} on ${c.callsign}`).join(", ")}` : ""}.
        {worse.length ? <> <span className="neg">Worse than status quo: {worse.join("; ")}.</span></> : " No flight is worse off."}
      </>
    );
  }
  if (status === "running" && msgs.length) return `Turn ${msgs[msgs.length - 1].round} of 3 · ${msgs.length} messages so far.`;
  const dis = wave.flights.filter((f) => f.disrupted).map((f) => f.callsign);
  return `${wave.focus ? `Disruption flight ${wave.focus}; its pilot negotiates with ATC and Ground. ` : ""}${wave.flights.length} flights in the plan, ${dis.length} disrupted (${dis.join(", ")}); ${wave.queue.length} other arrivals fixed. ${wave.metar ? `${wave.metar.raw.split(" ").slice(2, 5).join(" ")}, ${wave.metar.surface} runways.` : ""}`;
}

/* Pick the challenge input: one row of flights.csv becomes the wave's disruption flight. */
function FlightPicker({ airport, onPick }: { airport: string; onPick: (waveId: string) => void }) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<FlightRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const t = setTimeout(() => {
      api.flights(q, q ? "" : airport).then((r) => { setRows(r.flights); setTotal(r.total); }).catch(() => setRows([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, airport]);
  return (
    <details className="settings" ref={ref}>
      <summary className="btn text"><span className="lbl">Flight from flights.csv</span></summary>
      <div className="menu wide">
        <label className="field">Search by flight, airport or row
          <input className="select" style={{ backgroundImage: "none", cursor: "text" }} value={q} onChange={(e) => setQ(e.target.value)} placeholder={`For example AA1298, or ${airport}`} />
        </label>
        <div className="picklist" role="list">
          {rows == null ? <p className="muted">Loading…</p> : !rows.length ? <p className="muted">No flights match.</p> : rows.map((r) => (
            <button key={r.row_id} role="listitem" onClick={() => { ref.current?.removeAttribute("open"); onPick(r.wave_id); }}>
              <span><span className="code">{r.flight}</span> {r.origin} → {r.dest}</span>
              <span className="muted">{r.date} {r.sched_arr} · {r.arr_delay > 0 ? `+${r.arr_delay} min` : "on time"}</span>
            </button>
          ))}
        </div>
        {rows && total > rows.length && <p className="muted" style={{ fontSize: 12 }}>Showing {rows.length} of {total.toLocaleString("en-US")}; search to narrow.</p>}
      </div>
    </details>
  );
}
