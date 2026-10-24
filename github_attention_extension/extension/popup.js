(function (root) {
  "use strict";

  function describeQueue(queue, now, staleMs, kind) {
    if (!queue) return { label: "🟡 Not checked", detail: "Open this view to start watching it." };
    const checkedAt = typeof queue.checkedAt === "number" ? queue.checkedAt : Date.parse(queue.checkedAt);
    const fresh = Number.isFinite(checkedAt) && now - checkedAt <= staleMs;
    const age = Number.isFinite(checkedAt)
      ? `Last verified ${new Date(checkedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`
      : "No successful check yet.";
    if (queue.status === "inactive") {
      return { label: "🟡 Not watching", detail: `Open this view to resume. ${age}` };
    }
    if (queue.status === "loading") {
      return { label: "🟡 Checking", detail: queue.message || "Waiting for GitHub to load." };
    }
    if (queue.status !== "ok") {
      return { label: "🟡 Not verified", detail: `${queue.message || "Could not verify the queue."} ${age}` };
    }
    if (!fresh) {
      return { label: "🟡 Stale — reload to verify", detail: age };
    }
    if (!Number.isInteger(queue.count) || queue.count < 0) {
      return { label: "🟡 Not verified", detail: "No valid count is available." };
    }
    return {
      label: queue.count > 0 ? (kind === "approved"
        ? `🔴 ${queue.count} ${queue.count === 1 ? "PR" : "PRs"}`
        : `🔴 ${queue.count} ${queue.count === 1 ? "thread" : "threads"} shown`) : "🟢 Watching — queue empty",
      detail: queue.message ? `${age} ${queue.message}` : age,
    };
  }

  async function start() {
    const core = root.GitHubAttention;
    const button = root.document.getElementById("check-now");
    const operation = root.document.getElementById("operation");
    for (const kind of core.KINDS) {
      const link = root.document.getElementById(`${kind}-link`);
      link.href = core.URLS[kind];
      link.addEventListener("click", (event) => {
        event.preventDefault();
        root.chrome.tabs.create({ url: core.URLS[kind] });
      });
    }
    async function refresh() {
      try {
        const response = await root.chrome.runtime.sendMessage({ type: "attention:status" });
        for (const kind of core.KINDS) {
          const description = describeQueue(response && response.queues && response.queues[kind], Date.now(), core.STALE_MS, kind);
          root.document.getElementById(`${kind}-status`).textContent = description.label;
          root.document.getElementById(`${kind}-detail`).textContent = description.detail;
        }
      } catch (_) {
        operation.textContent = "Extension unavailable. Reload it in chrome://extensions.";
      }
    }
    button.addEventListener("click", async () => {
      button.disabled = true;
      operation.textContent = "Reloading watched tabs…";
      try {
        const result = await root.chrome.runtime.sendMessage({ type: "attention:refresh" });
        operation.textContent = result && result.message ? result.message : "Reload requested.";
        await refresh();
      } catch (_) {
        operation.textContent = "Could not request a reload.";
      } finally {
        button.disabled = false;
      }
    });
    await refresh();
    root.setInterval(refresh, 2000);
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { describeQueue };
  } else {
    start();
  }
})(globalThis);
