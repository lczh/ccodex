// ONE classifier for who a user-row message is FROM (the user 2026-08-18): the chat bubble, the
// rail dot, and the scrollbar notch all read the SAME verdict, so the surfaces can never
// desynchronize — the notch filter and the bubble dress used to be two hand-maintained predicates.
// The kinds mirror the dress taxonomy: "user" = the user's own typed words (blue bubble, blue dot,
// blue notch); "romp" = romp-injected (gray romp-bubble + swirl, gray notch); "tagged" =
// machine-sent under a sender-declared label (tag-bubble + ⚙, gray notch); "injected" = harness
// noise (system reminders, command stdout — the neutral note box, NO notch).
export type SenderKind = "user" | "romp" | "tagged" | "injected";

export function senderKind(ev: { human?: boolean; romp?: boolean; tag?: string; md?: string }): SenderKind {
  const romp = !!ev.romp;
  const injected = !ev.human && !romp;
  const tagged = !romp && !injected && !!ev.tag && !!ev.md;
  return romp ? "romp" : injected ? "injected" : tagged ? "tagged" : "user";
}
