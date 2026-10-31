import type { JointPlan, Move, RuleCheck, WaveScenario } from "../api/types";
import { clock } from "../format";

const W = 1158, LABEL = 64, RIGHT = 8, LANE = 22, BAR = 14, AXIS = 22, GAP = 16, NOTE = 20;

export interface Rejection { seq: number; move: Move; violations: RuleCheck[]; runway?: string }

interface Props {
  wave: WaveScenario;
  plan: JointPlan | null;
  other?: JointPlan | null;            // the plan not shown (status quo or agreed); keeps the lanes steady when toggling
  turn: number;                        // gate occupancy after in-block, the engine's turnaround (GND-GATE-FREE evidence)
  rejection: Rejection | null;
}

// greedy sub-rows so overlapping bars on one gate never sit on top of each other
function rows<T extends { from: number; to: number }>(items: T[]): (T & { row: number })[] {
  const ends: number[] = [];
  return [...items].sort((a, b) => a.from - b.from).map((it) => {
    let r = ends.findIndex((e) => e <= it.from);
    if (r < 0) { r = ends.length; ends.push(0); }
    ends[r] = it.to;
    return { ...it, row: r };
  });
}

export function Gantt({ wave, plan, other, turn, rejection }: Props) {
  const plans = [plan, other].filter(Boolean) as JointPlan[];
  const land = (p: JointPlan) => p.assignments.filter((a) => a.action === "land");
  const usedRwy = new Set([...wave.queue.map((q) => q.runway), ...plans.flatMap((p) => land(p).map((a) => a.runway ?? ""))]);
  const runways = wave.airport.runways.filter((r) => !r.open || usedRwy.has(r.id));
  const hiddenRwy = wave.airport.runways.length - runways.length;
  const usedGate = new Set([...wave.flights.map((f) => f.scheduled_gate), ...plans.flatMap((p) => land(p).map((a) => a.gate ?? ""))]);
  const gatesShown = wave.gates.filter((g) => usedGate.has(g.id));
  const hiddenGates = wave.gates.length - gatesShown.length;

  // the axis covers the wave hour plus 15 min, reaches back to the earliest landing or in-block shown,
  // and forward to the last landing, gate turnaround or fixed arrival (capped at 3 h; later ones are labelled at the edge)
  const times = plans.flatMap((p) => land(p).flatMap((a) => [a.slot_min ?? 0, a.metrics.in_block_min ?? 0]));
  const lastPlan = plans.flatMap((p) => land(p).map((a) => (a.metrics.in_block_min ?? a.slot_min ?? 0) + turn));
  const lastQueue = wave.queue.map((q) => q.slot_min + 2);
  const START = Math.min(0, Math.floor((Math.min(0, ...times) - 3) / 15) * 15);
  const END = Math.min(180, Math.max(75, Math.ceil((Math.max(0, ...lastPlan, ...lastQueue) + 3) / 15) * 15));
  const x = (m: number) => LABEL + ((Math.max(START, Math.min(END, m)) - START) / (END - START)) * (W - LABEL - RIGHT);

  const yRwy = (id: string) => AXIS + runways.findIndex((r) => r.id === id) * LANE;
  const rwyEnd = AXIS + runways.length * LANE + (hiddenRwy ? NOTE : 0);
  const gatesTop = rwyEnd + GAP;

  const lanes = gatesShown.map((g) => {
    const flights = (plan ? land(plan) : []).filter((a) => a.gate === g.id).map((a) => {
      const from = a.metrics.in_block_min ?? a.slot_min ?? 0;
      return { kind: "wave" as const, label: a.callsign, from, to: from + turn };
    });
    const busy = g.busy.map((b) => ({ kind: "busy" as const, label: b.label, from: b.from_min, to: b.to_min }));
    const items = rows([...busy, ...flights]);
    return { g, items, n: Math.max(1, ...items.map((i) => i.row + 1)) };
  });
  const gateY: Record<string, number> = {};
  let y = gatesTop;
  for (const l of lanes) { gateY[l.g.id] = y; y += l.n * LANE; }
  const H = y + (hiddenGates ? NOTE : 0) + 4;
  const ticks: number[] = [];
  for (let t = START; t <= END; t += 15) ticks.push(t);

  let rejY: number | null = null;
  if (rejection) {
    const p = rejection.move.params;
    const cs = rejection.move.callsigns[0];
    const rwy = typeof p.runway === "string" ? p.runway : rejection.runway ?? plan?.assignments.find((a) => a.callsign === cs)?.runway;
    if (typeof p.gate === "string" && gateY[p.gate] != null) rejY = gateY[p.gate];
    else if (rwy && runways.some((r) => r.id === rwy)) rejY = yRwy(rwy);
  }
  const real = (label: string) => !/simulated/i.test(label);

  return (
    <div className="gantt-wrap" tabIndex={0} role="region" aria-label="Runway and gate timeline, scrolls sideways on small screens">
      <svg className="gantt" viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
        aria-label={`Runway and gate timeline, ${clock(wave.window_start, START)} to ${clock(wave.window_start, END)}`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={AXIS - 4} y2={H} stroke={t === 0 || t === 60 ? "var(--control)" : "var(--gridline)"} />
            <text x={x(t)} y={12} textAnchor={t === START ? "start" : t === END ? "end" : "middle"}>{clock(wave.window_start, t)}</text>
          </g>
        ))}

        {runways.map((r) => {
          const ry = yRwy(r.id);
          return (
            <g key={r.id}>
              <text className="lane-label" x={0} y={ry + 14} style={r.open ? undefined : { fill: "var(--muted)" }}>{r.id}</text>
              {!r.open && (
                <>
                  <rect x={x(START)} y={ry + 4} width={x(END) - x(START)} height={BAR} fill="url(#hatch)" rx={3} />
                  <text x={x(START) + 8} y={ry + 15} style={{ fill: "var(--muted)" }}>Closed{r.closed_reason ? ` · ${r.closed_reason}` : ""}</text>
                </>
              )}
              {wave.queue.filter((q) => q.runway === r.id).map((q) => (
                <rect key={q.callsign} x={x(q.slot_min)} y={ry + 4} width={4} height={BAR} rx={1} fill="var(--queue)">
                  <title>{`${q.callsign} ${q.aircraft_type ?? ""} ${q.wake} · ${clock(wave.window_start, q.slot_min)} (fixed traffic)`}</title>
                </rect>
              ))}
            </g>
          );
        })}
        {hiddenRwy > 0 && <text x={LABEL} y={rwyEnd - 6} style={{ fontSize: 13 }}>{hiddenRwy} other runway{hiddenRwy === 1 ? "" : "s"} open, not used in this wave</text>}

        <text x={0} y={gatesTop - 4} style={{ fontSize: 13 }}>Gates</text>
        {lanes.map(({ g, items }) => (
          <g key={g.id}>
            <text className="lane-label" x={0} y={gateY[g.id] + 14}>{g.id === "RS" ? "Remote" : g.id}</text>
            {items.filter((i) => i.kind === "busy").map((b, i) => (
              <g key={i}>
                <rect x={x(b.from)} y={gateY[g.id] + b.row * LANE + 4} width={Math.max(2, x(b.to) - x(b.from))} height={BAR} rx={3} fill="var(--busy)"><title>{b.label}</title></rect>
                {real(b.label) && x(b.to) - x(b.from) > 110 && <text className="q-label" x={x(b.from) + 6} y={gateY[g.id] + b.row * LANE + 15}>{b.label}</text>}
              </g>
            ))}
          </g>
        ))}
        {hiddenGates > 0 && <text x={LABEL} y={H - 10} style={{ fontSize: 13 }}>{hiddenGates} other gate{hiddenGates === 1 ? "" : "s"} not used by these flights</text>}

        {plan && land(plan).map((a) => {
          const slot = a.slot_min ?? 0, inb = a.metrics.in_block_min ?? slot;
          const ry = yRwy(a.runway ?? "");
          const lane = lanes.find((l) => l.g.id === a.gate);
          const item = lane?.items.find((i) => i.kind === "wave" && i.label === a.callsign);
          const gy = (gateY[a.gate ?? ""] ?? 0) + (item?.row ?? 0) * LANE;
          const holdFrom = slot - a.metrics.hold_min;
          const near = (o: typeof a) => o !== a && o.runway === a.runway && Math.abs((o.slot_min ?? 0) - slot) < 7;
          const after = land(plan).some((o) => near(o) && (o.slot_min ?? 0) > slot);
          const before = land(plan).some((o) => near(o) && (o.slot_min ?? 0) <= slot && o.callsign < a.callsign);
          const offAxis = (m: number, y: number) => (
            <text x={x(END)} y={y + 15} textAnchor="end" style={{ font: "500 12px var(--mono)", fill: "var(--hue)" }}>
              {`${a.callsign} → ${clock(wave.window_start, m)}`}
            </text>
          );
          if (slot > END) return (
            <g key={a.callsign}>
              {offAxis(slot, ry)}
              {item && offAxis(inb, gy)}
              <title>{`${a.callsign}: runway ${a.runway} ${clock(wave.window_start, slot)}, gate ${a.gate} ${clock(wave.window_start, inb)} (after the chart)`}</title>
            </g>
          );
          return (
            <g key={a.callsign}>
              <g className="wave" style={{ transform: `translate(${x(slot)}px, ${ry}px)` }}>
                {a.metrics.hold_min > 0 && <line x1={x(holdFrom) - x(slot)} x2={0} y1={11} y2={11} stroke="var(--hue)" strokeDasharray="2 3" />}
                <rect x={0} y={4} width={6} height={BAR} rx={2} fill="var(--hue)" />
                {!(after && before) && <text x={after ? -4 : 10} y={15} textAnchor={after ? "end" : "start"} style={{ font: "500 12px var(--mono)", fill: "var(--hue)" }}>{a.callsign}</text>}
              </g>
              {item && (
                <g className="wave" style={{ transform: `translate(${x(inb)}px, ${gy}px)` }}>
                  <rect x={0} y={4} width={Math.max(6, x(inb + turn) - x(inb))} height={BAR} rx={3} fill="var(--hue)" />
                  <text className="bar-label" x={6} y={15}>{a.callsign}</text>
                </g>
              )}
              <title>{`${a.callsign}: runway ${a.runway} ${clock(wave.window_start, slot)}, gate ${a.gate} ${clock(wave.window_start, inb)}–${clock(wave.window_start, inb + turn)}`}</title>
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

export function GanttLegend({ simulated }: { simulated?: boolean }) {
  return (
    <div className="legend">
      <span><i style={{ background: "var(--hue)" }} />Negotiated flights</span>
      <span><i style={{ background: "var(--queue)", width: 4, height: 12, verticalAlign: -2 }} />Fixed arrivals</span>
      <span><i style={{ background: "var(--busy)" }} />Gate held by another flight{simulated ? " (simulated)" : ""}</span>
      <span><i style={{ width: 14, height: 0, borderTop: "1.5px dashed var(--hue)", borderRadius: 0, verticalAlign: 2 }} />Airborne holding</span>
    </div>
  );
}
