(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;

  if (!SDK || !window.__HERMES_PLUGINS__) {
    return;
  }

  const React = SDK.React;
  const h = React.createElement;
  const API = "/api/plugins/morph";

  const fieldTitle = s =>
    String(s)
      .replace(/_/g, " ")
      .replace(/\b\w/g, m => m.toUpperCase());

  const fmt = v =>
    v == null
      ? "—"
      : typeof v === "boolean"
        ? v ? "Yes" : "No"
        : typeof v === "number"
          ? v.toLocaleString(undefined, {
              maximumFractionDigits: 2
            })
          : String(v);

  const actionBySource = {
    orders: [
      "flag_for_review",
      "mark_followed_up"
    ],
    support: [
      "escalate_case",
      "resolve_case"
    ],
    inventory: [
      "request_restock"
    ]
  };

  const call = (path, payload) =>
    SDK.fetchJSON(
      API + path,
      payload === undefined
        ? undefined
        : {
            method: "POST",
            headers: {
              "Content-Type": "application/json"
            },
            body: JSON.stringify(payload)
          }
    );

  function HeaderIcon({ type }) {
    const icons = {
      orders: "◈",
      support: "◎",
      inventory: "▣"
    };

    return h(
      "span",
      {
        className: "morph-icon",
        "aria-hidden": true
      },
      icons[type] || "✦"
    );
  }

  function Widget({ widget, run }) {
    if (widget.error) {
      return h(
        "section",
        {
          className: "morph-widget morph-error"
        },
        h("h3", null, widget.title),
        h("p", null, widget.error)
      );
    }

    if (widget.kind === "metric") {
      return h(
        "section",
        {
          className: "morph-widget morph-metric"
        },
        h(
          "div",
          { className: "morph-muted" },
          h(HeaderIcon, {
            type: widget.source
          }),
          widget.title
        ),
        h(
          "div",
          { className: "morph-stat" },
          fmt(widget.value)
        ),
        h(
          "span",
          { className: "morph-stat-caption" },
          "Live data · " + widget.source
        )
      );
    }

    return h(
      "section",
      {
        className:
          "morph-widget morph-table-card"
      },

      h(
        "div",
        {
          className: "morph-widget-heading"
        },
        h(
          "div",
          null,
          h("h3", null, widget.title),
          h(
            "p",
            null,
            widget.source +
              " · " +
              widget.rows.length +
              " records"
          )
        ),
        h("span", {
          className: "morph-dot",
          title: "Connected source"
        })
      ),

      h(
        "div",
        {
          className: "morph-table-scroll"
        },
        h(
          "table",
          {
            className: "morph-table"
          },

          h(
            "thead",
            null,
            h(
              "tr",
              null,

              ...widget.fields.map(f =>
                h(
                  "th",
                  { key: f },
                  fieldTitle(f)
                )
              ),

              ...(
                actionBySource[widget.source]
                  ? [
                      h(
                        "th",
                        { key: "actions" },
                        "Actions"
                      )
                    ]
                  : []
              )
            )
          ),

          h(
            "tbody",
            null,

            ...(
              widget.rows.length
                ? widget.rows.map((row, i) =>
                    h(
                      "tr",
                      { key: row.id || i },

                      ...widget.fields.map(f =>
                        h(
                          "td",
                          { key: f },

                          f === "status" ||
                          f === "priority"
                            ? h(
                                "span",
                                {
                                  className:
                                    "morph-pill morph-pill-" +
                                    String(
                                      row[f]
                                    ).toLowerCase()
                                },
                                fmt(row[f])
                              )
                            : fmt(row[f])
                        )
                      ),

                      ...(
                        actionBySource[widget.source]
                          ? [
                              h(
                                "td",
                                {
                                  key: "actions",
                                  className:
                                    "morph-row-actions"
                                },

                                h(
                                  "select",
                                  {
                                    "aria-label":
                                      "Propose action on " +
                                      row.id,
                                    defaultValue: "",

                                    onChange: e => {
                                      if (e.target.value) {
                                        run(
                                          "/propose",
                                          {
                                            source:
                                              widget.source,
                                            record_id:
                                              row.id,
                                            operation:
                                              e.target.value
                                          },
                                          "Action proposed. Review it below."
                                        ).then(() => {
                                          e.target.value = "";
                                        });
                                      }
                                    }
                                  },

                                  h(
                                    "option",
                                    { value: "" },
                                    "Choose action"
                                  ),

                                  ...actionBySource[
                                    widget.source
                                  ].map(a =>
                                    h(
                                      "option",
                                      {
                                        key: a,
                                        value: a
                                      },
                                      fieldTitle(a)
                                    )
                                  )
                                )
                              )
                            ]
                          : []
                      )
                    )
                  )
                : [
                    h(
                      "tr",
                      { key: "empty" },
                      h(
                        "td",
                        {
                          colSpan:
                            widget.fields.length +
                            1
                        },
                        "No matching records"
                      )
                    )
                  ]
            )
          )
        )
      )
    );
  }

  function MorphPage() {
    const [data, setData] =
      React.useState(null);

    const [goal, setGoal] =
      React.useState("");

    const [error, setError] =
      React.useState("");

    const [notice, setNotice] =
      React.useState("");

    const [busy, setBusy] =
      React.useState(false);

    const [panel, setPanel] =
      React.useState("workspace");

    const refresh = React.useCallback(
      async () => {
        try {
          const incoming =
            await call("/state");

          setData(incoming);
        } catch (e) {
          setError(
            String(e.message || e)
          );
        }
      },
      []
    );

    React.useEffect(() => {
      refresh();

      const timer = setInterval(
        refresh,
        6000
      );

      return () =>
        clearInterval(timer);
    }, [refresh]);

    async function run(
      path,
      payload,
      message
    ) {
      setBusy(true);
      setError("");
      setNotice("");

      try {
        await call(path, payload);

        setNotice(
          message ||
            "Changes saved and verified."
        );

        await refresh();
      } catch (e) {
        setError(
          String(e.message || e)
        );
      } finally {
        setBusy(false);
      }
    }

    async function transform(e) {
      e.preventDefault();

      if (!goal.trim()) {
        return;
      }

      await run(
        "/quick-transform",
        { goal },
        "Workspace transformed using local presets. For a generative design, ask Hermes to run morph_design."
      );
    }

    const pending =
      (data && data.pending) || [];

    const widgets =
      (data && data.widgets) || [];

    return h(
      "div",
      { className: "morph-root" },

      h(
        "header",
        { className: "morph-header" },

        h(
          "div",
          null,
          h(
            "div",
            { className: "morph-eyebrow" },
            "INTELLIGENT WORKSPACE / HERMES"
          ),
          h(
            "h1",
            null,
            "MORPH",
            h(
              "span",
              {
                className:
                  "morph-symbol"
              },
              "✳"
            )
          ),
          h(
            "p",
            {
              className:
                "morph-subtitle"
            },
            "Software that adapts to you."
          )
        ),

        h(
          "div",
          { className: "morph-live" },
          h("span", {
            className: "morph-dot"
          }),
          "Connected · Local demo + approved sources"
        )
      ),

      h(
        "section",
        {
          className: "morph-command"
        },

        h(
          "div",
          {
            className:
              "morph-command-title"
          },

          h("span", null, "✦"),

          h(
            "div",
            null,
            h(
              "strong",
              null,
              "What do you need to focus on?"
            ),
            h(
              "p",
              null,
              "Quick Transform uses built-in presets. Ask Hermes to run morph_design for AI-generated layouts."
            )
          )
        ),

        h(
          "form",
          {
            className:
              "morph-goal-form",
            onSubmit: transform
          },

          h("input", {
            "aria-label":
              "Workspace goal",
            value: goal,
            maxLength: 400,
            placeholder:
              "e.g. Focus on customers with delayed orders",
            onChange: e =>
              setGoal(e.target.value)
          }),

          h(
            "button",
            {
              disabled:
                busy || !goal.trim(),
              type: "submit",
              className:
                "morph-primary"
            },
            busy
              ? "Working…"
              : "Transform →"
          )
        ),

        h(
          "div",
          {
            className:
              "morph-presets"
          },

          ...[
            "Manage my business operations",
            "Focus on delayed customer orders",
            "Review inventory and restocking"
          ].map(s =>
            h(
              "button",
              {
                type: "button",
                key: s,
                disabled: busy,
                onClick: () => {
                  setGoal(s);

                  run(
                    "/quick-transform",
                    { goal: s },
                    "Workspace transformed using built-in presets."
                  );
                }
              },
              s
            )
          )
        )
      ),

      error &&
        h(
          "div",
          {
            role: "alert",
            className:
              "morph-alert morph-error"
          },
          error
        ),

      notice &&
        h(
          "div",
          {
            role: "status",
            className:
              "morph-alert morph-notice"
          },
          notice
        ),

      h(
        "div",
        {
          className:
            "morph-toolbar"
        },

        h(
          "div",
          {
            className:
              "morph-tabs"
          },

          ...[
            ["workspace", "Workspace"],
            ["approvals", "Approvals"],
            ["activity", "Activity"]
          ].map(([k, v]) =>
            h(
              "button",
              {
                key: k,
                className:
                  k === panel
                    ? "active"
                    : "",
                onClick: () =>
                  setPanel(k)
              },

              v,

              k === "approvals" &&
              pending.length
                ? h(
                    "span",
                    {
                      className:
                        "morph-count"
                    },
                    pending.length
                  )
                : null
            )
          )
        ),

        h(
          "div",
          {
            className:
              "morph-revision"
          },

          data
            ? "Version " +
                data.revision +
                " · " +
                widgets.length +
                " widgets"
            : "Loading…"
        )
      ),

      panel === "workspace" &&
        h(
          "div",
          null,

          data &&
            h(
              "div",
              {
                className:
                  "morph-section-heading"
              },

              h(
                "div",
                null,
                h(
                  "div",
                  {
                    className:
                      "morph-eyebrow"
                  },
                  "CURRENT OBJECTIVE"
                ),
                h(
                  "h2",
                  null,
                  data.goal
                )
              ),

              h(
                "span",
                {
                  className:
                    "morph-revision"
                },
                "Synced from the underlying records"
              )
            ),

          h(
            "div",
            {
              className:
                "morph-widgets"
            },

            ...widgets.map(w =>
              h(Widget, {
                key: w.id,
                widget: w,
                run
              })
            )
          ),

          !data &&
            h(
              "div",
              {
                className:
                  "morph-loading"
              },
              "Connecting to MORPH…"
            )
        ),

      panel === "approvals" &&
        h(
          "div",
          {
            className:
              "morph-approvals"
          },

          h(
            "h2",
            null,
            "Human-controlled actions"
          ),

          h(
            "p",
            {
              className:
                "morph-hint"
            },
            "Hermes can propose actions, but only authenticated dashboard interactions can approve them. Every execution is verified against the stored record."
          ),

          ...(
            pending.length
              ? pending.map(item =>
                  h(
                    "div",
                    {
                      key: item.id,
                      className:
                        "morph-approval"
                    },

                    h(
                      "div",
                      null,
                      h(
                        "strong",
                        null,
                        fieldTitle(
                          item.operation
                        )
                      ),
                      h(
                        "p",
                        null,
                        item.source +
                          " / " +
                          item.record_id
                      ),
                      h(
                        "small",
                        null,
                        "Pending · no change executed"
                      )
                    ),

                    h(
                      "div",
                      {
                        className:
                          "morph-approval-buttons"
                      },

                      h(
                        "button",
                        {
                          disabled: busy,
                          onClick: () =>
                            run(
                              "/reject/" +
                                encodeURIComponent(
                                  item.id
                                ),
                              {},
                              "Action rejected."
                            )
                        },
                        "Reject"
                      ),

                      h(
                        "button",
                        {
                          disabled: busy,
                          className:
                            "morph-primary",
                          onClick: () =>
                            run(
                              "/approve/" +
                                encodeURIComponent(
                                  item.id
                                ),
                              {},
                              "Approved and verified by readback."
                            )
                        },
                        "Approve and verify"
                      )
                    )
                  )
                )
              : [
                  h(
                    "div",
                    {
                      key: "empty",
                      className:
                        "morph-empty"
                    },
                    "No actions require your approval."
                  )
                ]
          )
        ),

      panel === "activity" &&
        h(
          "div",
          {
            className:
              "morph-approvals"
          },

          h(
            "h2",
            null,
            "Verified event history"
          ),

          ...(
            data &&
            data.events &&
            data.events.length
              ? data.events.map(
                  (ev, i) =>
                    h(
                      "div",
                      {
                        key: i,
                        className:
                          "morph-event"
                      },

                      h("span", {
                        className:
                          "morph-dot"
                      }),

                      h(
                        "div",
                        null,

                        h(
                          "strong",
                          null,
                          ev.detail
                        ),

                        h(
                          "small",
                          null,
                          new Date(
                            ev.happened_at *
                              1000
                          ).toLocaleString()
                        )
                      )
                    )
                )
              : [
                  h(
                    "div",
                    {
                      key: "none",
                      className:
                        "morph-empty"
                    },
                    "Actions appear here after readback verification."
                  )
                ]
          )
        ),

      h(
        "footer",
        {
          className:
            "morph-footer"
        },

        h(
          "span",
          null,
          "MORPH / v0.1 · Hermes plugin"
        ),

        h(
          "span",
          null,
          "Declarative UI · Audited actions · No injected executable code"
        )
      )
    );
  }

  window.__HERMES_PLUGINS__.register(
    "morph",
    MorphPage
  );
})();
