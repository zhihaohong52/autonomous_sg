import { useEffect, useRef } from "react";
import type { AgentTurnBody, Assignment, DecisionMeta, JointPlan, NegotiationMessage } from "../api/types";
import { agentClass, agentName } from "../format";

const ROUND = ["Triage and opening plan", "Turn 1", "Turn 2", "Turn 3", "Fallback"];

function summary(m: NegotiationMessage): string {
  const b = m.body;
  switch (b.type) {
    case "TRIAGE": return `Severity ${b.levels[b.probabilities.indexOf(Math.max(...b.probabilities))]}`;
    case "OPEN": return `Opens with the first-come plan, ${b.evaluation.feasible ? "which passes every rule" : `which breaks ${b.evaluation.checks.filter((c) => !c.passed).length} rule${b.evaluation.checks.filter((c) => !c.passed).length === 1 ? "" : "s"}`}`;
    case "AGENT_TURN": return b.moves.length ? b.moves.map((x) => x.move.label).join("; ") : "No move; keeps the plan on the table";
    case "CHECK": return b.passed ? `Plan ${b.plan_id}${b.proposed_by ? ` from ${agentName(b.proposed_by)}` : ""} passes every rule` : `Plan ${b.plan_id} vetoed: ${b.violations.map((v) => `${v.rule} ${v.callsign ?? ""}`).join(", ")}`;
    case "CONSENSUS": return `Consensus on plan ${b.plan_id} in turn ${b.turn}, accepted by all ${b.accepted_by.length} agents`;
    case "NO_CONSENSUS": return `No consensus in turn ${b.turn}`;
    case "FALLBACK": return `Deterministic solver picks plan ${b.plan_id} (${b.method})`;
    case "MODEL_FALLBACK": return `${b.from_model} failed (${b.error}); ${b.to_model} answers`;
    // superseded in v2.0, still readable for old runs
    case "MOVE": return b.move.label;
    case "MOVE_REJECTED": return `${b.move.label}: vetoed, ${b.violations[0]?.rule ?? "rule"}`;
    case "COUNTER": return `Counter-plan · delay ${b.evaluation.totals.delay_min} min`;
    case "VOTE": return b.votes.map((v) => `${v.decision === "ACCEPT" ? "Accepts" : "Rejects"} ${v.plan_id}`).join("; ");
  }
}

// ---- what each agent's turn changed, against the plan on the table before it spoke
interface StepDiff { flights: string[]; delayBefore: number; delayAfter: number; breachesBefore: number | null; breachesAfter: number | null }

const n1 = (v: number) => (Math.round(v * 10) / 10).toString();
const tmin = (v?: number | null) => (v == null ? "?" : `T${v >= 0 ? "+" : ""}${n1(v)}`);
const totalDelay = (p: JointPlan) => p.assignments.reduce((s, a) => s + (a.metrics?.delay_min ?? 0), 0);

function flightDiff(a: Assignment, b: Assignment): string | null {
  const parts: string[] = [];
  if (a.action !== b.action) parts.push(b.action === "divert" ? "diverts" : "lands instead of diverting");
  if (b.action === "land") {
    if (a.runway !== b.runway) parts.push(`runway ${a.runway ?? "-"} → ${b.runway}`);
    if (Math.abs((a.slot_min ?? 0) - (b.slot_min ?? 0)) >= 0.1) parts.push(`lands ${tmin(a.slot_min)} → ${tmin(b.slot_min)}`);
    if (a.gate !== b.gate) parts.push(`gate ${a.gate ?? "-"} → ${b.gate === "RS" ? "remote stand" : b.gate}`);
    if (a.speed_reduction_min !== b.speed_reduction_min) parts.push(`slows ${n1(a.speed_reduction_min)} → ${n1(b.speed_reduction_min)} min en route`);
    if (a.ground_delay_min !== b.ground_delay_min) parts.push(`ground delay ${n1(a.ground_delay_min)} → ${n1(b.ground_delay_min)} min`);
    if (Math.abs(a.metrics.hold_min - b.metrics.hold_min) >= 0.1) parts.push(`holding ${n1(a.metrics.hold_min)} → ${n1(b.metrics.hold_min)} min`);
    if (a.metrics.pax_protected !== b.metrics.pax_protected) parts.push(`connecting pax ${a.metrics.pax_protected} → ${b.metrics.pax_protected} of ${b.metrics.pax_total}`);
  }
  if (!parts.length) return null;
  if (Math.abs(a.metrics.delay_min - b.metrics.delay_min) >= 0.1) parts.push(`delay ${n1(a.metrics.delay_min)} → ${n1(b.metrics.delay_min)} min`);
  return `${b.callsign}  ${parts.join(" · ")}`;
}

