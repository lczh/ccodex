// One unambiguous key for both halves of the optimistic queued-message cancel handshake.
// JSON encoding avoids delimiter collisions when either the session id or message contains spaces,
// NULs, or any other text a composer can carry.
export function queuedCancelKey(sessionId: string, markdown: string): string {
  return JSON.stringify([sessionId, markdown]);
}
