import { computeSubtotal } from "../domain/index.js";
import { writeDirectAuditRow } from "../storage/index.js";

export function renderCheckoutLine(unitCents: number, qty: number): string {
  const subtotal = computeSubtotal(unitCents, qty);
  const audit = writeDirectAuditRow("acct_ui");
  return `subtotal_cents=${subtotal};${audit}`;
}
