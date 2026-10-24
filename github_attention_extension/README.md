# GitHub Attention Tabs

A small, local Manifest V3 Chrome extension for four GitHub queues. See what's waiting by glancing at your pinned tabs.

## Quick start

Open these four URLs in the same Chrome profile where you are signed in to GitHub:

- **Review requests:** <https://github.com/notifications?query=reason%3Areview-requested>
- **Approved PRs:** <https://github.com/search?q=is%3Apr+is%3Aopen+author%3Arwgk+review%3Aapproved&type=pullrequests>
- **Mentions:** <https://github.com/notifications?query=reason%3Amention>
- **All notifications:** <https://github.com/notifications>

Suggested pinned-tab order: **Slack → Review requests → Approved PRs → Mentions → All notifications**. Slack is not monitored by this extension. The Approved PRs search is specifically for GitHub author `rwgk`; the notification views use the signed-in account.

No server, token, npm installation, or Codex setup is needed on the Mac.

## Install on the Mac

1. Clone or update `rwgk_config` on the Mac at `~/rwgk_config`.
2. Open `chrome://extensions`, turn on **Developer mode**, and click **Load unpacked**.
3. Select `~/rwgk_config/github_attention_extension/extension`, not the repository root or a file. This directory contains `manifest.json` directly.
4. Open the four Quick start links above. Reload any tabs that were already open before installation.
5. Pin and arrange the tabs in your preferred order. The extension does not create, pin, or rearrange them automatically.
6. Optionally pin the extension to the toolbar. Its popup shows each queue's status and last successful check; **Check now** explicitly reloads the watched tabs, including active ones. Clicking a queue's heading opens that view in a new tab.

No build step or npm installation is needed. Keep the checkout at a stable location: Chrome continues loading the extension directly from its `extension/` directory.

If you previously loaded the extension from a different location, remove the old entry in `chrome://extensions` before loading this one, so only one copy runs.

## Updates and removal

After `git pull` in `~/rwgk_config`, click **Reload** for the extension in `chrome://extensions`, then reload the four watched GitHub tabs.

When upgrading from the two-queue version, replace the old individual-review-request search and unread-only Mentions URLs with the Quick start URLs above, and add Approved PRs and All notifications. The old URLs are no longer watched. Old queue status is deliberately discarded on this upgrade so the new views start unverified rather than reusing counts with different meanings.

Disable or remove the extension there to stop monitoring; reload the tabs to remove any remaining favicon override.

## Indicators and refresh behavior

Each favicon keeps its queue character visible: **R** for Review requests, **A** for Approved PRs, **M** for Mentions, and **\*** for All notifications. A small top-right indicator shows its current state:

- **White 1–9 on a red badge:** that many PRs or notification threads are waiting in the view.
- **White X on a red badge:** ten or more items are waiting.
- **Pale-green dot:** the extension is watching this view and successfully verified that the queue is empty. This confirms a recent successful check, not just that the extension is installed. It changes to amber if the loaded page becomes stale.
- **Amber exclamation mark:** loading, signed out on a queue page, stale, or unrecognized page/error. This is not an empty queue. If GitHub redirects to a login page or Chrome shows a network-error page, that page may have its normal favicon instead; the extension popup reports the missing/failed watched view. Do not interpret that other page's favicon as a verified-empty queue.

The red indicator remains until the queue changes; simply looking at the tab does not acknowledge it. Counts are items currently waiting, **not new events since your last visit**. New comments on an existing thread can leave its count unchanged, and X hides increases above ten.

An alarm starts an automatic refresh cycle roughly every **two minutes**, reloading eligible tabs in **Review requests → Approved PRs → Mentions → All notifications** order. Reload starts are at least **five seconds apart**—normally spanning about 15 seconds for one tab per queue. Inactive tabs are eligible as usual; active tabs become eligible after **at least one minute without an in-page mouse press**. Each tab's URL, loading state, and activity are checked again just before its turn. Already-loading tabs and tabs no longer showing a watched view are skipped. If an active tab's activity cannot be verified, it is left alone. The extension never activates tabs or opens new ones automatically.

