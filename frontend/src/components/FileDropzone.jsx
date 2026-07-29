import { useRef, useState } from "react";

const baseClass =
  "flex flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed " +
  "border-input bg-background px-4 py-6 text-center text-sm text-muted-foreground " +
  "transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background " +
  "aria-disabled:cursor-not-allowed aria-disabled:opacity-50";
const draggingClass = "border-primary bg-accent text-accent-foreground";

export default function FileDropzone({ id, label, accept, file, onFileSelected, progress, disabled }) {
  const inputRef = useRef(null);
  const [isDragging, setIsDragging] = useState(false);

  function openPicker() {
    if (!disabled) inputRef.current?.click();
  }

  function handleDrop(e) {
    e.preventDefault();
    setIsDragging(false);
    if (disabled) return;
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) onFileSelected(dropped);
  }

  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-foreground">
        {label}
      </label>
      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        onClick={openPicker}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openPicker();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={`${baseClass} ${isDragging ? draggingClass : ""}`}
      >
        <input
          ref={inputRef}
          id={id}
          type="file"
          accept={accept}
          disabled={disabled}
          className="sr-only"
          onChange={(e) => {
            const picked = e.target.files?.[0];
            if (picked) onFileSelected(picked);
          }}
        />
        {file ? (
          <span className="font-mono text-foreground">{file.name}</span>
        ) : (
          <span>클릭하거나 파일을 여기로 드래그하세요</span>
        )}
        {progress != null && (
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden="true">
            <div className="h-full bg-primary transition-all" style={{ width: `${progress}%` }} />
          </div>
        )}
      </div>
    </div>
  );
}
