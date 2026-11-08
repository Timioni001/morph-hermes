
  /* ── action dialog ───────────────────────────────────────────────────────
   * A row action still needs its parameters, and an operator should see what the
   * write will set before it is even proposed. This is that step; it ends in a
   * proposal, never in a write.
   */

  function ActionDialog(props) {
    var operation = props.request.operation;
    var values = useState({});
    var busy = useState(false);
    var error = useState("");
    var params = operation.params || [];

    function update(name, value) {
      var next = {};
      for (var key in values[0]) { next[key] = values[0][key]; }
      next[name] = value;
      values[1](next);
    }

    function submit(event) {
      event.preventDefault();
      error[1]("");
      busy[1](true);
      props.onPropose({
        source: props.request.entity.split(".")[0],
        entity: props.request.entity.split(".")[1],
        record_id: props.request.record_id,
        operation: operation.id,
        params: values[0],
        reason: "Proposed from the workspace"
      }).then(function () { props.onClose(); })
        .catch(function (err) { error[1](errorText(err)); })
        .then(function () { busy[1](false); });
    }

    return h("div", {
      style: {
        position: "fixed", inset: 0, zIndex: 70, display: "grid", placeItems: "center",
        background: "rgba(11,18,32,.34)", padding: "16px"
      },
      onClick: function (event) { if (event.target === event.currentTarget) { props.onClose(); } }
    },
      h("div", {
        className: "mo-panel", role: "dialog", "aria-modal": "true",
        "aria-label": operation.label,
        style: { width: "min(460px, 100%)", boxShadow: "var(--mo-shadow-lift)" }
      },
        h("div", { className: "mo-panel-head" },
          h("h2", { className: "mo-panel-title" }, operation.label),
          h(Button, { small: true, variant: "ghost", icon: "close", onClick: props.onClose },
            "Close")),
        h("form", { className: "mo-panel-body", onSubmit: submit },
          h("div", { className: "mo-target" }, h(Icon, { name: "pin", size: 14 }),
            props.request.entity, " · ", h("strong", null, props.request.record_id)),
          operation.description
            ? h("span", { className: "mo-hint" }, operation.description) : null,
          Object.keys(operation.effect || {}).length
            ? h("div", { className: "mo-diff" },
              Object.keys(operation.effect).map(function (field) {
                var promised = operation.effect[field];
                var isParam = typeof promised === "string" && /^\{[a-z_]+\}$/.test(promised);
                return h("div", { key: field },
                  h("div", { className: "mo-diff-field" }, titleCase(field)),
                  h("div", { className: "mo-field-value" },
                    isParam ? "from the field below" : show(promised)));
              }))
            : null,
          params.map(function (param) {
            var value = values[0][param.name] === undefined ? "" : values[0][param.name];
            var id = "mo-dialog-" + param.name;
            var control;
            if (param.values && param.values.length) {
              control = h("select", {
                className: "mo-select", id: id, value: value,
                onChange: function (e) { update(param.name, e.target.value); }
              }, [h("option", { key: "", value: "" }, "Choose…")].concat(
                param.values.map(function (option) {
                  return h("option", { key: option, value: option }, titleCase(option));
                })));
            } else if (["int", "float", "money"].indexOf(param.type) >= 0) {
              control = h("input", {
                className: "mo-input", type: "number", inputMode: "numeric", id: id,
                min: param.minimum, max: param.maximum, value: value,
                onChange: function (e) { update(param.name, e.target.value); }
              });
            } else {
              control = h("textarea", {
                className: "mo-textarea", id: id, value: value,
                maxLength: param.max_length || 400, placeholder: param.description || "",
                onChange: function (e) { update(param.name, e.target.value); }
              });
            }
            return h("div", { className: "mo-form-field", key: param.name },
              h("label", { htmlFor: id }, param.label || titleCase(param.name),
                param.required ? h("span", { className: "req" }, "*") : null),
              control);
          }),
          error[0] ? h("div", { className: "mo-error", role: "alert" }, error[0]) : null,
          h("div", { className: "mo-approval-actions" },
            h(Button, { type: "submit", variant: "primary", icon: "shield", busy: busy[0] },
              "Propose for approval"),
            h(Button, { variant: "ghost", onClick: props.onClose }, "Cancel")),
          h("span", { className: "mo-hint" },
            "This queues the action. You approve it in the panel beside the workspace."))));
  }

  /* ── the page ─────────────────────────────────────────────────────────────── */

  var SUGGESTIONS = [
    "Work through delayed orders and the complaints about them",
    "Show stock running low and the restocks I should request",
    "Escalated support cases and the orders behind them",
    "Shipments running late and who is waiting on them"
  ];

  function MorphPage() {
    var stateBox = useState(null);
    var loading = useState(true);
    var morphing = useState(false);
    var fatal = useState("");
    var goal = useState("");
    var toasts = useState([]);
    var dialog = useState(null);
    var selectionOverride = useState({});
    var mounted = useRef(true);

    var data = stateBox[0];

    var notify = useCallback(function (title, body, toneName) {
      var entry = { id: Math.random().toString(36).slice(2), title: title, body: body, tone: toneName };
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
      return function () { mounted.current = false; clearInterval(timer); };
    }, []);

    var workspace = data && data.workspace;
    var schema = (workspace && workspace.schema) || null;
    var blocks = (data && data.data && data.data.blocks) || {};
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
      return entry && entry.reason ? entry.reason : "";
    }

    function onSelect(entityRef, recordId) {
      var next = {};
      for (var key in selectionOverride[0]) { next[key] = selectionOverride[0][key]; }
      next[entityRef] = recordId;
      selectionOverride[1](next);
      var patch = { selection: {} };
      patch.selection[entityRef] = recordId;
      api.post("/context", patch).then(function () { return load(); })
        .catch(function (error) { notify("Could not keep that selection", errorText(error), "bad"); });
    }

    function compose(text) {
      var wanted = (text || goal[0] || "").trim();
      if (wanted.length < 3) { return; }
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
          (planner.notes || []).forEach(function (note) { notify("Planner note", note, ""); });
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
            ? item.operation_ref + " matches the record in " + item.operation_ref.split(".")[0]
            : (report.reason || "the record does not match what was promised"),
          report.verified ? "good" : "bad");
      }).catch(function (error) { notify("Could not verify", errorText(error), "bad"); });
    }

    function refreshSources() {
      api.post("/refresh", {}).then(function (result) {
        var changes = result.changes || [];
        if (!changes.length) {
          notify("No changes", "Every connected application matches the last read.", "");
        } else {
          notify("Sources moved", changes.map(function (change) {
            var counts = change.counts;
            return change.entity + ": +" + counts.added + " −" + counts.removed
              + " ~" + counts.updated;
          }).join(" · "), "good");
        }
        return load();
      }).catch(function (error) { notify("Refresh failed", errorText(error), "bad"); });
    }

    function restore(revision) {
      api.post("/revisions/" + revision + "/restore", {}).then(function () {
        notify("Revision restored", "Back to revision " + revision, "good");
        return load();
      }).catch(function (error) { notify("Could not restore", errorText(error), "bad"); });
    }

    /* ── render ─────────────────────────────────────────────────────────── */

    if (loading[0]) {
      return h("div", { className: "morph" },
        h("div", { className: "mo-head" },
          h("div", { className: "mo-title" },
            h("div", { className: "mo-mark" }, h(Icon, { name: "morph", size: 18 })),
            h("div", null, h("h1", { className: "mo-wordmark" }, "MORPH"),
              h("p", { className: "mo-tagline" }, "Software that adapts to you")))),
        h("div", { className: "mo-grid", style: { marginTop: "22px" } },
          [0, 1, 2, 3].map(function (index) {
            return h("div", { className: "mo-card mo-span-sm", key: index },
              h("div", { className: "mo-metric" },
                h("div", { className: "mo-skeleton", style: { height: "12px", width: "56%" } }),
                h("div", { className: "mo-skeleton", style: { height: "30px", width: "40%" } })));
          }),
          h("div", { className: "mo-card mo-span-full" },
            h("div", { style: { padding: "16px", display: "grid", gap: "9px" } },
              [0, 1, 2, 3, 4].map(function (index) {
                return h("div", {
                  className: "mo-skeleton", key: index,
                  style: { height: "15px", width: (95 - index * 7) + "%" }
                });
              })))));
    }

    var health = (data && data.health) || [];
    var reachable = health.filter(function (probe) { return probe.ok; }).length;

    return h("div", { className: "morph" },
      h("header", { className: "mo-head" },
        h("div", { className: "mo-title" },
          h("div", { className: "mo-mark" }, h(Icon, { name: "morph", size: 18 })),
          h("div", { style: { minWidth: 0 } },
            h("h1", { className: "mo-wordmark" }, "MORPH"),
            h("p", { className: "mo-tagline" },
              workspace && workspace.goal
                ? workspace.goal
                : "Software that adapts to you"))),
        h("div", { className: "mo-head-actions" },
          data && data.principal
            ? h("span", { className: "mo-tag info", title: "role: " + data.principal.role },
              h(Icon, { name: "shield", size: 13 }), data.principal.display)
            : null,
          h(Button, { icon: "refresh", onClick: refreshSources, small: true },
            "Re-read sources"),
          workspace
            ? h("span", { className: "mo-card-sub" },
              "revision " + workspace.revision + " · " + workspace.mode)
            : null)),

      fatal[0] ? h(Notice, { tone: "bad", title: "MORPH could not load" }, fatal[0]) : null,

      h("div", { className: "mo-goal" },
        h("label", { className: "mo-goal-label", htmlFor: "mo-goal-input" },
          "What do you need to get done?"),
        h("div", { className: "mo-goal-row" },
          h("input", {
            id: "mo-goal-input", className: "mo-goal-input", value: goal[0],
            placeholder: "e.g. work through delayed orders and the complaints about them",
            maxLength: 400, autoComplete: "off",
            onChange: function (event) { goal[1](event.target.value); },
            onKeyDown: function (event) { if (event.key === "Enter") { compose(); } }
          }),
          h(Button, {
            variant: "primary", icon: "spark", busy: morphing[0],
            disabled: goal[0].trim().length < 3, onClick: function () { compose(); }
          }, workspace ? "Transform workspace" : "Compose workspace")),
        h("div", { className: "mo-suggestions" }, SUGGESTIONS.map(function (suggestion) {
          return h("button", {
            type: "button", className: "mo-chip", key: suggestion,
            onClick: function () { goal[1](suggestion); compose(suggestion); }
          }, suggestion);
        })),
        h(SourceStrip, { health: health })),

      !reachable
        ? h(Notice, { tone: "bad", title: "No connected application is answering" },
          "MORPH composes from live application schemas, so it has nothing to build from. "
          + "Start the bundled demo services with `python -m apps.serve`, or point MORPH at your "
          + "own services (docs/CONNECTORS.md).")
        : null,

      data && data.data && data.data.degraded
        ? h(Notice, { tone: "warn", title: "Showing a partial workspace" },
          "Some blocks could not be resolved: "
          + ((data.data.failures || []).map(function (failure) {
            return failure.source + " (" + failure.error + ")";
          }).join("; ") || "one or more applications failed")
          + ". Nothing here is filled in from a cache.")
        : null,

      workspace && workspace.schema_error
        ? h(Notice, { tone: "warn", title: "This workspace no longer matches the applications" },
          workspace.schema_error + " — set a goal again to recompose it.")
        : null,

      h("div", { className: "mo-body" },
        h("main", null,
          morphing[0]
            ? h("div", { className: "mo-morph-banner" }, h(Spinner),
              "Rethinking the workspace for your new goal — your selection is carried over.")
            : null,
          schema
            ? h("div", {
              className: cx("mo-stage", morphing[0] ? "morphing" : ""),
              key: "rev-" + workspace.revision
            }, (schema.sections || []).map(function (section) {
              return h("section", { className: "mo-section", key: section.id },
                h("div", { className: "mo-section-head" },
                  h("h2", { className: "mo-section-title" }, section.title),
                  section.description
                    ? h("p", { className: "mo-section-note" }, section.description) : null),
                h("div", { className: "mo-grid" }, (section.blocks || []).map(function (block) {
                  return h(Block, {
                    key: block.id, block: block, data: blocks[block.id],
                    entityFields: entityFields[block.entity] || [],
                    selection: selection, allowed: allowed, denyReason: denyReason,
                    onSelect: onSelect, onPropose: propose,
                    onAct: function (entityRef, recordId, operation) {
                      if ((operation.params || []).length) {
                        dialog[1]({ entity: entityRef, record_id: recordId, operation: operation });
                      } else {
                        propose({
                          source: entityRef.split(".")[0], entity: entityRef.split(".")[1],
                          record_id: recordId, operation: operation.id, params: {},
                          reason: "Proposed from the workspace"
                        }).catch(function (error) {
                          notify("Could not propose that", errorText(error), "bad");
                        });
                      }
                    }
                  });
                })));
            }))
            : h(Empty, {
              icon: "spark", title: "No workspace yet",
              text: "Describe what you need above and MORPH will compose one from the "
                + "applications it can reach."
            })),

        h("aside", { className: "mo-rail" },
          h(ApprovalsPanel, {
            proposals: (data && data.proposals) || [],
            onApprove: approve, onReject: reject
          }),
          h(DecisionsPanel, {
            decisions: (data && data.recent_decisions) || [], onVerify: verify
          }),
          h(RevisionsPanel, {
            revisions: (data && data.revisions) || [],
            current: workspace && workspace.revision, onRestore: restore
          }),
          h(AuditPanel, {
            audit: (data && data.audit) || [], chain: (data && data.audit_chain) || {}
          }))),

      dialog[0]
        ? h(ActionDialog, {
          request: dialog[0], onPropose: propose,
          onClose: function () { dialog[1](null); }
        })
        : null,

      h(Toasts, { toasts: toasts[0] }));
  }

  REGISTRY.register("morph", MorphPage);
})();
