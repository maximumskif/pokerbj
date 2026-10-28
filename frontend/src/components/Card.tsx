import { SUIT, isRed, rankLabel } from "../api";

interface SlotProps {
  value: string | null;
  status?: string;
  confidence?: number;
  source?: string;
  size?: "sm" | "lg";
  onClick?: () => void;
}

export function CardSlot({ value, status = "stable", confidence, source, size = "lg", onClick }: SlotProps) {
  const cls = `card ${size} st-${status}${source === "manual" ? " manual" : ""}${onClick ? " clickable" : ""}`;
  const title = `${status}${confidence !== undefined ? ` · recognition ${Math.round(confidence * 100)}%` : ""}${source === "manual" ? " · manual" : ""}`;
  if (status === "uncertain") {
    return (
      <div className={cls} title={title} onClick={onClick}>
        <span className="card-q">?</span>
      </div>
    );
  }
  if (!value || value === "empty") return null;
  if (value === "hidden") {
    return <div className={`${cls} back`} title="face down" onClick={onClick} />;
  }
  const r = value.slice(0, -1);
  const s = value.slice(-1);
  return (
    <div className={`${cls}${isRed(value) ? " red" : ""}`} title={title} onClick={onClick}>
      <span className="card-rank">{rankLabel(r)}</span>
      <span className="card-suit">{SUIT[s]}</span>
    </div>
  );
}

export function CardText({ value }: { value: string }) {
  if (value === "hidden") return <span className="ctext">🂠</span>;
  const r = value.slice(0, -1);
  const s = value.slice(-1);
  return (
    <span className={`ctext${isRed(value) ? " red" : ""}`}>
      {rankLabel(r)}
      {SUIT[s]}
    </span>
  );
}
