import { useCallback, useEffect, useRef, useState } from "react";
import { runStream } from "../api/client";
import type { Beat, RunState } from "../api/types";
import { RateLimited } from "../api/types";
import { stateFromBeats } from "./stages";

export interface RunController {
  beats: Beat[];
  state: RunState;
  elapsedSeconds: number;
  // Set when the run ended badly. `retryAfterSeconds` is only ever the number
  // the server sent in Retry-After, never an estimate of our own.
  failure: string | null;
  retryAfterSeconds: number | null;
  start: (pr: number) => void;
  cancel: () => void;
  reset: () => void;
  pr: number | null;
}

export function useRun(): RunController {
  const [beats, setBeats] = useState<Beat[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [retryAfterSeconds, setRetryAfter] = useState<number | null>(null);
  const [elapsedSeconds, setElapsed] = useState(0);
  const [pr, setPr] = useState<number | null>(null);
  const abort = useRef<AbortController | null>(null);

  // A run takes over a minute against the live service, and most of that is a
  // single gap while the specialists read. A visible clock is the difference
  // between "working" and "hung".
  useEffect(() => {
    if (!streaming) return;
    const started = Date.now();
    setElapsed(0);
    const id = window.setInterval(
      () => setElapsed(Math.round((Date.now() - started) / 1000)),
      1000
    );
    return () => window.clearInterval(id);
  }, [streaming]);

  useEffect(() => () => abort.current?.abort(), []);

  const start = useCallback(
    (next: number) => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;

      setBeats([]);
      setFailure(null);
      setRetryAfter(null);
      setPr(next);
      setStreaming(true);

      runStream({
        pr: next,
        approve: false,
        seed: false,
        signal: controller.signal,
        onBeat: (beat) => setBeats((prev) => [...prev, beat]),
      })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          if (err instanceof RateLimited) {
            setRetryAfter(err.retryAfterSeconds);
            setFailure("rate-limited");
            return;
          }
          setFailure(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (!controller.signal.aborted) setStreaming(false);
        });
    },
    []
  );

  const cancel = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    setStreaming(false);
  }, []);

  const reset = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    setStreaming(false);
    setBeats([]);
    setFailure(null);
    setRetryAfter(null);
    setPr(null);
  }, []);

  const state: RunState =
    failure === "rate-limited"
      ? "rate-limited"
      : failure
        ? "failed"
        : stateFromBeats(beats, streaming);

  return {
    beats,
    state,
    elapsedSeconds,
    failure,
    retryAfterSeconds,
    start,
    cancel,
    reset,
    pr,
  };
}
