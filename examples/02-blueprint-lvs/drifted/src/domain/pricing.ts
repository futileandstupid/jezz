export function computeSubtotal(unitCents: number, qty: number): number {
  if (unitCents < 1 || unitCents > 1_000_000 || qty < 1 || qty > 1_000) {
    throw new RangeError("unitCents or qty outside bounds");
  }
  return unitCents * qty;
}
