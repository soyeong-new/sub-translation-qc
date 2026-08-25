// 드래그앤드롭 또는 클릭으로 파일을 선택하는 재사용 가능한 업로드 UI 컴포넌트.

import { useRef, useState } from "react";

const baseClass =
  "flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed " +
  "border-border bg-background px-2.5 py-1.5 text-center text-xs text-muted-foreground " +
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
      <label id={`${id}-label`} htmlFor={id} className="mb-1.5 block text-sm font-medium text-foreground">
        {label}
      </label>
      <div
        role="button"
        aria-labelledby={`${id}-label`}
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
        onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setIsDragging(false); }}
        onDrop={handleDrop}
        className={`${baseClass} ${isDragging ? draggingClass : ""}`}
      >
        <input
          ref={inputRef}
          id={id}
          type="file"
          accept={accept}
          disabled={disabled}
          tabIndex={-1}
          className="sr-only"
          onChange={(e) => {
            const picked = e.target.files?.[0];
            if (picked) onFileSelected(picked);
          }}
        />
        {file ? (
          <span className="block w-full min-w-0 truncate font-mono text-foreground">{file.name}</span>
        ) : (
          <span>Upload</span>
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
