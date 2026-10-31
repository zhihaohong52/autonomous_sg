import type { JointPlan, Move, RuleCheck, WaveScenario } from "../api/types";
import { clock } from "../format";

const TURN = { narrow: 40, wide: 90 } as const;
const W = 832, LABEL = 64, RIGHT = 8, LANE = 25, BAR = 18, AXIS = 22, GAP = 14;
const SPAN = 90;                                   // T+0 … T+90 minutes
const x = (min: number) => LABEL + (Math.max(0, Math.min(SPAN, min)) / SPAN) * (W - LABEL - RIGHT);

export interface Rejection { seq: number; move: Move; violations: RuleCheck[]; runway?: string }

export function Gantt({ wave, plan, rejection }: { wave: WaveScenario; plan: JointPlan | null; rejection: Rejection | null }) {
  const runways = wave.airport.runways;
  const gates = wave.gates;
  const yRwy = (id: string) => AXIS + runways.findIndex((r) => r.id === id) * LANE;
  const gatesTop = AXIS + runways.length * LANE + GAP;
  const yGate = (id: string) => gatesTop + gates.findIndex((g) => g.id === id) * LANE;
  const H = gatesTop + gates.length * LANE + 4;
  const ticks = Array.from({ length: SPAN / 15 + 1 }, (_, i) => i * 15);

  // which lane the rejected move touched
  let rejY: number | null = null;
  if (rejection) {
    const p = rejection.move.params;
    const cs = rejection.move.callsigns[0];
    const rwy = typeof p.runway === "string" ? p.runway : rejection.runway ?? plan?.assignments.find((a) => a.callsign === cs)?.runway;
    if (typeof p.gate === "string" && gates.some((g) => g.id === p.gate)) rejY = yGate(p.gate);
    else if (rwy) rejY = yRwy(rwy);
  }

  return (
    <div className="gantt-wrap" tabIndex={0} role="region" aria-label="Runway and gate timeline, scrolls sideways on small screens">
      <svg className="gantt" viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
        aria-label={`Runway and gate timeline, ${clock(wave.window_start, 0)} to ${clock(wave.window_start, SPAN)}`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={AXIS - 4} y2={H} stroke="var(--gridline)" />
            <text x={x(t)} y={12} textAnchor={t === 0 ? "start" : t === SPAN ? "end" : "middle"}>{clock(wave.window_start, t)}</text>
          </g>
        ))}

        {runways.map((r) => {
          const y = yRwy(r.id);
          return (
            <g key={r.id}>
              <text className="lane-label" x={0} y={y + 14} style={r.open ? undefined : { fill: "var(--muted)" }}>{r.id}</text>
              {!r.open && (
                <>
                  <rect x={x(0)} y={y + 4} width={x(SPAN) - x(0)} height={BAR} fill="url(#hatch)" rx={3} />
                  <text x={x(0) + 8} y={y + 16} style={{ fill: "var(--soft)" }}>Closed · {r.closed_reason}</text>
                </>
              )}
              {wave.queue.filter((q) => q.runway === r.id).map((q) => (
                <rect key={q.callsign} x={x(q.slot_min)} y={y + 4} width={7} height={BAR} rx={2} fill="var(--queue)">
                  <title>{`${q.callsign} ${q.aircraft_type ?? ""} ${q.wake} · ${clock(wave.window_start, q.slot_min)} (fixed traffic)`}</title>
                </rect>
              ))}
            </g>
          );
        })}

        <text x={0} y={gatesTop - 4} style={{ fontSize: 11 }}>Gates</text>
        {gates.map((g) => {
          const y = yGate(g.id);
          return (
            <g key={g.id}>
              <text className="lane-label" x={0} y={y + 14}>{g.id === "RS" ? "Remote" : g.id}</text>
              {g.busy.map((b) => (
                <g key={b.label}>
                  <rect x={x(b.from_min)} y={y + 4} width={Math.max(2, x(b.to_min) - x(b.from_min))} height={BAR} rx={3} fill="var(--busy)" />
                  {x(b.to_min) - x(b.from_min) > 90 && <text className="q-label" x={x(b.from_min) + 6} y={y + 16}>{b.label}</text>}
                </g>
              ))}
            </g>
          );
        })}

        {plan?.assignments.filter((a) => a.action === "land").map((a) => {
          const f = wave.flights.find((fl) => fl.callsign === a.callsign)!;
          const slot = a.slot_min ?? 0, inb = a.metrics.in_block_min ?? slot;
          const end = Math.max(f.next_departure_min, inb + TURN[f.gate_size]);
          const ry = yRwy(a.runway ?? ""), gy = yGate(a.gate ?? "");
          const holdFrom = slot - a.metrics.hold_min;
          const crowded = plan.assignments.some((o) => o !== a && o.runway === a.runway && (o.slot_min ?? 0) > slot && (o.slot_min ?? 0) - slot < 6);
          return (
            <g key={a.callsign}>
              <g className="wave" style={{ transform: `translate(${x(slot)}px, ${ry}px)` }}>
                {a.metrics.hold_min > 0 && <line x1={x(holdFrom) - x(slot)} x2={0} y1={13} y2={13} stroke="var(--accent)" strokeDasharray="2 3" />}
                <rect x={0} y={4} width={8} height={BAR} rx={2} fill="var(--accent)" />
                <text x={crowded ? -4 : 12} y={16} textAnchor={crowded ? "end" : "start"} style={{ font: "500 11px var(--mono)", fill: "var(--accent)" }}>{a.callsign}</text>
              </g>
              <g className="wave" style={{ transform: `translate(${x(inb)}px, ${gy}px)` }}>
                <rect x={0} y={4} width={Math.max(8, x(end) - x(inb))} height={BAR} rx={3} fill="var(--accent)" />
                <text className="bar-label" x={6} y={16}>{a.callsign}</text>
              </g>
              <title>{`${a.callsign}: runway ${a.runway} ${clock(wave.window_start, slot)}, gate ${a.gate} ${clock(wave.window_start, inb)}`}</title>
            </g>
          );
        })}

        {rejection && rejY !== null && (
          <rect key={rejection.seq} className="flash" x={LABEL - 6} y={rejY + 1} width={W - LABEL + 4} height={LANE - 2} rx={4}
            fill="none" stroke="var(--negative)" strokeWidth={1.5} />
        )}
        <defs>
          <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--hatch)" strokeWidth="3" />
          </pattern>
        </defs>
      </svg>
    </div>
  );
}

export function GanttLegend() {
  return (
    <div className="legend">
      <span><i style={{ background: "var(--accent)" }} />Negotiated flights</span>
      <span><i style={{ background: "var(--queue)" }} />Fixed traffic</span>
      <span><i style={{ background: "var(--busy)" }} />Gate in use</span>
    </div>
  );
}
