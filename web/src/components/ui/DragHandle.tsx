interface DragHandleProps {
  onMouseDown: (e: React.MouseEvent) => void;
}

export function DragHandle({ onMouseDown }: DragHandleProps) {
  return (
    <div
      onMouseDown={onMouseDown}
      className="relative w-0 shrink-0 cursor-col-resize z-10"
    >
      <div className="absolute inset-y-0 -left-[3px] w-[7px] z-10 cursor-col-resize" />
      <div className="absolute inset-y-0 left-0 w-px bg-border" />
    </div>
  );
}
