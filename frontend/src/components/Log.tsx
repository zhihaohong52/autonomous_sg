import { useEffect, useRef } from "react";
import type { NegotiationMessage } from "../api/types";
import { agentClass, agentName } from "../format";

const ROUND = ["Triage and opening plan", "Round 1", "Round 2", "Round 3", "Fallback"];

function summary(m: NegotiationMessage): string {
  const b = m.body;
  switch (b.type) {
    case "TRIAGE": return `Severity ${b.levels[b.probabilities.indexOf(Math.max(...b.probabilities))]} (score ${b.score.toFixed(2)})`;
    case "OPEN": return `Opens with first-come plan, ${b.evaluation.feasible ? "passes all rules" : `${b.evaluation.checks.filter((c) => !c.passed).length} rule breach`}`;
    case "MOVE": return b.move.label;
    case "MOVE_REJECTED": return `${b.move.label}: vetoed, ${b.violations[0]?.rule ?? "rule"}`;
    case "COUNTER": return `Counter-plan · delay ${b.evaluation.totals.delay_min} min · ${b.evaluation.totals.pax_protected_pct.toFixed(1)}% pax protected`;
    case "CHECK": return b.passed ? `Plan ${b.plan_id} passes every rule` : `Plan ${b.plan_id} vetoed: ${b.violations.map((v) => `${v.rule} ${v.callsign ?? ""}`).join(", ")}`;
    case "VOTE": return b.votes.map((v) => `${v.decision === "ACCEPT" ? "Accepts" : "Rejects"} ${v.plan_id}${v.p_accept != null ? ` · P(accept) ${v.p_accept.toFixed(2)}` : ""}`).join("; ");
    case "CONSENSUS": return `Consensus on ${b.plan_id}, accepted by all ${b.accepted_by.length} agents`;
    case "NO_CONSENSUS": return `No consensus; ${b.valid_plan_ids.length} valid plan${b.valid_plan_ids.length === 1 ? "" : "s"}`;
    case "FALLBACK": return `Deterministic fallback picks ${b.plan_id} (${b.method})`;
    case "MODEL_FALLBACK": return `${b.from_model} failed (${b.error}); ${b.to_model} answers`;
  }
}

const ROLES: [string, string, string][] = [
  ["ATC", "atc", "Sees the runways, the queue and the wind. Moves: swap landing order, change runway, shift a slot."],
  ["PILOT", "pilot", "One per flight, sees only its own flight: fuel, ETA, connections. Moves: slow down, ground delay, divert, accept."],
  ["Ground", "ground", "Sees gates, stands and crews. Moves: reassign a gate, remote stand, swap gates."],
  ["Engine", "engine", "Deterministic code, not a model. Checks 9 safety rules after every move and vetoes any plan that breaks one."],
];

export function Log({ messages, running, pilots = [] }: { messages: NegotiationMessage[]; running: boolean; pilots?: string[] }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (running && box.current) box.current.scrollTop = box.current.scrollHeight; }, [messages.length, running]);
  if (!messages.length) {
    return (
      <div>
        <p className="empty" style={{ marginBottom: 12 }}>No messages yet. Start the negotiation to see each agent's moves, the engine's checks and every vote.</p>
        {ROLES.map(([name, cls, what]) => (
          <article key={name} className="msg" style={{ animation: "none" }}>
            <div className="msg-top"><span className={`who ${cls}`}>{name === "PILOT" ? `Pilot × ${pilots.length || "n"}` : name}</span></div>
            <div className="msg-why">{what}{name === "PILOT" && pilots.length ? ` ${pilots.join(", ")}.` : ""}</div>
          </article>
        ))}
      </div>
    );
  }
  const rounds = [...new Set(messages.map((m) => m.round))];
  return (
    <div className="log" ref={box} aria-live="polite">
      {rounds.map((r) => (
        <section key={r}>
          <h3 className="round-h">{ROUND[r] ?? `Round ${r}`}</h3>
          {messages.filter((m) => m.round === r).map((m) => {
            const rej = m.type === "MOVE_REJECTED" || (m.body.type === "CHECK" && !m.body.passed);
            const warn = m.type === "MODEL_FALLBACK" || (m.decision?.fallback_from?.length ?? 0) > 0;
            const d = m.decision;
            return (
              <article key={m.seq} className={`msg${rej ? " rej" : ""}`}>
                <div className="msg-top">
                  <span className={`who ${agentClass(m.sender)}`}>{agentName(m.sender)}</span>
                  <div className="msg-body">{summary(m)}</div>
                </div>
                {m.body.type === "MOVE_REJECTED" && m.body.violations[0] && (
                  <div className="msg-why neg">{m.body.violations[0].detail} · {m.body.violations[0].source}</div>
                )}
                {m.rationale && <div className="msg-why">{m.rationale}</div>}
                {d && (
                  <div className="msg-dec">
                    {d.kind === "formula" ? "formula" : d.model}
                    {d.confidence != null && ` · p ${d.confidence.toFixed(2)}`}
                    {` · ${d.latency_ms} ms`}
                    {warn && d.fallback_from?.length ? <span style={{ color: "var(--warning)" }}> · after {d.fallback_from.join(", ")} failed</span> : null}
                  </div>
                )}
              </article>
            );
          })}
        </section>
      ))}
    </div>
  );
}
