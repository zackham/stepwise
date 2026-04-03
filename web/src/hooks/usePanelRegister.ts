import { useEffect, useRef } from "react";
import { usePanelControls, type PanelControls } from "@/contexts/PanelContext";

/**
 * Register panel toggle controls from a page component.
 * Controls are automatically unregistered on unmount.
 *
 * Pass a new controls object whenever the relevant state changes.
 * Uses JSON serialization of scalar values to avoid redundant context updates.
 */
export function usePanelRegister(controls: PanelControls) {
  const { register, unregister } = usePanelControls();
  const controlsRef = useRef(controls);
  controlsRef.current = controls;

  // Build a fingerprint of the scalar values so we only re-register when something changes.
  const fingerprint = JSON.stringify({
    lv: controls.leftPanel?.visible,
    ld: controls.leftPanel?.disabled,
    rv: controls.rightPanel?.visible,
    rd: controls.rightPanel?.disabled,
    co: controls.chat?.open,
    cs: controls.chat?.isStreaming,
    cm: controls.chat?.agentMode,
    cb: controls.chat?.backgrounded,
    ar: !!controls.actions?.onRun,
    ai: controls.actions?.isRunning,
    ae: controls.actions?.parseErrors?.length,
  });

  useEffect(() => {
    register(controlsRef.current);
  }, [register, fingerprint]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => unregister();
  }, [unregister]);
}
