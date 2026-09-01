// Which webview→kernel ops carry USER INTENT — a typed message or an explicit
// state-changing pick — versus view chatter (focus, hover, scroll, fold state).
// The KernelPipe holds intent ops while its socket is down and STILL DELIVERS
// them after a reconnect: the old reconnect path wiped the whole queue, which
// silently ate a card reply sent during a kernel-restart window (the user
// 2026-07-21, roof). View chatter stays droppable — the reconnect reloads the
// webview, and its fresh "ready" resyncs all view state from the kernel.
export const INTENT_OPS: ReadonlySet<string> = new Set([
  // typed text — losing these loses the user's words
  "sendMessage", "askFollowUp", "askText", "addCustomAsk", "sendCommand", "rewindSend",
  // explicit clicks that mutate kernel/session state
  "interrupt", "apiRetry", "rewindDelete",
  "setModel", "setEffort", "setMode", "setFast", "setAuth",
  "renameSession", "endSession", "reviveSession",
  "nodeOverride", "askClear", "undoClear", "cardMove", "cardNotify", "redistill",
  "answerAsk", "submitAsk", "toggleAsk", "navAsk", "cancelAsk",
  "setSessionFlag", "setSessionColor", "setGlobalRetryPaused", "setTimelineViews",
  "setTimelineViewsOps", "openTagsDialog",
  // the tag-transaction REMOTE half (the v1.3.24 audit's P1.3): a reconnect used to replay
  // the LOCAL half (setTimelineViewsOps) while dropping the remote edits — editTag frames
  // dispatch only after their journal ack, so replaying one delivers an already-journaled
  // effect (at-least-once; refusals compensate). setUnionOps is deliberately NOT retained
  // since r59 P1.1 (reproduced: a retained journal body replayed after reconnect carried
  // the PRE-reset world and forked one gesture into two claimable identities — the panel's
  // unionTransportReset re-sends CURRENT state, and the r56 rule says a completion's CAS
  // can never survive its socket anyway).
  "editTag",
  "reorderTabs", "closeTab",
]);

export function intentOp(type: unknown): boolean {
  return typeof type === "string" && INTENT_OPS.has(type);
}
