import { View, div } from "gpui-kit";
import { v_flex, h_flex, Input, InputState } from "gpui-base";
import { exec, home, theme, props, closeOverlay, run, focused, spaces, pickFolder } from "herdr";

export default class ClaudeAccounts extends View {
  init(options = {}, cx) {
    this.compact = Boolean(props()?.compact || options.compact);
    this.rows = [];
    this.openSession = null;
    this.ns = null;
    this.addFlow = null;
    this.shared = false;
    this.copied = null;
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
    if ("share_conversations" in result) this.shared = Boolean(result.share_conversations);
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
      this.addFlow = account.warning ? null : { id: account.id, label: account.label, step: "browser" };
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
        "Account added.";
    });
  }

  login(id, cx) {
    this.action(cx, "Complete sign-in in your browser. Waiting up to 5 minutes…", async (cx) => {
      const result = await this.browserLogin(id, cx);
      this.message = result.state === "cancelled" ? "Sign-in cancelled. Profile retained." : "Sign-in completed.";
    });
  }

  async browserLogin(id, cx) {
    const flow = this.addFlow?.id === id ? this.addFlow : null;
    if (flow) flow.step = "browser";
    const ticket = await this.command(["prepare-login", id]);
    this.loginAttempt = ticket.attempt;
    this.cancelling = false;
    if (!this.closed) cx.notify();
    try {
      if (this.closed) await this.command(["cancel-login", ticket.attempt]);
      const result = await this.command(["login", id, "--browser", "--attempt", ticket.attempt]);
      if (flow) flow.step = result.state === "cancelled" ? "cancelled" : "done";
      return result;
    } catch (e) {
      if (flow) flow.step = "failed";
      throw e;
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

  keyOf(id) { return id || "system"; }

  idOf(key) { return key === "system" ? null : key; }

  account(key) { return key === "system" ? this.system : this.rows.find((a) => a.id === key); }

  headroom(account) {
    const windows = account?.usage?.windows || [];
    return windows.length ? Math.round(Math.min(...windows.map((w) => 100 - w.used_percent))) : null;
  }

  canLaunch(account) { return account.auth?.state !== "needs_login"; }

  age(started) {
    if (!started) return "";
    const ms = typeof started === "number" ? (started < 1e12 ? started * 1000 : started) : Date.parse(started);
    if (!ms) return "";
    const minutes = Math.max(0, Math.round((Date.now() - ms) / 60000));
    return minutes < 60 ? `${minutes}m` : minutes < 1440 ? `${Math.round(minutes / 60)}h` : `${Math.round(minutes / 1440)}d`;
  }

  shortPath(path) {
    if (!path) return "";
    const h = home();
    return path === h ? "~" : path.startsWith(h + "/") ? "~" + path.slice(h.length) : path;
  }

  // Which right-hand pane is open; the accounts list stays visible beside every one.
  pane() {
    return this.removed ? "removed" : (this.adding || this.addFlow) ? "add" : this.ns ? "new" : "detail";
  }

  back() {
    if (this.loginAttempt) return;
    this.adding = false; this.addFlow = null; this.ns = null; this.removed = null;
    this.pendingRemoval = null; this.pendingCheck = null;
    this.handle?.focus();
  }

  startNew(key, preset = {}) {
    if (this.busy) return;
    this.back();
    const fallback = [this.system, ...this.rows].find((a) => a.id === this.selected && this.canLaunch(a)) ||
      [this.system, ...this.rows].find((a) => this.canLaunch(a)) || this.system;
    this.ns = { account: key || this.keyOf(fallback.id), folder: null, start: "new", resume: null, remember: false, ...preset };
  }

  folders() {
    const seen = new Set();
    for (const a of [this.system, ...this.rows]) {
      for (const s of a.sessions?.sessions || []) if (s.cwd) seen.add(s.cwd);
    }
    if (this.ns?.folder) seen.add(this.ns.folder);
    return [...seen].slice(0, 5);
  }

  chooseFolder(cx) {
    cx.spawn(async (cx) => {
      const folder = await pickFolder({ prompt: "Use folder" });
      if (folder && this.ns && !this.closed) { this.ns.folder = folder; cx.notify(); }
    });
  }

  launchNew(cx) {
    const ns = this.ns;
    if (!ns || this.busy) return;
    const id = this.idOf(ns.account);
    const args = ns.start === "continue" ? ["--continue"] : ns.start === "resume" && ns.resume ? ["--resume", ns.resume] : [];
    const options = { folder: ns.folder, args };
    if (!ns.remember || id === this.selected) return this.openClaude(id, options);
    this.action(cx, "Selecting account…", async () => {
      await this.command(["select", ns.account]);
      this.selected = id;
      this.openClaude(id, options);
    });
  }

  openClaude(id, options = {}) {
    const space = spaces().find((s) => s.id === focused()?.space);
    const label = this.rows.find((a) => a.id === id)?.label || "System default";
    const args = options.args || [];
    const argv = [home() + "/.local/share/herdr-claude-accounts/bin/claude-accounts", "launch", id || "system",
      ...(args.length ? ["--", ...args] : [])];
    const base = { name: "Claude: " + label, argv };
    closeOverlay(props().overlay);
    if (!space || space.machine || options.folder) run("space.new", { ...base, machine: "", folder: options.folder || home() });
    else run("tab.new", { ...base, space: space.id });
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
    this.at = Math.max(0, Math.min(this.at, rows.length - 1));
    const active = rows[this.at];
    const pane = this.pane();
    const allowed = ["close", "cancel-login"];
    const button = (id, label, action, kind = "normal") => {
      const colors = { primary: [t.solid, t.solid_foreground], normal: [t.raised, t.foreground],
        ghost: [null, t.muted], danger: [null, t.blocked] }[kind];
      let el = div().id(id).px(11).py(6).rounded(6).text_size(12).cursor_pointer().text_color(colors[1])
        .hover((s) => kind === "primary" ? s.opacity(0.88) : s.bg(t.hover));
      if (colors[0]) el = el.bg(colors[0]);
      return el.on_click((e, cx) => {
        cx.stop_propagation();
        if (!this.busy || allowed.includes(id)) action(e, cx);
      }).child(label);
    };
    const pill = (text, on) => div().px(7).rounded(9).text_size(11)
      .bg(on ? t.accent : t.raised).text_color(on ? t.accent_foreground : t.muted).child(text);
    const dot = (color) => div().w(8).h(8).rounded(4).flex_shrink_0().bg(color);
    const bar = (percent, width) => div().w(width).h(5).flex_shrink_0().rounded(3).bg(t.raised)
      .child(div().w(Math.max(0, Math.min(100, percent)) * width / 100).h(5).rounded(3).bg(this.usageColor(percent)));
    const title = (text) => div().text_size(11).font_semibold().text_color(t.muted).child(text.toUpperCase());
    const section = (id, heading, ...items) => v_flex().id(id).px(20).py(12).gap(6).border_b(1).border_color(t.border)
      .child(title(heading)).children(items);
    const option = (id, on, main, hint, onClick) => h_flex().id(id).px(10).py(7).gap(8).items_center().rounded(7)
      .border_1().border_color(on ? t.accent : t.border).bg(on ? t.selected : t.popover).cursor_pointer()
      .hover((s) => s.bg(t.hover)).on_click((e, cx) => { cx.stop_propagation(); if (!this.busy) { onClick(e, cx); cx.notify(); } })
      .child(v_flex().flex_1().min_w(0).child(div().truncate().child(main))
        .when(Boolean(hint), (el) => el.child(div().text_size(11).text_color(t.muted).truncate().child(hint))));

    // ---------- left: the accounts list and list-level actions ----------
    const list = v_flex().w(250).flex_shrink_0().h_full().min_h(0).bg(t.chrome).border_r(1).border_color(t.border)
      .child(h_flex().px(10).py(10).gap(4).items_center().border_b(1).border_color(t.border)
        .child(button("add", "+ Add account", (_e, cx) => { this.back(); this.adding = true; this.name.focus(); cx.notify(); }))
        .child(button("refresh", "↻", (_e, cx) => this.refresh(cx), "ghost"))
        .child(div().flex_1())
        .child(button("removed", "Removed", (_e, cx) => this.showRemoved(cx), "ghost")))
      .child(v_flex().id("accounts-list").flex_1().min_h(0).overflow_y_scrollbar().children(rows.map((a, i) => {
        const key = this.keyOf(a.id);
        const room = this.headroom(a);
        const sessions = a.sessions?.available ? a.sessions.sessions.length : null;
        const signIn = a.auth?.state === "needs_login";
        return v_flex().id(key).px(12).py(9).gap(5).cursor_pointer()
          .border_l_2().border_color(i === this.at && pane === "detail" ? t.accent : t.chrome)
          .bg(i === this.at && pane === "detail" ? t.popover : t.chrome).hover((s) => s.bg(t.hover))
          .on_click((_e, cx) => {
            if (this.busy || this.loginAttempt) return;
            this.back(); this.at = i; this.openSession = null; cx.notify();
          })
          .child(h_flex().gap(8).items_center()
            .child(dot(signIn ? t.blocked : a.auth?.state === "saved" || a.auth?.state === "provider" ? t.done : t.dim))
            .child(div().flex_1().min_w(0).truncate().font_semibold().child(a.label))
            .when(a.id === this.selected, (el) => el.child(pill("new sessions", true))))
          .when(Boolean(a.usage?.windows?.length), (el) => el.child(h_flex().gap(6)
            .children(a.usage.windows.slice(0, 2).map((w) => bar(w.used_percent, 104)))))
          .child(div().text_size(11).text_color(signIn ? t.blocked : t.muted).truncate().child(signIn ? "Sign-in required" :
            [room === null ? "" : `${room}% headroom`, sessions === null ? "" : `${sessions} session${sessions === 1 ? "" : "s"}`]
              .filter(Boolean).join(" · ") || this.describe(a)));
      })))
      .child(div().px(12).py(8).border_t(1).border_color(t.border).text_size(11).text_color(t.dim)
        .child("↑↓ inspect · ↵ use for new sessions"));

    // ---------- right: one account ----------
    const detail = () => {
      const a = active;
      const key = this.keyOf(a.id);
      const isSelected = a.id === this.selected;
      const sessions = a.sessions?.sessions || [];
      const windows = a.usage?.windows || [];
      const confirm = this.pendingRemoval || this.pendingCheck;
      return v_flex().id("account-detail").flex_1().min_w(0).min_h(0).overflow_y_scrollbar()
        .child(v_flex().px(20).pt(16).pb(10).gap(4)
          .child(h_flex().gap(8).items_center()
            .child(div().flex_1().min_w(0).truncate().text_size(18).font_semibold().child(a.label))
            .child(isSelected ? pill("Used for new sessions", true) :
              button("select", "Use for new sessions", (_e, cx) => this.choose(a.id, cx))))
          .child(div().text_size(12).text_color(t.muted).child(this.describe(a)))
          .when(Boolean(this.detail(a)), (el) => el.child(div().text_size(12).text_color(t.muted).child(this.detail(a)))))
        .child(h_flex().px(20).pb(12).gap(6).items_center().border_b(1).border_color(t.border)
          .child(this.canLaunch(a) ?
            button("launch", "New session…", (_e, cx) => { this.startNew(key); cx.notify(); }, "primary") :
            button("login", "Sign in…", (_e, cx) => this.login(a.id, cx), "primary"))
          .child(div().flex_1())
          .child(button("refresh-usage-" + key, "Refresh usage", (_e, cx) => this.refreshUsage(a, cx), "ghost"))
          .when(Boolean(a.id) && this.canLaunch(a), (el) => el
            .child(button("relogin", "Sign in again", (_e, cx) => this.login(a.id, cx), "ghost")))
          .when(Boolean(a.id), (el) => el
            .child(button("check", "Check…", (_e, cx) => { this.pendingRemoval = null; this.pendingCheck = a; cx.notify(); }, "ghost"))
            .child(button("remove", "Remove…", (_e, cx) => this.requestRemove(cx), "danger"))))
        .when(Boolean(confirm), (el) => el.child(v_flex().mx(20).mt(12).p(12).gap(8).rounded(8).bg(t.surface)
          .child(div().font_semibold().child(this.pendingRemoval ? `Remove “${this.pendingRemoval.label}”?` : `Check access for “${this.pendingCheck?.label}”?`))
          .child(div().text_size(12).text_color(t.muted).child(this.pendingRemoval ?
            "System default will be selected. Profile data and running sessions are retained." :
            "Sends one short model request; it uses this account's quota."))
          .child(h_flex().gap(8)
            .child(this.pendingRemoval ? button("confirm-remove", "Yes, remove", (_e, cx) => this.remove(cx), "primary") :
              button("confirm-check", "Check", (_e, cx) => this.check(cx), "primary"))
            .child(button("cancel-confirm", "Cancel", (_e, cx) => { this.pendingRemoval = null; this.pendingCheck = null; cx.notify(); })))))
        .child(section("usage-" + key, this.usageSummary(a),
          ...windows.map((w) => h_flex().gap(10).items_center().text_size(12)
            .child(div().w(110).flex_shrink_0().truncate().child(this.usageLabel(w.label)))
            .child(bar(w.used_percent, 150))
            .child(div().w(70).flex_shrink_0().child(`${Math.round((100 - w.used_percent) * 10) / 10}% free`))
            .child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.muted).child(w.resets_at ? `resets ${this.stamp(w.resets_at)}` : "")))))
        .child(v_flex().id("session-list-" + key).px(20).py(12).gap(2)
          .child(title(a.sessions?.available ? `Running sessions · ${sessions.length}` : "Sessions unavailable"))
          .when(Boolean(a.sessions?.available) && sessions.length === 0, (el) => el.child(div().text_size(12).text_color(t.muted).child("No running sessions.")))
          .children(sessions.map((s) => {
            const sid = s.sessionId || s.id;
            const open = this.openSession === sid;
            const others = rows.filter((x) => x.id !== a.id && this.canLaunch(x));
            return v_flex()
              .child(h_flex().id("session-" + sid).px(8).py(6).gap(10).items_center().rounded(6).cursor_pointer()
                .bg(open ? t.selected : t.popover).hover((st) => st.bg(t.hover))
                .on_click((_e, cx) => { cx.stop_propagation(); this.openSession = open ? null : sid; cx.notify(); })
                .child(dot(s.state === "busy" ? t.working : s.state === "blocked" ? t.blocked : t.dim))
                .child(v_flex().flex_1().min_w(0)
                  .child(div().truncate().child(this.shortPath(s.cwd) || sid))
                  .child(div().text_size(11).text_color(t.muted).truncate().child([s.kind, sid.slice(0, 8)].filter(Boolean).join(" · "))))
                .child(div().text_size(11).text_color(t.muted).child([s.state, this.age(s.startedAt)].filter(Boolean).join(" · "))))
              .when(open, (el) => el.child(v_flex().id("session-actions-" + sid).ml(26).mt(2).mb(6).p(10).gap(8).rounded(8).bg(t.surface)
                .child(div().text_size(12).text_color(t.muted).child(`Runs as ${a.label} until it exits. To move it, exit it there, then resume it with another account.`))
                .child(h_flex().gap(6)
                  .when(others.length > 0 && Boolean(s.sessionId), (row) => row.child(button("continue-" + sid, "Continue with another account…", (_e, cx) => {
                    this.startNew(this.keyOf(others[0].id), { folder: s.cwd || null, start: "resume", resume: s.sessionId, from: a.label });
                    cx.notify();
                  })))
                  .when(Boolean(s.sessionId), (row) => row.child(button("copy-" + sid, this.copied === sid ? "Copied" : "Copy resume command", (_e, cx) => {
                    cx.write_to_clipboard(`claude --resume ${s.sessionId}`); this.copied = sid; cx.notify();
                  }, "ghost"))))));
          })));
    };

    // ---------- right: a new Claude session ----------
    const newSession = () => {
      const ns = this.ns;
      const target = this.account(ns.account) || this.system;
      const room = this.headroom(target);
      const candidates = rows.filter((a) => this.canLaunch(a));
      const current = focused()?.cwd;
      const resuming = ns.start === "resume" && ns.resume;
      return v_flex().id("new-session").flex_1().min_w(0).min_h(0).overflow_y_scrollbar()
        .child(v_flex().px(20).pt(16).pb(10).gap(4).border_b(1).border_color(t.border)
          .child(h_flex().items_center().child(div().flex_1().text_size(18).font_semibold().child("New Claude session"))
            .child(button("cancel-new", "Cancel", (_e, cx) => { this.back(); cx.notify(); }, "ghost")))
          .child(div().text_size(12).text_color(t.muted).child("Opens a new tab. Running sessions keep their accounts.")))
        .child(section("ns-accounts", "1 · Account",
          ...candidates.map((a) => {
            const key = this.keyOf(a.id);
            const r = this.headroom(a);
            return option("ns-account-" + key, ns.account === key, a.label + (a.id === this.selected ? "  · default" : ""),
              [r === null ? "usage unknown" : `${r}% headroom`, `${a.sessions?.sessions?.length || 0} running`].join(" · "),
              () => { ns.account = key; });
          }),
          ...(room !== null && room < 15 ? [div().text_size(12).text_color(t.blocked).child(`${target.label} has ${room}% headroom left; it will hit its limit soon.`)] : []),
          h_flex().id("ns-remember").gap(8).items_center().cursor_pointer().text_size(12).text_color(t.muted)
            .on_click((_e, cx) => { cx.stop_propagation(); ns.remember = !ns.remember; cx.notify(); })
            .child(ns.remember ? "☑" : "☐").child("Also use this account for future sessions")))
        .child(section("ns-folders", "2 · Folder",
          option("ns-folder-current", !ns.folder, "Current space", current ? this.shortPath(current) : "Where the focused tab is", () => { ns.folder = null; }),
          ...this.folders().map((f, i) => option("ns-folder-" + i, ns.folder === f, this.shortPath(f), "New space", () => { ns.folder = f; })),
          h_flex().child(button("ns-pick-folder", "Choose folder…", (_e, cx) => this.chooseFolder(cx), "ghost"))))
        .child(section("ns-conversation", "3 · Conversation",
          option("ns-start-new", ns.start === "new", "New conversation", "", () => { ns.start = "new"; }),
          option("ns-start-continue", ns.start === "continue", "Continue the latest in this folder",
            this.shared ? "claude --continue · shared history" : "claude --continue · this account's history only", () => { ns.start = "continue"; }),
          ...(ns.resume ? [option("ns-start-resume", ns.start === "resume", `Resume ${ns.resume.slice(0, 8)}${ns.from ? " from " + ns.from : ""}`,
            "Exit the running session first, or both will write to one conversation", () => { ns.start = "resume"; })] : [])))
        .child(v_flex().px(20).py(12).gap(6)
          .child(h_flex().child(button("ns-launch", `Launch with ${target.label}`, (_e, cx) => this.launchNew(cx), "primary")))
          .child(div().text_size(11).text_color(t.dim).child(
            `claude-accounts launch ${ns.account}` + (ns.start === "continue" ? " -- --continue" : resuming ? ` -- --resume ${ns.resume}` : ""))));
    };

    // ---------- right: add an account ----------
    const addAccount = () => {
      const flow = this.addFlow;
      const step = flow ? flow.step : "name";
      const added = flow && this.account(flow.id);
      const steps = [["name", "Name"], ["browser", "Sign in"], ["done", "Ready"]];
      const reached = { name: 0, browser: 1, cancelled: 1, failed: 1, done: 2 }[step];
      return v_flex().id("add-account").flex_1().min_w(0).min_h(0).overflow_y_scrollbar()
        .child(v_flex().px(20).pt(16).pb(12).gap(8).border_b(1).border_color(t.border)
          .child(div().text_size(18).font_semibold().child("Add account"))
          .child(h_flex().gap(6).children(steps.map(([_k, label], i) => pill(`${i + 1} · ${label}`, i <= reached)))))
        .child(v_flex().px(20).py(14).gap(10)
          .when(step === "name", (el) => el
            .child(div().text_size(12).text_color(t.muted).child("Account name, as shown in this list"))
            .child(Input.new(this.name).w_full().h(36).px(10).bg(t.surface).border_1().border_color(t.border).rounded(6))
            .child(div().text_size(12).text_color(t.muted).child("A separate profile is created. Settings, skills and history stay shared; the sign-in is separate."))
            .child(h_flex().gap(8)
              .child(button("create-login", "Continue in browser", (_e, cx) => this.add(cx), "primary"))
              .child(button("cancel-add", "Cancel", (_e, cx) => { this.back(); cx.notify(); }, "ghost"))))
          .when(step === "browser", (el) => el
            .child(div().font_semibold().child("Waiting for browser sign-in…"))
            .child(div().text_size(12).text_color(t.muted).child(`A claude.com tab opened. Sign in with the account for “${flow.label}” and press Authorize. This waits up to 5 minutes.`))
            .child(h_flex().gap(8)
              .child(button("cancel-login", this.cancelling ? "Cancelling…" : "Cancel sign-in", (_e, cx) => this.cancelLogin(cx), "danger"))))
          .when(step === "cancelled" || step === "failed", (el) => el
            .child(div().font_semibold().child(step === "cancelled" ? "Sign-in cancelled" : "Sign-in did not finish"))
            .child(div().text_size(12).text_color(t.muted).child(`The profile “${flow.label}” is kept. Retry opens a fresh sign-in tab.`))
            .child(h_flex().gap(8)
              .child(button("retry-login", "Retry sign-in", (_e, cx) => this.login(flow.id, cx), "primary"))
              .child(button("add-done", "Later", (_e, cx) => { this.back(); cx.notify(); }, "ghost"))))
          .when(step === "done", (el) => el
            .child(div().font_semibold().child(`${flow.label} is signed in`))
            .child(div().text_size(12).text_color(t.muted).child(added ? this.describe(added) : ""))
            .child(h_flex().gap(8)
              .child(button("add-launch", "New session with it…", (_e, cx) => { this.startNew(flow.id); cx.notify(); }, "primary"))
              .when(this.selected !== flow.id, (row) => row.child(button("add-select", "Use for new sessions", (_e, cx) => this.choose(flow.id, cx))))
              .child(button("add-done", "Done", (_e, cx) => { this.back(); cx.notify(); }, "ghost")))));
    };

    // ---------- right: retained removed profiles ----------
    const removed = () => v_flex().id("removed-list").flex_1().min_w(0).min_h(0).overflow_y_scrollbar().px(20).py(16).gap(8)
      .child(h_flex().items_center().child(div().flex_1().text_size(18).font_semibold().child("Removed accounts"))
        .child(button("back", "Back", (_e, cx) => { this.back(); cx.notify(); }, "ghost")))
      .child(div().text_size(12).text_color(t.muted).child("Profiles stay on disk; restoring keeps their sign-in."))
      .when(this.removed.length === 0, (el) => el.child(div().text_color(t.muted).child("No retained removed profiles")))
      .children(this.removed.map((r) => h_flex().items_center().px(10).py(8).rounded(7).bg(t.surface)
        .child(div().flex_1().min_w(0).truncate().child(r.account.label))
        .child(button("restore-" + r.account.id, "Restore", (_e, cx) => this.restore(r.account.id, cx)))));

    return v_flex().id("claude-accounts").size_full().bg(t.popover).text_color(t.foreground).text_size(13).track_focus(this.handle)
      .on_key_down((e, cx) => {
        if (e.key === "escape" && pane !== "detail" && !this.loginAttempt) { this.back(); cx.stop_propagation(); cx.notify(); return; }
        if (pane !== "detail" || this.busy || this.pendingRemoval || this.pendingCheck) return;
        if (e.key === "down") { this.at = (this.at + 1) % rows.length; this.openSession = null; }
        else if (e.key === "up") { this.at = (this.at + rows.length - 1) % rows.length; this.openSession = null; }
        else if (e.key === "enter") this.choose(active.id, cx);
        else return;
        cx.stop_propagation(); cx.notify();
      })
      .child(h_flex().px(18).py(12).gap(10).items_center().border_b(1).border_color(t.border)
        .child(div().text_size(16).font_semibold().child("Claude Accounts"))
        .child(div().flex_1())
        .child(button("new-session", "+ New session", (_e, cx) => { this.startNew(); cx.notify(); }, "primary"))
        .child(button("close", "Close · Esc", () => closeOverlay(props().overlay), "ghost")))
      .child(h_flex().flex_1().min_h(0)
        .child(list)
        .child(pane === "removed" ? removed() : pane === "add" ? addAccount() : pane === "new" ? newSession() : detail()))
      .child(h_flex().px(18).py(7).gap(8).items_center().border_t(1).border_color(t.border).text_size(12)
        .child(div().flex_1().min_w(0).truncate().text_color(this.error ? t.blocked : t.muted).child(this.error || this.message))
        .when(Boolean(this.loginAttempt) && pane !== "add", (el) => el.child(button("cancel-login",
          this.cancelling ? "Cancelling…" : "Cancel sign-in", (_e, cx) => this.cancelLogin(cx), "danger"))));
  }
}
