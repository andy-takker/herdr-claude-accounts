import { View, div } from "gpui-kit";
import { v_flex, h_flex, Input, InputState } from "gpui-base";
import { exec, home, theme, props, closeOverlay, run, focused, spaces } from "herdr";

export default class ClaudeAccounts extends View {
  init(options = {}, cx) {
    this.compact = Boolean(props()?.compact || options.compact);
    this.rows = [];
    this.expandedSessions = {};
    this.system = { id: null, label: "System default" };
    this.removed = null;
    this.adding = false;
    this.pendingCheck = null;
    this.loginAttempt = null;
    this.cancelling = false;
    this.closed = false;
    this.selected = undefined;
    this.pendingRemoval = null;
    this.at = 0;
    this.busy = false;
    this.refreshing = false;
    this.revision = 0;
    this.error = "";
    this.message = "Loading…";
    if (!this.compact) {
      this.handle = cx.focus_handle();
      this.handle.focus();
      this.name = InputState.new({ placeholder: "Account name" });
      this.name.on("submit", (_e, cx) => this.add(cx));
    }
    this.refresh(cx, true);
    cx.timer.every(this.compact ? 5000 : 15000, (cx) => this.refresh(cx));
  }

  async command(args) {
    const r = await exec(home() + "/.local/share/herdr-claude-accounts/bin/claude-accounts", args, { timeout: 600000 });
    if (r.code !== 0) throw new Error(r.stderr.trim() || "Could not complete the action");
    return JSON.parse(r.stdout);
  }

  refresh(cx, initial = false) {
    if (this.closed || this.refreshing || this.busy || this.pendingRemoval || this.adding || this.removed || this.pendingCheck) return;
    this.refreshing = true;
    const revision = this.revision;
    cx.spawn(async (cx) => {
      try {
        if (initial && !this.compact) {
          await this.reload(false, true);
          if (this.closed || revision !== this.revision) return;
          cx.notify();
        }
        await this.reload();
        if (this.closed || revision !== this.revision) return;
        this.error = "";
        this.message = "Selection applies to new Claude sessions";
      } catch (e) {
        if (!this.closed && revision === this.revision) this.error = String(e.message || e);
      } finally {
        this.refreshing = false;
        if (!this.closed && revision === this.revision) cx.notify();
      }
    });
  }

  async reload(withUsage = true, metadataOnly = false) {
    const revision = this.revision;
    const result = await this.command(this.compact || metadataOnly ? ["list"] : withUsage ? ["status", "--usage"] : ["status"]);
    if (this.closed || revision !== this.revision) return;
    const highlighted = this.rows[this.at - 1]?.id;
    const previous = new Map(this.rows.map((a) => [a.id, a]));
    this.rows = result.accounts.map((a) => Object.assign(previous.get(a.id) || {}, a));
    Object.assign(this.system, result.system);
    this.selected = result.selected;
    this.at = highlighted ? this.rows.findIndex((a) => a.id === highlighted) + 1 : 0;
  }

  action(cx, message, fn) {
    if (this.closed || this.busy) return;
    this.busy = true;
    this.revision++;
    this.error = "";
    this.message = message;
    cx.notify();
    cx.spawn(async (cx) => {
      try { await fn(cx); }
      catch (e) { if (!this.closed) this.error = String(e.message || e); }
      this.busy = false;
      if (!this.closed) cx.notify();
    });
  }

  add(cx) {
    if (!this.adding || this.busy) return;
    const label = this.name.value().trim();
    if (!label) { this.error = "Enter an account name"; cx.notify(); return; }
    this.action(cx, "Complete sign-in in your browser. Waiting up to 5 minutes…", async (cx) => {
      const account = await this.command(["add", label, "--no-login"]);
      this.adding = false;
      if (account.warning) {
        this.message = account.warning;
        await this.reload(false, true);
        this.at = this.rows.findIndex((a) => a.id === account.id) + 1;
        if (!this.closed) cx.notify();
        await this.reload(false);
        return;
      }
      const result = await this.browserLogin(account.id, cx);
      this.at = this.rows.findIndex((a) => a.id === account.id) + 1;
      this.message = result.state === "cancelled" ? "Sign-in cancelled. Profile retained for another attempt." :
        "Account added. Click it to select.";
    });
  }

  login(id, cx) {
    this.action(cx, "Complete sign-in in your browser. Waiting up to 5 minutes…", async (cx) => {
      const result = await this.browserLogin(id, cx);
      this.message = result.state === "cancelled" ? "Sign-in cancelled. Profile retained." : "Sign-in completed.";
    });
  }

