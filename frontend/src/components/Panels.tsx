import { Fragment, useState } from "react";
import type { JointPlan, PlanEvaluation, Provenance, RuleCheck, RuleId, WaveFlight, WaveScenario } from "../api/types";
import { clock, signed } from "../format";

/* ---------- outcome strip: agreed plan against the status quo ---------- */
export function Outcome({ ev, base }: { ev: PlanEvaluation | null; base: PlanEvaluation | null }) {
  const t = ev?.totals, b = base?.totals;
  const breaches = (e: PlanEvaluation | null) => (e ? e.checks.filter((c) => !c.passed).length : null);
  const dash = <span className="muted">—</span>;
  const cell = (label: string, value: React.ReactNode, unit: string, delta: React.ReactNode) => (
    <div className="kpi"><div className="label">{label}</div><div className="value">{value}{value !== dash && <small>{unit}</small>}</div><div className="delta">{delta}</div></div>
  );
  const vs = (now: number | undefined, then: number | undefined, unit: string, goodDown = true, digits = 0) => {
    if (now == null || then == null) return <span className="muted">Status quo —</span>;
    const diff = +(now - then).toFixed(digits);
    const good = goodDown ? diff < 0 : diff > 0;
    return <span>Status quo {then.toFixed(digits)}{unit} · <span className={diff === 0 ? "" : good ? "pos" : "neg"}>{signed(diff, unit)}</span></span>;
  };
  return (
    <div className="kpis">
      {cell("Total gate delay", t ? t.delay_min : dash, " min", vs(t?.delay_min, b?.delay_min, " min"))}
      {cell("Connecting pax protected", t ? t.pax_protected_pct.toFixed(1) : dash, "%", vs(t?.pax_protected_pct, b?.pax_protected_pct, "%", false, 1))}
      {cell("Holding fuel", t ? t.fuel_kg : dash, " kg", vs(t?.fuel_kg, b?.fuel_kg, " kg"))}
      {cell("Safety rule breaches", ev ? breaches(ev) : dash, "", base ? <span>Status quo <span className={breaches(base) ? "neg" : ""}>{breaches(base)}</span></span> : <span className="muted">Status quo —</span>)}
    </div>
  );
}

/* ---------- plan per flight ---------- */
const provShort = (p: Provenance[]) => {
  const sim = p.filter((x) => x.status !== "real").map((x) => x.layer);
  return sim.length ? `Simulated: ${sim.join(", ")}` : "All real";
};

