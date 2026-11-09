/* MORPH dashboard plugin — Hermes web dashboard extension.
 *
 * Contract (Hermes dashboard plugin docs): a plain IIFE loaded via <script>, which
 * takes React and the UI primitives from window.__HERMES_PLUGIN_SDK__ and registers
 * its page component on window.__HERMES_PLUGINS__. Nothing is bundled: no React, no
 * build toolchain, no runtime dependency to install.
 *
 * Rendering rule that is not negotiable: everything reaching the DOM goes through
 * React's text children. There is no innerHTML, no dangerouslySetInnerHTML, no
 * href built from data, and no eval anywhere in this file. A workspace is a
 * validated schema plus values read from connected applications; both are data, and
 * a renderer that cannot execute them is the only honest way to say so.
 *
 * Built from dashboard/src/*.js by scripts/build_dashboard.mjs — edit the sources.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var REGISTRY = window.__HERMES_PLUGINS__;
  if (!SDK || !SDK.React || !REGISTRY) { return; }

  var React = SDK.React;
  var h = React.createElement;
  var hooks = SDK.hooks || {};
  var useState = hooks.useState || React.useState;
  var useEffect = hooks.useEffect || React.useEffect;
  var useMemo = hooks.useMemo || React.useMemo;
  var useCallback = hooks.useCallback || React.useCallback;
  var useRef = hooks.useRef || React.useRef;

  var API = "/api/plugins/morph";
  var POLL_MS = 20000;

  /* ── transport ─────────────────────────────────────────────────────────── */

  function request(path, options) {
    var init = options || {};
    if (SDK.fetchJSON) { return SDK.fetchJSON(API + path, init); }
    return fetch(API + path, init).then(function (response) {
      if (!response.ok) { return response.json().then(function (b) { throw b; }); }
      return response.json();
    });
  }

  var api = {
    get: function (path) { return request(path); },
    post: function (path, body) {
      return request(path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // A cross-origin page cannot set this without a preflight the backend
          // never answers; the backend refuses any mutation without it.
          "X-MORPH-Request": "1"
        },
        body: JSON.stringify(body || {})
      });
    }
  };

  function errorText(error) {
    if (!error) { return "Something went wrong."; }
    var detail = error.detail !== undefined ? error.detail : error;
    if (detail && typeof detail === "object") {
      if (detail.error) { return String(detail.error); }
      if (detail.detail && detail.detail.error) { return String(detail.detail.error); }
      if (Array.isArray(detail)) { return detail.map(String).join("; "); }
    }
    if (typeof detail === "string" && detail) { return detail; }
    if (error.message) { return String(error.message); }
    return "Something went wrong.";
  }

  function errorCode(error) {
    var detail = error && (error.detail !== undefined ? error.detail : error);
    if (detail && typeof detail === "object" && detail.code) { return String(detail.code); }
    return "";
  }

  /* ── formatting ────────────────────────────────────────────────────────── */

  var TONE_GOOD = ["fulfilled", "delivered", "resolved", "paid", "done", "cleared", "ok",
                   "positive", "shipped", "in_transit", "true", "yes", "applied", "verified",
                   "won", "active", "healthy"];
  var TONE_WARN = ["pending", "processing", "placed", "open", "new", "preparing", "contacted",
                   "authorized", "normal", "medium", "neutral", "unverified", "applying",
                   "qualified"];
  var TONE_BAD = ["delayed", "cancelled", "failed", "exception", "escalated", "urgent", "high",
                  "flagged", "negative", "refunded", "stale", "expired", "rejected", "lost",
                  "blocked", "overdue"];

  function tone(value) {
    var text = String(value === null || value === undefined ? "" : value).toLowerCase();
    if (TONE_BAD.indexOf(text) >= 0) { return "bad"; }
    if (TONE_WARN.indexOf(text) >= 0) { return "warn"; }
    if (TONE_GOOD.indexOf(text) >= 0) { return "good"; }
    return "";
  }

  function titleCase(text) {
    return String(text === null || text === undefined ? "" : text)
      .replace(/_/g, " ")
      .replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function formatNumber(value, decimals) {
    try {
      return Number(value).toLocaleString(undefined, {
        maximumFractionDigits: decimals === undefined ? 2 : decimals
      });
    } catch (err) { return String(value); }
  }

  function formatValue(value, field) {
    if (value === null || value === undefined || value === "") { return "—"; }
    var type = field && field.type;
    if (typeof value === "boolean") { return value ? "Yes" : "No"; }
    if (type === "money") {
      return (field.unit ? field.unit + " " : "") + formatNumber(value, 2);
    }
    if (type === "datetime") { return formatWhen(value); }
    if (typeof value === "number") {
      return formatNumber(value) + (field && field.unit ? " " + field.unit : "");
    }
    var text = String(value);
    return text.length > 180 ? text.slice(0, 179) + "…" : text;
  }

  function formatWhen(value) {
    var date = new Date(value);
    if (isNaN(date.getTime())) { return String(value); }
    return date.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
    });
  }

  function shortWhen(value) {
    var date = new Date(value);
    if (isNaN(date.getTime())) { return String(value || ""); }
    return date.toLocaleString(undefined, { month: "short", day: "numeric",
                                            hour: "2-digit", minute: "2-digit" });
  }

  function timeAgo(epochSeconds) {
    if (!epochSeconds) { return ""; }
    var seconds = Math.max(0, Math.round(Date.now() / 1000 - epochSeconds));
    if (seconds < 60) { return seconds + "s ago"; }
    if (seconds < 3600) { return Math.round(seconds / 60) + "m ago"; }
    if (seconds < 86400) { return Math.round(seconds / 3600) + "h ago"; }
    return Math.round(seconds / 86400) + "d ago";
  }

  function show(value) {
    if (value === null || value === undefined || value === "") { return "—"; }
    if (typeof value === "boolean") { return value ? "Yes" : "No"; }
    var text = String(value);
    return text.length > 90 ? text.slice(0, 89) + "…" : text;
  }

  function cx() {
    var out = [];
    for (var i = 0; i < arguments.length; i += 1) {
      if (arguments[i]) { out.push(arguments[i]); }
    }
    return out.join(" ");
  }

  /* ── icons (inline SVG, no icon font, no network) ──────────────────────── */

  var PATHS = {
    morph: "M4 7h16M4 12h10M4 17h6",
    spark: "M12 3l1.7 4.6L18 9.3l-4.3 1.7L12 15.6l-1.7-4.6L6 9.3l4.3-1.7z",
    refresh: "M3 12a9 9 0 0115.5-6.4M21 12a9 9 0 01-15.5 6.4M18 3v4h-4M6 21v-4h4",
    check: "M4 12.5l5 5L20 6.5",
    close: "M6 6l12 12M18 6L6 18",
    alert: "M12 3l9 16H3zM12 9v5M12 17h.01",
    shield: "M12 3l8 3v6c0 4.4-3.3 8.2-8 9-4.7-.8-8-4.6-8-9V6z",
    layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
    link: "M9.5 14.5l5-5M8 11L6 13a3.5 3.5 0 005 5l2-2M16 13l2-2a3.5 3.5 0 00-5-5l-2 2",
    clock: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 7.5V12l3 2",
    table: "M3 5h18v14H3zM3 10h18M9 10v9",
    chart: "M4 20V9M10 20V4M16 20v-7M22 20H2",
    history: "M3 12a9 9 0 109-9 9 9 0 00-7.6 4.2M3 4v4h4M12 8v4.5l3.5 2",
    inbox: "M3 13l2.5-8h13L21 13v6H3zM3 13h6l1 2.5h4l1-2.5h6",
    search: "M10.5 17a6.5 6.5 0 100-13 6.5 6.5 0 000 13zM15.5 15.5L20 20",
    pin: "M12 3v8M12 11l4 4H8l4-4zM12 15v6",
    play: "M7 4l12 8-12 8z",
    plug: "M9 3v6M15 3v6M7 9h10v3a5 5 0 01-10 0zM12 17v4",
    board: "M4 4h5v16H4zM10 4h5v10h-5zM16 4h4v13h-4z",
    pulse: "M3 12h4l2.5-7 4 14L16.5 12H21",
    compass: "M12 3a9 9 0 100 18 9 9 0 000-18zM15.5 8.5l-2 5-5 2 2-5z",
    sliders: "M4 7h10M18 7h2M4 17h4M12 17h8M15 4v6M8 14v6",
    flow: "M5 4h5v5H5zM14 15h5v5h-5zM7.5 9v4.5a1.5 1.5 0 001.5 1.5h5",
    arrow: "M5 12h13M13 6l6 6-6 6"
  };

  function Icon(props) {
    var size = props.size || 16;
    return h("svg", {
      width: size, height: size, viewBox: "0 0 24 24", fill: "none",
      stroke: "currentColor", strokeWidth: props.weight || 1.7,
      strokeLinecap: "round", strokeLinejoin: "round",
      "aria-hidden": "true", focusable: "false", style: props.style
    }, h("path", { d: PATHS[props.name] || PATHS.morph }));
  }

  function Spinner() { return h("span", { className: "mo-spinner", role: "presentation" }); }

  function Tag(props) {
    return h("span", { className: cx("mo-tag", props.tone || tone(props.children)) },
      props.children);
  }

  function Empty(props) {
    return h("div", { className: "mo-empty" },
      h("div", { className: "mo-empty-icon" }, h(Icon, { name: props.icon || "search", size: 20 })),
      h("div", { className: "mo-empty-title" }, props.title),
      props.text ? h("div", { className: "mo-empty-text" }, props.text) : null,
      props.action || null);
  }

  function Notice(props) {
    return h("div", {
      className: cx("mo-notice", props.tone),
      role: props.tone === "bad" ? "alert" : null
    },
      h(Icon, { name: props.icon || "alert", size: 16 }),
      h("div", { style: { minWidth: 0 } },
        props.title ? h("div", { className: "mo-notice-title" }, props.title) : null,
        props.children));
  }

  function Button(props) {
    var rest = {};
    for (var key in props) {
      if (["className", "variant", "small", "icon", "children", "busy"].indexOf(key) < 0) {
        rest[key] = props[key];
      }
    }
    return h("button", Object.assign({
      type: props.type || "button",
      className: cx("mo-btn", props.variant, props.small ? "sm" : "", props.className),
      disabled: props.disabled || props.busy
    }, rest),
      props.busy ? h(Spinner) : (props.icon ? h(Icon, { name: props.icon, size: 15 }) : null),
      props.children);
  }

  function Panel(props) {
    return h("section", { className: "mo-panel" },
      h("div", { className: "mo-panel-head" },
        h("h2", { className: "mo-panel-title" }, props.title),
        props.aside || null),
      props.flush ? props.children
                  : h("div", { className: "mo-panel-body" }, props.children));
  }
