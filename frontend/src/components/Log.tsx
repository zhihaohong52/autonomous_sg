import { useEffect, useRef } from "react";
import type { AgentTurnBody, DecisionMeta, NegotiationMessage } from "../api/types";
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

const meta = (d: DecisionMeta) => `${d.kind === "formula" ? "formula" : d.model}${d.confidence != null ? ` · p ${d.confidence.toFixed(2)}` : ""} · ${d.latency_ms} ms`;

function Turn({ b, details }: { b: AgentTurnBody; details: boolean }) {
  const acc = b.votes.filter((v) => v.vote === "ACCEPT").length;
  return (
    <>
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
      ["Engine", "engine", "Deterministic code, not a model. Checks 11 safety rules on every plan and vetoes any that breaks one."],
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
                {b.type === "AGENT_TURN" && <Turn b={b} details={details} />}
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
