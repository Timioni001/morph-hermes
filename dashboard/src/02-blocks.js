
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
    return h("section", { className: cx("mo-card", "mo-span-" + (props.span || "md")) }, [
      h("div", { className: "mo-card-head", key: "head" }, [
        h("div", { key: "t", style: { minWidth: 0 } }, [
          h("h3", { className: "mo-card-title", key: "a" }, props.title),
          props.subtitle ? h("p", { className: "mo-card-sub", key: "b" }, props.subtitle) : null
        ]),
        props.aside || null
      ]),
      props.toolbar || null,
      h("div", {
        className: cx("mo-card-body", props.flush ? "flush" : ""), key: "body"
      }, props.children),
      props.footer || null
    ]);
  }

  function BlockError(props) {
    return h(BlockShell, { title: props.block.title, span: props.block.span },
      h(Notice, { tone: "bad", title: "This part could not be loaded" }, props.error));
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
        return h("td", null, "—");
      }
      return h("td", null, h(Tag, null, typeof value === "boolean"
        ? (value ? "Yes" : "No") : titleCase(value)));
    }
    return h("td", { className: cellClass(field) }, formatValue(value, field));
  }

  function RowActions(props) {
    if (!props.actions || !props.actions.length) { return null; }
    return h("div", { className: "mo-row-actions" }, props.actions.slice(0, 2).map(function (op) {
      var ref = props.entity + "." + op.id;
      var can = props.allowed(ref);
      return h(Button, {
        key: op.id, small: true, icon: "play", disabled: !can,
        title: can ? op.description : props.denyReason(ref),
        onClick: function (event) {
          event.stopPropagation();
          props.onAct(props.entity, props.recordId, op);
        }
      }, op.label);
    }));
  }

  /* ── metric ─────────────────────────────────────────────────────────────── */

  function MetricBlock(props) {
    var data = props.data || {};
    var value = data.value;
    var isCount = data.aggregate === "count";
    var display = value === null || value === undefined
      ? "—" : formatNumber(value, isCount ? 0 : 2);
    var narrowed = (props.block.filters || []).length > 0;
    // A share bar, only where it is a true statement: the filtered count against the
    // unfiltered total the same read reported. Never an invented trend line.
    var share = (isCount && narrowed && data.total > 0 && typeof value === "number")
      ? Math.max(2, Math.min(100, (value / data.total) * 100)) : 0;
    return h("section", {
      className: cx("mo-card", "mo-span-" + (props.block.span || "sm"))
    }, h("div", { className: cx("mo-metric", props.block.emphasis ? "emphasis" : "") }, [
      h("div", { className: "mo-metric-label", key: "l" }, [
        h(Icon, { name: isCount ? "layers" : "chart", size: 14, key: "i" }),
        props.block.title
      ]),
      h("div", { className: "mo-metric-value", key: "v" }, [
        display,
        data.unit ? h("span", { className: "mo-metric-unit", key: "u" }, data.unit) : null
      ]),
      share ? h("div", { className: "mo-share", key: "s" },
        h("span", { style: { width: share + "%" } })) : null,
      h("div", { className: "mo-metric-foot", key: "f" },
        props.block.description
          || (isCount
            ? (narrowed ? value + " of " + formatNumber(data.total, 0) + " match"
                        : "records in this view")
            : data.aggregate + " over " + (data.sample_size || 0) + " record(s)")),
      (narrowed && props.block.trend_hint === "lower_is_better" && value > 0)
        ? h("div", { key: "t" }, h(Tag, { tone: "warn" }, "needs attention")) : null
    ]));
  }

  /* ── table ──────────────────────────────────────────────────────────────── */

  function useTableView(rows, columns, block) {
    var query = useState("");
    var sort = useState(block.sort ? { field: block.sort.field, dir: block.sort.direction }
                                   : null);
    var view = useMemo(function () {
      var out = rows;
      var needle = query[0].trim().toLowerCase();
      if (needle) {
        out = out.filter(function (row) {
          return columns.some(function (name) {
            var value = row[name];
            return value !== null && value !== undefined
              && String(value).toLowerCase().indexOf(needle) >= 0;
          });
        });
      }
      if (sort[0]) {
        var key = sort[0].field;
        var factor = sort[0].dir === "desc" ? -1 : 1;
        out = out.slice().sort(function (a, b) {
          var x = a[key];
          var y = b[key];
          if (x === y) { return 0; }
          if (x === null || x === undefined) { return 1; }
          if (y === null || y === undefined) { return -1; }
          if (typeof x === "number" && typeof y === "number") { return (x - y) * factor; }
          return String(x).localeCompare(String(y)) * factor;
        });
      }
      return out;
    }, [rows, columns, query[0], sort[0]]);
    return { query: query, sort: sort, rows: view };
  }

  function TableBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var rows = data.rows || [];
    var fields = fieldIndex(props.entityFields);
    var actions = data.row_actions || [];
    var selected = props.selection[block.entity] || "";
    var view = useTableView(rows, block.columns, block);

    if (!rows.length) {
      return h(BlockShell, {
        title: block.title, span: block.span, subtitle: block.description || null
      }, h(Empty, {
        icon: "inbox", title: "Nothing matches this view",
        text: (block.filters && block.filters.length)
          ? "No record in " + block.entity + " matches the filters this workspace applies."
          : "There are no records in " + block.entity + " yet."
      }));
    }

    function header(name) {
      var field = fields[name];
      var active = view.sort[0] && view.sort[0].field === name;
      if (!block.sortable) {
        return h("th", { key: name, scope: "col" }, field ? field.label : titleCase(name));
      }
      return h("th", {
        key: name, scope: "col", className: "sortable",
        "aria-sort": active ? (view.sort[0].dir === "asc" ? "ascending" : "descending") : "none",
        onClick: function () {
          view.sort[1](active && view.sort[0].dir === "asc"
            ? { field: name, dir: "desc" } : { field: name, dir: "asc" });
        }
      }, [
        field ? field.label : titleCase(name),
        active ? h("span", { className: "mo-sort-mark", key: "m" },
          view.sort[0].dir === "asc" ? "▲" : "▼") : null
      ]);
    }

    var toolbar = block.searchable
      ? h("div", { className: "mo-toolbar" }, [
          h("input", {
            key: "q", className: "mo-search", type: "search",
            placeholder: "Filter these " + rows.length + " rows…",
            "aria-label": "Filter rows in " + block.title,
            value: view.query[0],
            onChange: function (event) { view.query[1](event.target.value); }
          }),
          view.query[0]
            ? h("span", { key: "n", className: "mo-hint" },
              view.rows.length + " of " + rows.length)
            : null
        ])
      : null;

    return h(BlockShell, {
      title: block.title, span: block.span, subtitle: block.description || null,
      aside: h("span", { className: "mo-card-sub" }, block.entity),
      toolbar: toolbar, flush: true,
      footer: h("div", { className: "mo-foot" }, [
        h("span", { key: "a" }, rows.length + " of " + (data.total || rows.length)
          + " record(s)" + (data.truncated ? " (truncated)" : "")),
        selected ? h("span", { key: "b" }, "selected: " + selected) : null
      ])
    }, h("div", { className: "mo-scroll" },
      h("table", { className: "mo-table" }, [
        h("thead", { key: "h" }, h("tr", null, [
          block.columns.map(header),
          actions.length ? h("th", { key: "__act", scope: "col" }, "Action") : null
        ])),
        h("tbody", { key: "b" }, view.rows.map(function (row) {
          var id = String(row[data.primary_key]);
          var isPicked = id === selected;
          return h("tr", {
            key: id, className: cx("pickable", isPicked ? "picked" : ""),
            tabIndex: 0, "aria-selected": isPicked,
            onClick: function () { props.onSelect(block.entity, id); },
            onKeyDown: function (event) {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                props.onSelect(block.entity, id);
              }
            }
          }, [
            block.columns.map(function (name) {
              return h(Cell, { key: name, field: fields[name], value: row[name] });
            }),
            actions.length
              ? h("td", { key: "__act", className: "actions" },
                h(RowActions, {
                  actions: actions, entity: block.entity, recordId: id,
                  allowed: props.allowed, denyReason: props.denyReason, onAct: props.onAct
                }))
              : null
          ]);
        }))
      ])));
  }

  /* ── board ──────────────────────────────────────────────────────────────── */

  function BoardBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var lanes = data.lanes || [];
    var fields = fieldIndex(props.entityFields);
    var selected = props.selection[block.entity] || "";
    var populated = lanes.filter(function (lane) { return lane.count > 0; });

    if (!populated.length) {
      return h(BlockShell, { title: block.title, span: "full" },
        h(Empty, { icon: "board", title: "Nothing on the board",
                   text: "No record in " + block.entity + " matches this view." }));
    }

    return h(BlockShell, {
      title: block.title, span: "full",
      subtitle: block.description || ("grouped by " + titleCase(data.group_by)),
      aside: h("span", { className: "mo-card-sub" }, block.entity), flush: true,
      footer: h("div", { className: "mo-foot" }, [
        h("span", { key: "a" }, data.total + " record(s) across "
          + populated.length + " state(s)"),
        selected ? h("span", { key: "b" }, "selected: " + selected) : null
      ])
    }, h("div", { className: "mo-board" }, populated.map(function (lane) {
      return h("div", { className: "mo-lane", key: lane.key }, [
        h("div", { className: "mo-lane-head", key: "h" }, [
          h("span", { className: "mo-lane-title", key: "t" }, lane.label),
          h("span", { className: "mo-lane-count", key: "c" }, lane.count)
        ]),
        h("div", { className: "mo-lane-cards", key: "c" },
          lane.cards.map(function (card) {
            var isPicked = card.id === selected;
            return h("div", { key: card.id }, [
              h("button", {
                key: "card", type: "button",
                className: cx("mo-bcard", isPicked ? "picked" : ""),
                "aria-pressed": isPicked,
                onClick: function () { props.onSelect(block.entity, card.id); }
              }, [
                h("div", { className: "mo-bcard-title", key: "t" }, card.title),
                h("div", { className: "mo-bcard-fields", key: "f" },
                  (block.columns || []).filter(function (name) {
                    return name !== data.title_field;
                  }).map(function (name) {
                    var field = fields[name];
                    return h("span", { className: "mo-bcard-field", key: name }, [
                      (field ? field.label : titleCase(name)) + " ",
                      h("b", { key: "v" }, formatValue(card.fields[name], field))
                    ]);
                  }))
              ]),
              isPicked
                ? h("div", { key: "a", style: { marginTop: "6px" } },
                  h(RowActions, {
                    actions: data.row_actions, entity: block.entity, recordId: card.id,
                    allowed: props.allowed, denyReason: props.denyReason, onAct: props.onAct
                  }))
                : null
            ]);
          })),
        lane.cards.length < lane.count
          ? h("div", { className: "mo-lane-empty", key: "m" },
            "+" + (lane.count - lane.cards.length) + " more")
          : null
      ]);
    })));
  }

  /* ── timeline ───────────────────────────────────────────────────────────── */

  function TimelineBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var items = data.items || [];
    var selected = props.selection[block.entity] || "";
    if (!items.length) {
      return h(BlockShell, { title: block.title, span: block.span },
        h(Empty, { icon: "clock", title: "Nothing on the timeline",
                   text: "No record in " + block.entity + " carries a "
                     + titleCase(data.time_field) + " yet." }));
    }
    return h(BlockShell, {
      title: block.title, span: block.span,
      subtitle: "by " + titleCase(data.time_field)
        + (data.undated ? " · " + data.undated + " without one" : ""),
      flush: true,
      footer: h("div", { className: "mo-foot" }, [
        h("span", { key: "a" }, items.length + " of " + data.total),
        h("span", { key: "b" }, block.entity)
      ])
    }, h("div", { className: "mo-timeline" }, items.map(function (item) {
      return h("div", { className: "mo-tl-item", key: item.id }, [
        h("div", { className: "mo-tl-when", key: "w" }, shortWhen(item.at)),
        h("div", { className: "mo-tl-rail", key: "r" },
          h("span", { className: "mo-tl-dot" })),
        h("div", { className: "mo-tl-body", key: "b" }, [
          h("button", {
            key: "l", type: "button",
            className: cx("mo-tl-label", item.id === selected ? "picked" : ""),
            onClick: function () { props.onSelect(block.entity, item.id); }
          }, item.label),
          item.status
            ? h("div", { key: "s", style: { marginTop: "3px" } },
              h(Tag, null, titleCase(item.status)))
            : null
        ])
      ]);
    })));
  }

  /* ── charts ─────────────────────────────────────────────────────────────── */

  function BarChart(props) {
    var points = props.points || [];
    var max = points.reduce(function (acc, p) { return Math.max(acc, p.value || 0); }, 0) || 1;
    return h("div", { className: "mo-bars" }, points.map(function (point) {
      return h("div", { className: "mo-bar-row", key: point.label }, [
        h("div", { className: "mo-bar-label", key: "l", title: point.label },
          titleCase(point.label)),
        h("div", { className: "mo-bar-track", key: "t" },
          h("div", {
            className: "mo-bar-fill",
            style: { width: Math.max(2, (point.value / max) * 100) + "%" }
          })),
        h("div", { className: "mo-bar-value", key: "v" }, formatNumber(point.value))
      ]);
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
    return h("div", null, [
      h("svg", {
        key: "svg", className: "mo-svg-chart",
        viewBox: "0 0 " + width + " " + height, preserveAspectRatio: "none",
        role: "img", "aria-label": props.label || "line chart"
      }, [
        h("defs", { key: "d" },
          h("linearGradient", { id: "mo-area-grad", x1: "0", y1: "0", x2: "0", y2: "1" }, [
            h("stop", { key: "a", offset: "0%", stopColor: "#1d4ed8", stopOpacity: "0.18" }),
            h("stop", { key: "b", offset: "100%", stopColor: "#1d4ed8", stopOpacity: "0" })
          ])),
        h("path", { key: "a", className: "mo-line-area", d: area }),
        h("path", {
          key: "l", className: "mo-line-path", d: line, vectorEffect: "non-scaling-stroke"
        })
      ]),
      h("div", { key: "lg", className: "mo-legend" }, points.map(function (point) {
        return h("span", { className: "mo-legend-item", key: point.label }, [
          titleCase(point.label), " ", h("strong", { key: "v" }, formatNumber(point.value))
        ]);
      }))
    ]);
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
        strokeDashoffset: -offset, transform: "rotate(-90 50 50)"
      });
      offset += fraction * circumference;
      return segment;
    });
    return h("div", null, [
      h("svg", {
        key: "svg", className: "mo-svg-chart", viewBox: "0 0 100 100",
        style: { maxWidth: "190px", margin: "0 auto" },
        role: "img", "aria-label": props.label || "donut chart"
      }, [
        h("circle", {
          key: "t", className: "mo-donut-track", cx: 50, cy: 50, r: radius, strokeWidth: 15
        }),
        segments,
        h("text", {
          key: "x", className: "mo-donut-total", x: 50, y: 50,
          textAnchor: "middle", dominantBaseline: "central"
        }, formatNumber(total, 0))
      ]),
      h("div", { key: "lg", className: "mo-legend" }, points.map(function (point, index) {
        return h("span", { className: "mo-legend-item", key: point.label }, [
          h("span", {
            key: "s", className: "mo-legend-swatch",
            style: { background: DONUT_COLOURS[index % DONUT_COLOURS.length] }
          }),
          titleCase(point.label), " ", h("strong", { key: "v" }, formatNumber(point.value))
        ]);
      }))
    ]);
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
      footer: h("div", { className: "mo-foot" }, [
        h("span", { key: "a" }, "over " + (data.sample_size || 0) + " record(s)"),
        h("span", { key: "b" }, block.entity)
      ])
    }, h("div", { className: "mo-chart" }, chart));
  }

  /* ── detail ─────────────────────────────────────────────────────────────── */

  function DetailBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var fields = fieldIndex(data.fields);
    if (!data.record) {
      return h(BlockShell, { title: block.title, span: block.span },
        h(Empty, {
          icon: "pin", title: "Nothing selected",
          text: "Choose a row above and its full record appears here, read live from "
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
      return h("div", { key: name }, [
        h("div", { className: "mo-field-label", key: "l" },
          field ? field.label : titleCase(name)),
        h("div", { className: "mo-field-value", key: "v" },
          (field && (field.type === "enum" || field.role === "status"))
            ? h(Tag, null, (value === null || value === undefined) ? "—" : titleCase(value))
            : formatValue(value, field))
      ]);
    })));
  }

  /* ── relation ───────────────────────────────────────────────────────────── */

  function RelationBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var rows = data.rows || [];
    var fields = fieldIndex(props.entityFields);
    var selected = props.selection[block.entity] || "";
    var link = data.link || {};
    var query = useState("");
    var aside = link.cross_application
      ? h("span", { className: "mo-link-note" }, [
          h(Icon, { name: "link", size: 13, key: "i" }), "joined across applications"
        ])
      : h("span", { className: "mo-card-sub" }, "joined");

    if (!rows.length) {
      return h(BlockShell, { title: block.title, span: "full", aside: aside },
        h(Empty, { icon: "link", title: "Nothing to join",
                   text: link.from + " → " + link.to }));
    }

    var needle = query[0].trim().toLowerCase();
    var view = !needle ? rows : rows.filter(function (entry) {
      var hay = JSON.stringify(entry.row) + JSON.stringify(entry.related || []);
      return hay.toLowerCase().indexOf(needle) >= 0;
    });

    return h(BlockShell, {
      title: block.title, span: "full", aside: aside,
      subtitle: (block.description ? block.description + " " : "") + link.from + " → " + link.to,
      toolbar: block.searchable ? h("div", { className: "mo-toolbar" },
        h("input", {
          className: "mo-search", type: "search", value: query[0],
          placeholder: "Filter these " + rows.length + " joined rows…",
          "aria-label": "Filter joined rows",
          onChange: function (event) { query[1](event.target.value); }
        })) : null,
      flush: true,
      footer: h("div", { className: "mo-foot" }, [
        h("span", { key: "a" }, view.length + " row(s), each with its related "
          + (data.related_label || "records")),
        h("span", { key: "b" }, link.name)
      ])
    }, h("div", { className: "mo-scroll" },
      h("table", { className: "mo-table" }, [
        h("thead", { key: "h" }, h("tr", null, [
          block.columns.map(function (name) {
            return h("th", { key: name, scope: "col" },
              fields[name] ? fields[name].label : titleCase(name));
          }),
          h("th", { key: "__rel", scope: "col" }, data.related_label || "Related")
        ])),
        h("tbody", { key: "b" }, view.map(function (entry) {
          var id = String(entry.row[data.primary_key]);
          return h("tr", {
            key: id, className: cx("pickable", id === selected ? "picked" : ""),
            tabIndex: 0,
            onClick: function () { props.onSelect(block.entity, id); },
            onKeyDown: function (event) {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                props.onSelect(block.entity, id);
              }
            }
          }, [
            block.columns.map(function (name) {
              return h(Cell, { key: name, field: fields[name], value: entry.row[name] });
            }),
            h("td", { key: "__rel" }, (entry.related && entry.related.length)
              ? h("div", { className: "mo-rel-related" }, entry.related.map(function (rel, i) {
                return h("span", { className: "mo-rel-chip", key: i },
                  (block.related_columns || []).map(function (name) {
                    var value = rel[name];
                    return (value === null || value === undefined) ? null : String(value);
                  }).filter(Boolean).join(" · "));
              }))
              : h("span", { className: "mo-rel-none" }, "none"))
          ]);
        }))
      ])));
  }

  /* ── form and actions ───────────────────────────────────────────────────── */

  function ParamField(props) {
    var param = props.param;
    var id = props.idPrefix + "-" + param.name;
    var value = props.value === undefined ? "" : props.value;
    var control;
    if (param.values && param.values.length) {
      control = h("select", {
        className: "mo-select", id: id, value: value,
        onChange: function (e) { props.onChange(param.name, e.target.value); }
      }, [h("option", { key: "", value: "" }, "Choose…")].concat(
        param.values.map(function (option) {
          return h("option", { key: option, value: option }, titleCase(option));
        })));
    } else if (["int", "float", "money"].indexOf(param.type) >= 0) {
      control = h("input", {
        className: "mo-input", type: "number", inputMode: "numeric", id: id,
        min: param.minimum, max: param.maximum, value: value,
        onChange: function (e) { props.onChange(param.name, e.target.value); }
      });
    } else if (param.type === "bool") {
      control = h("input", {
        type: "checkbox", id: id, checked: !!value,
        onChange: function (e) { props.onChange(param.name, e.target.checked); }
      });
    } else {
      control = h("textarea", {
        className: "mo-textarea", id: id, maxLength: param.max_length || 400,
        value: value, placeholder: param.description || "",
        onChange: function (e) { props.onChange(param.name, e.target.value); }
      });
    }
    return h("div", { className: "mo-form-field" }, [
      h("label", { key: "l", htmlFor: id }, [
        param.label || titleCase(param.name),
        param.required ? h("span", { key: "r", className: "req", title: "required" }, "*") : null
      ]),
      control,
      param.description && !(param.values || []).length
        ? h("span", { key: "h", className: "mo-hint" }, param.description) : null
    ]);
  }

  function FormBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var operation = data.operation || {};
    var params = data.params || [];
    var values = useState({});
    var busy = useState(false);
    var error = useState("");
    var target = data.target;
    var allowed = props.allowed(block.entity + "." + block.operation);

    function update(name, value) {
      var next = {};
      for (var key in values[0]) { next[key] = values[0][key]; }
      next[name] = value;
      values[1](next);
    }

    function submit(event) {
      event.preventDefault();
      if (!target) { return; }
      error[1]("");
      busy[1](true);
      props.onPropose({
        source: block.entity.split(".")[0],
        entity: block.entity.split(".")[1],
        record_id: target.record_id,
        operation: block.operation,
        params: values[0],
        reason: "Proposed from the " + block.title + " form"
      }).then(function () { values[1]({}); })
        .catch(function (err) { error[1](errorText(err)); })
        .then(function () { busy[1](false); });
    }

    return h(BlockShell, {
      title: block.title, span: block.span,
      subtitle: operation.description || block.description || null,
      aside: operation.risk
        ? h(Tag, {
            tone: operation.risk === "high" ? "bad" : (operation.risk === "medium" ? "warn" : "")
          }, operation.risk + " risk")
        : null
    }, h("form", { className: "mo-form", onSubmit: submit }, [
      target
        ? h("div", { className: "mo-target", key: "t" }, [
            h(Icon, { name: "pin", size: 14, key: "i" }), "Target: ",
            h("strong", { key: "s" }, target.label),
            " (" + target.record_id + (target.version ? ", v" + target.version : "") + ")"
          ])
        : h(Notice, { key: "t", tone: "warn", icon: "pin" },
          "Select a row first — an action always targets one record."),
      params.map(function (param) {
        return h(ParamField, {
          key: param.name, param: param, idPrefix: "mo-" + block.id,
          value: values[0][param.name], onChange: update
        });
      }),
      error[0] ? h("div", { key: "e", className: "mo-error", role: "alert" }, error[0]) : null,
      !allowed
        ? h(Notice, { key: "d", tone: "warn", icon: "shield" },
          props.denyReason(block.entity + "." + block.operation)
            || "Your role may not run this action.")
        : null,
      h(Button, {
        key: "s", type: "submit", variant: "primary", icon: "shield",
        busy: busy[0], disabled: !target || !allowed
      }, "Propose: " + (block.submit_label || operation.label || "action")),
      h("span", { key: "h", className: "mo-hint" },
        "Proposing does not change anything. It queues the action for approval, and the "
        + "approval is yours to give.")
    ]));
  }

  function ActionsBlock(props) {
    var block = props.block;
    var data = props.data || {};
    var target = data.target;
    var operations = data.operations || [];
    return h(BlockShell, {
      title: block.title, span: block.span, subtitle: block.description || null
    }, [
      target
        ? h("div", { key: "t", className: "mo-target", style: { marginBottom: "10px" } }, [
            h(Icon, { name: "pin", size: 14, key: "i" }), "Target: ",
            h("strong", { key: "s" }, target.label)
          ])
        : h(Notice, { key: "t", tone: "warn", icon: "pin" },
          "Select a row to target these actions."),
      h("div", { key: "a", className: "mo-approval-actions" }, operations.map(function (op) {
        var ref = block.entity + "." + op.id;
        var can = props.allowed(ref);
        return h(Button, {
          key: op.id, icon: "play", disabled: !target || !can,
          title: can ? op.description : props.denyReason(ref),
          onClick: function () { props.onAct(block.entity, target.record_id, op); }
        }, op.label);
      })),
      operations.some(function (o) { return !props.allowed(block.entity + "." + o.id); })
        ? h("span", {
            key: "h", className: "mo-hint", style: { marginTop: "8px", display: "block" }
          }, "Greyed-out actions are ones your role may not run.")
        : null
    ]);
  }

  function WorkflowBlock(props) {
    var steps = (props.data && props.data.steps) || props.block.steps || [];
    return h(BlockShell, {
      title: props.block.title, span: "full",
      aside: h("span", { className: "mo-card-sub" }, "how this workspace works")
    }, h("div", { className: "mo-steps" }, steps.map(function (step, index) {
      return h("div", { className: "mo-step", key: index }, [
        h("div", { className: "mo-step-n", key: "n" }, index + 1),
        h("div", { key: "b" }, [
          h("div", { className: "mo-step-title", key: "t" }, step.title),
          step.detail ? h("div", { className: "mo-step-detail", key: "d" }, step.detail) : null
        ])
      ]);
    })));
  }

  var RENDERERS = {
    metric: MetricBlock,
    table: TableBlock,
    board: BoardBlock,
    timeline: TimelineBlock,
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
