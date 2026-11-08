export function priceDigits(value: string | undefined): number {
  if (!value || !Number.isFinite(Number(value))) return 2;
  const [mantissa, exponent = "0"] = value.toLowerCase().split("e");
  return Math.min(12, Math.max(0, (mantissa.split(".")[1]?.replace(/0+$/, "").length ?? 0) - Number(exponent)));
}

export function formatPrice(value: string | number | null | undefined, digits: number): string {
  if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

export function fundingCountdown(next: number | undefined, now: number): string {
  if (!next || !Number.isFinite(next)) return "—";
  const seconds = Math.max(0, Math.ceil((next - now) / 1000));
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map(v => String(v).padStart(2, "0")).join(":");
}