function stepDiffs(messages: NegotiationMessage[]): Map<number, StepDiff> {
  const out = new Map<number, StepDiff>();
  let prev: JointPlan | null = null;
  let prevBreaches: number | null = null;
  messages.forEach((m, i) => {
    const b = m.body;
    if (b.type === "OPEN") { prev = b.plan; prevBreaches = b.evaluation.checks.filter((c) => !c.passed).length; return; }
    if (b.type !== "AGENT_TURN" || !b.proposal) return;
    const cur = b.proposal;
    const check = messages.slice(i + 1).find((x) => x.body.type === "CHECK" && x.body.plan_id === cur.id)?.body;
    const before: JointPlan | null = prev;
    const breaches = check && check.type === "CHECK" ? check.violations.length : before && before.id === cur.id ? prevBreaches : null;
    const byCs = new Map((before?.assignments ?? []).map((a) => [a.callsign, a]));
    const flights = before ? cur.assignments.map((a) => { const o = byCs.get(a.callsign); return o ? flightDiff(o, a) : null; }).filter((x): x is string => !!x) : [];
    out.set(m.seq, { flights, delayBefore: before ? totalDelay(before) : totalDelay(cur), delayAfter: totalDelay(cur), breachesBefore: prevBreaches, breachesAfter: breaches });
    prev = cur; prevBreaches = breaches;
  });
  return out;
}

function Changes({ d }: { d: StepDiff }) {
  const dd = d.delayAfter - d.delayBefore;
  const br = d.breachesBefore != null && d.breachesAfter != null && d.breachesBefore !== d.breachesAfter
    ? ` · rule breaches ${d.breachesBefore} → ${d.breachesAfter}${d.breachesAfter === 0 ? " ✓" : ""}`
    : d.breachesAfter ? ` · ${d.breachesAfter} rule breach${d.breachesAfter === 1 ? "" : "es"} remain` : "";
  if (!d.flights.length) return <div className="msg-diff muted">No change to the plan{br}</div>;
  return (
    <div className="msg-diff">
      {d.flights.map((f) => <div key={f}>{f}</div>)}
      <div className="msg-diff-total">
        Wave total delay {n1(d.delayBefore)} → {n1(d.delayAfter)} min
        {Math.abs(dd) >= 0.1 && <span className={dd < 0 ? "pos" : "neg"}> ({dd > 0 ? "+" : ""}{n1(dd)})</span>}
        {br}
      </div>
    </div>
  );
}

const meta = (d: DecisionMeta) => `${d.kind === "formula" ? "formula" : d.model}${d.confidence != null ? ` · p ${d.confidence.toFixed(2)}` : ""} · ${d.latency_ms} ms`;

