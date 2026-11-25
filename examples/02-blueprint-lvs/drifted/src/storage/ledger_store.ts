export function writeDirectAuditRow(accountId: string): string {
  if (accountId.trim().length < 3 || accountId.length > 64) {
    throw new RangeError("invalid accountId");
  }
  return `row:${accountId}`;
}