Only real left-, middle-, or right-button mouse presses inside the GitHub page reset the activity clock. Quiet reading, scrolling, typing, and clicking Chrome's tab strip do **not** count as activity, so they may be interrupted by an idle reload. A newly watched tab starts with a one-minute grace period when no activity history is available, including after a browser restart or extension reload. Once idle, it remains eligible on each two-minute cycle until another mouse press; automatic reloads do not reset this clock. There is no separate idle timer that triggers a reload: eligibility is checked at the tab's turn in the scheduled cycle. A reload normally occurs around one to three minutes after the last activity, plus staggering and Chrome scheduling delays.

Activity and verification are separate: a mouse press protects the active tab but does not make its page data fresh. Loaded data still becomes stale after six minutes without a fresh page load, even if recent clicks keep the active tab protected. **Check now** cancels the pending automatic sequence and immediately reloads all watched tabs, bypassing the activity protection.

Chrome treats a selected tab in any window as active, including an unfocused window. Such a tab can now refresh automatically once click-idle, without switching windows or tabs. Use one tab per queue to keep the popup's aggregate status unambiguous.

Leave all four watched tabs open. A closed tab is not monitored; the popup shows it as inactive. Chrome must be running and the laptop awake. Sleep, throttling, frozen/discarded tabs, network problems, and Chrome's alarm scheduling can delay checks; delayed reloads stay spaced apart rather than catching up in a burst. Worker startup may add a five-second safety delay. If Chrome stops the background worker mid-cycle, remaining tabs wait for the next cycle. If the activity-session checkpoint cannot be saved, automatic reloads pause until a later cycle can save it; queue reporting and manual **Check now** remain available. This is a glance aid, not a guaranteed real-time alert service. If Memory Saver prevents reliable checks, add `github.com` to Chrome's "Always keep these sites active" list yourself.

Only the first page and the exact filters above are watched. Other searches, notification filters, and later pages are left alone. In-page navigation into a watched queue requires a full reload before it can be verified; this prevents old cached page data from producing a false-clear result. Approved PRs uses GitHub's total search count. Notification counts come from visible thread rows across the displayed repository groups; the popup can show a first-page count rather than the full inbox total. A small count is not accepted as exact when pagination indicates unseen rows: the view remains amber unless at least ten rows already prove X.

## What the queues mean

**Review requests, Mentions, and All notifications are views of one shared notification inbox.** They include both read and unread threads: opening a notification does not clear it from these queues. Use GitHub's **Done** action when you have dealt with it or recorded the follow-up elsewhere. Done clears the same thread from all notification views after they refresh; the extension does not change the underlying review request or issue.

All notifications intentionally overlaps the filtered queues; it is not a residual "everything else" filter. Working left to right, mark handled threads Done, then reload All notifications when you arrive to remove already-handled duplicates. Until that refresh, indicators can temporarily overlap. Later activity can bring a previously handled thread back.

Notification reasons belong to threads rather than individual events. `reason:review-requested` includes team requests as well as individual requests and can show later activity on older request threads; it is not an exact inventory of outstanding individual review requests. `reason:mention` can likewise surface later updates to older mentions. A reason can change, so something may appear only in All notifications rather than the expected priority tab. Treat All notifications as a safety net, not necessarily low-importance work: changes requested or other feedback on your own PRs may land there.

For an occasional authoritative check of outstanding individual review requests, keep this search bookmarked (it is not watched): <https://github.com/search?q=is%3Apr+is%3Aopen+user-review-requested%3A%40me&type=pullrequests>.

**Approved PRs is a current-state search, not a notification queue.** It selects your open PRs with `review:approved`, including approved drafts. Approval does not guarantee passing CI, resolved conflicts, or merge readiness. There is no native Done action: leaving yourself a reminder elsewhere does not remove the PR from this search. It remains red while PRs match, regardless of when their approvals arrived. The extension adds no per-item acknowledgment, snoozing, or new-event tracking.

