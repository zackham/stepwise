import { useState, useEffect, memo } from "react";
import { formatDuration } from "@/lib/utils";

interface LiveDurationProps {
  startTime: string | null;
  endTime: string | null;
}

export const LiveDuration = memo(function LiveDuration({ startTime, endTime }: LiveDurationProps) {
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!startTime || endTime) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [startTime, endTime]);

  return <>{formatDuration(startTime, endTime)}</>;
});
