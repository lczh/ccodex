# AGENTS.md — standing decisions for agents working on ccodex

ccodex is a fork of [romp](https://github.com/romp-on/romp) distributed to a
small circle of users from this repo's release tags. Mechanical hardening with
real tests is welcome here and has been consistently merged. The decisions
below are SETTLED by the maintainer after explicit review rounds (2026-08-14
through 2026-08-16). Do not re-propose or reverse them; changes that flip one
get reverted on review, however good the surrounding work is.

1. **Release verification is conditional, not mandatory.** `bootstrap.sh` and
   the kernel updater verify release tags as a HARD gate only when a trust
   root is configured (`ROMP_RELEASE_ALLOWED_SIGNERS`, `ROMP_VERIFY_RELEASES=1`,
   or the allowed-signers path bootstrap persists into the clone's git
   config). With no trust root, verification is attempted and its outcome
   loudly noted — never a refusal. Rationale: the published releases are
   unsigned through v1.2.1 and no key existed then; a mandatory gate refuses
   every default install and every installed updater, which shipped-and-
   verified reality contradicts. From v1.2.2 releases are SSH-signed with the
   key published at docs/release-key.pub — the older unsigned tags stay
   untouched (a published tag is immutable history, never re-signed), and a
   configured trust root rightly refuses them while accepting v1.2.2+.

2. **The dashboard Restart button restarts everything attached** (the
   recorded 2026-07-29 decision): `/restart` defaults `fleet:true`;
   `{"fleet":false}` is the explicit local-only opt-out, and EVERY restart
   surface (dashboard rail, feed gear, strip, mobile bar) sends the same
   default — no surface silently disagrees. A malformed body (non-object
   JSON, junk, a non-boolean `fleet`) is a 400, never the broadest action by
   default-fallthrough (the maintainer, 2026-08-16). One deliberate
   exception: a peer restarted after `_ask_peer_to_pull` gets `fleet:false`
   — scoped to the machine just updated, never transitive.

3. **Bounce reasons are finite codes on the wire** (`PEER_BOUNCE_REASONS`),
   rendered from the receiving side's own table; peer free text never reaches
   a sender's chat. Codes, not flattening: the sender must still be able to
   tell wrong-name from too-large. (This design came from an agent
   contribution — improvements at this standard are exactly what's wanted.)

4. **Docs never declare the product uninstallable.** The default install
   works today and must keep working; describe the unsigned-release state
   truthfully (noted-not-enforced) rather than instructing users to wait for
   signing infrastructure that does not exist.

House rules that reviews here enforce (same spirit as the repo's CLAUDE.md):
every behavior change lands with a test; fail loudly, never silently degrade
or silently fall back; addressable delivery failures bounce rather than drop;
fixtures are synthetic (placeholder UUIDs, `TESTHOST`, invented prompts) —
never real session data or personal identifiers.
