import { useEffect, useState } from "react";
import { Benchmark } from "./Benchmark";
import { WaveScreen } from "./WaveScreen";

const icon = {
  wave: <svg viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M2 5h9M2 9h14M2 13h6" /><path d="M13 3l3 2-3 2" /></svg>,
  bench: <svg viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M3 15V9M9 15V3M15 15v-5" /><path d="M1.5 15.5h15" /></svg>,
};

export function App() {
  const [route, setRoute] = useState(location.hash);
  useEffect(() => {
    const on = () => setRoute(location.hash);
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  const bench = route.startsWith("#/benchmark");
  return (
    <div className="shell">
      <nav className="rail" aria-label="Screens">
        <img className="mark" src="/mark.svg" alt="Disruption Negotiator" />
        <a href="#/wave" aria-current={!bench ? "page" : undefined} aria-label="Arrival wave">{icon.wave}<span className="tip">Arrival wave</span></a>
        <a href="#/benchmark" aria-current={bench ? "page" : undefined} aria-label="Benchmark">{icon.bench}<span className="tip">Benchmark</span></a>
      </nav>
      {bench ? <Benchmark /> : <WaveScreen />}
    </div>
  );
}
