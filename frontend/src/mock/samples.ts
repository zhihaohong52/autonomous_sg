// Offline demo: the backend's own recorded v2 run (frontend/src/api/samples, written by the backend).
import type { BenchmarkReport, Config, FlightsResponse, NegotiationResult, WaveScenario, WaveSummary } from "../api/types";
import configJson from "../api/samples/config.json";
import waveJson from "../api/samples/wave.json";
import resultJson from "../api/samples/result.json";
import flightsJson from "../api/samples/flights.json";
import benchJson from "../api/samples/benchmark.json";

export const mockConfig = configJson as unknown as Config;
export const wave = waveJson as unknown as WaveScenario;
export const mockResult = resultJson as unknown as NegotiationResult;
export const mockLog = mockResult.log ?? [];
export const mockFlights = flightsJson as unknown as FlightsResponse;
export const mockBenchmark = benchJson as unknown as BenchmarkReport;

const end = new Date(new Date(wave.window_start).getTime() + 3600e3);
export const mockWaves: WaveSummary[] = [{
  id: wave.id,
  airport: { iata: wave.airport.iata, icao: wave.airport.icao, name: wave.airport.name },
  window_start: wave.window_start,
  window_end: end.toISOString().slice(0, 19),
  n_flights: wave.flights.length,
  n_queue: wave.queue.length,
  callsigns: wave.flights.map((f) => f.callsign),
  disrupted_callsigns: wave.flights.filter((f) => f.disrupted).map((f) => f.callsign),
  weather_summary: wave.metar?.raw.split(" ").slice(2, 5).join(" ") ?? "",
  replay_available: true,
}];