function Turn({ b, details, diff }: { b: AgentTurnBody; details: boolean; diff?: StepDiff }) {
  const acc = b.votes.filter((v) => v.vote === "ACCEPT").length;
  return (
    <>
      {diff && <Changes d={diff} />}
      {b.rejected.map((r) => (
        <div key={r.move.id} className="msg-why neg">Vetoed: {r.move.label}. {r.violations[0]?.detail ?? r.reason}</div>
      ))}
      <div className="msg-why">
        Own plan scores {b.proposal_utility.toFixed(2)} · accepts {acc} of {b.votes.length} plan{b.votes.length === 1 ? "" : "s"} on the table
        {b.votes.filter((v) => v.vote === "REJECT").map((v) => ` · rejects ${v.plan_id} (${v.utility.toFixed(2)} < ${v.reservation.toFixed(2)})`).join("")}
      </div>
      {details && b.moves.map((x) => <div key={x.move.id} className="msg-dec">{x.move.label.split(" ").slice(0, 2).join(" ")} · {meta(x.decision)}</div>)}
      {details && b.decision && <div className="msg-dec">vote · {meta(b.decision)}{b.decision.fallback_from?.length ? <span style={{ color: "var(--warning)" }}> · after {b.decision.fallback_from.join(", ")} failed</span> : null}</div>}
      {!details && b.decision?.fallback_from?.length ? <div className="msg-dec" style={{ color: "var(--warning)" }}>Model fallback: {b.decision.fallback_from.join(", ")} failed</div> : null}
    </>
  );
}

export function Log({ messages, running, focus, details = false }: { messages: NegotiationMessage[]; running: boolean; focus?: string | null; details?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (running && box.current) box.current.scrollTop = box.current.scrollHeight; }, [messages.length, running]);
  if (!messages.length) {
    const roles: [string, string, string][] = [
      ["ATC", "atc", "Sees the runways, the queue and the wind. Sequences every flight: landing order, runway, slot, speed control or ground delay."],
      [focus ? `Pilot ${focus}` : "Pilot", "pilot", `Flies the disruption flight${focus ? ` ${focus}` : ""} only: its fuel, ETA and connections. Slows down, takes a ground delay, asks to divert or accepts.`],
      ["Ground", "ground", "Sees gates, stands and crews. Reassigns a gate, sends a flight to a remote stand, swaps gates."],
      ["Engine", "engine", "Deterministic code, not a model. Checks 12 safety rules on every plan and vetoes any that breaks one."],
    ];
    return (
      <div>
        <p className="empty" style={{ marginBottom: 12 }}>No messages yet. Each agent speaks once per turn, for at most three turns; the engine checks every plan in between.</p>
        {roles.map(([name, cls, what]) => (
          <article key={cls} className="msg" style={{ animation: "none" }}>
            <div className="msg-top"><span className={`who ${cls}`}>{name}</span><div className="msg-body muted">{what}</div></div>
          </article>
        ))}
      </div>
    );
  }
  const rounds = [...new Set(messages.map((m) => m.round))];
  const diffs = stepDiffs(messages);
  return (
    <div className="log" ref={box} tabIndex={0} role="region" aria-label="Negotiation log">
      {rounds.map((r) => (
        <section key={r}>
          <h3 className="round-h">{ROUND[r] ?? `Turn ${r}`}</h3>
          {messages.filter((m) => m.round === r).map((m) => {
            const b = m.body;
            const rej = b.type === "MOVE_REJECTED" || (b.type === "CHECK" && !b.passed);
            const d = b.type === "AGENT_TURN" ? undefined : m.decision;
            return (
              <article key={m.seq} className={`msg${rej ? " rej" : ""}${b.type === "AGENT_TURN" ? " turn" : ""}`}>
                <div className="msg-top">
                  <span className={`who ${agentClass(m.sender)}`}>{agentName(m.sender)}</span>
                  <div className="msg-body">{summary(m)}</div>
                </div>
                {b.type === "AGENT_TURN" && <Turn b={b} details={details} diff={diffs.get(m.seq)} />}
                {(b.type === "NO_CONSENSUS" || b.type === "FALLBACK") && <div className="msg-why">{b.reason}</div>}
                {b.type === "MOVE_REJECTED" && b.violations[0] && <div className="msg-why neg">{b.violations[0].detail} · {b.violations[0].source}</div>}
                {b.type !== "AGENT_TURN" && b.type !== "TRIAGE" && m.rationale && <div className="msg-why">{m.rationale}</div>}
                {d && (details || d.fallback_from?.length) && <div className="msg-dec">{meta(d)}{d.fallback_from?.length ? <span style={{ color: "var(--warning)" }}> · after {d.fallback_from.join(", ")} failed</span> : null}</div>}
              </article>
            );
          })}
        </section>
      ))}
    </div>
  );
}
