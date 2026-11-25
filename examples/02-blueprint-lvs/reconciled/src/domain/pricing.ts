/**
 * @requires unitCents >= 1 and unitCents <= 1000000 and qty >= 1 and qty <= 1000
 * @ensures result >= unitCents
 */
export function computeSubtotal(unitCents: number, qty: number): number {
  if (unitCents < 1 || unitCents > 1_000_000 || qty < 1 || qty > 1_000) {
    throw new RangeError("unitCents or qty outside bounds");
  }
  return unitCents * qty;
}
