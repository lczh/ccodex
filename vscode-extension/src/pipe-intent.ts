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
  // the tag-transaction halves (the v1.3.24 audit's P1.3): a reconnect used to replay the
  // LOCAL half (setTimelineViewsOps) while dropping the REMOTE edits and the compensation
  // journal — the multi-host gesture landed one-legged with no rollback record. FIFO order
  // is the queue's own; classifying all three keeps the transaction whole.
  "editTag", "setUnionOps",
  "reorderTabs", "closeTab",
]);

export function intentOp(type: unknown): boolean {
  return typeof type === "string" && INTENT_OPS.has(type);
}
