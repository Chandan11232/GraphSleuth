import { useCallback, useEffect, useRef, useState } from "react";
import {
  API_BASE,
  api,
  type AnalysisStep,
  type GraphResponse,
  type Hotspot,
  type InvestigationResult,
  type InvestigationSummary,
  type RiskyChange,
} from "../services/api";

/** Streams an investigation's steps over SSE, then holds the final result. */
export function useInvestigation(id: string | undefined) {
  const [steps, setSteps] = useState<AnalysisStep[]>([]);
  const [result, setResult] = useState<InvestigationResult | null>(null);
  const [status, setStatus] = useState<string>("connecting");
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!id) return;
    setSteps([]);
    setResult(null);
    setError(null);
    setStatus("running");
    const source = new EventSource(`${API_BASE}/investigations/${id}/events`);
    sourceRef.current = source;
    let finished = false;
    source.onmessage = (event) => {
      if (event.data === "[DONE]") {
        finished = true;
        source.close();
        return;
      }
      const ev = JSON.parse(event.data);
      if (ev.type === "step")
        setSteps((prev) =>
          prev.some((s) => s.step === ev.step.step) ? prev : [...prev, ev.step],
        );
      else if (ev.type === "status") setMessage(ev.message);
      else if (ev.type === "result") {
        setResult(ev.result);
        setSteps(ev.result.steps);
        setStatus(ev.result.status);
        // The stored copy also carries human confirmations made after the run.
        api
          .get(`/investigations/${id}`)
          .then((r) => {
            setResult(r.data);
            setStatus(r.data.status);
          })
          .catch(() => undefined);
      } else if (ev.type === "error") {
        setError(ev.message);
        setStatus("failed");
      }
    };
    source.onerror = () => {
      source.close();
      if (finished) return;
      // Fall back to the stored result (e.g. the stream was cut by a proxy).
      api
        .get(`/investigations/${id}`)
        .then((r) => {
          setResult(r.data.status === "running" ? null : r.data);
          setSteps(r.data.steps || []);
          setStatus(r.data.status);
          if (r.data.error) setError(r.data.error);
        })
        .catch(() => {
          setError("Investigation not found");
          setStatus("failed");
        });
    };
    return () => source.close();
  }, [id]);

  const reload = useCallback(async () => {
    if (!id) return;
    const r = await api.get(`/investigations/${id}`);
    setResult(r.data);
    setStatus(r.data.status);
  }, [id]);

  return { steps, result, status, message, error, reload };
}

export function useEvidenceGraph(id: string | undefined, enabled: boolean) {
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!id || !enabled) return;
    setLoading(true);
    api
      .get(`/graph/investigation/${id}`)
      .then((r) => setGraph(r.data))
      .catch(() => setGraph(null))
      .finally(() => setLoading(false));
  }, [id, enabled]);
  return { graph, loading };
}

export function useRepoInsights(
  repoId: string | null,
  ready: boolean,
  commitLimit: number = 10,
) {
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [riskyChanges, setRiskyChanges] = useState<RiskyChange[]>([]);
  const [investigations, setInvestigations] = useState<InvestigationSummary[]>(
    [],
  );
  useEffect(() => {
    if (!repoId || !ready) return;
    api
      .get(`/graph/${repoId}/hotspots`, { params: { limit: 6 } })
      .then((r) => setHotspots(r.data))
      .catch(() => setHotspots([]));
    api
      .get(`/graph/${repoId}/risky-changes`, {
        params: { commits: commitLimit },
      })
      .then((r) => setRiskyChanges(r.data))
      .catch(() => setRiskyChanges([]));
    api
      .get("/investigations", { params: { repository_id: repoId } })
      .then((r) => setInvestigations(r.data))
      .catch(() => setInvestigations([]));
  }, [repoId, ready, commitLimit]);
  return { hotspots, riskyChanges, investigations };
}
