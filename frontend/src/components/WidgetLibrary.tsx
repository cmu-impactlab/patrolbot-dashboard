import * as Dialog from "@radix-ui/react-dialog";
import { Plus } from "lucide-react";
import { useLayoutStore } from "../stores/layoutStore";
import { WIDGET_REGISTRY } from "../widgets/registry";

export function WidgetLibrary({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const widgets = useLayoutStore((state) => state.widgets);
  const addWidget = useLayoutStore((state) => state.addWidget);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content">
          <Dialog.Title asChild>
            <h2>Widget library</h2>
          </Dialog.Title>
          <Dialog.Close className="btn dialog-close" aria-label="Close widget library">×</Dialog.Close>
          {Object.values(WIDGET_REGISTRY).map((definition) => {
            const present = widgets.includes(definition.id);
            return (
              <div className="library-item" key={definition.id}>
                <div>
                  <div style={{ fontWeight: 600 }}>{definition.title}</div>
                  <div className="subtext">{definition.description}</div>
                </div>
                <button
                  className="btn"
                  aria-label={`${present ? "Added" : "Add"} ${definition.title}`}
                  disabled={present}
                  onClick={() => addWidget(definition.id, definition.defaultSize)}
                >
                  <Plus size={14} /> {present ? "Added" : "Add"}
                </button>
              </div>
            );
          })}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
