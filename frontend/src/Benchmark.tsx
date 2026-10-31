import { useEffect, useState } from "react";
import { api, ApiError } from "./api/client";
import type { BenchmarkReport, ModelInfo } from "./api/types";
import { dateLabel } from "./format";

type Arm = BenchmarkReport["arms"][number];
const COLS: { key: keyof Arm["summary"]; label: string; unit: string; digits: number; goodDown: boolean }[] = [
  { key: "consensus_rate_pct", label: "Consensus", unit: "%", digits: 1, goodDown: false },
  { key: "mean_rounds", label: "Rounds", unit: "", digits: 1, goodDown: true },
  { key: "rule_violations_in_agreed_plans", label: "Rule breaches", unit: "", digits: 0, goodDown: true },
  { key: "delay_min", label: "Delay", unit: " min", digits: 1, goodDown: true },
  { key: "pax_protected_pct", label: "Pax protected", unit: "%", digits: 1, goodDown: false },
  { key: "fuel_kg", label: "Hold fuel", unit: " kg", digits: 0, goodDown: true },
  { key: "brier", label: "Brier", unit: "", digits: 3, goodDown: true },
  { key: "latency_p95_ms", label: "p95 latency", unit: " ms", digits: 0, goodDown: true },
  { key: "error_rate_pct", label: "Errors", unit: "%", digits: 1, goodDown: true },
  { key: "cost_usd_per_wave", label: "Cost / wave", unit: "", digits: 3, goodDown: true },
];

export function Benchmark() {
  const [rep, setRep] = useState<BenchmarkReport | null>(null);
  const [err, setErr] = useState<{ code: string; message: string } | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  useEffect(() => { api.config().then((c) => setModels(c.models)).catch(() => setModels([])); }, []);
  useEffect(() => {
    api.benchmark().then(setRep).catch((e) => setErr(e instanceof ApiError ? { code: e.code, message: e.message } : { code: "error", message: String(e) }));
  }, []);

  return (
    <main className="canvas">
      <header className="titlerow">
        <div>
          <h1>Benchmark</h1>
          <p className="sub">{rep ? <>{rep.n_waves} real waves, seed {rep.seed}, every arm on the same waves. <b>Winner: {rep.selection.winner}</b> ({rep.selection.rule}).</> : "Each decision model against the formulas and the status quo on the same real waves."}</p>
        </div>
        {rep && <span className="muted" style={{ fontSize: 12 }}>Generated {dateLabel(rep.generated_at)}</span>}
      </header>

      {!rep ? (
        <section className="card">
          <div className="card-head"><h2>Model arms</h2><span className="meta">{err ? "Not generated yet" : "Loading…"}</span></div>
          <p className="empty" style={{ marginBottom: 16 }}>{err?.code === "not_found" || err?.code === "404"
            ? "The benchmark has not been run yet. Each arm below gets its figures once the backend has run it on the same real waves."
            : err ? `Couldn't load the benchmark: ${err.message}` : "Loading the benchmark…"}</p>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Model arms, not yet measured">
            <table>
              <thead><tr><th>Model</th>{COLS.map((c) => <th key={c.key} className="num">{c.label}</th>)}</tr></thead>
              <tbody>
                {[...models, { id: "status-quo", provider: "formula", label: "Status quo (first come, first served)", available: true } as ModelInfo].map((m) => (
                  <tr key={m.id}>
                    <td><span className="code">{m.label}</span><span className="sub">{m.id === "status-quo" ? "baseline" : m.provider}{m.available ? "" : " · unavailable"}</span></td>
                    {COLS.map((c) => <td key={c.key} className="num muted">—</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <div className="grid">
          <section className="card span-12">
            <div className="card-head"><h2>Model arms</h2><span className="meta">Status quo: {rep.baseline.delay_min.toFixed(1)} min delay · {rep.baseline.pax_protected_pct.toFixed(1)}% pax protected · {rep.baseline.fuel_kg} kg hold fuel</span></div>
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Model arms">
              <table>
                <thead><tr><th>Model</th>{COLS.map((c) => <th key={c.key} className="num">{c.label}</th>)}</tr></thead>
                <tbody>
                  {rep.arms.map((a) => (
                    <tr key={a.model}>
                      <td><span className="code">{a.label}</span>{a.model === rep.selection.winner && <span className="pill pos" style={{ marginLeft: 8 }}>Selected</span>}<span className="sub">{a.provider}</span></td>
                      {COLS.map((c) => {
                        const v = a.summary[c.key];
                        if (v == null) return <td key={c.key} className="num muted" title="No data for this arm">—</td>;
                        const bad = c.key === "rule_violations_in_agreed_plans" && v > 0;
                        return <td key={c.key} className={`num${bad ? " neg" : ""}`}>{c.key === "cost_usd_per_wave" ? `$${v.toFixed(c.digits)}` : `${v.toFixed(c.digits)}${c.unit}`}</td>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="card span-6">
            <div className="card-head"><h2>Why this model</h2></div>
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>{rep.selection.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            <dl className="kv" style={{ marginTop: 16 }}>
              <dt>Engine switched off</dt><dd>{rep.ablation_no_engine_unsafe_pct == null ? <span className="muted">Not measured in this run</span> : <><span className="neg">{rep.ablation_no_engine_unsafe_pct.toFixed(1)}%</span> of agreed plans break a rule</>}</dd>
              <dt>Outage test</dt><dd>{rep.outage_test.primary_disabled} disabled: {rep.outage_test.resolved} of {rep.outage_test.waves} waves resolved ({Object.entries(rep.outage_test.fallbacks_used).map(([k, v]) => `${k} ${v}`).join(", ")})</dd>
            </dl>
          </section>
          <section className="card span-6">
            <div className="card-head"><h2>Failures</h2><span className="meta">{rep.failures.length} listed</span></div>
            {rep.failures.length ? (
              <table><tbody>{rep.failures.slice(0, 8).map((f) => (
                <tr key={f.wave_id + f.model}><td><a className="code" style={{ color: "var(--text)" }} href={`#/wave/${f.wave_id}`}>{f.wave_id}</a><span className="sub">{f.model}</span></td><td style={{ whiteSpace: "normal" }}>{f.reason}</td></tr>
              ))}</tbody></table>
            ) : <p className="empty">No failures in this run.</p>}
          </section>
        </div>
      )}
    </main>
  );
}
