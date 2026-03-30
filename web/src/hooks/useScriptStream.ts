import { useEffect, useRef, useState, useMemo } from "react";
import { fetchScriptOutput } from "../lib/api";
import { subscribeScriptOutput } from "./useStepwiseWebSocket";
import type { ScriptOutputMessage } from "@/lib/types";

const MAX_RETAINED_BYTES = 512 * 1024;

interface Chunk {
  text: string;
  bytes: number;
}

interface ScriptStreamState {
  stdoutChunks: Chunk[];
  stderrChunks: Chunk[];
  totalStdoutBytes: number;
  totalStderrBytes: number;
  truncated: boolean;
}

function appendChunk(
  chunks: Chunk[],
  totalBytes: number,
  text: string,
): { chunks: Chunk[]; totalBytes: number; truncated: boolean } {
  const byteLen = new TextEncoder().encode(text).length;
  const newChunks = [...chunks, { text, bytes: byteLen }];
  let newTotal = totalBytes + byteLen;
  let truncated = false;
  while (newTotal > MAX_RETAINED_BYTES && newChunks.length > 1) {
    const removed = newChunks.shift()!;
    newTotal -= removed.bytes;
    truncated = true;
  }
  return { chunks: newChunks, totalBytes: newTotal, truncated };
}

export function useScriptStream(runId: string | undefined): {
  stdout: string;
  stderr: string;
  truncated: boolean;
  version: number;
} {
  const [version, setVersion] = useState(0);
  const stateRef = useRef<ScriptStreamState>({
    stdoutChunks: [], stderrChunks: [],
    totalStdoutBytes: 0, totalStderrBytes: 0, truncated: false,
  });
  const backfilledRef = useRef(false);
  const knownStdoutOffset = useRef(0);
  const knownStderrOffset = useRef(0);
  const queueRef = useRef<ScriptOutputMessage[]>([]);

  // Reset on runId change
  useEffect(() => {
    stateRef.current = {
      stdoutChunks: [], stderrChunks: [],
      totalStdoutBytes: 0, totalStderrBytes: 0, truncated: false,
    };
    backfilledRef.current = false;
    knownStdoutOffset.current = 0;
    knownStderrOffset.current = 0;
    queueRef.current = [];
    setVersion(0);
  }, [runId]);

  function applyWsMessage(msg: ScriptOutputMessage) {
    const state = stateRef.current;
    if (msg.stdout) {
      const msgEnd = msg.stdout_offset + new TextEncoder().encode(msg.stdout).length;
      if (msgEnd > knownStdoutOffset.current) {
        let text = msg.stdout;
        if (msg.stdout_offset < knownStdoutOffset.current) {
          const overlapBytes = knownStdoutOffset.current - msg.stdout_offset;
          const encoded = new TextEncoder().encode(msg.stdout);
          text = new TextDecoder().decode(encoded.slice(overlapBytes));
        }
        if (text) {
          const result = appendChunk(state.stdoutChunks, state.totalStdoutBytes, text);
          state.stdoutChunks = result.chunks;
          state.totalStdoutBytes = result.totalBytes;
          if (result.truncated) state.truncated = true;
        }
        knownStdoutOffset.current = msgEnd;
      }
    }
    if (msg.stderr) {
      const msgEnd = msg.stderr_offset + new TextEncoder().encode(msg.stderr).length;
      if (msgEnd > knownStderrOffset.current) {
        let text = msg.stderr;
        if (msg.stderr_offset < knownStderrOffset.current) {
          const overlapBytes = knownStderrOffset.current - msg.stderr_offset;
          const encoded = new TextEncoder().encode(msg.stderr);
          text = new TextDecoder().decode(encoded.slice(overlapBytes));
        }
        if (text) {
          const result = appendChunk(state.stderrChunks, state.totalStderrBytes, text);
          state.stderrChunks = result.chunks;
          state.totalStderrBytes = result.totalBytes;
        }
        knownStderrOffset.current = msgEnd;
      }
    }
  }

  // REST backfill
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    fetchScriptOutput(runId)
      .then((data) => {
        if (controller.signal.aborted) return;
        if (data.stdout) {
          const result = appendChunk([], 0, data.stdout);
          stateRef.current.stdoutChunks = result.chunks;
          stateRef.current.totalStdoutBytes = result.totalBytes;
          stateRef.current.truncated = result.truncated;
        }
        if (data.stderr) {
          const result = appendChunk([], 0, data.stderr);
          stateRef.current.stderrChunks = result.chunks;
          stateRef.current.totalStderrBytes = result.totalBytes;
        }
        knownStdoutOffset.current = data.stdout_offset;
        knownStderrOffset.current = data.stderr_offset;
        backfilledRef.current = true;
        for (const msg of queueRef.current) applyWsMessage(msg);
        queueRef.current = [];
        setVersion((v) => v + 1);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        backfilledRef.current = true;
        for (const msg of queueRef.current) applyWsMessage(msg);
        queueRef.current = [];
        setVersion((v) => v + 1);
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  // WS subscription
  useEffect(() => {
    if (!runId) return;
    return subscribeScriptOutput((msg) => {
      if (msg.run_id !== runId) return;
      if (!backfilledRef.current) {
        queueRef.current.push(msg);
        return;
      }
      applyWsMessage(msg);
      setVersion((v) => v + 1);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  const stdout = useMemo(
    () => stateRef.current.stdoutChunks.map((c) => c.text).join(""),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version],
  );
  const stderr = useMemo(
    () => stateRef.current.stderrChunks.map((c) => c.text).join(""),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version],
  );

  return { stdout, stderr, truncated: stateRef.current.truncated, version };
}
