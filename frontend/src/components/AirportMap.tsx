import type { JointPlan, Runway, WaveScenario } from "../api/types";

// Runways drawn to scale from their end coordinates (each end's own threshold is le_*, its far end he_*),
// with the METAR wind as a compass arrow. Blue marks the runway ends the plan lands on, nothing else.
const W = 560, H = 380, PAD = 52, ROSE = 132;

type End = Runway & { le_lat: number; le_lon: number; he_lat: number; he_lon: number };
const hasGeo = (r: Runway): r is End => [r.le_lat, r.le_lon, r.he_lat, r.he_lon].every((v) => typeof v === "number");

export function AirportMap({ wave, plan }: { wave: WaveScenario; plan: JointPlan | null }) {
  const ends = wave.airport.runways.filter(hasGeo);
  if (!ends.length) return <p className="empty">No runway coordinates for {wave.airport.iata}, so the map can't be drawn.</p>;

  // equirectangular projection around the airport, corrected for latitude, fitted into the left of the box
  const lat0 = wave.airport.lat ?? ends[0].le_lat, k = Math.cos((lat0 * Math.PI) / 180);
  const px = (lon: number) => lon * k, py = (lat: number) => -lat;
  const xs = ends.flatMap((e) => [px(e.le_lon), px(e.he_lon)]), ys = ends.flatMap((e) => [py(e.le_lat), py(e.he_lat)]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const s = Math.min((W - ROSE - 2 * PAD) / (x1 - x0 || 1e-6), (H - 2 * PAD) / (y1 - y0 || 1e-6));
  const ox = PAD + (W - ROSE - 2 * PAD - (x1 - x0) * s) / 2, oy = PAD + (H - 2 * PAD - (y1 - y0) * s) / 2;
  const X = (lon: number) => ox + (px(lon) - x0) * s, Y = (lat: number) => oy + (py(lat) - y0) * s;

  // one strip per physical runway: the two ends share coordinates, swapped
  const key = (e: End) => [e.le_lat, e.le_lon, e.he_lat, e.he_lon].map((v) => v.toFixed(3));
  const strips: End[][] = [];
  for (const e of ends) {
    const [a, b, c, d] = key(e);
    const twin = strips.find((g) => { const [p, q, r, t] = key(g[0]); return (p === a && q === b && r === c && t === d) || (p === c && q === d && r === a && t === b); });
    if (twin) twin.push(e); else strips.push([e]);
  }
  const used = new Map<string, number>();
  for (const a of plan?.assignments ?? []) if (a.action === "land" && a.runway) used.set(a.runway, (used.get(a.runway) ?? 0) + 1);
  const windOf = (id: string) => wave.runway_wind.find((w) => w.runway === id);

  const m = wave.metar;
  const cx = W - ROSE / 2, cy = 74, R = 40;
  const dir = m?.wind_dir_deg, calm = !m || !m.wind_kt;
  const rad = dir != null ? ((dir - 90) * Math.PI) / 180 : 0;    // the direction the wind blows from, clockwise from north
  const ax = Math.cos(rad), ay = Math.sin(rad);

  return (
    <svg className="airport-map" viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
      aria-label={`${wave.airport.iata} runways to scale; wind ${calm ? "calm" : `from ${dir ?? "variable"}° at ${m?.wind_kt} kt`}`}>
      <defs>
        <marker id="wind-head" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0L10 5L0 10z" fill="var(--text)" />
        </marker>
      </defs>
      {strips.map((g) => {
        const e = g[0], open = g.some((x) => x.open), inUse = g.some((x) => used.has(x.id));
        const width = Math.max(4, Math.min(9, (e.length_ft ?? 9000) / 1500));
        return (
          <g key={g.map((x) => x.id).join("/")}>
            <line x1={X(e.le_lon)} y1={Y(e.le_lat)} x2={X(e.he_lon)} y2={Y(e.he_lat)} strokeLinecap="butt" strokeWidth={width}
              stroke={inUse ? "var(--hue)" : open ? "#9aa1ab" : "var(--busy)"} strokeDasharray={open ? undefined : "6 4"}>
              <title>{g.map((x) => `${x.id}: ${x.open ? "open" : `closed, ${x.closed_reason ?? ""}`}`).join("\n")}{e.length_ft ? `\n${e.length_ft.toLocaleString("en-US")} ft` : ""}</title>
            </line>
          </g>
        );
      })}
      {ends.map((e) => {
        // label sits just beyond the threshold, on the approach side
        const dx = X(e.le_lon) - X(e.he_lon), dy = Y(e.le_lat) - Y(e.he_lat), len = Math.hypot(dx, dy) || 1;
        const ux = dx / len, uy = dy / len, tx = X(e.le_lon) + ux * 14, ty = Y(e.le_lat) + uy * 14;
        const n = used.get(e.id), w = windOf(e.id);
        return (
          <g key={e.id}>
            <text x={tx} y={ty + 4} textAnchor="middle" className={`end-label${n ? " used" : ""}${e.open ? "" : " closed"}`}>
              {e.id}<title>{`${e.id}: ${e.open ? "open" : `closed, ${e.closed_reason ?? ""}`}${w ? `\nhead ${w.headwind_kt} kt · cross ${w.crosswind_kt} kt · tail ${w.tailwind_kt} kt` : ""}${n ? `\n${n} negotiated landing${n === 1 ? "" : "s"}` : ""}`}</title>
            </text>
            {n ? (
              // landing chevron: points down the runway from the threshold
              <path d={`M ${X(e.le_lon) - ux * 6 - uy * 3} ${Y(e.le_lat) - uy * 6 + ux * 3} L ${X(e.le_lon) - ux * 11} ${Y(e.le_lat) - uy * 11} L ${X(e.le_lon) - ux * 6 + uy * 3} ${Y(e.le_lat) - uy * 6 - ux * 3}`}
                fill="none" stroke="#fff" strokeWidth={1.6} strokeLinejoin="round" />
            ) : null}
          </g>
        );
      })}

      <g>
        <circle cx={cx} cy={cy} r={R} fill="none" stroke="var(--border)" />
        <text x={cx} y={cy - R - 6} textAnchor="middle" className="rose">N</text>
        {calm ? <text x={cx} y={cy + 4} textAnchor="middle" className="rose">Calm</text>
          : dir == null ? <text x={cx} y={cy + 4} textAnchor="middle" className="rose">Variable</text>
          : <line x1={cx + ax * (R - 4)} y1={cy + ay * (R - 4)} x2={cx - ax * (R - 6)} y2={cy - ay * (R - 6)} stroke="var(--text)" strokeWidth={2} markerEnd="url(#wind-head)" />}
        <text x={cx} y={cy + R + 22} textAnchor="middle" className="rose-value">
          {calm ? "Wind calm" : `${dir != null ? `${String(Math.round(dir)).padStart(3, "0")}°` : "VRB"} ${m!.wind_kt} kt${m!.gust_kt ? ` G${m!.gust_kt}` : ""}`}
        </text>
        <text x={cx} y={cy + R + 40} textAnchor="middle" className="rose">{m ? `${m.surface} runway${m.visibility_sm != null ? ` · ${m.visibility_sm} SM` : ""}` : "No METAR"}</text>
      </g>
      <text x={PAD} y={H - 8} className="rose">Drawn to scale from runway end coordinates · arrow shows where the wind blows to</text>
    </svg>
  );
}
