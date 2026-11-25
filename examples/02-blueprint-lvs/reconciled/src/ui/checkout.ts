import { computeSubtotal } from "../domain/index.js";

export function renderCheckoutLine(unitCents: number, qty: number): string {
  const subtotal = computeSubtotal(unitCents, qty);
  return `subtotal_cents=${subtotal}`;
}