  async browserLogin(id, cx) {
    const ticket = await this.command(["prepare-login", id]);
    this.loginAttempt = ticket.attempt;
    this.cancelling = false;
    if (!this.closed) cx.notify();
    try {
      if (this.closed) await this.command(["cancel-login", ticket.attempt]);
      return await this.command(["login", id, "--browser", "--attempt", ticket.attempt]);
    } finally {
      this.loginAttempt = null;
      this.cancelling = false;
      if (!this.closed) await this.reload(false);
    }
  }

  cancelLogin(cx) {
    if (!this.loginAttempt || this.cancelling) return;
    const ticket = this.loginAttempt;
    this.cancelling = true;
    this.message = "Cancelling sign-in…";
    cx.notify();
    cx.spawn(async (cx) => {
      try { await this.command(["cancel-login", ticket]); }
      catch (e) {
        if (this.loginAttempt === ticket) {
          this.cancelling = false;
          this.error = "Could not cancel sign-in: " + String(e.message || e);
        }
      }
      if (!this.closed) cx.notify();
    });
  }

  async close() {
    this.closed = true;
    this.revision++;
    if (this.loginAttempt) await this.command(["cancel-login", this.loginAttempt]);
  }

  usageSummary(account) {
    const u = account.usage;
    if (!u) return "Usage: loading…";
    const reasons = { timeout: "timed out", unavailable: "Claude unavailable", unsupported: "CLI version does not support usage",
      invalid_response: "unexpected CLI response", not_available: "no data for this plan",
      needs_login: "sign-in required", provider: "API / other provider", refreshing: "refreshing" };
    if (!u.windows?.length) return "Usage unavailable" + (reasons[u.reason] ? " · " + reasons[u.reason] : "");
    const stale = u.state === "stale" || Date.now() / 1000 - u.checked_at > 330 ||
      u.windows.some((w) => w.resets_at && w.resets_at <= Date.now() / 1000);
    return `${stale ? "Usage out of date" : "Usage"} · ${this.stamp(u.checked_at)}` +
      (u.reason && reasons[u.reason] ? " · " + reasons[u.reason] : "");
  }

  refreshUsage(account, cx) {
    this.action(cx, `Refreshing usage: ${account.label}…`, async () => {
      account.usage = await this.command(["refresh-usage", account.id || "system"]);
      this.message = account.usage.state === "available" ? "Usage updated." : "Could not refresh usage; see the account card for details.";
    });
  }

  usageColor(percent) {
    return percent < 70 ? "#2563eb" : percent < 80 ? "#eab308" : percent < 90 ? "#f97316" : "#dc2626";
  }

  usageLabel(label) {
    // Older cached limits keep their labels until the next usage refresh.
    return label.replace(/^(5 часов|Неделя|Лимит)(?= · |$)/,
      (period) => ({ "5 часов": "5 hours", "Неделя": "Weekly", "Лимит": "Limit" })[period]);
  }

