import { useEffect, useState } from "react";
import { Benchmark } from "./Benchmark";
import { WaveScreen } from "./WaveScreen";

export function App() {
  const [route, setRoute] = useState(location.hash);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setRoute(location.hash);
    const sc = () => setScrolled(scrollY > 4);
    addEventListener("hashchange", on);
    addEventListener("scroll", sc, { passive: true });
    return () => { removeEventListener("hashchange", on); removeEventListener("scroll", sc); };
  }, []);
  const bench = route.startsWith("#/benchmark");
  return (
    <>
      <header className={`topbar${scrolled ? " scrolled" : ""}`}>
        <div className="bar-inner">
          <a className="brand" href="#/wave"><img src="/mark.svg" alt="" /><span>Disruption Negotiator</span></a>
          <nav className="topnav" aria-label="Screens">
            <a href="#/wave" aria-current={!bench ? "page" : undefined}>Arrival wave</a>
            <a href="#/benchmark" aria-current={bench ? "page" : undefined}>Benchmark</a>
          </nav>
        </div>
      </header>
      {bench ? <Benchmark /> : <WaveScreen />}
    </>
  );
}
