# vscode-extension/ — the VS Code / Cursor extension

Hosts the romp panes (chat, feed, fleet, timeline) inside VS Code / Cursor —
styled to look like the official Claude Code panel, but live for *any* session
(including ones running in a terminal / romp), which the official panel can't
drive. Ships as the `romp-chat-view` extension (the historical ID, kept stable
for installs and `vscode://romp.romp-chat-view` deep links).

It is a thin client of the romp **kernel** (`kernel/kernel.py`): it connects over
WebSocket and renders what the kernel pushes, so the panes update live as the
session advances. A browser tab and this extension share one kernel and render
the same UI — the pane sources live in `../ui/webview/` and are bundled here by
`esbuild.js`.

## How it works

- The **kernel** parses each session's transcript into an event tree
  (`kernel/event_model.py`) and pushes pane payloads over WebSocket.
- `src/extension.ts` (extension host) asks the manager to ensure a kernel, attaches to it, and hosts the
  webviews, piping `postMessage` both ways — it does not parse transcripts.
- `../ui/webview/render.ts` + `feed.ts` + `styles.css` (webview) render the
  pushed events. Base colors/fonts come from VS Code theme variables; the
  accents (green rail dot, warm code tones) match the shipped Claude Code CSS.

Thinking text is only stored in plaintext for small models; the big reasoning
models store a signature-only block, so those render as a `Thinking…` placeholder.

## Develop

Use Node.js 22 or newer (the pinned VSIX packager's supported runtime).

```sh
npm ci
npm run build      # or: npm run watch
```

Then open this folder in VS Code/Cursor and press **F5** (Run ccodex Agent View).
In the dev host, run **“ccodex: Open”**. The command id remains `rompChat.open`
so existing keybindings and deep links continue to work.
