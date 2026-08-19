// THE source of truth for the webview <body> skeletons — the container elements
// that decide WHAT each view renders. Consumed directly by the VS Code extension
// (buildHtml / buildFeedHtml in extension.ts).
//
// The web front end is the Python kernel (bin/romp-kernel), which carries a
// hand-PORTED copy of these bodies (grep its "ported from page-skeleton.chatBody"
// note). They are NOT auto-shared — if you add or rename a container here, mirror
// it in bin/romp-kernel by hand or the web view drifts from VS Code.
//
// Each host owns its own <head> (VS Code: a CSP meta + nonce; the browser: the
// --vscode-* theme vars) and its own trailing <script> tags (VS Code:
// asWebviewUri + nonce; the browser: an acquireVsCodeApi() shim + /dist/*). Only
// the body — the part that defines the UI — lives here. (render.ts/feed.ts already
// compile to one shared bundle each; this is the HTML that hosts those bundles.)

// Chat view: window frame, tab bar, ledger, transcript, the live-ask picker, and
// the footer (statusline + composer). The composer's attach-button tooltip is the
// one genuinely host-specific bit — VS Code intercepts drag-and-drop, a browser
// doesn't — so it's passed in.
export function chatBody(attachTitle: string): string {
  return `  <div id="winframe"></div>
  <div id="tabbar"><span id="tabs"></span></div>
  <div id="tabbar-resize" title="Drag to resize the tab strip"></div>
  <div id="ledger" style="display:none"></div>
  <div id="content"><div id="live-ask" style="display:none"></div></div>
  <div id="footer">
    <div id="composer-resize" title="Drag to resize the message box"></div>
    <div id="statusline" class="statusline"></div>
    <div id="composer"><div id="composer-files" style="display:none"></div><div id="composer-staged" style="display:none"></div><div id="composer-chips" style="display:none"></div><textarea id="composer-input" rows="1" placeholder="Message this session…  (⏎ send · ⇧⏎ newline · ⌘⏎ stage · ↑ history · / for commands)"></textarea><button id="composer-attach" title="${attachTitle}" aria-label="Attach file"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg></button><button id="composer-send" title="Send (⏎)" aria-label="Send">➤</button></div>
  </div>`;
}

// The composer attach tooltip per host (drag-and-drop is intercepted only in VS Code).
export const ATTACH_TITLE_VSCODE = "Attach a file — inserts its path (drag-and-drop is intercepted by VS Code; use this or paste instead)";
export const ATTACH_TITLE_WEB = "Attach a file — inserts its path";

// Feed view: the (now hidden) head bar + the column/card list (feed.js builds the
// three state columns and their header chips inside #feed-list at runtime), and the
// docked #feed-foot control bar BELOW the list. feed.js appends its Clear-all /
// Undo / toggle controls into #feed-foot — without it they fall back to
// document.body and render as unstyled full-size buttons (the user 2026-07-13).
export const FEED_BODY = `  <div id="feed-head"></div>
  <div id="feed-list"></div>
  <div id="feed-foot"></div>`;

// Fleet ("Outline") view: docked search bar, the per-session goal-tree list, and
// the docked control bar. Mirrors the kernel's _fleet_page body.
export const FLEET_BODY = `  <div id="fleet-search-bar"><div id="fleet-search-wrap">
    <input id="fleet-search" type="search" autocomplete="off" placeholder="Search sessions and tasks…">
    <button id="fleet-search-clear" type="button" aria-label="Clear search" title="Clear search" hidden>×</button>
  </div></div>
  <div id="fleet-list"></div><div id="fleet-foot"></div>`;

// Timeline view: one host div — TimelinePanel (bundled into dist/timeline.js)
// builds everything inside it. Mirrors the kernel's _timeline_page body.
export const TIMELINE_BODY = `  <div id="host"></div>`;