  stamp(seconds) {
    const d = new Date(seconds * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())} ${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`;
  }

  showRemoved(cx) {
    this.action(cx, "Retained removed profiles", async () => {
      this.removed = await this.command(["removed"]);
    });
  }

  restore(id, cx) {
    this.action(cx, "Restoring…", async () => {
      try { await this.command(["restore", id]); }
      finally { this.removed = null; await this.reload(); }
      this.message = "Profile restored. Current account unchanged.";
    });
  }

  check(cx) {
    if (!this.pendingCheck) return;
    const id = this.pendingCheck.id;
    this.action(cx, "Checking Claude access…", async () => {
      await this.command(["check", id]);
      this.pendingCheck = null;
      await this.reload();
      this.message = "Check completed; see the account card for the result.";
    });
  }

  describe(account) {
    const auth = account.auth || {};
    const labels = { saved: "Sign-in saved (local)", needs_login: "Sign-in required",
      provider: "API / provider sign-in", unknown: "Sign-in status unknown" };
    return [auth.email || (account.id === null ? "Email unavailable" : ""),
      labels[auth.state] || "Not checked yet",
      auth.checked_at ? this.stamp(auth.checked_at) : ""
    ].filter(Boolean).join(" · ");
  }

  detail(account) {
    const labels = { verified: "Access verified", needs_login: "Sign in again",
      limited: "Usage limit reached", access_denied: "Access / billing restriction",
      network_error: "Network error", unknown: "Could not verify access" };
    const online = account.online;
    const reasons = { timeout: "Claude timed out", unavailable: "Could not start Claude",
      invalid_response: "Unexpected Claude response format", command_failed: "Claude command failed" };
    return [online?.state ? `${labels[online.state] || online.state} · ${this.stamp(online.checked_at)}` : "",
      reasons[online?.reason] || reasons[account.auth?.reason] || reasons[account.sessions?.reason] || ""].filter(Boolean).join(" · ");
  }

  choose(id, cx) {
    if (this.pendingRemoval) return;
    this.action(cx, "Selecting account…", async () => {
      await this.command(["select", id || "system"]);
      this.selected = id;
      this.message = "Account selected. New Claude sessions will use it.";
    });
  }

  requestRemove(cx) {
    if (this.busy || this.pendingRemoval) return;
    const account = this.rows[this.at - 1];
    if (!account) return;
    this.pendingRemoval = { id: account.id, label: account.label };
    cx.notify();
  }

  remove(cx) {
    if (!this.pendingRemoval) return;
    const account = this.pendingRemoval;
    this.action(cx, "Removing account…", async () => {
      await this.command(["remove", account.id]);
      this.rows = this.rows.filter((a) => a.id !== account.id);
      this.selected = null;
      this.at = 0;
      this.pendingRemoval = null;
      this.message = "Account removed from the list. System default selected.";
    });
  }

  openClaude(id) {
    const space = spaces().find((s) => s.id === focused().space);
    const label = this.rows.find((a) => a.id === id)?.label || "System default";
    const args = { name: "Claude: " + label, argv: [home() + "/.local/share/herdr-claude-accounts/bin/claude-accounts", "launch", id || "system"] };
    closeOverlay(props().overlay);
    if (!space || space.machine) run("space.new", { ...args, machine: "", folder: home() });
    else run("tab.new", { ...args, space: space.id });
  }

  render() {
    const t = theme();
    if (this.compact) {
      const label = this.selected === undefined ? "…" : this.selected === null ? "System default" :
        this.rows.find((a) => a.id === this.selected)?.label || "unavailable";
      return h_flex().id("claude-current").size_full().px(14).items_center().bg(t.chrome)
        .text_size(12).text_color(this.error ? t.blocked : t.foreground).cursor_pointer()
        .hover((s) => s.bg(t.hover)).on_click(() => run("mod.open", { name: "claude-accounts" }))
        .child("Claude: " + (this.error ? "error · open" : label));
    }
    const rows = [this.system, ...this.rows];
    const active = rows[this.at];
    const normal = !this.adding && !this.removed && !this.pendingRemoval && !this.pendingCheck;
    const button = (id, label, action) => div().id(id).px(12).py(8).rounded(6)
      .bg(t.raised).cursor_pointer().hover((s) => s.bg(t.hover))
      .on_click((e, cx) => {
        cx.stop_propagation();
        if (!this.busy || id === "close" || id === "cancel-login") action(e, cx);
      }).child(label);
    const cancel = (_e, cx) => {
      this.adding = false; this.removed = null; this.pendingRemoval = null; this.pendingCheck = null;
      this.handle.focus(); cx.notify();
    };
    return v_flex().id("claude-accounts").size_full().p(20).gap(12)
      .bg(t.popover).text_color(t.foreground).text_size(13).track_focus(this.handle)
      .on_key_down((e, cx) => {
        if (!normal || this.busy) return;
        if (e.key === "down") this.at = (this.at + 1) % rows.length;
        else if (e.key === "up") this.at = (this.at + rows.length - 1) % rows.length;
        else if (e.key === "enter") this.choose(rows[this.at].id, cx);
        else return;
        cx.stop_propagation(); cx.notify();
      })
      .child(h_flex().items_center().justify_between()
        .child(div().text_size(19).font_semibold().child("Claude Accounts"))
        .child(button("close", "Close · Esc", () => closeOverlay(props().overlay))))
      .child(div().text_color(t.muted).child("Accounts on this computer · shared settings"))
      .when(!this.removed, (el) => el.child(v_flex().id("accounts-list").flex_1().min_h(0).overflow_y_scrollbar().gap(6).children(rows.map((a, i) =>
        v_flex().id(a.id || "system").p(12).gap(4).rounded(7).cursor_pointer()
          .bg(i === this.at ? t.selected : t.surface).hover((s) => s.bg(t.hover))
          .on_click((_e, cx) => { if (normal && !this.busy) { this.at = i; this.choose(a.id, cx); } })
          .child(div().font_semibold().child((a.id === this.selected ? "✓  " : "    ") + a.label))
          .child(div().text_size(12).text_color(t.muted).child(this.describe(a)))
          .child(div().text_size(11).text_color(t.muted).child(this.usageSummary(a)))
          .children((a.usage?.windows || []).map((w) => h_flex().gap(8).items_center().text_size(12)
            .child(div().w(80).flex_shrink_0().h(4).rounded(2).bg(t.raised)
              .child(div().w(w.used_percent * 0.8).h(4).rounded(2).bg(this.usageColor(w.used_percent))))
            .child(div().w(104).flex_shrink_0().child(this.usageLabel(w.label)))
            .child(div().w(90).flex_shrink_0().child(`${Math.round((100 - w.used_percent) * 10) / 10}% free`))
            .child(div().flex_1().child(w.resets_at ? `resets ${this.stamp(w.resets_at)}` : ""))))
          .child(h_flex().gap(8).items_center().justify_between()
            .child(div().id("sessions-" + (a.id || "system")).cursor_pointer()
              .child(a.sessions?.available ?
                `${this.expandedSessions[a.id || "system"] ? "▾" : "▸"} Sessions: ${a.sessions.sessions.length}` : "Sessions unavailable")
              .on_click((_e, cx) => {
                cx.stop_propagation();
                if (this.busy || !a.sessions?.available) return;
                const key = a.id || "system";
                this.expandedSessions[key] = !this.expandedSessions[key];
                cx.notify();
              }))
            .child(button("refresh-usage-" + (a.id || "system"), "Refresh usage", (_e, cx) => this.refreshUsage(a, cx))))
          .when(Boolean(this.expandedSessions[a.id || "system"] && a.sessions?.available), (el) => el.child(
            v_flex().id("session-list-" + (a.id || "system")).max_h(100).overflow_y_scrollbar().gap(3)
              .when(a.sessions.sessions.length === 0, (list) => list.child("No active sessions"))
              .children(a.sessions.sessions.map((s) =>
                div().text_size(11).text_color(t.muted).child(`${s.state} · ${s.cwd || s.id}`)))))
          .when(Boolean(this.detail(a)), (el) => el.child(div().text_size(12).text_color(t.muted).child(this.detail(a))))))))
      .when(Boolean(this.removed), (el) => el.child(v_flex().id("removed-list").flex_1().min_h(0).overflow_y_scrollbar().gap(8)
        .child(div().font_semibold().child("Removed accounts"))
        .when(this.removed.length === 0, (el) => el.child("No retained removed profiles"))
        .children(this.removed.map((r) => h_flex().items_center().justify_between()
          .child(r.account.label)
          .child(button("restore-" + r.account.id, "Restore", (_e, cx) => this.restore(r.account.id, cx)))))))
      .child(div().text_color(this.error ? t.blocked : t.muted).child(this.error || this.message))
      .when(Boolean(this.removed), (el) => el.child(h_flex().gap(8)
        .child(button("back", "Back", cancel))))
      .when(Boolean(this.loginAttempt), (el) => el.child(button("cancel-login",
        this.cancelling ? "Cancelling…" : "Cancel sign-in", (_e, cx) => this.cancelLogin(cx))))
      .when(this.adding, (el) => el.child(v_flex().gap(8)
        .child(Input.new(this.name).w_full().h(36).px(10).bg(t.surface).rounded(6))
        .child(h_flex().gap(8)
          .child(button("create-login", "Add and sign in via browser", (_e, cx) => this.add(cx)))
          .child(button("cancel-add", "Cancel", cancel)))))
      .when(Boolean(this.pendingRemoval), (el) => el.child(v_flex().gap(8)
        .child(div().font_semibold().child(`Remove “${this.pendingRemoval.label}”?`))
        .child(div().text_size(12).text_color(t.muted).child("System default will be selected. Data and running sessions will be retained."))
        .child(h_flex().gap(8)
          .child(button("confirm-remove", "Yes, remove", (_e, cx) => this.remove(cx)))
          .child(button("cancel-remove", "Cancel", cancel)))))
      .when(Boolean(this.pendingCheck), (el) => el.child(v_flex().gap(8)
        .child(`Check access for “${this.pendingCheck.label}”? One short request uses quota.`)
        .child(h_flex().gap(8)
          .child(button("confirm-check", "Check", (_e, cx) => this.check(cx)))
          .child(button("cancel-check", "Cancel", cancel)))))
      .when(normal, (el) => el.child(v_flex().gap(8)
        .child(h_flex().gap(8)
          .child(button("launch", "Launch in new tab", () => this.openClaude(active.id)))
          .when(Boolean(active.id), (el) => el
            .child(button("login", "Sign in again", (_e, cx) => this.login(active.id, cx)))
            .child(button("check", "Check access…", (_e, cx) => { this.pendingCheck = active; cx.notify(); }))))
        .child(h_flex().gap(8)
        .child(button("add", "Add…", (_e, cx) => { this.adding = true; this.name.focus(); cx.notify(); }))
        .child(button("refresh", "Refresh", (_e, cx) => this.refresh(cx)))
        .child(button("removed", "Removed…", (_e, cx) => this.showRemoved(cx)))
        .when(Boolean(active.id), (el) => el.child(
          button("remove", "Remove account…", (_e, cx) => this.requestRemove(cx)))))));
  }
}
