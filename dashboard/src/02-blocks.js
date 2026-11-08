
  /* ── block renderers ───────────────────────────────────────────────────────
   *
   * One renderer per block kind in morph.ui.v1. Each receives the validated block
   * description, the values the engine resolved for it, and the page callbacks.
   * A block that failed to resolve renders its own error and nothing else: a dead
   * application must not blank the workspace around it.
   */

  function fieldIndex(fields) {
    var index = {};
    (fields || []).forEach(function (field) { index[field.name] = field; });
    return index;
  }

  function BlockShell(props) {
    return h("section", { className: cx("mo-card", "mo-span-" + (props.span || "md")) },
      h("div", { className: "mo-card-head" },
        h("div", { style: { minWidth: 0 } },
          h("h3", { className: "mo-card-title" }, props.title),
          props.subtitle ? h("p", { className: "mo-card-sub" }, props.subtitle) : null),
        props.aside || null),
      h("div", { className: cx("mo-card-body", props.flush ? "flush" : "") }, props.children),
      props.footer || null);
  }

  function BlockError(props) {
    return h(BlockShell, { title: props.block.title, span: props.block.span },
      h(Notice, { tone: "bad", title: "This part could not be loaded" }, props.error));
  }

  function FilterSummary(props) {
    var filters = props.filters || [];
    if (!filters.length) { return null; }
    return h("p", { className: "mo-card-sub" }, filters.map(function (filter, index) {
      var value = Array.isArray(filter.value) ? filter.value.join(", ") : String(filter.value);
      return (index ? " · " : "") + titleCase(filter.field) + " " + filter.op + " " + value;
    }).join(""));
  }

  function MetricBlock(props) {
    var data = props.data || {};
    var value = data.value;
    var display = value === null || value === undefined
      ? "—"
      : formatNumber(value, data.aggregate === "count" ? 0 : 2);
    var toneClass = "";
    if (data.aggregate === "count" && typeof value === "number" && value > 0) {
      toneClass = props.block.trend_hint === "lower_is_better" ? "warn" : "";
    }
    return h("section", {
      className: cx("mo-card", "mo-span-" + (props.block.span || "sm"))
    }, h("div", { className: cx("mo-metric", props.block.emphasis ? "emphasis" : "") },
      h("div", { className: "mo-metric-label" },
        h(Icon, { name: data.aggregate === "count" ? "layers" : "chart", size: 14 }),
        props.block.title),
      h("div", { className: "mo-metric-value" },
        display,
        data.unit ? h("span", { className: "mo-metric-unit" }, data.unit) : null),
      h("div", { className: "mo-metric-foot" },
        props.block.description
          || (data.aggregate === "count"
            ? "records matching" + (props.block.filters && props.block.filters.length
              ? " " + props.block.filters.map(function (f) {
                return titleCase(f.field) + " " + f.op + " "
                  + (Array.isArray(f.value) ? f.value.join("/") : f.value);
              }).join(", ") : " this view")
            : data.aggregate + " over " + (data.sample_size || 0) + " record(s)")),
      toneClass && props.block.trend_hint === "lower_is_better"
        ? h("div", null, h(Tag, { tone: "warn" }, "needs attention")) : null));
  }

  function cellClass(field) {
    if (!field) { return ""; }
    if (field.role === "key") { return "key"; }
    if (["int", "float", "money"].indexOf(field.type) >= 0) { return "num"; }
    return "";
  }

  function Cell(props) {
    var field = props.field;
    var value = props.value;
    if (field && (field.type === "enum" || field.role === "status" || field.type === "bool")) {
      if (value === null || value === undefined || value === "") {
        return h("td", { className: "" }, "—");
      }
      return h("td", null, h(Tag, null, typeof value === "boolean"
        ? (value ? "Yes" : "No") : titleCase(value)));
    }
    return h("td", { className: cellClass(field) }, formatValue(value, field));
  }

  function TableBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var rows = data.rows || [];
    var fields = fieldIndex(props.entityFields);
    var actions = data.row_actions || [];
    var selected = props.selection[block.entity] || "";

    if (!rows.length) {
      return h(BlockShell, {
        title: block.title, span: block.span,
        subtitle: block.description || null
      }, h(Empty, {
        icon: "inbox",
        title: "Nothing matches this view",
        text: block.filters && block.filters.length
          ? "No record in " + block.entity + " matches the filters this workspace applies."
          : "There are no records in " + block.entity + " yet."
      }));
    }

    return h(BlockShell, {
      title: block.title,
      span: block.span,
      subtitle: block.description || null,
      aside: h("span", { className: "mo-card-sub" }, block.entity),
      flush: true,
      footer: h("div", { className: "mo-foot" },
        h("span", null, rows.length + " of " + (data.total || rows.length) + " record(s)"
          + (data.truncated ? " (truncated)" : "")),
        selected ? h("span", null, "selected: " + selected) : null)
    },
      h("div", { className: "mo-scroll" },
        h("table", { className: "mo-table" },
          h("thead", null, h("tr", null,
            block.columns.map(function (name) {
              var field = fields[name];
              return h("th", { key: name, scope: "col" },
                field ? field.label : titleCase(name));
            }),
            actions.length ? h("th", { key: "__act", scope: "col" }, "Action") : null)),
          h("tbody", null, rows.map(function (row) {
            var id = String(row[data.primary_key]);
            var isPicked = id === selected;
            return h("tr", {
              key: id,
              className: cx("pickable", isPicked ? "picked" : ""),
              tabIndex: 0,
              "aria-selected": isPicked,
              onClick: function () { props.onSelect(block.entity, id); },
              onKeyDown: function (event) {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  props.onSelect(block.entity, id);
                }
              }
            },
              block.columns.map(function (name) {
                return h(Cell, { key: name, field: fields[name], value: row[name] });
              }),
              actions.length ? h("td", { key: "__act", className: "actions" },
                h("div", { className: "mo-row-actions" }, actions.slice(0, 2).map(function (op) {
                  return h(Button, {
                    key: op.id, small: true, icon: "play",
                    disabled: !props.allowed(block.entity + "." + op.id),
                    title: props.allowed(block.entity + "." + op.id)
                      ? op.description : props.denyReason(block.entity + "." + op.id),
                    onClick: function (event) {
                      event.stopPropagation();
                      props.onAct(block.entity, id, op);
                    }
                  }, op.label);
                }))) : null);
          })))));
  }

  function BarChart(props) {
    var points = props.points || [];
    var max = points.reduce(function (acc, p) { return Math.max(acc, p.value || 0); }, 0) || 1;
    return h("div", { className: "mo-bars" }, points.map(function (point) {
      return h("div", { className: "mo-bar-row", key: point.label },
        h("div", { className: "mo-bar-label", title: point.label }, titleCase(point.label)),
        h("div", { className: "mo-bar-track" },
          h("div", {
            className: "mo-bar-fill",
            style: { width: Math.max(2, (point.value / max) * 100) + "%" }
          })),
        h("div", { className: "mo-bar-value" }, formatNumber(point.value)));
    }));
  }

  function LineChart(props) {
    var points = props.points || [];
    if (points.length < 2) { return h(BarChart, { points: points }); }
    var width = 100;
    var height = 42;
    var max = points.reduce(function (a, p) { return Math.max(a, p.value || 0); }, 0) || 1;
    var step = width / (points.length - 1);
    var coords = points.map(function (point, index) {
      return [index * step, height - (point.value / max) * (height - 6) - 3];
    });
    var line = coords.map(function (c, i) {
      return (i ? "L" : "M") + c[0].toFixed(2) + " " + c[1].toFixed(2);
    }).join(" ");
    var area = line + " L" + width + " " + height + " L0 " + height + " Z";
    return h("div", null,
      h("svg", {
        className: "mo-svg-chart", viewBox: "0 0 " + width + " " + height,
        preserveAspectRatio: "none", role: "img",
        "aria-label": props.label || "line chart"
      },
        h("defs", null, h("linearGradient", { id: "mo-area-grad", x1: "0", y1: "0", x2: "0", y2: "1" },
          h("stop", { offset: "0%", stopColor: "#1d4ed8", stopOpacity: "0.18" }),
          h("stop", { offset: "100%", stopColor: "#1d4ed8", stopOpacity: "0" }))),
        h("path", { className: "mo-line-area", d: area }),
        h("path", { className: "mo-line-path", d: line, vectorEffect: "non-scaling-stroke" })),
      h("div", { className: "mo-legend" }, points.map(function (point) {
        return h("span", { className: "mo-legend-item", key: point.label },
          titleCase(point.label), " ", h("strong", null, formatNumber(point.value)));
      })));
  }

  var DONUT_COLOURS = ["#1d4ed8", "#4f7dfb", "#7aa2fd", "#0e7490", "#0f766e", "#6366f1",
                       "#9aa3b2", "#c9d6fb"];

  function DonutChart(props) {
    var points = (props.points || []).filter(function (p) { return (p.value || 0) > 0; });
    var total = points.reduce(function (acc, p) { return acc + (p.value || 0); }, 0);
    if (!total) { return h(Empty, { title: "Nothing to chart", icon: "chart" }); }
    var radius = 38;
    var circumference = 2 * Math.PI * radius;
    var offset = 0;
    var segments = points.map(function (point, index) {
      var fraction = point.value / total;
      var segment = h("circle", {
        key: point.label, className: "mo-donut-seg", cx: 50, cy: 50, r: radius,
        stroke: DONUT_COLOURS[index % DONUT_COLOURS.length], strokeWidth: 15,
        strokeDasharray: (fraction * circumference) + " " + circumference,
        strokeDashoffset: -offset,
        transform: "rotate(-90 50 50)"
      });
      offset += fraction * circumference;
      return segment;
    });
    return h("div", null,
      h("svg", {
        className: "mo-svg-chart", viewBox: "0 0 100 100",
        style: { maxWidth: "190px", margin: "0 auto" }, role: "img",
        "aria-label": props.label || "donut chart"
      },
        h("circle", { className: "mo-donut-track", cx: 50, cy: 50, r: radius, strokeWidth: 15 }),
        segments,
        h("text", {
          x: 50, y: 50, textAnchor: "middle", dominantBaseline: "central",
          style: { fontSize: "13px", fontWeight: 650, fill: "#0b1220" }
        }, formatNumber(total, 0))),
      h("div", { className: "mo-legend" }, points.map(function (point, index) {
        return h("span", { className: "mo-legend-item", key: point.label },
          h("span", {
            className: "mo-legend-swatch",
            style: { background: DONUT_COLOURS[index % DONUT_COLOURS.length] }
          }),
          titleCase(point.label), " ", h("strong", null, formatNumber(point.value)));
      })));
  }

  function ChartBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var points = data.points || [];
    var subtitle = (data.aggregate === "count" ? "count" : data.aggregate + " of " + block.field)
      + " by " + titleCase(data.group_by);
    if (!points.length) {
      return h(BlockShell, { title: block.title, span: block.span, subtitle: subtitle },
        h(Empty, { icon: "chart", title: "No data to chart yet" }));
    }
    var chart = data.chart_type === "donut"
      ? h(DonutChart, { points: points, label: block.title })
      : (data.chart_type === "line"
        ? h(LineChart, { points: points, label: block.title })
        : h(BarChart, { points: points }));
    return h(BlockShell, {
      title: block.title, span: block.span, subtitle: subtitle,
      footer: h("div", { className: "mo-foot" },
        h("span", null, "over " + (data.sample_size || 0) + " record(s)"),
        h("span", null, block.entity))
    }, h("div", { className: "mo-chart" }, chart));
  }

  function DetailBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var fields = fieldIndex(data.fields);
    if (!data.record) {
      return h(BlockShell, { title: block.title, span: block.span },
        h(Empty, {
          icon: "pin", title: "Nothing selected",
          text: "Choose a row in a table above and its full record appears here, read live from "
            + block.entity.split(".")[0] + "."
        }));
    }
    return h(BlockShell, {
      title: block.title, span: block.span,
      aside: data.version ? h(Tag, { tone: "info" }, "v" + data.version) : null,
      subtitle: data.record_id ? block.entity + " · " + data.record_id : null
    }, h("div", { className: "mo-fields" }, block.fields.map(function (name) {
      var field = fields[name];
      var value = data.record[name];
      return h("div", { key: name },
        h("div", { className: "mo-field-label" }, field ? field.label : titleCase(name)),
        h("div", { className: "mo-field-value" },
          field && (field.type === "enum" || field.role === "status")
            ? h(Tag, null, value === null || value === undefined ? "—" : titleCase(value))
            : formatValue(value, field)));
    })));
  }

  function RelationBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var rows = data.rows || [];
    var fields = fieldIndex(props.entityFields);
    var selected = props.selection[block.entity] || "";
    var link = data.link || {};
    var aside = link.cross_application
      ? h("span", { className: "mo-link-note" }, h(Icon, { name: "link", size: 13 }),
        "joined across applications")
      : h("span", { className: "mo-card-sub" }, "joined");

    if (!rows.length) {
      return h(BlockShell, { title: block.title, span: "full", aside: aside },
        h(Empty, { icon: "link", title: "Nothing to join", text: link.from + " → " + link.to }));
    }
    return h(BlockShell, {
      title: block.title, span: "full", aside: aside,
      subtitle: (block.description ? block.description + " " : "") + link.from + " → " + link.to,
      flush: true,
      footer: h("div", { className: "mo-foot" },
        h("span", null, rows.length + " row(s), each with its related "
          + (data.related_label || "records")),
        h("span", null, link.name))
    },
      h("div", { className: "mo-scroll" },
        h("table", { className: "mo-table" },
          h("thead", null, h("tr", null,
            block.columns.map(function (name) {
              return h("th", { key: name, scope: "col" },
                fields[name] ? fields[name].label : titleCase(name));
            }),
            h("th", { scope: "col" }, data.related_label || "Related"))),
          h("tbody", null, rows.map(function (entry) {
            var id = String(entry.row[data.primary_key]);
            return h("tr", {
              key: id,
              className: cx("pickable", id === selected ? "picked" : ""),
              tabIndex: 0,
              onClick: function () { props.onSelect(block.entity, id); },
              onKeyDown: function (event) {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  props.onSelect(block.entity, id);
                }
              }
            },
              block.columns.map(function (name) {
                return h(Cell, { key: name, field: fields[name], value: entry.row[name] });
              }),
              h("td", null, entry.related && entry.related.length
                ? h("div", { className: "mo-rel-related" }, entry.related.map(function (rel, i) {
                  return h("span", { className: "mo-rel-chip", key: i },
                    (block.related_columns || []).map(function (name) {
                      var value = rel[name];
                      return value === null || value === undefined ? null : String(value);
                    }).filter(Boolean).join(" · "));
                }))
                : h("span", { className: "mo-rel-none" }, "none")));
          })))));
  }

  function FormBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var operation = data.operation || {};
    var params = data.params || [];
    var state = useState({});
    var values = state[0];
    var setValues = state[1];
    var busyState = useState(false);
    var errorState = useState("");
    var target = data.target;
    var allowed = props.allowed(block.entity + "." + block.operation);

    function update(name, value) {
      var next = {};
      for (var key in values) { next[key] = values[key]; }
      next[name] = value;
      setValues(next);
    }

    function submit(event) {
      event.preventDefault();
      if (!target) { return; }
      errorState[1]("");
      busyState[1](true);
      props.onPropose({
        source: block.entity.split(".")[0],
        entity: block.entity.split(".")[1],
        record_id: target.record_id,
        operation: block.operation,
        params: values,
        reason: "Proposed from the " + block.title + " form"
      }).then(function () {
        setValues({});
      }).catch(function (error) {
        errorState[1](errorText(error));
      }).then(function () { busyState[1](false); });
    }

    return h(BlockShell, {
      title: block.title, span: block.span,
      subtitle: operation.description || block.description || null,
      aside: operation.risk ? h(Tag, { tone: operation.risk === "high" ? "bad"
        : (operation.risk === "medium" ? "warn" : "") }, operation.risk + " risk") : null
    },
      h("form", { className: "mo-form", onSubmit: submit },
        target
          ? h("div", { className: "mo-target" }, h(Icon, { name: "pin", size: 14 }),
            "Target: ", h("strong", null, target.label), " (", target.record_id,
            target.version ? ", v" + target.version : "", ")")
          : h(Notice, { tone: "warn", icon: "pin" },
            "Select a row first — an action always targets one record."),
        params.map(function (param) {
          var value = values[param.name] === undefined ? "" : values[param.name];
          var control;
          if (param.values && param.values.length) {
            control = h("select", {
              className: "mo-select", id: "mo-" + block.id + "-" + param.name,
              value: value, onChange: function (e) { update(param.name, e.target.value); }
            }, [h("option", { key: "", value: "" }, "Choose…")].concat(
              param.values.map(function (option) {
                return h("option", { key: option, value: option }, titleCase(option));
              })));
          } else if (param.type === "int" || param.type === "float" || param.type === "money") {
            control = h("input", {
              className: "mo-input", type: "number", inputMode: "numeric",
              id: "mo-" + block.id + "-" + param.name,
              min: param.minimum, max: param.maximum, value: value,
              onChange: function (e) { update(param.name, e.target.value); }
            });
          } else if (param.type === "bool") {
            control = h("input", {
              type: "checkbox", id: "mo-" + block.id + "-" + param.name,
              checked: !!value, onChange: function (e) { update(param.name, e.target.checked); }
            });
          } else {
            control = h("textarea", {
              className: "mo-textarea", id: "mo-" + block.id + "-" + param.name,
              maxLength: param.max_length || 400, value: value,
              placeholder: param.description || "",
              onChange: function (e) { update(param.name, e.target.value); }
            });
          }
          return h("div", { className: "mo-form-field", key: param.name },
            h("label", { htmlFor: "mo-" + block.id + "-" + param.name },
              param.label || titleCase(param.name),
              param.required ? h("span", { className: "req", title: "required" }, "*") : null),
            control,
            param.description
              ? h("span", { className: "mo-hint" }, param.description) : null);
        }),
        errorState[0] ? h("div", { className: "mo-error", role: "alert" }, errorState[0]) : null,
        !allowed
          ? h(Notice, { tone: "warn", icon: "shield" },
            props.denyReason(block.entity + "." + block.operation)
              || "Your role may not run this action.")
          : null,
        h(Button, {
          type: "submit", variant: "primary", icon: "shield",
          busy: busyState[0], disabled: !target || !allowed
        }, "Propose: " + (block.submit_label || operation.label || "action")),
        h("span", { className: "mo-hint" },
          "Proposing does not change anything. It queues the action for approval, "
          + "and the approval is yours to give.")));
  }

  function ActionsBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var target = data.target;
    var operations = data.operations || [];
    return h(BlockShell, {
      title: block.title, span: block.span, subtitle: block.description || null
    },
      target
        ? h("div", { className: "mo-target", style: { marginBottom: "10px" } },
          h(Icon, { name: "pin", size: 14 }), "Target: ", h("strong", null, target.label))
        : h(Notice, { tone: "warn", icon: "pin" }, "Select a row to target these actions."),
      h("div", { className: "mo-approval-actions" }, operations.map(function (operation) {
        var ref = block.entity + "." + operation.id;
        var can = props.allowed(ref);
        return h(Button, {
          key: operation.id, icon: "play", disabled: !target || !can,
          title: can ? operation.description : props.denyReason(ref),
          onClick: function () { props.onAct(block.entity, target.record_id, operation); }
        }, operation.label);
      })),
      operations.some(function (o) { return !props.allowed(block.entity + "." + o.id); })
        ? h("span", { className: "mo-hint", style: { marginTop: "8px", display: "block" } },
          "Greyed-out actions are ones your role may not run.")
        : null);
  }

  function WorkflowBlock(props) {
    var steps = (props.data && props.data.steps) || props.block.steps || [];
    return h(BlockShell, {
      title: props.block.title, span: "full",
      aside: h("span", { className: "mo-card-sub" }, "how this workspace works")
    }, h("div", { className: "mo-steps" }, steps.map(function (step, index) {
      return h("div", { className: "mo-step", key: index },
        h("div", { className: "mo-step-n" }, index + 1),
        h("div", null,
          h("div", { className: "mo-step-title" }, step.title),
          step.detail ? h("div", { className: "mo-step-detail" }, step.detail) : null));
    })));
  }

  var RENDERERS = {
    metric: MetricBlock,
    table: TableBlock,
    chart: ChartBlock,
    detail: DetailBlock,
    relation: RelationBlock,
    form: FormBlock,
    actions: ActionsBlock,
    workflow: WorkflowBlock
  };

  function Block(props) {
    var data = props.data || {};
    if (data.error) { return h(BlockError, { block: props.block, error: data.error }); }
    var Renderer = RENDERERS[props.block.kind];
    if (!Renderer) {
      return h(BlockShell, { title: props.block.title, span: props.block.span },
        h(Empty, { title: "Unsupported block", text: props.block.kind }));
    }
    return h(Renderer, props);
  }
