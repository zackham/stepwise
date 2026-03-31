import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "@tanstack/react-router";
import { subscribeTickMessages } from "./useStepwiseWebSocket";
import { fetchRuns, fetchJob } from "@/lib/api";

const STORAGE_KEY = "stepwise-notifications-enabled";

function getInitialEnabled(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

/**
 * Fire browser notifications when steps transition to SUSPENDED
 * while the tab is not focused. Opt-in via toggle (persisted to localStorage).
 */
export function useNotifySuspended() {
  const router = useRouter();
  const seenSuspendedRef = useRef<Set<string>>(new Set());
  const [enabled, setEnabledState] = useState(getInitialEnabled);
  const [permission, setPermission] = useState<NotificationPermission>(
    "Notification" in window ? Notification.permission : "denied"
  );

  const setEnabled = useCallback((value: boolean) => {
    setEnabledState(value);
    try {
      localStorage.setItem(STORAGE_KEY, String(value));
    } catch {
      // ignore
    }
  }, []);

  const toggle = useCallback(() => {
    if (!enabled) {
      // Turning on: request permission if needed
      if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission().then((p) => {
          setPermission(p);
          if (p === "granted") setEnabled(true);
        });
      } else if ("Notification" in window && Notification.permission === "granted") {
        setEnabled(true);
      }
    } else {
      setEnabled(false);
    }
  }, [enabled, setEnabled]);

  useEffect(() => {
    if (!enabled || permission !== "granted") return;

    return subscribeTickMessages(async (msg) => {
      // Only notify when tab is not focused
      if (document.hasFocus()) return;

      for (const jobId of msg.changed_jobs) {
        try {
          const runs = await fetchRuns(jobId);
          const suspendedRuns = runs.filter(
            (r) => r.status === "suspended" && !seenSuspendedRef.current.has(r.id),
          );

          if (suspendedRuns.length === 0) continue;

          const job = await fetchJob(jobId);
          const jobName = job.name || job.objective;

          for (const run of suspendedRuns) {
            seenSuspendedRef.current.add(run.id);

            const notification = new Notification(
              `Step suspended: ${run.step_name}`,
              {
                body: `Job: ${jobName}`,
                tag: `suspended-${run.id}`,
              },
            );

            notification.onclick = () => {
              window.focus();
              router.navigate({ to: "/jobs/$jobId", params: { jobId } });
              notification.close();
            };
          }
        } catch {
          // Ignore fetch errors — notification is best-effort
        }
      }
    });
  }, [router, enabled, permission]);

  return { enabled, permission, toggle };
}
