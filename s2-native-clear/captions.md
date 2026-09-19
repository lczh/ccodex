# Screenshots for the S2 PR (Codex native /clear)

All three were taken over a hermetic lab kernel built from the branch (commit under review), driven by
a scratch Playwright driver through the served browser tests' harness (tests/test_ship_reship_served
`kernel_env`, tests/dist_copy, a Playwright node driver). Synthetic data only: the notes-api demo sessions
`web` / `api` / `tests` under `/TESTDIR`, placeholder uuids, invented prompt text, no hostname shown. The lab
kernel's real Codex backend had no app-server (ROMP_CODEX_BIN=/bin/false), so nothing ran a turn.

## chip-midturn.png

Session `web`, mid-turn (the chip reads Working, 47 s). The user typed a follow-up message during the turn
and then `/clear`: the queued bubble shows both in press order, "2 queued items", with the `/clear` command
chip as the last entry and its cancel (the parked `("clear",)` op the route parks when the session is not
quiet). How the state was frozen: the transcript ends in an unanswered user record (an open turn), the
registry row carries a persisted queued send (what keeps the backend's busy() True, so the drain holds the
parked clear), and pending-ops.json holds the parked clear.

## after-clear.png

Session `api`, idle (Ready) right after `/clear`: the registry names a fresh, empty thread file, so the chat
shows only the collapsed system card and the user-side `/clear` gesture chip (the durable twin the backend
writes to states/<sid>.jsonl; the live chip is in-memory and a fresh kernel does not have it). No head card
yet: the boundary lands on the first prompt into the fresh conversation, as the doc says.

## head-card.png

Session `tests`, one prompt into the fresh conversation. The real episode tick recorded the boundary (the
fresh file's root head) beside the seed row the old file earned, and settled the one open card, so the chat
leads with the "Conversation cleared — a fresh one starts here" card, "1 card dropped". The driver clicked
its head to expand it: the body renders the OLD Codex conversation from its own materialized file (a sibling
in the same projects directory), ending in the `/clear` chip where it happened; beneath it the fresh
conversation's system card, first prompt and reply.
