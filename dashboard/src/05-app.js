
  /* ── the page ──────────────────────────────────────────────────────────────
   *
   * One component owns the state and the routing; the five views are pure. Polling
   * pauses while a dialog is open or the tab is hidden, because a workspace that
   * reshuffles under a half-filled form is worse than a slightly stale one.
   */

  var VIEWS = [
    { id: "workspace", label: "Workspace", icon: "layers",
      subtitle: "Composed from the applications MORPH can reach" },
    { id: "connections", label: "Connections", icon: "plug",
      subtitle: "What MORPH is connected to, and what it may do there" },
    { id: "approvals", label: "Approvals", icon: "shield",
      subtitle: "Actions waiting for a person" },
    { id: "activity", label: "Activity", icon: "pulse",
      subtitle: "What happened, with the evidence" },
    { id: "context", label: "Context", icon: "compass",
      subtitle: "The objective, how MORPH read it, and what carried over" }
  ];

  function LoadingShell() {
    return h("div", { className: "morph" }, h("div", { className: "mo-shell" }, [
      h("nav", { className: "mo-rail", key: "rail" }, [
        h("div", { className: "mo-mark", key: "m" }, h(Icon, { name: "morph", size: 18 })),
        VIEWS.map(function (view) {
          return h("div", { key: view.id, className: "mo-rail-btn" },
            h(Icon, { name: view.icon, size: 19 }));
        })
      ]),
      h("div", { className: "mo-main", key: "main" }, [
        h("div", { className: "mo-topbar", key: "top" },
          h("div", null, [
            h("h1", { className: "mo-view-title", key: "a" }, "MORPH"),
            h("p", { className: "mo-view-sub", key: "b" }, "Software that adapts to you")
          ])),
        h("div", { className: "mo-view", key: "v" },
          h("div", { className: "mo-grid" }, [
            [0, 1, 2, 3].map(function (index) {
              return h("div", { className: "mo-card mo-span-sm", key: "m" + index },
                h("div", { className: "mo-metric" }, [
                  h("div", {
                    key: "a", className: "mo-skeleton",
                    style: { height: "12px", width: "56%" }
                  }),
                  h("div", {
                    key: "b", className: "mo-skeleton",
                    style: { height: "32px", width: "40%" }
                  })
                ]));
            }),
            h("div", { className: "mo-card mo-span-full", key: "t" },
              h("div", { style: { padding: "16px", display: "grid", gap: "9px" } },
                [0, 1, 2, 3, 4].map(function (index) {
                  return h("div", {
                    key: index, className: "mo-skeleton",
                    style: { height: "15px", width: (95 - index * 7) + "%" }
                  });
                })))
          ]))
      ])
    ]));
  }

  /* Which theme the host is wearing.
   *
   * Hermes ships a dark shell, and the browser's own prefers-color-scheme says
   * nothing about that — a light-preference browser showing a dark Hermes left
   * MORPH as a white rectangle pasted into it. So read the colour the host
   * actually paints behind us, and match it.
   *
   * This only reads from the host and writes to MORPH's own root element. A
   * plugin that restyled the host's markup would be reaching into core UI.
   */
  function relativeLuminance(value) {
    if (!value) { return null; }
    try {
      var probe = document.createElement("span");
      probe.style.display = "none";
      probe.style.color = "";
      probe.style.color = value;
      if (!probe.style.color) { return null; }      // the browser refused it
      document.body.appendChild(probe);
      var resolved = getComputedStyle(probe).color;
      document.body.removeChild(probe);
      var parts = (resolved || "").match(/[\d.]+/g);
      if (!parts || parts.length < 3) { return null; }
      if (parts.length >= 4 && parseFloat(parts[3]) === 0) { return null; }  // transparent
      var channels = parts.slice(0, 3).map(function (n) {
        var c = parseFloat(n);
        if (c > 1) { c = c / 255; }
        c = Math.min(1, Math.max(0, c));
        return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
      });
      return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    } catch (err) {
      return null;
    }
  }

  function hostIsDark() {
    try {
      var styles = getComputedStyle(document.documentElement);
      var candidates = [
        (styles.getPropertyValue("--background") || "").trim(),
        getComputedStyle(document.body).backgroundColor,
        styles.backgroundColor
      ];
      for (var i = 0; i < candidates.length; i++) {
        var luminance = relativeLuminance(candidates[i]);
        if (luminance !== null) { return luminance < 0.4; }
      }
    } catch (err) { /* fall through to the browser preference */ }
    return !!(window.matchMedia
      && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function useHostTheme() {
    var theme = useState(null);
    useEffect(function () {
      function settle() { theme[1](hostIsDark() ? "dark" : "light"); }
      settle();
      var timer = setTimeout(settle, 400);      // the host may still be painting
      var media = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
      if (media && media.addEventListener) { media.addEventListener("change", settle); }
      return function () {
        clearTimeout(timer);
        if (media && media.removeEventListener) { media.removeEventListener("change", settle); }
      };
    }, []);
    return theme[0];
  }

  function MorphPage() {
    var hostTheme = useHostTheme();
    var stateBox = useState(null);
    var loading = useState(true);
    var morphing = useState(false);
    var fatal = useState("");
    var goal = useState("");
    var toasts = useState([]);
    var dialog = useState(null);
    var view = useState("workspace");
    var selectionOverride = useState({});
    var mounted = useRef(true);
    var goalRef = useRef(null);

    var data = stateBox[0];

    var notify = useCallback(function (title, body, toneName) {
      var entry = {
        id: Math.random().toString(36).slice(2), title: title, body: body, tone: toneName
      };
      toasts[1](function (list) { return list.concat([entry]); });
      setTimeout(function () {
        if (!mounted.current) { return; }
        toasts[1](function (list) {
          return list.filter(function (item) { return item.id !== entry.id; });
        });
      }, 7000);
    }, []);

    var load = useCallback(function (options) {
      var opts = options || {};
      return api.get("/state" + (opts.refresh ? "?refresh=true" : ""))
        .then(function (next) {
          if (!mounted.current) { return next; }
          stateBox[1](next);
          fatal[1]("");
          selectionOverride[1]({});
          return next;
        })
        .catch(function (error) {
          if (!mounted.current) { return null; }
          fatal[1](errorText(error));
          return null;
        })
        .then(function (value) {
          if (mounted.current) { loading[1](false); }
          return value;
        });
    }, []);

    useEffect(function () {
      mounted.current = true;
      load();
      var timer = setInterval(function () {
        if (document.hidden || dialog[0]) { return; }
        load();
      }, POLL_MS);
      function onKey(event) {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
          event.preventDefault();
          view[1]("workspace");
          setTimeout(function () {
            if (goalRef.current) { goalRef.current.focus(); goalRef.current.select(); }
          }, 20);
        }
        if (event.key === "Escape" && dialog[0]) { dialog[1](null); }
      }
      window.addEventListener("keydown", onKey);
      return function () {
        mounted.current = false;
        clearInterval(timer);
        window.removeEventListener("keydown", onKey);
      };
    }, []);

    var workspace = data && data.workspace;
    var permissions = useMemo(function () {
      var index = {};
      ((data && data.permissions) || []).forEach(function (entry) { index[entry.ref] = entry; });
      return index;
    }, [data]);

    var entityFields = useMemo(function () {
      var index = {};
      (((data && data.graph) || {}).sources || []).forEach(function (source) {
        (source.entities || []).forEach(function (entity) {
          index[source.id + "." + entity.name] = entity.fields || [];
        });
      });
      return index;
    }, [data]);

    var selection = useMemo(function () {
      var base = (workspace && workspace.context && workspace.context.selection) || {};
      var merged = {};
      for (var key in base) { merged[key] = base[key]; }
      for (var override in selectionOverride[0]) {
        merged[override] = selectionOverride[0][override];
      }
      return merged;
    }, [workspace, selectionOverride[0]]);

    function allowed(ref) {
      var entry = permissions[ref];
      return entry ? entry.allowed !== false : true;
    }

    function denyReason(ref) {
      var entry = permissions[ref];
      return (entry && entry.reason) ? entry.reason : "";
    }

    function onSelect(entityRef, recordId) {
      var next = {};
      for (var key in selectionOverride[0]) { next[key] = selectionOverride[0][key]; }
      next[entityRef] = recordId;
      selectionOverride[1](next);
      var patch = { selection: {} };
      patch.selection[entityRef] = recordId;
      api.post("/context", patch).then(function () { return load(); })
        .catch(function (error) {
          notify("Could not keep that selection", errorText(error), "bad");
        });
    }

    function clearContext(ref) {
      api.post("/context", { clear: [ref] }).then(function () { return load(); })
        .catch(function (error) { notify("Could not clear that", errorText(error), "bad"); });
    }

    function compose(text) {
      var wanted = (typeof text === "string" ? text : goal[0] || "").trim();
      if (wanted.length < 3) { return; }
      view[1]("workspace");
      morphing[1](true);
      api.post("/goal", { goal: wanted, preserve_context: true })
        .then(function (result) {
          goal[1]("");
          var planner = result.plan || {};
          var carried = (result.context_carried || []).length;
          notify("Workspace transformed",
            "Revision " + result.workspace.revision + " · planned "
            + (planner.mode === "deterministic" ? "deterministically" : "by the host model")
            + (carried ? " · carried " + carried + " piece(s) of your context" : ""),
            "good");
          (planner.notes || []).forEach(function (note) {
            notify("MORPH could not cover everything", note, "");
          });
          return load();
        })
        .catch(function (error) { notify("Could not compose that", errorText(error), "bad"); })
        .then(function () { if (mounted.current) { morphing[1](false); } });
    }

    function propose(body) {
      return api.post("/propose", body).then(function (proposal) {
        notify("Proposed, awaiting your approval",
          proposal.operation_ref + " on " + proposal.record_id, "");
        return load();
      });
    }

    function act(entityRef, recordId, operation) {
      if ((operation.params || []).length) {
        dialog[1]({ entity: entityRef, record_id: recordId, operation: operation });
        return;
      }
      propose({
        source: entityRef.split(".")[0], entity: entityRef.split(".")[1],
        record_id: recordId, operation: operation.id, params: {},
        reason: "Proposed from the workspace"
      }).catch(function (error) {
        notify("Could not propose that", errorText(error), "bad");
      });
    }

    function approve(proposal) {
      return api.post("/approve/" + proposal.id, { confirm_token: proposal.confirm_token })
        .then(function (result) {
          var verification = (result.result && result.result.verification) || {};
          notify(verification.verified ? "Applied and verified" : "Applied but NOT verified",
            proposal.operation_ref + " on " + proposal.record_id
            + (verification.verified
              ? " — re-read from " + proposal.source + " and it matches"
              : " — the read-back disagreed"),
            verification.verified ? "good" : "bad");
          return load({ refresh: true });
        })
        .catch(function (error) {
          var code = errorCode(error);
          notify(code === "stale_record" ? "The record moved" : "Not applied",
            errorText(error), "bad");
          return load().then(function () { throw error; });
        });
    }

    function reject(proposal) {
      return api.post("/reject/" + proposal.id, { reason: "Rejected in the dashboard" })
        .then(function () {
          notify("Rejected", "Nothing was changed in " + proposal.source, "");
          return load();
        });
    }

    function verify(item) {
      api.get("/verify/" + item.id).then(function (report) {
        notify(report.verified ? "Still verified" : "Verification failed",
          report.verified
            ? item.operation_ref + " matches the record in "
              + item.operation_ref.split(".")[0]
            : (report.reason || "the record does not match what was promised"),
          report.verified ? "good" : "bad");
      }).catch(function (error) { notify("Could not verify", errorText(error), "bad"); });
    }

    function refreshSources() {
      api.post("/refresh", {}).then(function (result) {
        var feed = result.source_feed || {};
        var fromFeed = feed.changes || [];
        if (fromFeed.length) {
          notify("Sources moved", fromFeed.slice(0, 4).map(function (change) {
            return change.entity_ref + " " + change.record_id
              + (change.actor ? " (" + change.actor + ")" : "");
          }).join(" · "), "good");
        } else if ((result.changes || []).length) {
          notify("Sources moved", (result.changes || []).map(function (change) {
            var counts = change.counts;
            return change.entity + ": +" + counts.added + " −" + counts.removed
              + " ~" + counts.updated;
          }).join(" · "), "good");
        } else {
          notify("No changes", "Every connected application matches the last read.", "");
        }
        return load();
      }).catch(function (error) { notify("Refresh failed", errorText(error), "bad"); });
    }

    function restore(revision) {
      api.post("/revisions/" + revision + "/restore", {}).then(function () {
        notify("Revision restored", "Back to revision " + revision, "good");
        view[1]("workspace");
        return load();
      }).catch(function (error) { notify("Could not restore", errorText(error), "bad"); });
    }

    if (loading[0]) { return h(LoadingShell); }

    var current = VIEWS.filter(function (entry) { return entry.id === view[0]; })[0] || VIEWS[0];
    var pendingCount = ((data && data.proposals) || []).length;
    var shared = {
      state: data, selection: selection, entityFields: entityFields,
      allowed: allowed, denyReason: denyReason, onSelect: onSelect, onPropose: propose,
      onAct: act, onApprove: approve, onReject: reject, onVerify: verify,
      onRestore: restore, onClearContext: clearContext, onNavigate: view[1]
    };

    var body;
    if (view[0] === "connections") { body = h(ConnectionsView, shared); }
    else if (view[0] === "approvals") { body = h(ApprovalsView, shared); }
    else if (view[0] === "activity") { body = h(ActivityView, shared); }
    else if (view[0] === "context") { body = h(ContextView, shared); }
    else {
      body = h(WorkspaceView, Object.assign({}, shared, {
        goal: goal[0], goalRef: goalRef, morphing: morphing[0],
        onGoalChange: goal[1], onCompose: compose
      }));
    }

    return h("div", { className: "morph", "data-mo-theme": hostTheme || null }, [
      h("div", { className: "mo-shell", key: "shell" }, [
        h("nav", { className: "mo-rail", key: "rail", "aria-label": "MORPH sections" }, [
          h("div", { className: "mo-mark", key: "mark" },
            h(Icon, { name: "morph", size: 18 })),
          VIEWS.map(function (entry) {
            return h("button", {
              key: entry.id, type: "button", className: "mo-rail-btn",
              "aria-current": view[0] === entry.id ? "page" : null,
              title: entry.label, "aria-label": entry.label,
              onClick: function () { view[1](entry.id); }
            }, [
              h(Icon, { key: "i", name: entry.icon, size: 19 }),
              (entry.id === "approvals" && pendingCount)
                ? h("span", { key: "b", className: "mo-rail-badge" }, pendingCount)
                : null
            ]);
          }),
          h("div", { className: "mo-rail-spacer", key: "sp" })
        ]),

        h("div", { className: "mo-main", key: "main" }, [
          h("header", { className: "mo-topbar", key: "top" }, [
            h("div", { key: "t", style: { minWidth: 0 } }, [
              h("h1", { className: "mo-view-title", key: "a" }, current.label),
              h("p", { className: "mo-view-sub", key: "b" },
                (view[0] === "workspace" && workspace && workspace.goal)
                  ? workspace.goal : current.subtitle)
            ]),
            h("div", { className: "mo-topbar-actions", key: "a" }, [
              (data && data.principal)
                ? h("span", {
                    key: "who", className: "mo-tag info",
                    title: "role: " + data.principal.role
                  }, [h(Icon, { name: "shield", size: 13, key: "i" }), data.principal.display])
                : null,
              h(Button, {
                key: "r", icon: "refresh", small: true, onClick: refreshSources
              }, "Re-read sources"),
              (workspace && view[0] === "workspace")
                ? h("span", { key: "rev", className: "mo-card-sub" },
                  "revision " + workspace.revision + " · " + workspace.mode)
                : null
            ])
          ]),

          h("div", { className: "mo-view", key: "view" }, [
            fatal[0]
              ? h(Notice, { key: "f", tone: "bad", title: "MORPH could not load" }, fatal[0])
              : null,
            body
          ])
        ])
      ]),

      dialog[0]
        ? h(ActionDialog, {
            key: "dialog", request: dialog[0], onPropose: propose,
            onClose: function () { dialog[1](null); }
          })
        : null,

      h(Toasts, { key: "toasts", toasts: toasts[0] })
    ]);
  }

  REGISTRY.register("morph", MorphPage);
})();
