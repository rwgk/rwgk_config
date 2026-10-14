# GitHub Attention Tabs

A small, local Manifest V3 Chrome extension for two personal queues:

- [Open individual review requests](https://github.com/search?q=is%3Apr+is%3Aopen+user-review-requested%3A%40me&type=pullrequests)
- [Unread mention notifications](https://github.com/notifications?query=is%3Aunread%20reason%3Amention)

No server, token, npm installation, or Codex setup is needed on the Mac.

## Install on the Mac

1. Clone or update `rwgk_config` on the Mac at `~/rwgk_config`.
2. Open `chrome://extensions`, turn on **Developer mode**, and click **Load unpacked**.
3. Select `~/rwgk_config/github_attention_extension/extension`, not the repository root or a file. This directory contains `manifest.json` directly.
4. Open the two queue links above in the same Chrome profile where you are signed in to GitHub. Reload any tabs that were already open before installation.
5. Optionally pin the extension to the toolbar. Its popup shows each queue's status and last successful check; **Check now** explicitly reloads the watched tabs, including an active one.

No ZIP, build step, or npm installation is needed. Keep the checkout at a stable location: Chrome continues loading files from its `extension/` directory.

If replacing an earlier installation loaded from an extracted ZIP, remove that old extension entry in `chrome://extensions` first so only one copy runs.

## Updates and removal

After `git pull` in `~/rwgk_config`, click **Reload** for the extension in `chrome://extensions`, then reload the two watched GitHub tabs.

Disable or remove the extension there to stop monitoring; reload the tabs to remove any remaining favicon override.

## Indicators and refresh behavior

- **Red dot:** the watched queue contains at least one item. It stays red until the queue is verified empty; simply looking at the tab does not acknowledge it.
- **Pale-green dot:** the extension is watching this view and successfully verified that the queue is empty. This confirms a recent successful check, not just that the extension is installed. It changes to amber if the loaded page becomes stale.
- **Amber exclamation mark:** loading, signed out on a queue page, stale, or unrecognized page/error. This is not an empty queue. If GitHub redirects to a login page or Chrome shows a network-error page, that page may have its normal favicon instead; the extension popup reports the missing/failed watched view. Do not interpret that other page's favicon as a verified-empty queue.

An alarm checks roughly every **two minutes** and reloads only existing, inactive tabs matching these exact views. It never activates tabs or opens new ones automatically. Active tabs are not auto-reloaded, so reading, scrolling, or making selections is not interrupted. Their indicator becomes stale after six minutes without a fresh page load. Switch away to allow the next automatic reload, or use **Check now**.

Chrome treats a selected tab in any window as active, including an unfocused window. Keep the watched views as background tabs rather than selected in separate windows if you want automatic refresh. Use one tab per queue to keep the popup's aggregate status unambiguous.

Leave both watched tabs open. You can pin them. A closed tab is not monitored; the popup shows it as inactive. Chrome must be running and the laptop awake. Sleep, throttling, frozen/discarded tabs, network problems, and Chrome's alarm scheduling can delay checks; this is a glance aid, not a guaranteed real-time alert service. If Memory Saver prevents reliable checks, add `github.com` to Chrome's "Always keep these sites active" list yourself.

Only the first page and the exact filters above are watched. Other searches, notification filters, and later pages are left alone. In-page navigation into a watched queue requires a full reload before it can be verified; this prevents old cached page data from producing a false-clear result. Review requests use GitHub's total search count. Mention counts are the number of rows on the displayed first page, not necessarily a total; that is sufficient for the red-dot decision.

GitHub's `reason:mention` filter identifies notification threads, **not only newly written @mentions**. An old mentioned thread can become unread again after unrelated updates. Likewise, GitHub remains authoritative for which reviews are still requested. The extension preserves these existing GitHub semantics; it does not decide whether a request is actionable.

## Privacy and safety

- Permissions: `alarms`, `storage`, and access to **github.com only**. Chrome's GitHub site-access warning reflects this host permission; there is no access to Slack, email, other sites, cookies APIs, browser history, or an external service.
- Uses ordinary authenticated page loads in your existing Chrome session. It never reads/copies cookies or asks for a personal access token.
- Reads the rendered queue state; changes only its favicon. No notification is marked read/done, no subscription is changed, and no GitHub action is submitted.
- Stores only queue kind, count, check time, and status/message in `chrome.storage.local`. No PR titles, comments, notification contents, IDs, tokens, or page responses are retained. It does not use sync storage or telemetry.
- JavaScript is packaged locally. No remote scripts, dynamic evaluation, or CSP weakening.

## First-run check

This version was tested with local DOM fixtures and mocked Chrome APIs on headless Ubuntu, **not with your signed-in Mac browser**. GitHub's private notifications markup may differ; an unrecognized layout deliberately produces amber, never a false "clear".

1. For a nonempty queue, confirm its favicon turns red after loading; compare the popup with the visible list.
2. For an empty queue, confirm the pale-green dot appears and the popup says "Watching — queue empty" with a recent verification time. No configuration is needed beyond opening the two exact views in your signed-in profile and reloading them after installation.
3. Switch to Slack or another tab. After about two minutes, confirm the two watched tabs have refreshed without being activated.
4. Keep one queue active through an alarm: it should not reload. **Check now** should reload it when you explicitly click that button.
5. If either view stays amber after loading, tell Codex which view and the popup's message. Do not send cookies, tokens, or full private HTML. A narrow, sanitized DOM excerpt around the result count or empty-state panel may be enough to adjust the parser.

## Development

Requires Node.js 18+ only for offline testing. Runtime has no npm dependencies.

```sh
cd ~/rwgk_config/github_attention_extension
npm ci --ignore-scripts
npm test
npm run check
```

`linkedom` is pinned and used only to parse synthetic HTML fixtures in tests. The runtime assets are already in `extension/`; tests and npm dependencies are not needed to load it in Chrome.

Reference: [Chrome alarms](https://developer.chrome.com/docs/extensions/reference/api/alarms), [host permissions and tabs](https://developer.chrome.com/docs/extensions/reference/api/tabs), and [isolated content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts).