## Privacy and safety

- Permissions: `alarms`, `storage`, and access to **github.com only**. Chrome's GitHub site-access warning reflects this host permission; there is no access to Slack, email, other sites, cookies APIs, browser history, or an external service.
- Uses ordinary authenticated page loads in your existing Chrome session. It never reads/copies cookies or asks for a personal access token.
- Reads the rendered queue state; changes only its favicon. It does not submit read/done actions, change subscriptions, or submit any other GitHub action. Ordinary GitHub page loads retain GitHub's own behavior.
- Stores only queue kind, count, check time, and status/message in `chrome.storage.local`. No PR titles, comments, notification contents, GitHub object IDs, tokens, or page responses are retained. It does not use sync storage or telemetry.
- Keeps each watched browser tab's last activity timestamp and ephemeral tab ID in `chrome.storage.session`, surviving automatic page reloads and background-worker restarts but not a browser restart or extension reload. This activity state is removed when the tab closes or leaves the watched views. It records no click targets, text, coordinates, or keystrokes; no additional permissions or site access are needed.
- JavaScript is packaged locally. No remote scripts, dynamic evaluation, or CSP weakening.

## First-run check

This version was tested with local DOM fixtures and mocked Chrome APIs on headless Ubuntu, **not with your signed-in Mac browser**. GitHub's private notifications markup may differ; an unrecognized layout deliberately produces amber, never a false "clear".

1. Check the pinned tabs at their normal size on the Mac: **R / A / M / \*** should remain visible, with a small top-right badge. For a nonempty queue, confirm the white number (1–9) or X (10+) on red is legible; compare the popup with the visible list. Notification rows across different repository groups should all count.
2. For an empty queue, confirm the queue character remains visible beside the pale-green dot and the popup says "Watching — queue empty" with a recent verification time. No configuration is needed beyond opening the four exact views in your signed-in profile and reloading them after installation.
3. Switch to Slack or another tab. At the next roughly two-minute cycle, confirm the four watched tabs refresh without being activated, in R / A / M / \* order with at least five seconds between reload starts. With one tab per queue, the sequence normally spans about 15 seconds; Chrome may delay it further.
4. Click inside a queue and keep it active: it should not auto-reload during the next minute. Then leave it without another in-page mouse press; it should join the next automatic cycle, normally at one to three minutes plus staggering/Chrome delays. It should continue refreshing each cycle while idle, even after a reload. Another in-page mouse press should restart the one-minute protection. Switching to iTerm2 without changing Chrome tabs should not prevent these idle reloads. **Check now** should still reload all watched tabs immediately, including active ones, without waiting for the automatic stagger.
5. If a suitable notification is available, mark it Done in Review requests or Mentions, then reload All notifications and confirm it is absent there too. The corresponding PR may still appear in a current-state search.
6. If any view stays amber after loading, tell Codex which view and the popup's message. Do not send cookies, tokens, or full private HTML. A narrow, sanitized DOM excerpt around the result count or empty-state panel may be enough to adjust the parser.

## Development

Requires Node.js 18+ only for offline testing. Runtime has no npm dependencies.

```sh
cd ~/rwgk_config/github_attention_extension
npm ci --ignore-scripts
npm test
npm run check
```

`linkedom` is pinned and used only to parse synthetic HTML fixtures in tests. The runtime assets are already in `extension/`; tests and npm dependencies are not needed to load it in Chrome.

Reference: [GitHub inbox filters](https://docs.github.com/en/subscriptions-and-notifications/reference/inbox-filters), [PR review search](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests#search-by-pull-request-review-status-and-reviewer), [Chrome alarms](https://developer.chrome.com/docs/extensions/reference/api/alarms), [host permissions and tabs](https://developer.chrome.com/docs/extensions/reference/api/tabs), and [isolated content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts).
