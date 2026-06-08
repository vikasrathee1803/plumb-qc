import type { ParamHint } from "./types";

export function paramToInput(value: unknown, hint: ParamHint): string {
  if (value === undefined || value === null) return "";
  if (hint.type === "list" && Array.isArray(value)) return value.join(", ");
  return String(value);
}

export function inputToParam(raw: string, hint: ParamHint): unknown {
  const t = raw.trim();
  if (t === "") return undefined;
  if (hint.type === "list") return t.split(",").map((s) => s.trim()).filter(Boolean);
  if (hint.type === "int") return parseInt(t, 10);
  if (hint.type === "float") return parseFloat(t);
  if (hint.type === "bool") return t.toLowerCase() === "true";
  return t;
}
