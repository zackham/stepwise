import { useEffect, useRef } from "react";
import { useRouter } from "@tanstack/react-router";
import { subscribeTickMessages } from "./useStepwiseWebSocket";
import { fetchRuns, fetchJob } from "@/lib/api";

/**
 * Fire browser notifications when steps transition to SUSPENDED
 * while the tab is not focused.
 */
export function useNotifySuspended() {
  const router = useRouter();
  const seenSuspendedRef = useRef<Set<string>>(new Set());

  // Request notification permission on mount
  useEffect(() => {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }, []);

  useEffect(() => {
    return subscribeTickMessages(async (msg) => {
      // Only notify when tab is not focused
      if (document.hasFocus()) return;
      if (!("Notification" in window) || Notification.permission !== "granted") return;

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
  }, [router]);
}
