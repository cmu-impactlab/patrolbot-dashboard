import * as Dialog from "@radix-ui/react-dialog";
import { MousePointerClick, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useAcknowledgeTour, useTourStatus } from "../api/queries";
import { useUiStore } from "../stores/uiStore";

export function TourPrompt({
  onResolved,
  tourAlreadyStarted = false,
}: {
  onResolved: () => void;
  tourAlreadyStarted?: boolean;
}) {
  const status = useTourStatus();
  const acknowledge = useAcknowledgeTour();
  const startTour = useUiStore((state) => state.startTour);
  const [handled, setHandled] = useState(false);
  const resolved = useRef(false);

  const resolveOnce = () => {
    if (resolved.current) return;
    resolved.current = true;
    onResolved();
  };

  useEffect(() => {
    if (status.isError || (status.data && !status.data.should_prompt)) resolveOnce();
    if (tourAlreadyStarted && status.data?.should_prompt && !handled) {
      // A persistent help link has already launched the tour. Count that as
      // accepting onboarding without placing the offer dialog over the tour.
      setHandled(true);
      resolveOnce();
      acknowledge.mutate();
    }
  });

  const dismiss = (start: boolean) => {
    if (handled) return;
    setHandled(true);
    if (start) startTour();
    resolveOnce();
    acknowledge.mutate();
  };

  const open = status.data?.should_prompt === true && !handled && !tourAlreadyStarted;

  return (
    <Dialog.Root open={open} onOpenChange={(next) => { if (!next && open) dismiss(false); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content help-prompt">
          <Dialog.Title asChild><h2>Take a quick dashboard tour?</h2></Dialog.Title>
          <Dialog.Description className="subtext">
            We’ll point to the important controls one at a time and explain
            what to click. The tour never moves or changes the robot.
          </Dialog.Description>
          <Dialog.Close asChild>
            <button className="dialog-close" aria-label="Close guided tour offer">
              <X size={17} aria-hidden="true" />
            </button>
          </Dialog.Close>
          <div className="dialog-actions">
            <button className="btn" onClick={() => dismiss(false)}>Not now</button>
            <button className="btn primary" onClick={() => dismiss(true)}>
              <MousePointerClick size={15} aria-hidden="true" /> Start guided tour
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