export function PlanTable({ wave, plan, baseline }: { wave: WaveScenario; plan: JointPlan | null; baseline: JointPlan | null }) {
  const c = (m: number | null | undefined) => (m == null ? "—" : clock(wave.window_start, m));
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Plan per flight">
      <table>
        <thead>
          <tr>
            <th>Flight</th><th>Runway</th><th>Gate</th><th className="num">In-block</th><th className="num">Delay</th>
            <th className="num">Pax protected</th>
          </tr>
        </thead>
        <tbody>
          {wave.flights.map((f: WaveFlight) => {
            const a = plan?.assignments.find((x) => x.callsign === f.callsign);
            const b = baseline?.assignments.find((x) => x.callsign === f.callsign);
            const dDelay = a && b ? a.metrics.delay_min - b.metrics.delay_min : null;
            const dPax = a && b ? a.metrics.pax_protected - b.metrics.pax_protected : null;
            return (
              <tr key={f.callsign}>
                <td title={provShort(f.provenance)}><span className="code">{f.callsign}</span><span className="sub">{f.aircraft_type ?? "—"} from {f.origin}{f.disrupted ? ` · ${Math.max(f.delays.weather, f.delays.late_aircraft)} min ${f.delays.weather >= f.delays.late_aircraft ? "weather" : "late aircraft"}` : ""}</span></td>
                <td>{a ? <><span className="code">{a.action === "divert" ? "Divert" : a.runway}</span><span className="sub">{c(a.slot_min)}{a.metrics.hold_min ? ` · hold ${a.metrics.hold_min} min` : ""}{a.ground_delay_min ? ` · ${a.ground_delay_min} min at ${f.origin}` : ""}</span></> : <span className="muted">—</span>}</td>
                <td>{a ? <><span className="code">{a.gate}</span><span className="sub">{a.gate !== f.scheduled_gate ? `was ${f.scheduled_gate}` : "scheduled"}</span></> : <span className="code muted">{f.scheduled_gate}</span>}</td>
                <td className="num">{a ? <>{c(a.metrics.in_block_min)}<span className="sub">{a.metrics.next_dep_delay_min ? `next dep ${a.metrics.next_dep_delay_min} min late` : "next dep on time"}</span></> : <span className="muted">sched {c(f.sched_min)}</span>}</td>
                <td className="num">{a ? <>{a.metrics.delay_min} min{dDelay != null && <span className={`sub ${dDelay > 0 ? "neg" : dDelay < 0 ? "pos" : ""}`}>{signed(dDelay, " min")} vs status quo</span>}</> : <span className="muted">—</span>}</td>
                <td className="num">{a ? <>{a.metrics.pax_total ? `${a.metrics.pax_protected} of ${a.metrics.pax_total}` : <span className="muted">no connections</span>}{dPax != null && a.metrics.pax_total > 0 && <span className={`sub ${dPax < 0 ? "neg" : dPax > 0 ? "pos" : ""}`}>{signed(dPax)} vs status quo</span>}</> : <span className="muted">—</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ---------- rule checks: rules down, flights across ---------- */
const RULES: { id: RuleId; name: string }[] = [
  { id: "ATC-RWY-OPEN", name: "Runway open" }, { id: "ATC-WAKE-SEP", name: "Wake separation" },
  { id: "ATC-TAXI-ROUTE", name: "Taxi route" }, { id: "ATC-WIND-LIMITS", name: "Wind limits" },
  { id: "PLT-EARLIEST-ETA", name: "Earliest ETA" }, { id: "PLT-FUEL-RESERVE", name: "Fuel reserve" },
  { id: "GND-GATE-SIZE", name: "Gate size" }, { id: "GND-GATE-FREE", name: "Gate free" }, { id: "GND-CREW-DUTY", name: "Crew duty" },
];

export function Checks({ wave, ev, base }: { wave: WaveScenario; ev: PlanEvaluation | null; base: PlanEvaluation | null }) {
  const [which, setWhich] = useState<"plan" | "base">("plan");
  const e = which === "plan" ? ev : base;
  const firstFail = e?.checks.find((c) => !c.passed) ?? (() => {
    const b = base?.checks.find((c) => !c.passed);
    return b ? e?.checks.find((c) => c.rule === b.rule && c.callsign === b.callsign) : undefined;
  })();
  const [sel, setSel] = useState<{ rule: RuleId; cs: string } | null>(null);
  const pick = sel ?? (firstFail ? { rule: firstFail.rule, cs: firstFail.callsign ?? "" } : null);
  const chosen: RuleCheck | undefined = pick ? e?.checks.find((c) => c.rule === pick.rule && c.callsign === pick.cs) : undefined;
  return (
    <div className="card">
      <div className="card-head">
        <h2>Safety rules</h2>
        {ev && <div className="seg" role="group" aria-label="Plan to check">
          <button aria-pressed={which === "plan"} onClick={() => { setWhich("plan"); setSel(null); }} disabled={!ev}>Agreed plan</button>
          <button aria-pressed={which === "base"} onClick={() => { setWhich("base"); setSel(null); }} disabled={!base}>Status quo</button>
        </div>}
      </div>
      {!e ? <p className="empty">Every plan is checked against 9 deterministic rules for every flight. The checks appear once the agents have a plan.</p> : (
        <>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Safety rule checks">
            <table className="matrix" aria-label="Safety rule checks by flight">
              <thead><tr><th>Rule</th>{wave.flights.map((f) => <th key={f.callsign} className="code">{f.callsign}</th>)}</tr></thead>
              <tbody>
                {RULES.map((r) => (
                  <tr key={r.id}>
                    <td style={{ height: 34 }}>{r.name} <span className="code muted" style={{ fontSize: 11 }}>{r.id}</span></td>
                    {wave.flights.map((f) => {
                      const c = e.checks.find((x) => x.rule === r.id && x.callsign === f.callsign);
                      const on = pick?.rule === r.id && pick.cs === f.callsign;
                      return (
                        <td key={f.callsign} style={{ height: 34 }}>
                          {c ? (
                            <button className={`cell${c.passed ? "" : " fail"}`} aria-pressed={on} onClick={() => setSel({ rule: r.id, cs: f.callsign })}
                              aria-label={`${r.name} for ${f.callsign}: ${c.passed ? "pass" : "fail"}`}>{c.passed ? "Pass" : "Fail"}</button>
                          ) : <span className="muted">—</span>}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="evidence" aria-live="polite">
            {chosen ? (
              <>
                <div><span className={`pill ${chosen.passed ? "pos" : "neg"}`}>{chosen.passed ? "Pass" : "Fail"}</span> <span className="code">{chosen.rule}</span> · <span className="code">{chosen.callsign}</span></div>
                <p style={{ marginTop: 8, fontSize: 13 }}>{chosen.detail}{which === "plan" && chosen.passed && base?.checks.some((c) => !c.passed && c.rule === chosen.rule && c.callsign === chosen.callsign) ? <span className="muted"> · the status quo breaks this rule</span> : null}</p>
                <dl>
                  <dt>Source</dt><dd style={{ fontFamily: "var(--font)" }}>{chosen.source}</dd>
                  {Object.entries(chosen.evidence).map(([k, v]) => <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{String(v)}</dd></Fragment>)}
                </dl>
              </>
            ) : <span className="muted">All {e.checks.length} checks pass. Choose a cell to see its evidence.</span>}
          </div>
        </>
      )}
    </div>
  );
}

/* ---------- weather and runway wind ---------- */
export function Weather({ wave }: { wave: WaveScenario }) {
  const m = wave.metar;
  return (
    <div className="card">
      <div className="card-head"><h2>Weather and runways</h2><span className="meta">{m ? `METAR ${clock(m.observed_at, 0)}` : "No METAR"}</span></div>
      {m ? <p className="code" style={{ fontSize: 12, color: "var(--soft)", marginBottom: 12, overflowWrap: "anywhere" }}>{m.raw}</p>
        : <p className="empty" style={{ marginBottom: 12 }}>No METAR for this hour; wind limits use the generic model.</p>}
      <table>
        <thead><tr><th>Runway</th><th className="num">Head</th><th className="num">Cross</th><th className="num">Limit</th><th>Status</th></tr></thead>
        <tbody>
          {wave.runway_wind.map((w) => {
            const r = wave.airport.runways.find((x) => x.id === w.runway);
            return (
              <tr key={w.runway}>
                <td className="code">{w.runway}</td>
                <td className="num">{w.headwind_kt} kt</td>
                <td className="num">{w.crosswind_kt} kt</td>
                <td className="num">{w.limit_crosswind_kt} kt</td>
                <td>{r && !r.open ? <span className="pill warn">Closed</span> : w.within_limits ? <span className="pill">Within limits</span> : <span className="pill neg">Over limit</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>Limits: {[...new Set(wave.runway_wind.map((w) => w.limit_source))].join("; ")}. Crosswind includes gusts.</p>
    </div>
  );
}

/* ---------- data sources ---------- */
export function Sources({ wave }: { wave: WaveScenario }) {
  return (
    <div className="card">
      <div className="card-head"><h2>Data sources</h2><span className="meta">{wave.provenance.filter((p) => p.status === "real").length} of {wave.provenance.length} layers real</span></div>
      <dl className="kv">
        <dt>per flight</dt><dd><span className="pill warn" style={{ marginRight: 8 }}>simulated</span>{[...new Set(wave.flights.flatMap((f) => f.provenance.filter((p) => p.status !== "real").map((p) => p.layer)))].join(", ")}, seeded</dd>
        {wave.provenance.map((p) => (
          <Fragment key={p.layer}><dt>{p.layer.replace("_", " ")}</dt>
            <dd><span className={`pill ${p.status === "real" ? "" : "warn"}`} style={{ marginRight: 8 }}>{p.status}</span>{p.source}</dd></Fragment>
        ))}
      </dl>
    </div>
  );
}
