// Rewarded ad page: the server owns the timer and the reward. This component only asks
// it to start, reports "still here" heartbeats and asks it to complete; the displayed
// seconds are re-synced to the server's own count after every heartbeat.
// Every expression used by the template is a property reference (Alpine CSP build).

const TICK_MS = 1000;

export function registerRewardedAds(Alpine) {
  Alpine.data("rewardedAd", () => ({
    phase: "idle", // idle | starting | running | finishing | done | failed
    message: "Press start, then keep this page open until the timer finishes.",
    required: 0,
    watched: 0,
    token: "",
    beatEvery: 5,
    sinceBeat: 0,
    timer: null,

    init() {
      this.required = Number(this.$root.dataset.minWatch) || 0;
    },
    get percent() {
      if (!this.required) return 0;
      return Math.min(100, Math.round((this.watched / this.required) * 100));
    },
    get startDisabled() {
      return this.phase !== "idle" && this.phase !== "failed";
    },
    get startHidden() {
      return this.phase === "done";
    },
    get doneHidden() {
      return this.phase !== "done";
    },

    async post(url, params) {
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": this.$root.dataset.csrf,
          "X-Requested-With": "XMLHttpRequest",
        },
        body: new URLSearchParams(params),
      });
      let data = {};
      try {
        data = await response.json();
      } catch (error) {
        console.debug("Rewarded ad response was not JSON", error);
      }
      return data;
    },

    fail(data) {
      this.stop();
      this.phase = "failed";
      this.message = data.message || "Something went wrong. Please try again.";
    },

    stop() {
      if (this.timer !== null) {
        window.clearInterval(this.timer);
        this.timer = null;
      }
    },

    async begin() {
      this.phase = "starting";
      this.message = "Starting…";
      let data;
      try {
        data = await this.post(this.$root.dataset.startUrl, {});
      } catch (error) {
        console.debug("Rewarded ad start failed", error);
        this.fail({ message: "Connection problem. Please try again." });
        return;
      }
      if (!data.ok) {
        this.fail(data);
        return;
      }
      this.token = data.token;
      this.required = data.min_watch_seconds;
      this.beatEvery = data.heartbeat_seconds || 5;
      this.watched = 0;
      this.sinceBeat = 0;
      this.phase = "running";
      this.message = "Watching… keep this page open.";
      this.timer = window.setInterval(() => this.tick(), TICK_MS);
    },

    tick() {
      if (this.phase !== "running") return;
      if (document.hidden) {
        this.message = "Paused: bring this tab back into view to continue.";
        return;
      }
      this.message = "Watching… keep this page open.";
      this.watched += 1;
      this.sinceBeat += 1;
      if (this.watched >= this.required) {
        this.finish();
      } else if (this.sinceBeat >= this.beatEvery) {
        this.beat();
      }
    },

    async beat() {
      this.sinceBeat = 0;
      try {
        const data = await this.post(this.$root.dataset.heartbeatUrl, {
          token: this.token,
          claimed: String(this.watched),
        });
        if (!data.ok) {
          this.fail(data);
          return;
        }
        this.watched = Math.min(this.watched, data.watched_seconds + this.beatEvery);
      } catch (error) {
        console.debug("Heartbeat failed", error);
      }
    },

    async finish() {
      if (this.phase !== "running") return;
      this.phase = "finishing";
      this.message = "Checking with the server…";
      let data;
      try {
        data = await this.post(this.$root.dataset.completeUrl, { token: this.token });
      } catch (error) {
        console.debug("Rewarded ad completion failed", error);
        this.phase = "running";
        this.message = "Connection problem. Retrying…";
        return;
      }
      if (data.ok) {
        this.stop();
        this.phase = "done";
        this.watched = this.required;
        this.message = data.message || "Done. Thank you for watching.";
        return;
      }
      if (data.error === "not_watched_long_enough") {
        this.required = this.watched + (data.remaining_seconds || 1);
        this.phase = "running";
        this.message = "Almost there… keep this page open.";
        return;
      }
      this.fail(data);
    },
  }));
}
