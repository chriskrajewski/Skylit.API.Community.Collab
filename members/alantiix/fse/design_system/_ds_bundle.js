/* @ds-bundle: {"format":4,"namespace":"FseDesignSystem_043819","components":[{"name":"Button","sourcePath":"components/actions/Button.jsx"},{"name":"IconButton","sourcePath":"components/actions/IconButton.jsx"},{"name":"Icon","sourcePath":"components/brand/Icon.jsx"},{"name":"Kbd","sourcePath":"components/brand/Kbd.jsx"},{"name":"PoweredBy","sourcePath":"components/brand/PoweredBy.jsx"},{"name":"StatusDot","sourcePath":"components/brand/StatusDot.jsx"},{"name":"Wordmark","sourcePath":"components/brand/Wordmark.jsx"},{"name":"Badge","sourcePath":"components/data/Badge.jsx"},{"name":"CandleChart","sourcePath":"components/data/CandleChart.jsx"},{"name":"DataTable","sourcePath":"components/data/DataTable.jsx"},{"name":"KeyValue","sourcePath":"components/data/KeyValue.jsx"},{"name":"LevelLadder","sourcePath":"components/data/LevelLadder.jsx"},{"name":"MeterBar","sourcePath":"components/data/MeterBar.jsx"},{"name":"Sparkline","sourcePath":"components/data/Sparkline.jsx"},{"name":"Stat","sourcePath":"components/data/Stat.jsx"},{"name":"FindingCard","sourcePath":"components/domain/FindingCard.jsx"},{"name":"Checkbox","sourcePath":"components/forms/Checkbox.jsx"},{"name":"Input","sourcePath":"components/forms/Input.jsx"},{"name":"Segmented","sourcePath":"components/forms/Segmented.jsx"},{"name":"Select","sourcePath":"components/forms/Select.jsx"},{"name":"Switch","sourcePath":"components/forms/Switch.jsx"},{"name":"Breadcrumbs","sourcePath":"components/navigation/Breadcrumbs.jsx"},{"name":"SideNav","sourcePath":"components/navigation/SideNav.jsx"},{"name":"Tabs","sourcePath":"components/navigation/Tabs.jsx"},{"name":"Banner","sourcePath":"components/surfaces/Banner.jsx"},{"name":"CodeBlock","sourcePath":"components/surfaces/CodeBlock.jsx"},{"name":"Dialog","sourcePath":"components/surfaces/Dialog.jsx"},{"name":"Panel","sourcePath":"components/surfaces/Panel.jsx"},{"name":"Toast","sourcePath":"components/surfaces/Toast.jsx"},{"name":"Tooltip","sourcePath":"components/surfaces/Tooltip.jsx"}],"sourceHashes":{"components/actions/Button.jsx":"44ac7318cc63","components/actions/IconButton.jsx":"0df4e1278c45","components/brand/Icon.jsx":"63612903dbd0","components/brand/Kbd.jsx":"1d92e900dd2a","components/brand/PoweredBy.jsx":"11ca68c070d1","components/brand/StatusDot.jsx":"32635295be70","components/brand/Wordmark.jsx":"262592a23a95","components/data/Badge.jsx":"74476e3cbed6","components/data/CandleChart.jsx":"f42fc881eb54","components/data/DataTable.jsx":"cfcb74b6d4af","components/data/KeyValue.jsx":"119729a3cfba","components/data/LevelLadder.jsx":"de473d5c8d5b","components/data/MeterBar.jsx":"d5b12f1de211","components/data/Sparkline.jsx":"562c0ea005f1","components/data/Stat.jsx":"5bbf4c085233","components/domain/FindingCard.jsx":"424bb1c4c203","components/forms/Checkbox.jsx":"987eb8f52fa1","components/forms/Input.jsx":"ff3b3d4d2261","components/forms/Segmented.jsx":"ff0f278cea4d","components/forms/Select.jsx":"a9160e32c53e","components/forms/Switch.jsx":"9215c628017d","components/navigation/Breadcrumbs.jsx":"c6dae3672ef7","components/navigation/SideNav.jsx":"609191b5860e","components/navigation/Tabs.jsx":"ab1ff9374038","components/surfaces/Banner.jsx":"9072b995ea72","components/surfaces/CodeBlock.jsx":"b853b58f6b6d","components/surfaces/Dialog.jsx":"b10c6428dba3","components/surfaces/Panel.jsx":"e275c0e1fb7d","components/surfaces/Toast.jsx":"71904ab8adc7","components/surfaces/Tooltip.jsx":"e98d2509ba97","ui_kits/console/CardsScreen.jsx":"f551d0c3d87b","ui_kits/console/ConfigScreen.jsx":"33a55ca64805","ui_kits/console/ExperimentsScreen.jsx":"04055452adfa","ui_kits/console/LiveScreen.jsx":"400a1139b561","ui_kits/console/MapScreen.jsx":"0c7f9fe16293","ui_kits/console/ReportScreen.jsx":"3a178086cbd8","ui_kits/console/RunsScreen.jsx":"a8831304ad5a","ui_kits/console/Shell.jsx":"94baf2bc95ec","ui_kits/console/data.js":"3db5ad3d9f0f"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.FseDesignSystem_043819 = window.FseDesignSystem_043819 || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/brand/Icon.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const BASE = 'https://unpkg.com/lucide-static@0.469.0/icons/';
const cache = {};
function load(name) {
  if (!cache[name]) cache[name] = fetch(BASE + name + '.svg').then(r => r.ok ? r.text() : '').then(t => t.replace(/<!--[\s\S]*?-->/g, '').replace(/width="24"|height="24"/g, '').replace('<svg', '<svg width="100%" height="100%"')).catch(() => '');
  return cache[name];
}
function Icon({
  name,
  size = 14,
  className,
  style,
  title,
  ...rest
}) {
  const [svg, setSvg] = React.useState(() => typeof cache[name] === 'string' ? cache[name] : '');
  React.useEffect(() => {
    let on = true;
    load(name).then(s => {
      if (on) setSvg(s);
    });
    return () => {
      on = false;
    };
  }, [name]);
  return /*#__PURE__*/React.createElement("span", _extends({
    role: title ? 'img' : undefined,
    "aria-label": title,
    "aria-hidden": title ? undefined : true,
    className: 'fse-icon' + (className ? ' ' + className : ''),
    style: {
      width: size,
      height: size,
      ...style
    },
    dangerouslySetInnerHTML: {
      __html: svg
    }
  }, rest));
}
Object.assign(__ds_scope, { Icon });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/brand/Icon.jsx", error: String((e && e.message) || e) }); }

// components/actions/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  iconRight,
  kbd,
  children,
  className,
  type = 'button',
  ...rest
}) {
  const is = size === 'sm' ? 12 : 14;
  return /*#__PURE__*/React.createElement("button", _extends({
    type: type,
    className: cx('fse-btn', 'fse-btn--' + variant, size !== 'md' && 'fse-btn--' + size, className)
  }, rest), icon && /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: is
  }), children, iconRight && /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: iconRight,
    size: is
  }), kbd && /*#__PURE__*/React.createElement("span", {
    className: "fse-btn__kbd"
  }, kbd));
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/actions/Button.jsx", error: String((e && e.message) || e) }); }

// components/actions/IconButton.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function IconButton({
  icon,
  label,
  variant = 'ghost',
  size = 'md',
  className,
  type = 'button',
  ...rest
}) {
  return /*#__PURE__*/React.createElement("button", _extends({
    type: type,
    "aria-label": label,
    title: label,
    className: cx('fse-btn', 'fse-btn--icon', 'fse-btn--' + variant, size !== 'md' && 'fse-btn--' + size, className)
  }, rest), /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: size === 'sm' ? 12 : size === 'lg' ? 16 : 14
  }));
}
Object.assign(__ds_scope, { IconButton });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/actions/IconButton.jsx", error: String((e && e.message) || e) }); }

// components/brand/Kbd.jsx
try { (() => {
function Kbd({
  children
}) {
  return /*#__PURE__*/React.createElement("kbd", {
    className: "fse-kbd"
  }, children);
}
Object.assign(__ds_scope, { Kbd });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/brand/Kbd.jsx", error: String((e && e.message) || e) }); }

// components/brand/PoweredBy.jsx
try { (() => {
function PoweredBy({
  label = 'Data powered by',
  suffix = 'API',
  logoSrc,
  href = 'https://www.skylit.ai/',
  size = 'sm',
  className
}) {
  const fs = size === 'md' ? 13 : 11,
    lh = size === 'md' ? 22 : 16;
  return /*#__PURE__*/React.createElement("a", {
    href: href,
    target: "_blank",
    rel: "noopener noreferrer",
    className: 'fse-powered' + (className ? ' ' + className : ''),
    style: {
      fontSize: fs
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-powered__label"
  }, label), logoSrc && /*#__PURE__*/React.createElement("img", {
    src: logoSrc,
    alt: "",
    "aria-hidden": "true",
    className: "fse-powered__logo",
    style: {
      height: lh,
      width: lh
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "fse-powered__name"
  }, "Skylit.ai"), suffix && /*#__PURE__*/React.createElement("span", {
    className: "fse-powered__label"
  }, suffix));
}
Object.assign(__ds_scope, { PoweredBy });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/brand/PoweredBy.jsx", error: String((e && e.message) || e) }); }

// components/brand/StatusDot.jsx
try { (() => {
function StatusDot({
  status = 'idle',
  label,
  className
}) {
  return /*#__PURE__*/React.createElement("span", {
    className: 'fse-dot fse-dot--' + status + (className ? ' ' + className : '')
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-dot__d"
  }), label);
}
Object.assign(__ds_scope, { StatusDot });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/brand/StatusDot.jsx", error: String((e && e.message) || e) }); }

// components/brand/Wordmark.jsx
try { (() => {
const S = {
  sm: [15, 11],
  md: [20, 12],
  lg: [32, 15],
  xl: [64, 22]
};
function Wordmark({
  size = 'md',
  lockup = true,
  stacked = false,
  className
}) {
  const [m, n] = S[size] || S.md;
  return /*#__PURE__*/React.createElement("span", {
    className: 'fse-wordmark' + (className ? ' ' + className : ''),
    "aria-label": "fse \u2014 futures strategy engine"
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-wordmark__mark",
    style: {
      fontSize: m
    }
  }, "fse", /*#__PURE__*/React.createElement("i", null, "_")), lockup && /*#__PURE__*/React.createElement("span", {
    className: "fse-wordmark__rule"
  }), lockup && /*#__PURE__*/React.createElement("span", {
    className: "fse-wordmark__name",
    style: {
      fontSize: n
    }
  }, stacked ? /*#__PURE__*/React.createElement(React.Fragment, null, "futures", /*#__PURE__*/React.createElement("br", null), "strategy engine") : 'futures strategy engine'));
}
Object.assign(__ds_scope, { Wordmark });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/brand/Wordmark.jsx", error: String((e && e.message) || e) }); }

// components/data/Badge.jsx
try { (() => {
const GRADE = {
  A_Plus: ['accent', 'A+'],
  Alert_2R: ['warning-outline', '2R alert'],
  Pass: ['neutral', 'Pass']
};
const MODE = {
  paper: ['outline', 'Paper'],
  practice: ['warning', 'Practice'],
  combine: ['danger', 'Combine']
};
const SIDE = {
  long: ['bull', 'Long'],
  short: ['bear', 'Short']
};
function Badge({
  variant = 'neutral',
  grade,
  mode,
  side,
  dot,
  children,
  className
}) {
  let v = variant,
    c = children;
  if (grade && GRADE[grade]) {
    v = GRADE[grade][0];
    c = c || GRADE[grade][1];
  }
  if (mode && MODE[mode]) {
    v = MODE[mode][0];
    c = c || MODE[mode][1];
  }
  if (side && SIDE[side]) {
    v = SIDE[side][0];
    c = c || SIDE[side][1];
  }
  return /*#__PURE__*/React.createElement("span", {
    className: 'fse-badge fse-badge--' + v + (className ? ' ' + className : '')
  }, dot && /*#__PURE__*/React.createElement("span", {
    className: "fse-badge__dot"
  }), c);
}
Object.assign(__ds_scope, { Badge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/Badge.jsx", error: String((e && e.message) || e) }); }

// components/data/CandleChart.jsx
try { (() => {
const LV = {
  king: 'var(--level-king)',
  ceiling: 'var(--level-ceiling)',
  floor: 'var(--level-floor)',
  gatekeeper: 'var(--fg-3)',
  air: 'var(--fg-4)',
  spot: 'var(--fg-1)'
};
function CandleChart({
  candles = [],
  levels = [],
  height = 240,
  axis = true,
  times,
  className
}) {
  const ref = React.useRef(null);
  const [w, setW] = React.useState(600);
  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(e => setW(Math.max(120, Math.floor(e[0].contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  if (!candles.length) return /*#__PURE__*/React.createElement("div", {
    ref: ref,
    className: "fse-candles",
    style: {
      height
    }
  });
  const ax = axis ? 64 : 0,
    pad = 8,
    cw = w - ax,
    chH = height - (times ? 18 : 0);
  let lo = Math.min(...candles.map(c => c.l)),
    hi = Math.max(...candles.map(c => c.h));
  levels.forEach(l => {
    lo = Math.min(lo, l.price);
    hi = Math.max(hi, l.price);
  });
  const span = hi - lo || 1;
  lo -= span * .04;
  hi += span * .04;
  const y = p => pad + (1 - (p - lo) / (hi - lo)) * (chH - pad * 2);
  const tagY = {};
  [...levels].map((l, i) => ({
    i,
    yy: y(l.price)
  })).sort((a, b) => a.yy - b.yy).reduce((prev, t) => {
    const v = Math.max(t.yy, prev + 15);
    tagY[t.i] = v;
    return v;
  }, -Infinity);
  const step = cw / candles.length,
    bw = Math.max(1, Math.min(9, step * .62));
  const ticks = [];
  const raw = (hi - lo) / 5,
    mag = Math.pow(10, Math.floor(Math.log10(raw))),
    st = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || raw;
  for (let t = Math.ceil(lo / st) * st; t <= hi; t += st) ticks.push(t);
  return /*#__PURE__*/React.createElement("div", {
    ref: ref,
    className: 'fse-candles' + (className ? ' ' + className : '')
  }, /*#__PURE__*/React.createElement("svg", {
    width: w,
    height: height
  }, ticks.map(t => /*#__PURE__*/React.createElement("g", {
    key: t
  }, /*#__PURE__*/React.createElement("line", {
    className: "fse-candles__grid",
    x1: "0",
    x2: cw,
    y1: y(t),
    y2: y(t)
  }), axis && /*#__PURE__*/React.createElement("text", {
    className: "fse-candles__axis",
    x: cw + 8,
    y: y(t) + 3
  }, t.toFixed(2)))), levels.map((l, i) => {
    const c = LV[l.kind] || 'var(--fg-2)';
    const yy = y(l.price);
    return /*#__PURE__*/React.createElement("g", {
      key: 'l' + i
    }, /*#__PURE__*/React.createElement("line", {
      x1: "0",
      x2: cw,
      y1: yy,
      y2: yy,
      stroke: c,
      strokeWidth: l.kind === 'king' ? 1.25 : 1,
      strokeDasharray: l.kind === 'king' || l.kind === 'spot' ? undefined : l.kind === 'air' ? '1 3' : '4 3',
      opacity: l.kind === 'gatekeeper' || l.kind === 'air' ? .8 : 1
    }), /*#__PURE__*/React.createElement("text", {
      x: 4,
      y: yy - 4,
      fill: c,
      style: {
        fontWeight: l.kind === 'king' ? 600 : 400,
        textTransform: 'uppercase',
        letterSpacing: '.06em'
      }
    }, l.label), axis && /*#__PURE__*/React.createElement("g", null, /*#__PURE__*/React.createElement("rect", {
      x: cw + 2,
      y: tagY[i] - 7,
      width: ax - 4,
      height: 14,
      rx: "2",
      fill: l.kind === 'king' ? 'var(--level-king)' : l.kind === 'spot' ? 'var(--fg-1)' : 'var(--surface-active)'
    }), /*#__PURE__*/React.createElement("text", {
      x: cw + 8,
      y: tagY[i] + 3,
      fill: l.kind === 'king' || l.kind === 'spot' ? 'var(--ink-0)' : 'var(--fg-1)',
      style: {
        fontWeight: 600
      }
    }, l.price.toFixed(2))));
  }), candles.map((c, i) => {
    const up = c.c >= c.o,
      x = i * step + step / 2,
      top = y(Math.max(c.o, c.c)),
      bot = y(Math.min(c.o, c.c));
    return /*#__PURE__*/React.createElement("g", {
      key: i
    }, /*#__PURE__*/React.createElement("line", {
      x1: x,
      x2: x,
      y1: y(c.h),
      y2: y(c.l),
      stroke: up ? 'var(--color-candle-up-wick)' : 'var(--color-candle-down-wick)',
      strokeWidth: "1"
    }), /*#__PURE__*/React.createElement("rect", {
      x: x - bw / 2,
      y: top,
      width: bw,
      height: Math.max(1, bot - top),
      fill: up ? 'var(--color-candle-up-body)' : 'var(--color-candle-down-body)',
      stroke: up ? 'var(--color-candle-up-border)' : 'var(--color-candle-down-border)',
      strokeWidth: ".75"
    }));
  }), times && times.map((t, i) => t && /*#__PURE__*/React.createElement("text", {
    key: 't' + i,
    className: "fse-candles__axis",
    x: i * step + step / 2,
    y: height - 4,
    textAnchor: "middle"
  }, t))));
}
Object.assign(__ds_scope, { CandleChart });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/CandleChart.jsx", error: String((e && e.message) || e) }); }

// components/data/DataTable.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function DataTable({
  columns = [],
  rows = [],
  rowKey = 'id',
  dense,
  onRowClick,
  selectedKey,
  className,
  maxHeight
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "fse-table-wrap",
    style: maxHeight ? {
      maxHeight
    } : undefined
  }, /*#__PURE__*/React.createElement("table", {
    className: cx('fse-table', dense && 'fse-table--dense', onRowClick && 'fse-table--hover', className)
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, columns.map(c => /*#__PURE__*/React.createElement("th", {
    key: c.key,
    className: c.align === 'right' ? 'is-num' : undefined,
    style: c.width ? {
      width: c.width
    } : undefined
  }, c.label)))), /*#__PURE__*/React.createElement("tbody", null, rows.map((r, i) => {
    const k = r[rowKey] ?? i;
    return /*#__PURE__*/React.createElement("tr", {
      key: k,
      className: k === selectedKey ? 'is-selected' : undefined,
      onClick: onRowClick ? () => onRowClick(r) : undefined
    }, columns.map(c => /*#__PURE__*/React.createElement("td", {
      key: c.key,
      className: cx(c.align === 'right' && 'is-num', c.muted && 'is-muted')
    }, c.render ? c.render(r[c.key], r) : r[c.key])));
  }))));
}
Object.assign(__ds_scope, { DataTable });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/DataTable.jsx", error: String((e && e.message) || e) }); }

// components/data/KeyValue.jsx
try { (() => {
function KeyValue({
  items = [],
  columns = 1,
  className
}) {
  return /*#__PURE__*/React.createElement("dl", {
    className: 'fse-kv' + (columns > 1 ? ' fse-kv--cols' : '') + (className ? ' ' + className : ''),
    style: columns > 1 ? {
      '--kv-cols': columns
    } : undefined
  }, items.map(([k, v], i) => /*#__PURE__*/React.createElement(React.Fragment, {
    key: i
  }, /*#__PURE__*/React.createElement("dt", null, k), /*#__PURE__*/React.createElement("dd", null, v))));
}
Object.assign(__ds_scope, { KeyValue });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/KeyValue.jsx", error: String((e && e.message) || e) }); }

// components/data/LevelLadder.jsx
try { (() => {
const KIND = {
  king: 'King',
  ceiling: 'Ceiling',
  floor: 'Floor',
  gatekeeper: 'Gatekeeper',
  air: 'Air pocket'
};
function LevelLadder({
  levels = [],
  spot,
  spotLabel = 'Spot',
  className
}) {
  const sorted = [...levels].sort((a, b) => b.level - a.level);
  const maxAbs = Math.max(1, ...levels.map(l => Math.abs(l.value || 0)));
  const rows = [];
  let placed = spot == null;
  const spotRow = /*#__PURE__*/React.createElement("div", {
    key: "spot",
    className: "fse-ladder__spot"
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-ladder__kind"
  }, spotLabel), /*#__PURE__*/React.createElement("span", {
    className: "fse-ladder__lvl"
  }, Number(spot).toFixed(2)), /*#__PURE__*/React.createElement("span", null), /*#__PURE__*/React.createElement("hr", null));
  sorted.forEach((l, i) => {
    if (!placed && l.level < spot) {
      rows.push(spotRow);
      placed = true;
    }
    const cls = l.kind === 'king' ? 'king' : l.kind === 'ceiling' ? 'ceiling' : l.kind === 'floor' ? 'floor' : l.kind === 'air' ? 'air' : 'gk';
    const w = Math.round(Math.abs(l.value || 0) / maxAbs * 100);
    rows.push(/*#__PURE__*/React.createElement("div", {
      key: i,
      className: 'fse-ladder__row fse-ladder__row--' + cls
    }, /*#__PURE__*/React.createElement("span", {
      className: "fse-ladder__kind"
    }, l.label || KIND[l.kind] || l.kind), /*#__PURE__*/React.createElement("span", {
      className: "fse-ladder__lvl"
    }, Number(l.level).toFixed(2)), /*#__PURE__*/React.createElement("span", {
      className: "fse-ladder__strike"
    }, l.strike), /*#__PURE__*/React.createElement("span", {
      className: "fse-ladder__bar"
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: Math.max(w, 2) + '%'
      }
    }))));
  });
  if (!placed) rows.push(spotRow);
  return /*#__PURE__*/React.createElement("div", {
    className: 'fse-ladder' + (className ? ' ' + className : '')
  }, rows);
}
Object.assign(__ds_scope, { LevelLadder });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/LevelLadder.jsx", error: String((e && e.message) || e) }); }

// components/data/MeterBar.jsx
try { (() => {
function MeterBar({
  label,
  value,
  max = 100,
  display,
  tone = 'ink',
  labelWidth,
  className
}) {
  const pct = Math.max(0, Math.min(100, value / (max || 1) * 100));
  return /*#__PURE__*/React.createElement("div", {
    className: 'fse-meter' + (tone !== 'ink' ? ' fse-meter--' + tone : '') + (className ? ' ' + className : ''),
    style: labelWidth ? {
      '--meter-label': typeof labelWidth === 'number' ? labelWidth + 'px' : labelWidth
    } : undefined
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-meter__label",
    title: typeof label === 'string' ? label : undefined
  }, label), /*#__PURE__*/React.createElement("span", {
    className: "fse-meter__track"
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-meter__fill",
    style: {
      width: pct + '%',
      display: 'block'
    }
  })), /*#__PURE__*/React.createElement("span", {
    className: "fse-meter__value"
  }, display ?? value));
}
Object.assign(__ds_scope, { MeterBar });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/MeterBar.jsx", error: String((e && e.message) || e) }); }

// components/data/Sparkline.jsx
try { (() => {
function Sparkline({
  values = [],
  width = 120,
  height = 28,
  tone = 'accent',
  baseline = true,
  fill = false,
  responsive = false,
  className
}) {
  if (!values.length) return null;
  const min = Math.min(0, ...values),
    max = Math.max(0, ...values),
    span = max - min || 1;
  const x = i => i / Math.max(1, values.length - 1) * width,
    y = v => height - 1 - (v - min) / span * (height - 2);
  const d = values.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ');
  const t = tone === 'auto' ? values[values.length - 1] >= 0 ? 'bull' : 'bear' : tone;
  const col = {
    accent: 'var(--accent)',
    bull: 'var(--color-bull)',
    bear: 'var(--color-bear)',
    muted: 'var(--fg-3)',
    ink: 'var(--fg-1)'
  }[t] || 'var(--fg-1)';
  return /*#__PURE__*/React.createElement("svg", {
    className: className,
    width: responsive ? '100%' : width,
    height: height,
    viewBox: '0 0 ' + width + ' ' + height,
    preserveAspectRatio: responsive ? 'none' : undefined,
    style: {
      display: 'block',
      overflow: 'visible'
    }
  }, baseline && /*#__PURE__*/React.createElement("line", {
    x1: "0",
    x2: width,
    y1: y(0),
    y2: y(0),
    stroke: "var(--line-2)",
    strokeDasharray: "2 2",
    vectorEffect: "non-scaling-stroke"
  }), fill && /*#__PURE__*/React.createElement("path", {
    d: d + ' L' + width + ' ' + y(0) + ' L0 ' + y(0) + 'Z',
    fill: col,
    opacity: ".12"
  }), /*#__PURE__*/React.createElement("path", {
    d: d,
    fill: "none",
    stroke: col,
    strokeWidth: "1.25",
    strokeLinejoin: "round",
    vectorEffect: "non-scaling-stroke"
  }));
}
Object.assign(__ds_scope, { Sparkline });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/Sparkline.jsx", error: String((e && e.message) || e) }); }

// components/data/Stat.jsx
try { (() => {
const cx = (...a) => a.filter(Boolean).join(' ');
function Stat({
  label,
  value,
  unit,
  sub,
  tone,
  size = 'md',
  className
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: cx('fse-stat', size !== 'md' && 'fse-stat--' + size, className)
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-stat__label"
  }, label), /*#__PURE__*/React.createElement("span", {
    className: cx('fse-stat__value', tone && 'fse-' + tone)
  }, value, unit && /*#__PURE__*/React.createElement("span", {
    className: "fse-stat__unit"
  }, unit)), sub && /*#__PURE__*/React.createElement("span", {
    className: "fse-stat__sub"
  }, sub));
}
Object.assign(__ds_scope, { Stat });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/Stat.jsx", error: String((e && e.message) || e) }); }

// components/domain/FindingCard.jsx
try { (() => {
const GRADE = {
  A_Plus: 'A+',
  Alert_2R: '2R',
  Pass: 'Pass'
};
const LV = {
  king: 'var(--level-king)',
  ceiling: 'var(--level-ceiling)',
  floor: 'var(--level-floor)',
  gatekeeper: 'var(--level-gatekeeper)',
  wall: 'var(--level-wall)',
  air: 'var(--level-air)'
};
const fx = (n, d = 2) => n == null ? '—' : Number(n).toFixed(d);
function useWidth() {
  const ref = React.useRef(null),
    [w, setW] = React.useState(640);
  React.useLayoutEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(e => setW(Math.max(320, Math.round(e[0].contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}
function TradeChart({
  t
}) {
  const [ref, W] = useWidth();
  const H = 372,
    G = 156,
    PW = W - G,
    short = t.direction === 'short';
  const bars = t.bars || [],
    tg = t.targets || [];
  const plan = [t.entry, t.stop, t.spot, ...tg.map(x => x.price)];
  const lo0 = Math.min(...plan, ...bars.map(b => b.l)),
    hi0 = Math.max(...plan, ...bars.map(b => b.h));
  const pad = (hi0 - lo0) * 0.12,
    lo = lo0 - pad,
    hi = hi0 + pad;
  const inRange = l => l.to != null ? Math.max(l.price, l.to) >= lo && Math.min(l.price, l.to) <= hi : l.price >= lo && l.price <= hi;
  const lvls = (t.levels || []).filter(inRange),
    off = (t.levels || []).filter(l => !inRange(l));
  const y = p => 14 + (hi - p) / (hi - lo) * (H - 28);
  const split = PW * 0.5,
    cw = split / Math.max(bars.length, 1);
  const near = p => plan.some(q => Math.abs(q - p) < (hi - lo) * 0.012);
  const xs = [split, PW * 0.62, PW * 0.74, PW * 0.86];
  const pts = [[xs[0], t.spot], [xs[1], t.entry], ...tg.map((x, i) => [xs[2 + i] ?? PW * 0.9, x.price])];
  const pct = p => (p - t.spot) / t.spot * 100;
  // gutter labels, de-overlapped
  const gut = [{
    k: 'stop',
    label: 'STOP',
    p: t.stop,
    c: 'var(--color-bear)'
  }, {
    k: 'entry',
    label: 'ENTRY',
    p: t.entry,
    c: 'var(--color-ice)'
  }, {
    k: 'now',
    label: 'NOW',
    p: t.spot,
    c: 'var(--text-primary)'
  }, ...tg.map(x => ({
    k: x.label,
    label: x.label,
    p: x.price,
    c: 'var(--color-bull)'
  }))].map(g => ({
    ...g,
    y: y(g.p)
  })).sort((a, b) => a.y - b.y);
  for (let i = 1; i < gut.length; i++) if (gut[i].y - gut[i - 1].y < 26) gut[i].y = gut[i - 1].y + 26;
  const lines = [{
    p: t.stop,
    c: 'var(--color-bear)',
    dash: '5 4',
    label: 'STOP'
  }, {
    p: t.entry,
    c: 'var(--color-ice)',
    label: 'ENTRY'
  }, ...tg.map(x => ({
    p: x.price,
    c: 'var(--color-bull)',
    label: x.label
  }))];
  return /*#__PURE__*/React.createElement("div", {
    ref: ref,
    className: "fse-tcard__chart"
  }, /*#__PURE__*/React.createElement("svg", {
    width: W,
    height: H,
    viewBox: `0 0 ${W} ${H}`,
    role: "img",
    "aria-label": `${t.instrument} ${t.direction} plan chart`
  }, /*#__PURE__*/React.createElement("defs", null, /*#__PURE__*/React.createElement("linearGradient", {
    id: "fse-tc-proj",
    x1: "0",
    x2: "1"
  }, /*#__PURE__*/React.createElement("stop", {
    offset: "0",
    stopColor: "var(--color-ice)",
    stopOpacity: "0"
  }), /*#__PURE__*/React.createElement("stop", {
    offset: "1",
    stopColor: "var(--color-ice)",
    stopOpacity: ".05"
  }))), /*#__PURE__*/React.createElement("rect", {
    x: split,
    y: "0",
    width: PW - split,
    height: H,
    fill: "url(#fse-tc-proj)"
  }), /*#__PURE__*/React.createElement("line", {
    x1: split,
    x2: split,
    y1: "0",
    y2: H,
    stroke: "var(--border-default)",
    strokeDasharray: "2 3"
  }), /*#__PURE__*/React.createElement("text", {
    x: split + 6,
    y: H - 6,
    className: "fse-tcard__ax"
  }, "projected \xB7 \u2460 fill \u2192 \u2461 ", tg[0] ? tg[0].label : '', tg[1] ? ' → ③ ' + tg[1].label : ''), /*#__PURE__*/React.createElement("text", {
    x: 4,
    y: H - 6,
    className: "fse-tcard__ax"
  }, "last ", bars.length, " \xD7 1-min"), lvls.map((l, i) => l.to != null ? /*#__PURE__*/React.createElement("g", {
    key: i
  }, /*#__PURE__*/React.createElement("rect", {
    x: "0",
    width: PW,
    y: y(Math.max(l.price, l.to)),
    height: Math.abs(y(l.price) - y(l.to)),
    fill: "var(--text-primary)",
    opacity: ".035"
  }), /*#__PURE__*/React.createElement("text", {
    x: 4,
    y: y(Math.max(l.price, l.to)) + 11,
    className: "fse-tcard__lv",
    fill: "var(--text-tertiary)"
  }, (l.label || 'AIR POCKET').toUpperCase(), " ", fx(l.price, 0), "\u2013", fx(l.to, 0))) : /*#__PURE__*/React.createElement("g", {
    key: i
  }, !near(l.price) && /*#__PURE__*/React.createElement("line", {
    x1: "0",
    x2: PW,
    y1: y(l.price),
    y2: y(l.price),
    stroke: LV[l.kind] || 'var(--text-tertiary)',
    strokeOpacity: ".45",
    strokeDasharray: l.kind === 'gatekeeper' ? '1 3' : '3 4'
  }), /*#__PURE__*/React.createElement("text", {
    x: 4,
    y: y(l.price) - 4,
    className: "fse-tcard__lv",
    fill: LV[l.kind] || 'var(--text-tertiary)'
  }, (l.label || l.kind).toUpperCase(), " ", fx(l.price, 0)))), bars.map((b, i) => {
    const up = b.c >= b.o,
      x = i * cw + cw / 2,
      bw = Math.max(2, cw * 0.6);
    return /*#__PURE__*/React.createElement("g", {
      key: i
    }, /*#__PURE__*/React.createElement("line", {
      x1: x,
      x2: x,
      y1: y(b.h),
      y2: y(b.l),
      stroke: up ? 'var(--color-candle-up-wick)' : 'var(--color-candle-down-wick)',
      strokeOpacity: up ? 1 : .55
    }), /*#__PURE__*/React.createElement("rect", {
      x: x - bw / 2,
      y: y(Math.max(b.o, b.c)),
      width: bw,
      height: Math.max(1, Math.abs(y(b.o) - y(b.c))),
      fill: up ? 'var(--color-candle-up-body)' : 'var(--color-candle-down-body)',
      stroke: up ? 'var(--color-candle-up-border)' : 'none',
      strokeWidth: ".5"
    }));
  }), lines.map((l, i) => /*#__PURE__*/React.createElement("g", {
    key: i
  }, /*#__PURE__*/React.createElement("line", {
    x1: i === 0 ? 0 : split,
    x2: PW,
    y1: y(l.p),
    y2: y(l.p),
    stroke: l.c,
    strokeWidth: l.dash ? 1 : 1.25,
    strokeDasharray: l.dash
  }), /*#__PURE__*/React.createElement("text", {
    x: PW - 4,
    y: y(l.p) - 5,
    textAnchor: "end",
    className: "fse-tcard__pl",
    fill: l.c
  }, l.label))), /*#__PURE__*/React.createElement("line", {
    x1: "0",
    x2: PW,
    y1: y(t.spot),
    y2: y(t.spot),
    stroke: "var(--text-primary)",
    strokeOpacity: ".5",
    strokeWidth: ".75"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: pts.slice(0, 2).map(p => `${p[0]},${y(p[1])}`).join(' '),
    fill: "none",
    stroke: "var(--text-secondary)",
    strokeWidth: "1.5",
    strokeDasharray: "4 4"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: pts.slice(1).map(p => `${p[0]},${y(p[1])}`).join(' '),
    fill: "none",
    stroke: "var(--color-bull)",
    strokeWidth: "6",
    strokeOpacity: ".12",
    strokeLinejoin: "round"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: pts.slice(1).map(p => `${p[0]},${y(p[1])}`).join(' '),
    fill: "none",
    stroke: "var(--color-bull)",
    strokeWidth: "2",
    strokeLinejoin: "round"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: pts[0][0],
    cy: y(t.spot),
    r: "4",
    fill: "var(--text-primary)"
  }), pts.slice(1).map((p, i) => {
    const c = i === 0 ? 'var(--color-ice)' : 'var(--color-bull)';
    return /*#__PURE__*/React.createElement("g", {
      key: i
    }, /*#__PURE__*/React.createElement("circle", {
      cx: p[0],
      cy: y(p[1]),
      r: "9",
      fill: "var(--surface-panel)",
      stroke: c,
      strokeWidth: "1.5"
    }), /*#__PURE__*/React.createElement("text", {
      x: p[0],
      y: y(p[1]) + 3.5,
      textAnchor: "middle",
      className: "fse-tcard__num",
      fill: c
    }, i + 1));
  }), /*#__PURE__*/React.createElement("line", {
    x1: PW,
    x2: PW,
    y1: "0",
    y2: H,
    stroke: "var(--border-subtle)"
  }), gut.map((g, i) => /*#__PURE__*/React.createElement("g", {
    key: i
  }, /*#__PURE__*/React.createElement("text", {
    x: PW + 10,
    y: g.y + 4,
    className: "fse-tcard__gp"
  }, g.k === 'now' ? '' : (pct(g.p) >= 0 ? '+' : '') + pct(g.p).toFixed(2) + '%'), /*#__PURE__*/React.createElement("text", {
    x: W - 2,
    y: g.y + 5,
    textAnchor: "end",
    className: "fse-tcard__gv",
    fill: g.c
  }, fx(g.p))))), off.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__off"
  }, off.map((l, i) => /*#__PURE__*/React.createElement("span", {
    key: i
  }, /*#__PURE__*/React.createElement("b", {
    style: {
      color: LV[l.kind]
    }
  }, l.label || l.kind), " ", fx(l.price), " ", l.price > hi ? '↑' : '↓')), /*#__PURE__*/React.createElement("span", null, "off scale")));
}
function Ring({
  pct,
  label,
  color
}) {
  const r = 30,
    C = 2 * Math.PI * r;
  return /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__ring"
  }, /*#__PURE__*/React.createElement("svg", {
    width: "76",
    height: "76",
    viewBox: "0 0 76 76"
  }, /*#__PURE__*/React.createElement("circle", {
    cx: "38",
    cy: "38",
    r: r,
    fill: "none",
    stroke: "var(--surface-active)",
    strokeWidth: "6"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "38",
    cy: "38",
    r: r,
    fill: "none",
    stroke: color,
    strokeWidth: "6",
    strokeLinecap: "round",
    strokeDasharray: `${C * pct / 100} ${C}`,
    transform: "rotate(-90 38 38)"
  }), /*#__PURE__*/React.createElement("text", {
    x: "38",
    y: "43",
    textAnchor: "middle",
    className: "fse-tcard__ringv"
  }, pct, "%")), /*#__PURE__*/React.createElement("span", null, label));
}
function RichCard({
  c,
  className
}) {
  const t = c.trade,
    short = t.direction === 'short',
    dirC = short ? 'var(--color-bear)' : 'var(--color-bull)';
  const risk = Math.abs(t.entry - t.stop),
    ticks = p => Math.round(Math.abs(p - t.entry) / (t.tick || 0.25));
  const usd = (p, q) => ticks(p) * (t.tickValue || 1.25) * q;
  const others = (c.setups || []).slice(1);
  const oddsC = ['var(--color-ice)', 'var(--color-bull)', 'var(--color-bull)'];
  return /*#__PURE__*/React.createElement("article", {
    className: 'fse-fcard fse-tcard' + (className ? ' ' + className : '')
  }, /*#__PURE__*/React.createElement("header", {
    className: "fse-tcard__head"
  }, /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__tile",
    "data-grade": t.grade
  }, GRADE[t.grade] || t.grade), /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__id"
  }, /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__sym"
  }, t.instrument, /*#__PURE__*/React.createElement("span", {
    style: {
      color: dirC
    }
  }, t.direction.toUpperCase())), /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__sub"
  }, t.name, " \xB7 ", t.pattern, " \xB7 ", t.node, " \xB7 tap ", t.tap, " \xB7 ", c.time)), /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__pill",
    style: {
      '--pc': dirC
    }
  }, t.direction.toUpperCase(), " \xB7 ", GRADE[t.grade], " \xB7 ", fx(t.rr, 1), "R"), c.mode && /*#__PURE__*/React.createElement(__ds_scope.Badge, {
    mode: c.mode
  })), /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__stats"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Spot"), /*#__PURE__*/React.createElement("b", null, fx(t.spot))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Day"), /*#__PURE__*/React.createElement("b", {
    style: {
      color: t.dayPct < 0 ? 'var(--color-bear)' : 'var(--color-bull)'
    }
  }, t.dayPct > 0 ? '+' : '', fx(t.dayPct), "%")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Range"), /*#__PURE__*/React.createElement("b", null, fx(t.rangePct), "%")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Regime"), /*#__PURE__*/React.createElement("b", null, c.regime)), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Map"), /*#__PURE__*/React.createElement("b", null, c.mapGrade)), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Trinity"), /*#__PURE__*/React.createElement("b", {
    style: {
      color: c.trinity === 'agree' ? 'var(--color-bull)' : undefined
    }
  }, c.trinity || '—')), t.gates && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Gates"), /*#__PURE__*/React.createElement("b", null, t.gates))), /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__grid"
  }, /*#__PURE__*/React.createElement("section", {
    className: "fse-tcard__panel fse-tcard__panel--chart"
  }, /*#__PURE__*/React.createElement("h4", {
    className: "fse-tcard__h"
  }, "Trade \xB7 to scale"), /*#__PURE__*/React.createElement(TradeChart, {
    t: t
  })), /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__col"
  }, /*#__PURE__*/React.createElement("section", {
    className: "fse-tcard__panel"
  }, /*#__PURE__*/React.createElement("h4", {
    className: "fse-tcard__h"
  }, "The plan"), /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__plan"
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-tcard__tag",
    "data-tone": "ice"
  }, "IF"), /*#__PURE__*/React.createElement("span", null, t.plan.if), /*#__PURE__*/React.createElement("span", {
    className: "fse-tcard__tag",
    "data-tone": "bear"
  }, "OUT"), /*#__PURE__*/React.createElement("span", null, t.plan.out), /*#__PURE__*/React.createElement("span", {
    className: "fse-tcard__tag",
    "data-tone": "bull"
  }, "TGT"), /*#__PURE__*/React.createElement("span", null, t.plan.tgt)), t.context && /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__ctx"
  }, t.context.map((x, i) => /*#__PURE__*/React.createElement("div", {
    key: i
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: x.icon,
    size: 13,
    style: {
      color: x.icon === 'crown' ? 'var(--level-king)' : 'var(--text-tertiary)'
    }
  }), /*#__PURE__*/React.createElement("span", null, x.text))))), t.odds && /*#__PURE__*/React.createElement("section", {
    className: "fse-tcard__panel"
  }, /*#__PURE__*/React.createElement("h4", {
    className: "fse-tcard__h"
  }, "Backtest odds", /*#__PURE__*/React.createElement("span", null, t.odds.label)), /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__rings"
  }, t.odds.items.map((o, i) => /*#__PURE__*/React.createElement(Ring, {
    key: i,
    pct: o.pct,
    label: o.label,
    color: oddsC[i] || 'var(--color-bull)'
  })))))), t.action && /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__do"
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-tcard__dotag"
  }, t.actionLabel || 'Armed'), /*#__PURE__*/React.createElement("span", {
    className: "fse-tcard__dotext"
  }, t.action), c.mode === 'paper' && /*#__PURE__*/React.createElement("span", {
    className: "fse-tcard__dometa"
  }, "paper \xB7 no live money")), /*#__PURE__*/React.createElement("section", {
    className: "fse-tcard__panel"
  }, /*#__PURE__*/React.createElement("h4", {
    className: "fse-tcard__h"
  }, "Bracket \xB7 ", t.qty, " ", t.instrument, /*#__PURE__*/React.createElement("span", null, "$", fx(t.tickValue), " / tick \xB7 stop only tightens")), /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__table",
    role: "table"
  }, /*#__PURE__*/React.createElement("div", {
    className: "th",
    role: "row"
  }, /*#__PURE__*/React.createElement("span", null, "Leg"), /*#__PURE__*/React.createElement("span", null, "Order"), /*#__PURE__*/React.createElement("span", null, "Price"), /*#__PURE__*/React.createElement("span", null, "Ticks"), /*#__PURE__*/React.createElement("span", null, "Qty"), /*#__PURE__*/React.createElement("span", null, "P&L"), /*#__PURE__*/React.createElement("span", null, "R")), /*#__PURE__*/React.createElement("div", {
    role: "row"
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--color-ice)'
    }
  }, "Entry"), /*#__PURE__*/React.createElement("span", null, short ? 'sell' : 'buy', " limit"), /*#__PURE__*/React.createElement("span", {
    className: "v"
  }, fx(t.entry)), /*#__PURE__*/React.createElement("span", null, "\u2014"), /*#__PURE__*/React.createElement("span", null, "\xD7", t.qty), /*#__PURE__*/React.createElement("span", null, "\u2014"), /*#__PURE__*/React.createElement("span", null, "\u2014")), /*#__PURE__*/React.createElement("div", {
    role: "row"
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--color-bear)'
    }
  }, "Stop"), /*#__PURE__*/React.createElement("span", null, short ? 'buy' : 'sell', " stop"), /*#__PURE__*/React.createElement("span", {
    className: "v",
    style: {
      color: 'var(--color-bear)'
    }
  }, fx(t.stop)), /*#__PURE__*/React.createElement("span", null, ticks(t.stop)), /*#__PURE__*/React.createElement("span", null, "\xD7", t.qty), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--color-bear)'
    }
  }, "\u2212$", fx(usd(t.stop, t.qty))), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--color-bear)'
    }
  }, "\u22121.00")), (t.targets || []).map((x, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    role: "row"
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--color-bull)'
    }
  }, x.label), /*#__PURE__*/React.createElement("span", null, short ? 'buy' : 'sell', " limit"), /*#__PURE__*/React.createElement("span", {
    className: "v",
    style: {
      color: 'var(--color-bull)'
    }
  }, fx(x.price)), /*#__PURE__*/React.createElement("span", null, ticks(x.price)), /*#__PURE__*/React.createElement("span", null, "\xD7", x.qty), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--color-bull)'
    }
  }, "+$", fx(usd(x.price, x.qty))), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--color-bull)'
    }
  }, "+", fx(Math.abs(x.price - t.entry) / risk)))))), /*#__PURE__*/React.createElement("section", {
    className: "fse-tcard__panel fse-tcard__panel--map"
  }, /*#__PURE__*/React.createElement("h4", {
    className: "fse-tcard__h"
  }, "Map", c.flip && /*#__PURE__*/React.createElement("span", null, "King flip ", c.flip)), /*#__PURE__*/React.createElement("div", {
    className: "fse-tcard__maps"
  }, (c.maps || []).map((m, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "fse-tcard__map"
  }, /*#__PURE__*/React.createElement("span", {
    className: "s"
  }, m.sym), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", null, "K"), /*#__PURE__*/React.createElement("b", {
    style: {
      color: 'var(--level-king)'
    }
  }, m.king ?? '—')), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", null, "C"), /*#__PURE__*/React.createElement("b", {
    style: {
      color: 'var(--level-ceiling)'
    }
  }, m.ceiling ?? '—')), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", null, "F"), /*#__PURE__*/React.createElement("b", {
    style: {
      color: 'var(--level-floor)'
    }
  }, m.floor ?? '—')), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", null, "spot"), /*#__PURE__*/React.createElement("b", null, m.spot ?? '—')))))), others.length > 0 && /*#__PURE__*/React.createElement("section", {
    className: "fse-tcard__panel"
  }, /*#__PURE__*/React.createElement("h4", {
    className: "fse-tcard__h"
  }, "Other setups", c.notListed > 0 && /*#__PURE__*/React.createElement("span", null, "+", c.notListed, " not listed \xB7 watch ", c.watchUp, " \u2191 \xB7 ", c.watchDown, " \u2193")), others.map((s, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "fse-fcard__setup"
  }, /*#__PURE__*/React.createElement(__ds_scope.Badge, {
    grade: s.grade
  }), /*#__PURE__*/React.createElement("span", {
    className: "t"
  }, s.key), s.reasons && /*#__PURE__*/React.createElement("span", {
    className: "fse-tcard__why"
  }, s.reasons.join(' · '))))));
}
function FindingCard({
  card = {},
  className
}) {
  const c = card,
    ua = v => v == null ? /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--text-disabled)'
      }
    }, "unavailable") : v;
  if (c.trade && c.kind !== 'premarket') return /*#__PURE__*/React.createElement(RichCard, {
    c: c,
    className: className
  });
  return /*#__PURE__*/React.createElement("article", {
    className: 'fse-fcard' + (className ? ' ' + className : '')
  }, /*#__PURE__*/React.createElement("header", {
    className: "fse-fcard__head"
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: c.kind === 'premarket' ? 'sunrise' : 'radar',
    size: 14,
    style: {
      color: 'var(--text-tertiary)'
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "fse-fcard__time"
  }, c.kind === 'premarket' ? 'Premarket' : 'Decision', " \xB7 ", c.time), c.mode && /*#__PURE__*/React.createElement(__ds_scope.Badge, {
    mode: c.mode
  })), /*#__PURE__*/React.createElement("div", {
    className: "fse-fcard__sec"
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-fcard__h"
  }, "Map"), /*#__PURE__*/React.createElement("div", {
    className: "fse-fcard__row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "l"
  }, "Regime"), /*#__PURE__*/React.createElement("span", null, ua(c.regime)), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-tertiary)'
    }
  }, "grade"), /*#__PURE__*/React.createElement("span", null, ua(c.mapGrade)), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-tertiary)'
    }
  }, "trinity"), /*#__PURE__*/React.createElement("span", null, ua(c.trinity))), (c.maps || []).map((m, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "fse-fcard__row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "l"
  }, m.sym), /*#__PURE__*/React.createElement("span", null, "K ", /*#__PURE__*/React.createElement("b", {
    style: {
      color: 'var(--level-king)',
      fontWeight: 500
    }
  }, ua(m.king))), /*#__PURE__*/React.createElement("span", null, "C ", ua(m.ceiling)), /*#__PURE__*/React.createElement("span", null, "F ", ua(m.floor)), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-tertiary)'
    }
  }, "spot ", ua(m.spot)))), c.flip && /*#__PURE__*/React.createElement("div", {
    className: "fse-fcard__row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "l"
  }, "Flip"), /*#__PURE__*/React.createElement("span", null, "King ", c.flip))), c.kind !== 'premarket' && /*#__PURE__*/React.createElement("div", {
    className: "fse-fcard__sec"
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-fcard__h"
  }, "Setups"), (c.setups || []).length === 0 && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-disabled)'
    }
  }, "none"), (c.setups || []).map((s, i) => /*#__PURE__*/React.createElement(React.Fragment, {
    key: i
  }, /*#__PURE__*/React.createElement("div", {
    className: "fse-fcard__setup"
  }, /*#__PURE__*/React.createElement(__ds_scope.Badge, {
    grade: s.grade
  }), /*#__PURE__*/React.createElement("span", {
    className: "t"
  }, s.key)), s.reasons && s.reasons.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "fse-fcard__reasons"
  }, s.reasons.join(' · ')))), c.notListed > 0 && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-tertiary)',
      fontSize: 11
    }
  }, "+", c.notListed, " not listed")), c.kind !== 'premarket' && /*#__PURE__*/React.createElement("div", {
    className: "fse-fcard__sec"
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-fcard__h"
  }, "Orders \xB7 positions"), /*#__PURE__*/React.createElement("div", {
    className: "fse-fcard__row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "l"
  }, "Work"), /*#__PURE__*/React.createElement("span", null, c.orders || /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-disabled)'
    }
  }, "none"))), /*#__PURE__*/React.createElement("div", {
    className: "fse-fcard__row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "l"
  }, "Pos"), /*#__PURE__*/React.createElement("span", {
    className: /^(long|buy)/i.test(String(c.position || '')) ? 'is-long' : /^(short|sell)/i.test(String(c.position || '')) ? 'is-short' : undefined
  }, c.position || 'flat')), /*#__PURE__*/React.createElement("div", {
    className: "fse-fcard__row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "l"
  }, "Watch"), /*#__PURE__*/React.createElement("span", null, ua(c.watchUp), " \u2191 \xB7 ", ua(c.watchDown), " \u2193"))));
}
Object.assign(__ds_scope, { FindingCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/domain/FindingCard.jsx", error: String((e && e.message) || e) }); }

// components/forms/Checkbox.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function Checkbox({
  label,
  disabled,
  className,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("label", {
    className: cx('fse-check', disabled && 'fse-check--disabled', className)
  }, /*#__PURE__*/React.createElement("input", _extends({
    type: "checkbox",
    disabled: disabled
  }, rest)), /*#__PURE__*/React.createElement("span", {
    className: "fse-check__box"
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "check",
    size: 10
  })), label);
}
Object.assign(__ds_scope, { Checkbox });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Checkbox.jsx", error: String((e && e.message) || e) }); }

// components/forms/Input.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function Input({
  label,
  hint,
  error,
  warning,
  icon,
  prefix,
  suffix,
  size = 'md',
  disabled,
  className,
  id,
  ...rest
}) {
  const fid = id || (label ? 'in-' + String(label).replace(/\W+/g, '-').toLowerCase() : undefined);
  return /*#__PURE__*/React.createElement("label", {
    className: cx('fse-field', className),
    htmlFor: fid
  }, label && /*#__PURE__*/React.createElement("span", {
    className: "fse-field__label"
  }, label), /*#__PURE__*/React.createElement("span", {
    className: cx('fse-input', size === 'sm' && 'fse-input--sm', error && 'fse-input--error', !error && warning && 'fse-input--warning', disabled && 'fse-input--disabled')
  }, icon && /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: 12,
    style: {
      color: 'var(--text-tertiary)'
    }
  }), prefix && /*#__PURE__*/React.createElement("span", {
    className: "fse-input__affix"
  }, prefix), /*#__PURE__*/React.createElement("input", _extends({
    id: fid,
    disabled: disabled
  }, rest)), suffix && /*#__PURE__*/React.createElement("span", {
    className: "fse-input__affix"
  }, suffix)), (error || warning || hint) && /*#__PURE__*/React.createElement("span", {
    className: cx('fse-field__hint', error && 'fse-field__hint--error', !error && warning && 'fse-field__hint--warning')
  }, error || warning || hint));
}
Object.assign(__ds_scope, { Input });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Input.jsx", error: String((e && e.message) || e) }); }

// components/forms/Segmented.jsx
try { (() => {
function Segmented({
  options = [],
  value,
  onChange,
  className
}) {
  return /*#__PURE__*/React.createElement("div", {
    role: "group",
    className: 'fse-seg' + (className ? ' ' + className : '')
  }, options.map(o => {
    const v = typeof o === 'string' ? o : o.value;
    const l = typeof o === 'string' ? o : o.label;
    return /*#__PURE__*/React.createElement("button", {
      key: v,
      type: "button",
      "aria-pressed": v === value,
      onClick: () => onChange && onChange(v)
    }, l);
  }));
}
Object.assign(__ds_scope, { Segmented });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Segmented.jsx", error: String((e && e.message) || e) }); }

// components/forms/Select.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function Select({
  label,
  hint,
  options = [],
  size = 'md',
  className,
  id,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("label", {
    className: cx('fse-field', className)
  }, label && /*#__PURE__*/React.createElement("span", {
    className: "fse-field__label"
  }, label), /*#__PURE__*/React.createElement("span", {
    className: cx('fse-input', 'fse-select', size === 'sm' && 'fse-input--sm')
  }, /*#__PURE__*/React.createElement("select", _extends({
    id: id
  }, rest), options.map(o => typeof o === 'string' ? /*#__PURE__*/React.createElement("option", {
    key: o,
    value: o
  }, o) : /*#__PURE__*/React.createElement("option", {
    key: o.value,
    value: o.value
  }, o.label))), /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "chevrons-up-down",
    size: 12
  })), hint && /*#__PURE__*/React.createElement("span", {
    className: "fse-field__hint"
  }, hint));
}
Object.assign(__ds_scope, { Select });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Select.jsx", error: String((e && e.message) || e) }); }

// components/forms/Switch.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const cx = (...a) => a.filter(Boolean).join(' ');
function Switch({
  label,
  disabled,
  className,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("label", {
    className: cx('fse-switch', disabled && 'fse-switch--disabled', className)
  }, /*#__PURE__*/React.createElement("input", _extends({
    type: "checkbox",
    role: "switch",
    disabled: disabled
  }, rest)), /*#__PURE__*/React.createElement("span", {
    className: "fse-switch__track"
  }), label);
}
Object.assign(__ds_scope, { Switch });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Switch.jsx", error: String((e && e.message) || e) }); }

// components/navigation/Breadcrumbs.jsx
try { (() => {
function Breadcrumbs({
  items = [],
  className
}) {
  return /*#__PURE__*/React.createElement("nav", {
    "aria-label": "Breadcrumb",
    className: 'fse-crumbs' + (className ? ' ' + className : '')
  }, items.map((it, i) => {
    const last = i === items.length - 1;
    return /*#__PURE__*/React.createElement(React.Fragment, {
      key: i
    }, i > 0 && /*#__PURE__*/React.createElement("span", {
      className: "fse-crumbs__sep"
    }, "/"), last ? /*#__PURE__*/React.createElement("span", {
      className: "fse-crumbs__cur"
    }, it.label) : it.onClick ? /*#__PURE__*/React.createElement("button", {
      type: "button",
      onClick: it.onClick
    }, it.label) : /*#__PURE__*/React.createElement("a", {
      href: it.href || '#'
    }, it.label));
  }));
}
Object.assign(__ds_scope, { Breadcrumbs });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/Breadcrumbs.jsx", error: String((e && e.message) || e) }); }

// components/navigation/SideNav.jsx
try { (() => {
function SideNav({
  sections = [],
  value,
  onChange,
  className
}) {
  return /*#__PURE__*/React.createElement("nav", {
    className: 'fse-nav' + (className ? ' ' + className : '')
  }, sections.map((s, si) => /*#__PURE__*/React.createElement("div", {
    key: si,
    className: "fse-nav__sec"
  }, s.title && /*#__PURE__*/React.createElement("div", {
    className: "fse-nav__title"
  }, s.title), s.items.map(it => /*#__PURE__*/React.createElement("button", {
    key: it.value,
    type: "button",
    className: "fse-nav__item",
    "aria-current": it.value === value ? 'page' : undefined,
    onClick: () => onChange && onChange(it.value)
  }, it.icon && /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: it.icon,
    size: 14
  }), /*#__PURE__*/React.createElement("span", {
    className: "l"
  }, it.label), it.meta != null && /*#__PURE__*/React.createElement("span", {
    className: "fse-nav__meta"
  }, it.meta))))));
}
Object.assign(__ds_scope, { SideNav });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/SideNav.jsx", error: String((e && e.message) || e) }); }

// components/navigation/Tabs.jsx
try { (() => {
function Tabs({
  items = [],
  value,
  onChange,
  className
}) {
  return /*#__PURE__*/React.createElement("div", {
    role: "tablist",
    className: 'fse-tabs' + (className ? ' ' + className : '')
  }, items.map(t => /*#__PURE__*/React.createElement("button", {
    key: t.value,
    role: "tab",
    "aria-selected": t.value === value,
    onClick: () => onChange && onChange(t.value)
  }, t.label, t.count != null && /*#__PURE__*/React.createElement("span", {
    className: "fse-tabs__count"
  }, t.count))));
}
Object.assign(__ds_scope, { Tabs });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/navigation/Tabs.jsx", error: String((e && e.message) || e) }); }

// components/surfaces/Banner.jsx
try { (() => {
const IC = {
  info: 'info',
  warning: 'triangle-alert',
  alarm: 'octagon-x'
};
function Banner({
  tone = 'info',
  title,
  children,
  actions,
  flush,
  icon,
  className
}) {
  return /*#__PURE__*/React.createElement("div", {
    role: tone === 'alarm' ? 'alert' : 'status',
    className: 'fse-banner fse-banner--' + tone + (flush ? ' fse-banner--flush' : '') + (className ? ' ' + className : '')
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon || IC[tone],
    size: 16
  }), title && /*#__PURE__*/React.createElement("span", {
    className: "fse-banner__title"
  }, title), children && /*#__PURE__*/React.createElement("span", {
    className: "fse-banner__msg"
  }, children), actions && /*#__PURE__*/React.createElement("span", {
    className: "fse-banner__actions"
  }, actions));
}
Object.assign(__ds_scope, { Banner });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/surfaces/Banner.jsx", error: String((e && e.message) || e) }); }

// components/surfaces/CodeBlock.jsx
try { (() => {
function tok(line) {
  const out = [];
  const ci = line.search(/(^|\s)#/);
  const code = ci >= 0 ? line.slice(0, ci) : line,
    com = ci >= 0 ? line.slice(ci) : '';
  const m = code.match(/^(\s*-?\s*)([\w.]+)(:)(.*)$/);
  if (m) {
    out.push(m[1], /*#__PURE__*/React.createElement("span", {
      key: "k",
      className: "k"
    }, m[2]), /*#__PURE__*/React.createElement("span", {
      key: "p",
      className: "p"
    }, m[3]));
    const v = m[4];
    out.push(/*#__PURE__*/React.createElement("span", {
      key: "v",
      className: /\btrue\b/.test(v) && !/\bfalse\b/.test(v) ? 't' : /\bfalse\b/.test(v) ? 'f' : /\bnull\b/.test(v) ? 'b' : 'v'
    }, v));
  } else out.push(/*#__PURE__*/React.createElement("span", {
    key: "v",
    className: "v"
  }, code));
  if (com) out.push(/*#__PURE__*/React.createElement("span", {
    key: "c",
    className: "c"
  }, com));
  return out;
}
function CodeBlock({
  code = '',
  highlight = [],
  numbers = true,
  plain,
  maxHeight,
  className
}) {
  const lines = code.replace(/\n$/, '').split('\n');
  return /*#__PURE__*/React.createElement("pre", {
    className: 'fse-code' + (className ? ' ' + className : ''),
    style: maxHeight ? {
      maxHeight
    } : undefined
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("tbody", null, lines.map((l, i) => /*#__PURE__*/React.createElement("tr", {
    key: i,
    className: highlight.includes(i + 1) ? 'is-hl' : undefined
  }, numbers && /*#__PURE__*/React.createElement("td", {
    className: "n"
  }, i + 1), /*#__PURE__*/React.createElement("td", null, plain ? l : tok(l), '\n'))))));
}
Object.assign(__ds_scope, { CodeBlock });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/surfaces/CodeBlock.jsx", error: String((e && e.message) || e) }); }

// components/surfaces/Dialog.jsx
try { (() => {
function Dialog({
  open = true,
  title,
  children,
  footer,
  onClose,
  tone,
  inline,
  width,
  alarmLabel
}) {
  if (!open) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: 'fse-scrim' + (inline ? ' fse-scrim--inline' : ''),
    onClick: e => {
      if (e.target === e.currentTarget && onClose) onClose();
    }
  }, /*#__PURE__*/React.createElement("div", {
    role: "dialog",
    "aria-modal": "true",
    className: 'fse-dialog' + (tone === 'danger' ? ' fse-dialog--danger' : ''),
    style: width ? {
      maxWidth: width
    } : undefined
  }, tone === 'danger' && /*#__PURE__*/React.createElement("div", {
    className: "fse-dialog__alarm"
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "triangle-alert",
    size: 12
  }), alarmLabel || 'Affects live state'), /*#__PURE__*/React.createElement("div", {
    className: "fse-dialog__head"
  }, /*#__PURE__*/React.createElement("h2", {
    className: "fse-dialog__title"
  }, title), onClose && /*#__PURE__*/React.createElement(__ds_scope.IconButton, {
    icon: "x",
    label: "Close",
    size: "sm",
    onClick: onClose
  })), /*#__PURE__*/React.createElement("div", {
    className: "fse-dialog__body"
  }, children), footer && /*#__PURE__*/React.createElement("div", {
    className: "fse-dialog__foot"
  }, footer)));
}
Object.assign(__ds_scope, { Dialog });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/surfaces/Dialog.jsx", error: String((e && e.message) || e) }); }

// components/surfaces/Panel.jsx
try { (() => {
function Panel({
  title,
  subtitle,
  icon,
  actions,
  footer,
  flush,
  children,
  className,
  style
}) {
  return /*#__PURE__*/React.createElement("section", {
    className: 'fse-panel' + (className ? ' ' + className : ''),
    style: style
  }, (title || actions) && /*#__PURE__*/React.createElement("header", {
    className: "fse-panel__head"
  }, /*#__PURE__*/React.createElement("div", {
    className: "fse-panel__title"
  }, icon && /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: 14,
    style: {
      color: 'var(--text-tertiary)'
    }
  }), title, subtitle && /*#__PURE__*/React.createElement("span", {
    className: "fse-panel__sub"
  }, subtitle)), actions && /*#__PURE__*/React.createElement("div", {
    className: "fse-panel__actions"
  }, actions)), /*#__PURE__*/React.createElement("div", {
    className: 'fse-panel__body' + (flush ? ' fse-panel__body--flush' : '')
  }, children), footer && /*#__PURE__*/React.createElement("footer", {
    className: "fse-panel__foot"
  }, footer));
}
Object.assign(__ds_scope, { Panel });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/surfaces/Panel.jsx", error: String((e && e.message) || e) }); }

// components/surfaces/Toast.jsx
try { (() => {
const IC = {
  info: 'info',
  accent: 'zap',
  ok: 'circle-check',
  warning: 'triangle-alert',
  danger: 'octagon-alert'
};
function Toast({
  tone = 'info',
  title,
  message,
  icon,
  onClose,
  action
}) {
  return /*#__PURE__*/React.createElement("div", {
    role: "status",
    className: 'fse-toast fse-toast--' + tone
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon || IC[tone] || 'info',
    size: 14,
    className: "fse-toast__icon"
  }), /*#__PURE__*/React.createElement("div", {
    className: "fse-toast__body"
  }, /*#__PURE__*/React.createElement("span", {
    className: "fse-toast__title"
  }, title), message && /*#__PURE__*/React.createElement("span", {
    className: "fse-toast__msg"
  }, message), action), onClose && /*#__PURE__*/React.createElement(__ds_scope.IconButton, {
    icon: "x",
    label: "Dismiss",
    size: "sm",
    onClick: onClose
  }));
}
Object.assign(__ds_scope, { Toast });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/surfaces/Toast.jsx", error: String((e && e.message) || e) }); }

// components/surfaces/Tooltip.jsx
try { (() => {
function Tooltip({
  content,
  side = 'top',
  children
}) {
  return /*#__PURE__*/React.createElement("span", {
    className: 'fse-tip' + (side === 'bottom' ? ' fse-tip--bottom' : '')
  }, children, /*#__PURE__*/React.createElement("span", {
    role: "tooltip",
    className: "fse-tip__bubble"
  }, content));
}
Object.assign(__ds_scope, { Tooltip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/surfaces/Tooltip.jsx", error: String((e && e.message) || e) }); }

// ui_kits/console/CardsScreen.jsx
try { (() => {
(function () {
  const {
    FindingCard,
    Segmented,
    Badge,
    Button
  } = window.FseDesignSystem_043819;
  function CardsScreen() {
    const D = window.FSE_DATA;
    const [k, setK] = React.useState('all');
    const cards = D.cards.filter(c => k === 'all' || (k === 'premarket' ? c.kind === 'premarket' : c.kind !== 'premarket'));
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(PageHead, {
      title: "Finding cards",
      sub: "Sent to console, file and webhook sinks \xB7 one card at most per Decision_Time",
      actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Segmented, {
        options: ['all', 'decision', 'premarket'],
        value: k,
        onChange: setK
      }), /*#__PURE__*/React.createElement(Button, {
        icon: "external-link"
      }, "Webhook log"))
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: '0 24px 24px',
        display: 'grid',
        gap: 14,
        maxWidth: 1120
      }
    }, cards.map((c, i) => /*#__PURE__*/React.createElement("div", {
      key: i,
      style: {
        display: 'grid',
        gridTemplateColumns: '64px minmax(0,1fr)',
        gap: 14
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        paddingTop: 12,
        fontSize: 11,
        color: 'var(--text-tertiary)',
        textAlign: 'right'
      }
    }, c.time.replace(' ET', '')), /*#__PURE__*/React.createElement(FindingCard, {
      card: c
    })))));
  }
  window.CardsScreen = CardsScreen;
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/console/CardsScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/console/ConfigScreen.jsx
try { (() => {
(function () {
  const {
    Panel,
    CodeBlock,
    Segmented,
    Switch,
    Input,
    Button,
    Badge,
    Icon
  } = window.FseDesignSystem_043819;
  const Banner = window.FseDesignSystem_043819.Banner || (() => null);
  const YAML = `schema_version: 1
config_id: playbook_baseline
order_mode: paper # paper | practice | combine

regime:
  min_abs_value: 1000000.0

gates:
  stale_map: {enabled: true} # live map hard rule
  map_grade: {enabled: true}
  midpoint: {enabled: true} # never trade the midpoint
  chart_confluence: {enabled: true}
  dark_pool_confluence: {enabled: false} # confluence only
  tap_count: {enabled: true} # 3rd tap = pass
  min_reward_risk:
    enabled: true
    min: 3.0
    alert_min: 2.0 # 2:1 alerts, ping only

orders:
  flatten_time: "15:55"
  max_open: 1

sizing:
  mode: fixed_contracts
  fixed: {MES: 5, MNQ: 3}`;
  const FILES = [['configs', 1, 'folder'], ['playbook_baseline.yaml', 2, 'file'], ['example_strategy.yaml', 2, 'file'], ['calendars', 1, 'folder'], ['economic_events.yaml', 2, 'file'], ['roll_calendar.yaml', 2, 'file']];
  const GATES = ['stale_map', 'map_grade', 'midpoint', 'chart_confluence', 'dark_pool_confluence', 'stdev_fib_zone', 'trinity_agreement', 'tap_count', 'sloppy_seconds', 'air_pocket_fade', 'news_window', 'vix_gap'];
  function ConfigScreen() {
    const [v, setV] = React.useState('yaml');
    const [f, setF] = React.useState('playbook_baseline.yaml');
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(PageHead, {
      title: f,
      sub: "Strategy_Config \xB7 schema 1 \xB7 hash 3f1a9c\u2026e2 \xB7 every rule is a toggleable Pattern, Gate, Exit_Mode or Kill_Switch",
      actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Segmented, {
        options: [{
          value: 'yaml',
          label: 'YAML'
        }, {
          value: 'form',
          label: 'Form'
        }],
        value: v,
        onChange: setV
      }), /*#__PURE__*/React.createElement(Button, {
        icon: "check"
      }, "Validate"), /*#__PURE__*/React.createElement(Button, {
        variant: "primary",
        icon: "play"
      }, "Backtest this"))
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: '0 24px 12px'
      }
    }, /*#__PURE__*/React.createElement(Banner, {
      tone: "warning",
      title: "OPERATOR: required"
    }, "fills.costs.MNQ.exchange_fee has no value. fse backtest exits 2 until it is set.")), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: '0 24px 24px',
        display: 'grid',
        gridTemplateColumns: '220px minmax(0,1fr)',
        gap: 16,
        alignItems: 'start'
      }
    }, /*#__PURE__*/React.createElement(Panel, {
      flush: true,
      title: "Files",
      icon: "folder-tree"
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        padding: '6px 0'
      }
    }, FILES.map(([n, d, k]) => /*#__PURE__*/React.createElement("button", {
      key: n,
      onClick: () => k === 'file' && setF(n),
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: '100%',
        height: 28,
        padding: '0 12px 0 ' + d * 12 + 'px',
        border: 0,
        background: n === f ? 'var(--surface-active)' : 'transparent',
        color: n === f ? 'var(--text-primary)' : 'var(--text-secondary)',
        font: 'var(--fw-regular) var(--fs-12)/1 var(--font-mono)',
        cursor: k === 'file' ? 'pointer' : 'default',
        textAlign: 'left'
      }
    }, /*#__PURE__*/React.createElement(Icon, {
      name: k === 'folder' ? 'folder' : 'file-code',
      size: 13,
      style: {
        color: 'var(--text-tertiary)'
      }
    }), n)))), v === 'yaml' ? /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gap: 8
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        fontSize: 11,
        color: 'var(--text-tertiary)'
      }
    }, /*#__PURE__*/React.createElement(Badge, {
      variant: "outline"
    }, "YAML"), /*#__PURE__*/React.createElement("span", null, "27 lines \xB7 1 changed vs main")), /*#__PURE__*/React.createElement(CodeBlock, {
      code: YAML,
      highlight: [17]
    })) : /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)',
        gap: 16,
        alignItems: 'start'
      }
    }, /*#__PURE__*/React.createElement(Panel, {
      title: "Gates",
      subtitle: GATES.length + ' of 27',
      icon: "filter"
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gap: 12
      }
    }, GATES.map(g => /*#__PURE__*/React.createElement(Switch, {
      key: g,
      label: g,
      defaultChecked: !['dark_pool_confluence', 'stdev_fib_zone'].includes(g)
    })))), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gap: 16
      }
    }, /*#__PURE__*/React.createElement(Panel, {
      title: "min_reward_risk",
      icon: "scale"
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: 12
      }
    }, /*#__PURE__*/React.createElement(Input, {
      label: "min",
      defaultValue: "3.0",
      suffix: "R"
    }), /*#__PURE__*/React.createElement(Input, {
      label: "alert_min",
      defaultValue: "2.0",
      suffix: "R",
      hint: "Ping only, never ordered"
    }))), /*#__PURE__*/React.createElement(Panel, {
      title: "orders",
      icon: "clock"
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: 12
      }
    }, /*#__PURE__*/React.createElement(Input, {
      label: "flatten_time",
      defaultValue: "15:55",
      suffix: "ET"
    }), /*#__PURE__*/React.createElement(Input, {
      label: "max_open",
      defaultValue: "1"
    }))), /*#__PURE__*/React.createElement(Panel, {
      title: "fills.costs",
      icon: "receipt"
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: 12
      }
    }, /*#__PURE__*/React.createElement(Input, {
      label: "MES exchange_fee",
      prefix: "$",
      defaultValue: "1.00"
    }), /*#__PURE__*/React.createElement(Input, {
      label: "MNQ exchange_fee",
      prefix: "$",
      defaultValue: "",
      warning: "OPERATOR: required"
    })))))));
  }
  window.ConfigScreen = ConfigScreen;
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/console/ConfigScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/console/ExperimentsScreen.jsx
try { (() => {
(function () {
  const {
    Tabs,
    Panel,
    DataTable,
    Badge,
    Button,
    Select,
    Stat
  } = window.FseDesignSystem_043819;
  function ExperimentsScreen() {
    const D = window.FSE_DATA;
    const [t, setT] = React.useState('abl');
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(PageHead, {
      title: "Experiments",
      sub: "fse experiment \xB7 ablation, sweep, pass estimate, walk-forward, cadence, holdout",
      actions: /*#__PURE__*/React.createElement(Button, {
        variant: "primary",
        icon: "flask-conical"
      }, "New experiment")
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: '0 24px'
      }
    }, /*#__PURE__*/React.createElement(Tabs, {
      value: t,
      onChange: setT,
      items: [{
        value: 'abl',
        label: 'Ablation'
      }, {
        value: 'sweep',
        label: 'Frontier sweep'
      }, {
        value: 'wf',
        label: 'Walk-forward'
      }]
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: '16px 24px 24px',
        display: 'grid',
        gridTemplateColumns: 'minmax(0,1fr)',
        gap: 16
      }
    }, t === 'abl' && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 12,
        alignItems: 'flex-end'
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        width: 260
      }
    }, /*#__PURE__*/React.createElement(Select, {
      label: "Variant",
      options: ['− dark_pool_confluence', '− tap_count', '− trinity_agreement', '− candle_color']
    })), /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: 11,
        color: 'var(--text-tertiary)',
        paddingBottom: 8
      }
    }, "Base bt_8f2c41 \xB7 variant bt_77a019 \xB7 248 sessions each")), /*#__PURE__*/React.createElement(Panel, {
      flush: true,
      title: "Base vs variant",
      icon: "split"
    }, /*#__PURE__*/React.createElement(DataTable, {
      rows: D.ablation,
      columns: [{
        key: 'm',
        label: 'Metric'
      }, {
        key: 'b',
        label: 'Base',
        align: 'right'
      }, {
        key: 'v',
        label: 'Variant',
        align: 'right'
      }, {
        key: 'd',
        label: 'Change',
        align: 'right',
        render: v => /*#__PURE__*/React.createElement("span", {
          className: v.startsWith('+') ? 'fse-pos' : 'fse-neg'
        }, v)
      }]
    }))), t === 'sweep' && /*#__PURE__*/React.createElement(Panel, {
      flush: true,
      title: "min_reward_risk.min",
      subtitle: "5 configurations \xB7 Pareto set marked",
      icon: "chart-scatter"
    }, /*#__PURE__*/React.createElement(DataTable, {
      rows: D.frontier,
      columns: [{
        key: 'rr',
        label: 'Min R:R',
        align: 'right'
      }, {
        key: 'wr',
        label: 'Win %',
        align: 'right'
      }, {
        key: 'tr',
        label: 'Trades',
        align: 'right'
      }, {
        key: 'ex',
        label: 'Exp R',
        align: 'right',
        render: v => /*#__PURE__*/React.createElement("span", {
          className: v > 0 ? 'fse-pos' : 'fse-neg'
        }, (v > 0 ? '+' : '') + v.toFixed(2))
      }, {
        key: 'pareto',
        label: 'Pareto',
        render: v => v ? /*#__PURE__*/React.createElement(Badge, {
          variant: "accent-soft"
        }, "Pareto") : null
      }]
    })), t === 'wf' && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gridTemplateColumns: 'repeat(3,minmax(0,1fr))',
        gap: 16
      }
    }, /*#__PURE__*/React.createElement(Stat, {
      label: "Out-of-sample exp",
      value: "+0.15",
      unit: "R",
      tone: "pos"
    }), /*#__PURE__*/React.createElement(Stat, {
      label: "In-sample exp",
      value: "+0.30",
      unit: "R"
    }), /*#__PURE__*/React.createElement(Stat, {
      label: "No selection",
      value: "1",
      sub: "of 6 windows"
    })), /*#__PURE__*/React.createElement(Panel, {
      flush: true,
      title: "Windows",
      subtitle: "90-day train \xB7 30-day test",
      icon: "calendar-range"
    }, /*#__PURE__*/React.createElement(DataTable, {
      rows: D.wf,
      columns: [{
        key: 'w',
        label: 'Window'
      }, {
        key: 'span',
        label: 'Train span',
        muted: true
      }, {
        key: 'pick',
        label: 'Selected'
      }, {
        key: 'is',
        label: 'IS exp R',
        align: 'right'
      }, {
        key: 'oos',
        label: 'OOS exp R',
        align: 'right',
        render: v => /*#__PURE__*/React.createElement("span", {
          className: v.startsWith('+') ? 'fse-pos' : v === '—' ? 'fse-flat' : 'fse-neg'
        }, v)
      }]
    })))));
  }
  window.ExperimentsScreen = ExperimentsScreen;
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/console/ExperimentsScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/console/LiveScreen.jsx
try { (() => {
(function () {
  const {
    Panel,
    Stat,
    Button,
    Badge,
    LevelLadder,
    DataTable,
    FindingCard,
    Dialog,
    Segmented,
    Toast,
    StatusDot
  } = window.FseDesignSystem_043819;
  const CandleChart = window.FseDesignSystem_043819.CandleChart || (() => null);
  const Banner = window.FseDesignSystem_043819.Banner || (() => null);
  const toLines = (lv, spot) => lv.filter(l => l.kind !== 'gatekeeper').map(l => ({
    price: l.level,
    kind: l.kind,
    label: l.kind === 'air' ? 'Air pocket' : l.kind[0].toUpperCase() + l.kind.slice(1)
  })).concat([{
    price: spot,
    kind: 'spot',
    label: 'Spot'
  }]);
  const EV = {
    card: 'inbox',
    order: 'arrow-right-left',
    grade: 'star',
    flip: 'repeat',
    reject: 'x',
    map: 'layers',
    gate: 'filter'
  };
  function LiveScreen() {
    const D = window.FSE_DATA;
    const [halt, setHalt] = React.useState(false);
    const [halted, setHalted] = React.useState(false);
    const [sym, setSym] = React.useState('ES');
    const [toast, setToast] = React.useState(true);
    return /*#__PURE__*/React.createElement("div", null, halted && /*#__PURE__*/React.createElement(Banner, {
      tone: "alarm",
      flush: true,
      title: "Runner halted",
      actions: /*#__PURE__*/React.createElement(Button, {
        size: "sm",
        variant: "secondary",
        icon: "rotate-ccw",
        onClick: () => setHalted(false)
      }, "fse clear")
    }, "Operator halt at 10:35:12 ET. Working orders cancelled, positions flattened. Entries blocked."), /*#__PURE__*/React.createElement(PageHead, {
      title: "Live runner",
      sub: "2026-10-06 \xB7 RTH \xB7 Paper Order_Mode \u2014 no order is sent to ProjectX",
      actions: /*#__PURE__*/React.createElement(React.Fragment, null, halted ? /*#__PURE__*/React.createElement(Badge, {
        variant: "danger-solid"
      }, "Halted") : /*#__PURE__*/React.createElement(StatusDot, {
        status: "live",
        label: "Running"
      }), /*#__PURE__*/React.createElement(Button, {
        icon: "pause",
        disabled: halted
      }, "Pause entries"), /*#__PURE__*/React.createElement(Button, {
        variant: "danger-outline",
        icon: "octagon-x",
        disabled: halted,
        onClick: () => setHalt(true)
      }, "Halt"))
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: '0 24px 24px',
        display: 'grid',
        gridTemplateColumns: 'minmax(0,1fr)',
        gap: 16
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        display: 'flex',
        flexWrap: 'wrap',
        border: '1px solid var(--border-subtle)',
        borderRadius: 6,
        overflow: 'hidden',
        background: 'var(--surface-panel)'
      }
    }, [['Session', '+2.41', 'R', 'pos', '1 closed · 0 open'], ['Day P&L', '+$903.75', null, 'pos', 'cap $1,200'], ['Position', 'flat', null, 'flat', 'MES · MNQ'], ['Working', '1', null, null, 'MES sell limit 5910.25 ×5'], ['Trailing MLL', '$48,212', null, null, 'floor $47,500 · 50K Combine']].map(([l, v, u, t, s]) => /*#__PURE__*/React.createElement("div", {
      key: l,
      style: {
        flex: '1 1 170px',
        padding: '14px 16px',
        boxShadow: 'inset -1px -1px 0 var(--border-subtle)',
        marginRight: -1,
        marginBottom: -1
      }
    }, /*#__PURE__*/React.createElement(Stat, {
      label: l,
      value: v,
      unit: u,
      tone: t,
      sub: s
    })))), /*#__PURE__*/React.createElement(Panel, {
      title: sym === 'ES' ? 'MES' : 'MNQ',
      subtitle: '1 min · ' + (sym === 'ES' ? 'ES' : 'NQ') + ' levels',
      icon: "chart-candlestick",
      actions: /*#__PURE__*/React.createElement(Segmented, {
        options: ['ES', 'NQ'],
        value: sym,
        onChange: setSym
      })
    }, /*#__PURE__*/React.createElement(CandleChart, {
      height: 260,
      candles: D.bars[sym],
      times: D.times,
      levels: toLines(D.levels[sym], D.spot[sym])
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit,minmax(340px,1fr))',
        gap: 16,
        alignItems: 'start'
      }
    }, /*#__PURE__*/React.createElement(Panel, {
      title: "Map",
      subtitle: sym + ' · gamma',
      icon: "layers",
      flush: true
    }, /*#__PURE__*/React.createElement(LevelLadder, {
      levels: D.levels[sym],
      spot: D.spot[sym]
    })), /*#__PURE__*/React.createElement(Panel, {
      title: "Decision log",
      icon: "list",
      flush: true,
      footer: "Every decision is written to ~/.skylit-fse/live-state/2026-10-06.jsonl"
    }, /*#__PURE__*/React.createElement(DataTable, {
      dense: true,
      rows: D.log,
      columns: [{
        key: 't',
        label: 'Time',
        muted: true,
        width: 72
      }, {
        key: 'ev',
        label: 'Event',
        width: 80,
        render: v => /*#__PURE__*/React.createElement("span", {
          style: {
            display: 'inline-flex',
            gap: 6,
            alignItems: 'center',
            color: v === 'reject' ? 'var(--text-tertiary)' : v === 'order' ? 'var(--text-accent)' : v === 'grade' ? 'var(--color-warning)' : v === 'flip' ? 'var(--color-ice-soft)' : 'var(--text-secondary)'
          }
        }, v)
      }, {
        key: 'msg',
        label: 'Detail',
        render: v => /*#__PURE__*/React.createElement("span", {
          style: {
            whiteSpace: 'normal'
          }
        }, v)
      }]
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gap: 10
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        font: 'var(--type-micro)',
        letterSpacing: 'var(--ls-caps)',
        textTransform: 'uppercase',
        color: 'var(--text-disabled)'
      }
    }, "Latest Finding_Card"), /*#__PURE__*/React.createElement(FindingCard, {
      card: D.cards[0]
    })))), toast && /*#__PURE__*/React.createElement("div", {
      style: {
        position: 'fixed',
        right: 20,
        bottom: 40,
        zIndex: 20
      }
    }, /*#__PURE__*/React.createElement(Toast, {
      tone: "warning",
      icon: "bell",
      title: "2R alert \xB7 MES floor_ceiling_bounce long",
      message: "Entry 5850.25 \xB7 stop 5846.00 \xB7 2.4R \xB7 alert only, no order placed",
      onClose: () => setToast(false)
    })), /*#__PURE__*/React.createElement(Dialog, {
      open: halt,
      tone: "danger",
      alarmLabel: "Paper \xB7 affects live runner state",
      title: "Halt the live runner?",
      onClose: () => setHalt(false),
      footer: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
        variant: "ghost",
        onClick: () => setHalt(false)
      }, "Cancel"), /*#__PURE__*/React.createElement(Button, {
        variant: "danger",
        icon: "octagon-x",
        onClick: () => {
          setHalted(true);
          setHalt(false);
        }
      }, "Halt and flatten"))
    }, "The working MES order is cancelled and any open position is closed at market. New entries stay blocked until ", /*#__PURE__*/React.createElement("code", {
      style: {
        color: 'var(--text-primary)'
      }
    }, "fse clear"), "."));
  }
  window.LiveScreen = LiveScreen;
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/console/LiveScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/console/MapScreen.jsx
try { (() => {
(function () {
  const {
    Panel,
    LevelLadder,
    Segmented,
    DataTable,
    Badge,
    KeyValue,
    Tooltip
  } = window.FseDesignSystem_043819;
  const CandleChart = window.FseDesignSystem_043819.CandleChart || (() => null);
  function MapScreen() {
    const D = window.FSE_DATA;
    const [sym, setSym] = React.useState('ES');
    const [m, setM] = React.useState('gamma');
    const nodes = D.levels[sym].map((l, i) => ({
      id: i,
      strike: l.strike,
      level: l.level.toFixed(2),
      value: l.value,
      kind: l.kind,
      taps: l.kind === 'king' ? 2 : l.kind === 'gatekeeper' ? 1 : 0,
      state: l.kind === 'air' ? 'empty' : i % 3 === 0 ? 'growing' : 'stable'
    }));
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(PageHead, {
      title: "Map",
      sub: 'Heatseeker ' + m + ' · ' + (sym === 'ES' ? 'SPX → ES' : 'QQQ, NDXP → NQ') + ' · Snapshot asOf 10:34:58 ET',
      actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Segmented, {
        options: ['ES', 'NQ'],
        value: sym,
        onChange: setSym
      }), /*#__PURE__*/React.createElement(Segmented, {
        options: ['gamma', 'vanna'],
        value: m,
        onChange: setM
      }))
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: '0 24px 24px',
        display: 'grid',
        gridTemplateColumns: 'minmax(0,1fr)',
        gap: 16
      }
    }, /*#__PURE__*/React.createElement(Panel, {
      title: 'Price vs ' + m + ' map',
      subtitle: "1 min \xB7 all nodes",
      icon: "chart-candlestick"
    }, /*#__PURE__*/React.createElement(CandleChart, {
      height: 380,
      candles: D.bars[sym],
      times: D.times,
      levels: D.levels[sym].map(l => ({
        price: l.level,
        kind: l.kind,
        label: (l.kind === 'air' ? 'Air pocket' : l.kind[0].toUpperCase() + l.kind.slice(1)) + (l.strike !== '—' ? ' ' + l.strike : '')
      })).concat([{
        price: D.spot[sym],
        kind: 'spot',
        label: 'Spot'
      }])
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit,minmax(380px,1fr))',
        gap: 16,
        alignItems: 'start'
      }
    }, /*#__PURE__*/React.createElement(Panel, {
      title: "Levels",
      subtitle: "futures points",
      icon: "layers",
      flush: true
    }, /*#__PURE__*/React.createElement(LevelLadder, {
      levels: D.levels[sym],
      spot: D.spot[sym]
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gap: 16
      }
    }, /*#__PURE__*/React.createElement(Panel, {
      title: "Regime",
      icon: "activity"
    }, /*#__PURE__*/React.createElement(KeyValue, {
      columns: 2,
      items: [['regime', /*#__PURE__*/React.createElement(Badge, {
        variant: "outline"
      }, "Positive_Gamma")], ['map grade', 'B'], [/*#__PURE__*/React.createElement(Tooltip, {
        content: "Gamma King of SPX, SPY, QQQ vs spot"
      }, /*#__PURE__*/React.createElement("span", {
        style: {
          borderBottom: '1px dashed var(--fg-4)'
        }
      }, "trinity")), 'agree · all above'], ['VIX gap', 'none'], ['King flips', '1 today'], ['stale', 'no · 2s']]
    })), /*#__PURE__*/React.createElement(Panel, {
      title: "Nodes",
      icon: "git-commit-horizontal",
      flush: true
    }, /*#__PURE__*/React.createElement(DataTable, {
      dense: true,
      rows: nodes,
      columns: [{
        key: 'kind',
        label: 'Label',
        render: v => /*#__PURE__*/React.createElement("span", {
          style: {
            color: v === 'king' ? 'var(--level-king)' : v === 'ceiling' ? 'var(--level-ceiling)' : v === 'floor' ? 'var(--level-floor)' : v === 'air' ? 'var(--text-tertiary)' : 'var(--text-primary)'
          }
        }, v)
      }, {
        key: 'strike',
        label: 'Strike',
        align: 'right',
        muted: true
      }, {
        key: 'level',
        label: 'Level',
        align: 'right'
      }, {
        key: 'value',
        label: '|' + m + '| M',
        align: 'right'
      }, {
        key: 'taps',
        label: 'Taps',
        align: 'right'
      }, {
        key: 'state',
        label: 'State',
        muted: true
      }]
    }))))));
  }
  window.MapScreen = MapScreen;
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/console/MapScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/console/ReportScreen.jsx
try { (() => {
(function () {
  const {
    Tabs,
    Panel,
    Stat,
    KeyValue,
    Sparkline,
    DataTable,
    MeterBar,
    Button,
    IconButton,
    Badge,
    CodeBlock
  } = window.FseDesignSystem_043819;
  function Funnel() {
    const D = window.FSE_DATA;
    const tot = D.funnel[0][1];
    return /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit,minmax(380px,1fr))',
        gap: 16,
        alignItems: 'start'
      }
    }, /*#__PURE__*/React.createElement(Panel, {
      title: "Gate funnel",
      subtitle: tot + ' candidate setups',
      icon: "filter"
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gap: 2
      }
    }, D.funnel.slice(1).map(([g, n]) => /*#__PURE__*/React.createElement(MeterBar, {
      key: g,
      label: g,
      value: n,
      max: tot,
      labelWidth: 180,
      tone: n === 0 ? 'muted' : 'ink',
      display: n === 0 ? 'disabled' : n
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        height: 6
      }
    }), /*#__PURE__*/React.createElement(MeterBar, {
      label: "accepted \u2192 filled",
      value: 38,
      max: tot,
      labelWidth: 180,
      tone: "bull"
    }))), /*#__PURE__*/React.createElement(Panel, {
      title: "Shadow trades",
      subtitle: "rejected by one Gate only",
      icon: "ghost",
      flush: true,
      footer: "A shadow trade is simulated as if its only rejecting Gate were off."
    }, /*#__PURE__*/React.createElement(DataTable, {
      dense: true,
      rows: D.shadow,
      columns: [{
        key: 'gate',
        label: 'Gate'
      }, {
        key: 'key',
        label: 'Setup_Key',
        muted: true
      }, {
        key: 'r',
        label: 'R',
        align: 'right',
        render: v => /*#__PURE__*/React.createElement("span", {
          className: v.startsWith('+') ? 'fse-pos' : 'fse-neg'
        }, v)
      }]
    })));
  }
  function ReportScreen({
    id,
    back
  }) {
    const D = window.FSE_DATA;
    const run = D.runs.find(r => r.id === id) || D.runs[0];
    const [tab, setTab] = React.useState('sum');
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(PageHead, {
      title: run.id,
      sub: run.cfg + ' · ' + run.ses + ' sessions · 2025-10-06 → 2026-10-02 · seed ' + run.seed,
      actions: /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Button, {
        icon: "arrow-left",
        variant: "ghost",
        onClick: back
      }, "Runs"), /*#__PURE__*/React.createElement(IconButton, {
        icon: "copy",
        label: "Copy run id",
        variant: "secondary"
      }), /*#__PURE__*/React.createElement(Button, {
        icon: "file-text"
      }, "report.md"))
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: '0 24px'
      }
    }, /*#__PURE__*/React.createElement(Tabs, {
      value: tab,
      onChange: setTab,
      items: [{
        value: 'sum',
        label: 'Summary'
      }, {
        value: 'trades',
        label: 'Trades',
        count: run.tr
      }, {
        value: 'funnel',
        label: 'Gate funnel'
      }, {
        value: 'man',
        label: 'Manifest'
      }]
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: '16px 24px 24px',
        display: 'grid',
        gridTemplateColumns: 'minmax(0,1fr)',
        gap: 16
      }
    }, tab === 'sum' && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      style: {
        font: 'var(--type-small)',
        color: 'var(--text-tertiary)'
      }
    }, "These figures are measured backtest results on cached data. They are not a forecast of future results."), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))',
        gap: 16
      }
    }, /*#__PURE__*/React.createElement(Stat, {
      size: "lg",
      label: "Combine pass",
      value: run.pass == null ? '—' : run.pass.toFixed(4)
    }), /*#__PURE__*/React.createElement(Stat, {
      label: "Primary win rate",
      value: run.wr.toFixed(1),
      unit: "%",
      sub: "95% CI 29.3 \u2013 60.1"
    }), /*#__PURE__*/React.createElement(Stat, {
      label: "Expectancy",
      value: (run.ex > 0 ? '+' : '') + run.ex.toFixed(2),
      unit: "R",
      tone: run.ex > 0 ? 'pos' : 'neg',
      sub: "95% CI 0.08 \u2013 0.54"
    }), /*#__PURE__*/React.createElement(Stat, {
      label: "Max drawdown",
      value: run.dd.toFixed(2),
      unit: "R",
      tone: "neg"
    }), /*#__PURE__*/React.createElement(Stat, {
      label: "Trades",
      value: run.tr,
      sub: (run.tr / run.ses).toFixed(2) + ' per session'
    })), /*#__PURE__*/React.createElement(Panel, {
      title: "Cumulative R",
      icon: "trending-up",
      subtitle: "by trade, net of $1.00/side"
    }, /*#__PURE__*/React.createElement(Sparkline, {
      values: run.curve,
      width: 1000,
      height: 180,
      fill: true,
      responsive: true,
      tone: "auto"
    })), /*#__PURE__*/React.createElement(Panel, {
      title: "Header"
    }, /*#__PURE__*/React.createElement(KeyValue, {
      columns: 3,
      items: [['config', 'playbook_baseline.yaml'], ['config hash', '3f1a9c…e2'], ['bars', '1 min · Atlas'], ['fills', 'trade-through 1 tick'], ['costs', '$1.00 / contract / side'], ['holdout', 'not included'], ['sizing', 'fixed 5 MES / 3 MNQ'], ['skipped', '2 mixed_roll_session'], ['bootstrap', '10,000 resamples · seed 1']]
    }))), tab === 'trades' && /*#__PURE__*/React.createElement(Panel, {
      flush: true
    }, /*#__PURE__*/React.createElement(DataTable, {
      dense: true,
      rows: D.trades,
      columns: [{
        key: 'date',
        label: 'Session',
        muted: true
      }, {
        key: 'time',
        label: 'Entry',
        muted: true
      }, {
        key: 'inst',
        label: 'Inst'
      }, {
        key: 'pattern',
        label: 'Pattern'
      }, {
        key: 'dir',
        label: 'Dir',
        render: v => /*#__PURE__*/React.createElement(Badge, {
          side: v
        })
      }, {
        key: 'qty',
        label: 'Qty',
        align: 'right'
      }, {
        key: 'entry',
        label: 'Entry',
        align: 'right'
      }, {
        key: 'stop',
        label: 'Stop',
        align: 'right'
      }, {
        key: 'exit',
        label: 'Exit',
        align: 'right'
      }, {
        key: 'r',
        label: 'R',
        align: 'right',
        render: v => /*#__PURE__*/React.createElement("span", {
          className: v.startsWith('+') ? 'fse-pos' : v.startsWith('0') ? 'fse-flat' : 'fse-neg'
        }, v)
      }]
    })), tab === 'funnel' && /*#__PURE__*/React.createElement(Funnel, null), tab === 'man' && /*#__PURE__*/React.createElement(CodeBlock, {
      plain: true,
      code: JSON.stringify({
        run_id: run.id,
        config_id: 'playbook_baseline',
        config_hash: '3f1a9c0b7d…e2',
        schema_version: 1,
        seed: run.seed,
        sessions: run.ses,
        skipped: [{
          session: '2025-12-15',
          reason: 'mixed_roll_session'
        }, {
          session: '2026-03-16',
          reason: 'mixed_roll_session'
        }],
        data_cache: '~/.skylit-fse/cache',
        secrets: '[REDACTED]'
      }, null, 2)
    })));
  }
  Object.assign(window, {
    ReportScreen
  });
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/console/ReportScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/console/RunsScreen.jsx
try { (() => {
(function () {
  const {
    Input,
    Segmented,
    DataTable,
    Sparkline,
    Badge,
    Button,
    Panel
  } = window.FseDesignSystem_043819;
  function RunsScreen({
    openRun
  }) {
    const D = window.FSE_DATA;
    const [q, setQ] = React.useState('');
    const [k, setK] = React.useState('all');
    const rows = D.runs.filter(r => (k === 'all' || r.kind === k) && (r.id + r.cfg).toLowerCase().includes(q.toLowerCase()));
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(PageHead, {
      title: "Runs",
      sub: "Backtests and experiments in ~/.skylit-fse/runs \xB7 newest first",
      actions: /*#__PURE__*/React.createElement(Button, {
        variant: "primary",
        icon: "play"
      }, "New backtest")
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        padding: '0 24px 24px',
        display: 'grid',
        gridTemplateColumns: 'minmax(0,1fr)',
        gap: 12
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 10,
        alignItems: 'center',
        flexWrap: 'wrap'
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        width: 280
      }
    }, /*#__PURE__*/React.createElement(Input, {
      icon: "search",
      size: "sm",
      placeholder: "Filter by run id or config",
      value: q,
      onChange: e => setQ(e.target.value)
    })), /*#__PURE__*/React.createElement(Segmented, {
      options: ['all', 'backtest', 'ablation', 'sweep', 'walkforward', 'cadence'],
      value: k,
      onChange: setK
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1
      }
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: 11,
        color: 'var(--text-tertiary)'
      }
    }, rows.length, " runs")), /*#__PURE__*/React.createElement(Panel, {
      flush: true,
      style: {
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement(DataTable, {
      rows: rows,
      onRowClick: r => openRun(r.id),
      columns: [{
        key: 'id',
        label: 'Run',
        render: v => /*#__PURE__*/React.createElement("span", {
          style: {
            color: 'var(--text-primary)',
            fontWeight: 500
          }
        }, v)
      }, {
        key: 'cfg',
        label: 'Config',
        render: v => /*#__PURE__*/React.createElement("span", {
          style: {
            color: 'var(--text-secondary)'
          }
        }, v)
      }, {
        key: 'kind',
        label: 'Kind',
        render: v => /*#__PURE__*/React.createElement(Badge, {
          variant: "outline"
        }, v)
      }, {
        key: 'ses',
        label: 'Sessions',
        align: 'right'
      }, {
        key: 'tr',
        label: 'Trades',
        align: 'right'
      }, {
        key: 'wr',
        label: 'Win %',
        align: 'right',
        render: v => v.toFixed(1)
      }, {
        key: 'ex',
        label: 'Exp R',
        align: 'right',
        render: v => /*#__PURE__*/React.createElement("span", {
          className: v > 0 ? 'fse-pos' : 'fse-neg'
        }, (v > 0 ? '+' : '') + v.toFixed(2))
      }, {
        key: 'pass',
        label: 'Pass p',
        align: 'right',
        render: v => v == null ? /*#__PURE__*/React.createElement("span", {
          style: {
            color: 'var(--text-disabled)'
          }
        }, "\u2014") : v.toFixed(4)
      }, {
        key: 'curve',
        label: 'Curve',
        render: v => /*#__PURE__*/React.createElement(Sparkline, {
          values: v,
          width: 88,
          height: 16,
          tone: "auto"
        })
      }, {
        key: 'date',
        label: 'Finished',
        muted: true
      }]
    }))));
  }
  window.RunsScreen = RunsScreen;
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/console/RunsScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/console/Shell.jsx
try { (() => {
(function () {
  const PoweredBy = window.FseDesignSystem_043819.PoweredBy || (() => null);
  const {
    Wordmark,
    Breadcrumbs,
    Input,
    StatusDot,
    Badge,
    IconButton,
    SideNav,
    Kbd
  } = window.FseDesignSystem_043819;
  const NAV = [{
    title: 'Operate',
    items: [{
      value: 'live',
      label: 'Live runner',
      icon: 'radio',
      meta: 'paper'
    }, {
      value: 'map',
      label: 'Map',
      icon: 'layers'
    }, {
      value: 'cards',
      label: 'Finding cards',
      icon: 'inbox',
      meta: 3
    }]
  }, {
    title: 'Research',
    items: [{
      value: 'runs',
      label: 'Runs',
      icon: 'history',
      meta: 7
    }, {
      value: 'experiments',
      label: 'Experiments',
      icon: 'flask-conical'
    }]
  }, {
    title: 'Configure',
    items: [{
      value: 'config',
      label: 'Strategy config',
      icon: 'sliders-horizontal'
    }]
  }];
  function TopBar({
    crumbs,
    theme,
    setTheme
  }) {
    return /*#__PURE__*/React.createElement("header", {
      style: {
        height: 'var(--topbar-h)',
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        padding: '0 14px',
        borderBottom: '1px solid var(--border-subtle)',
        background: 'var(--surface-panel)'
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        flex: 'none',
        display: 'flex'
      }
    }, /*#__PURE__*/React.createElement(Wordmark, {
      size: "sm"
    })), /*#__PURE__*/React.createElement("span", {
      style: {
        width: 1,
        height: 20,
        background: 'var(--border-default)'
      }
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1,
        minWidth: 0,
        overflow: 'hidden'
      }
    }, /*#__PURE__*/React.createElement(Breadcrumbs, {
      items: crumbs,
      className: "fse-crumbs--bar"
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        width: 240,
        flexShrink: 1,
        minWidth: 140,
        position: 'relative'
      }
    }, /*#__PURE__*/React.createElement(Input, {
      icon: "search",
      size: "sm",
      placeholder: "Jump to run, config, gate"
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        position: 'absolute',
        right: 6,
        top: 3,
        display: 'flex',
        gap: 2
      }
    }, /*#__PURE__*/React.createElement(Kbd, null, "\u2318"), /*#__PURE__*/React.createElement(Kbd, null, "K"))), /*#__PURE__*/React.createElement(Badge, {
      mode: "paper",
      dot: true
    }), /*#__PURE__*/React.createElement(IconButton, {
      icon: theme === 'light' ? 'moon' : 'sun',
      label: "Toggle theme",
      size: "sm",
      onClick: () => setTheme(theme === 'light' ? 'dark' : 'light')
    }));
  }
  function StatusBar() {
    return /*#__PURE__*/React.createElement("footer", {
      className: "fse-statusbar",
      style: {
        height: 24,
        display: 'flex',
        alignItems: 'center',
        gap: 18,
        padding: '0 14px',
        overflow: 'hidden',
        whiteSpace: 'nowrap',
        borderTop: '1px solid var(--border-subtle)',
        background: 'var(--surface-panel)',
        fontSize: 11,
        color: 'var(--text-tertiary)'
      }
    }, /*#__PURE__*/React.createElement(StatusDot, {
      status: "live",
      label: "Heatseeker stream"
    }), /*#__PURE__*/React.createElement(StatusDot, {
      status: "ok",
      label: "Atlas bars"
    }), /*#__PURE__*/React.createElement("span", {
      className: "sb-lo",
      style: {
        display: 'contents'
      }
    }, /*#__PURE__*/React.createElement(StatusDot, {
      status: "paper",
      label: "Paper broker"
    }), /*#__PURE__*/React.createElement(StatusDot, {
      status: "idle",
      label: "Narrator off"
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1,
        minWidth: 12
      }
    }), /*#__PURE__*/React.createElement(PoweredBy, null), /*#__PURE__*/React.createElement("span", {
      className: "sb-lo",
      style: {
        width: 1,
        height: 12,
        background: 'var(--border-default)',
        flex: 'none'
      }
    }), /*#__PURE__*/React.createElement("span", {
      className: "sb-lo",
      style: {
        flex: 'none'
      }
    }, "~/.skylit-fse/"), /*#__PURE__*/React.createElement("span", {
      className: "sb-lo",
      style: {
        flex: 'none'
      }
    }, "config 3f1a9c"), /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--text-secondary)',
        flex: 'none'
      }
    }, "10:35:12 ET"));
  }
  function Shell({
    page,
    setPage,
    crumbs,
    theme,
    setTheme,
    children
  }) {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        height: '100vh',
        display: 'grid',
        gridTemplateRows: 'auto 1fr auto'
      }
    }, /*#__PURE__*/React.createElement(TopBar, {
      crumbs: crumbs,
      theme: theme,
      setTheme: setTheme
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'grid',
        gridTemplateColumns: 'var(--sidenav-w) minmax(0,1fr)',
        minHeight: 0
      }
    }, /*#__PURE__*/React.createElement("aside", {
      style: {
        borderRight: '1px solid var(--border-subtle)',
        background: 'var(--surface-panel)',
        overflow: 'auto'
      }
    }, /*#__PURE__*/React.createElement(SideNav, {
      sections: NAV,
      value: page,
      onChange: setPage
    })), /*#__PURE__*/React.createElement("main", {
      style: {
        overflow: 'auto',
        minWidth: 0
      }
    }, children)), /*#__PURE__*/React.createElement(StatusBar, null));
  }
  function PageHead({
    title,
    sub,
    actions,
    children
  }) {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        alignItems: 'flex-end',
        gap: 16,
        padding: '20px 24px 16px'
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1,
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("h1", {
      style: {
        margin: 0,
        font: 'var(--type-h1)',
        letterSpacing: 'var(--ls-heading)'
      }
    }, title), sub && /*#__PURE__*/React.createElement("div", {
      style: {
        marginTop: 6,
        font: 'var(--type-small)',
        color: 'var(--text-tertiary)'
      }
    }, sub), children), actions && /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 8
      }
    }, actions));
  }
  Object.assign(window, {
    Shell,
    PageHead
  });
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/console/Shell.jsx", error: String((e && e.message) || e) }); }

// ui_kits/console/data.js
try { (() => {
(function () {
  let s = 7;
  const r = () => (s = s * 16807 % 2147483647) / 2147483647;
  const cum = n => {
    let a = [0],
      v = 0;
    for (let i = 0; i < n; i++) {
      v += r() < .45 ? 1.8 + r() * 1.4 : -1;
      a.push(+v.toFixed(2));
    }
    return a;
  };
  const runs = [{
    id: 'bt_8f2c41',
    cfg: 'playbook_baseline',
    date: '2026-10-06 18:42',
    ses: 248,
    tr: 38,
    wr: 44.7,
    ex: .31,
    dd: -4.2,
    pass: .184,
    kind: 'backtest',
    seed: 1
  }, {
    id: 'bt_77a019',
    cfg: 'baseline · −dark_pool',
    date: '2026-10-06 17:10',
    ses: 248,
    tr: 41,
    wr: 41.5,
    ex: .22,
    dd: -5.0,
    pass: .151,
    kind: 'ablation',
    seed: 1
  }, {
    id: 'bt_3e90cd',
    cfg: 'baseline · rr 2.5',
    date: '2026-10-05 22:03',
    ses: 248,
    tr: 57,
    wr: 38.6,
    ex: -.04,
    dd: -7.8,
    pass: .092,
    kind: 'sweep',
    seed: 1
  }, {
    id: 'bt_c1d2aa',
    cfg: 'baseline · 5m bars',
    date: '2026-10-05 20:31',
    ses: 244,
    tr: 29,
    wr: 48.3,
    ex: .40,
    dd: -3.1,
    pass: .201,
    kind: 'cadence',
    seed: 1
  }, {
    id: 'bt_a0b9f2',
    cfg: 'baseline · −tap_count',
    date: '2026-10-05 19:55',
    ses: 248,
    tr: 66,
    wr: 36.4,
    ex: -.11,
    dd: -9.4,
    pass: .061,
    kind: 'ablation',
    seed: 1
  }, {
    id: 'wf_51e7d0',
    cfg: 'walk-forward 6×(90/30)',
    date: '2026-10-04 23:12',
    ses: 240,
    tr: 33,
    wr: 42.4,
    ex: .18,
    dd: -4.9,
    pass: null,
    kind: 'walkforward',
    seed: 2
  }, {
    id: 'bt_19cc3b',
    cfg: 'playbook_baseline',
    date: '2026-10-03 16:20',
    ses: 180,
    tr: 27,
    wr: 40.7,
    ex: .12,
    dd: -4.6,
    pass: .133,
    kind: 'backtest',
    seed: 1
  }];
  runs.forEach(x => x.curve = cum(x.tr));
  const levels = {
    ES: [{
      kind: 'ceiling',
      level: 5925,
      strike: 5930,
      value: 820
    }, {
      kind: 'gatekeeper',
      level: 5910,
      strike: 5915,
      value: 300
    }, {
      kind: 'king',
      level: 5900,
      strike: 5905,
      value: 1400
    }, {
      kind: 'air',
      level: 5866,
      strike: '—',
      value: 90
    }, {
      kind: 'gatekeeper',
      level: 5862,
      strike: 5865,
      value: 260
    }, {
      kind: 'floor',
      level: 5850,
      strike: 5855,
      value: 610
    }, {
      kind: 'gatekeeper',
      level: 5825,
      strike: 5830,
      value: 180
    }],
    NQ: [{
      kind: 'ceiling',
      level: 20900,
      strike: 509,
      value: 640
    }, {
      kind: 'king',
      level: 20810,
      strike: 507,
      value: 980
    }, {
      kind: 'gatekeeper',
      level: 20740,
      strike: 505,
      value: 280
    }, {
      kind: 'air',
      level: 20705,
      strike: '—',
      value: 70
    }, {
      kind: 'floor',
      level: 20650,
      strike: 503,
      value: 520
    }]
  };
  const spot = {
    ES: 5884.25,
    NQ: 20774.5
  };
  const funnel = [['candidates', 1830], ['stale_map', 412], ['map_grade', 96], ['midpoint', 141], ['deflection_band', 188], ['chart_confluence', 117], ['trinity_agreement', 84], ['candle_color', 62], ['tap_count', 208], ['min_reward_risk', 355], ['opposition_inside_target', 51], ['fomo_travel', 33], ['open_shuffle', 29], ['entry_cutoff', 17], ['dark_pool_confluence', 0], ['stdev_fib_zone', 0]];
  const trades = [['2026-09-29', '10:41', 'MES', 'gatekeeper_fade', 'short', 5, '5910.25', '5914.50', '5900.00', '+2.41'], ['2026-09-30', '11:05', 'MES', 'floor_ceiling_bounce', 'long', 5, '5850.25', '5846.00', '5862.75', '+2.94'], ['2026-10-01', '10:12', 'MNQ', 'rug', 'short', 3, '20808.00', '20822.25', '20740.50', '−1.00'], ['2026-10-01', '13:48', 'MES', 'beach_ball', 'long', 5, '5871.50', '5867.25', '5884.00', '+2.94'], ['2026-10-02', '10:33', 'MES', 'gatekeeper_fade', 'short', 5, '5925.00', '5929.25', '5925.00', '0.00'], ['2026-10-02', '14:20', 'MNQ', 'trend_follow', 'long', 3, '20755.75', '20741.50', '20741.50', '−1.00']].map((t, i) => ({
    id: i,
    date: t[0],
    time: t[1],
    inst: t[2],
    pattern: t[3],
    dir: t[4],
    qty: t[5],
    entry: t[6],
    stop: t[7],
    exit: t[8],
    r: t[9]
  }));
  const shadow = [['stale_map', 'MES rug long 5850 tap 2', '+1.20'], ['min_reward_risk', 'MES gatekeeper_fade short 5910 tap 2', '−1.00'], ['tap_count', 'MNQ floor_ceiling_bounce long 20650 tap 3', '−1.00'], ['trinity_agreement', 'MES beach_ball long 5862 tap 1', '+2.10']].map((t, i) => ({
    id: i,
    gate: t[0],
    key: t[1],
    r: t[2]
  }));
  const log = [['10:35:00', 'card', 'Decision card sent · A+ MES gatekeeper_fade short 5910'], ['10:35:00', 'order', 'PlaceBracket MES sell limit 5910.25 ×5 · stop 5914.50 · tp 5900.00'], ['10:30:00', 'grade', 'MES floor_ceiling_bounce long 5850 → Alert_2R (2.4R)'], ['10:25:00', 'flip', 'ES King 5925 → 5905 (gamma)'], ['10:20:00', 'reject', 'MNQ rug long 20700 tap 3 · tap_count, min_reward_risk'], ['10:15:00', 'map', 'Snapshot asOf 10:14:58 · SPX, SPY, QQQ, NDXP'], ['09:45:00', 'gate', 'open_shuffle window closed'], ['09:20:00', 'card', 'Premarket card sent · Whipsaw · map C']].map((l, i) => ({
    id: i,
    t: l[0],
    ev: l[1],
    msg: l[2]
  }));
  const cards = [{
    time: '10:35:00 ET',
    mode: 'paper',
    trade: {
      instrument: 'MES',
      name: 'Micro E-mini S&P',
      direction: 'short',
      grade: 'A_Plus',
      pattern: 'gatekeeper_fade',
      node: 'ES 5910 gatekeeper',
      tap: 1,
      spot: 5884.25,
      dayPct: -0.18,
      rangePct: 0.62,
      tick: 0.25,
      tickValue: 1.25,
      qty: 5,
      rr: 4.3,
      gates: '15/15',
      entry: 5910.25,
      entryTag: 'gatekeeper',
      stop: 5914.50,
      targets: [{
        label: 'TP1',
        price: 5900,
        qty: 3,
        tag: 'King'
      }, {
        label: 'TP2',
        price: 5880,
        qty: 2,
        tag: 'air pocket'
      }],
      levels: [{
        kind: 'ceiling',
        price: 5925,
        label: 'Ceiling'
      }, {
        kind: 'gatekeeper',
        price: 5910,
        label: 'Gatekeeper'
      }, {
        kind: 'king',
        price: 5900,
        label: 'King'
      }, {
        kind: 'air',
        price: 5893,
        to: 5887,
        label: 'Air pocket'
      }, {
        kind: 'floor',
        price: 5850,
        label: 'Floor'
      }],
      plan: {
        if: 'Rallies into the 5910 gatekeeper — sell limit armed',
        out: 'Stop 5914.50 · moves to breakeven at TP1',
        tgt: '5900 King (2.4R), then 5880 (7.1R) — scale out 3 / 2'
      },
      context: [{
        icon: 'crown',
        text: 'King 5900 · flipped down from 5925'
      }, {
        icon: 'shield',
        text: 'Ceiling 5925 caps the stop · 10.5 pts above'
      }, {
        icon: 'ruler',
        text: 'entry is 26.00 pts away · 1.4× 15-min ATR'
      }],
      odds: {
        label: 'gatekeeper_fade · n=212',
        items: [{
          pct: 64,
          label: 'fill · this session'
        }, {
          pct: 58,
          label: 'TP1 · after fill'
        }, {
          pct: 31,
          label: 'TP2 · after fill'
        }]
      },
      action: 'SELL LIMIT 5910.25 ×5 MES · stop 5914.50 · TP 5900.00 ×3 / 5880.00 ×2',
      actionLabel: 'Armed'
    },
    regime: 'Positive_Gamma',
    mapGrade: 'B',
    trinity: 'agree',
    flip: '5925 → 5905',
    maps: [{
      sym: 'ES',
      king: '5900.00',
      ceiling: '5925.00',
      floor: '5850.00',
      spot: '5884.25'
    }, {
      sym: 'NQ',
      king: '20810.00',
      ceiling: '20900.00',
      floor: '20650.00',
      spot: '20774.50'
    }],
    setups: [{
      grade: 'A_Plus',
      key: 'MES gatekeeper_fade short 5910 tap 1'
    }, {
      grade: 'Alert_2R',
      key: 'MES floor_ceiling_bounce long 5850 tap 1'
    }, {
      grade: 'Pass',
      key: 'MNQ rug long 20700 tap 3',
      reasons: ['tap_count', 'min_reward_risk']
    }],
    notListed: 2,
    orders: 'MES sell limit 5910.25 ×5',
    position: 'flat',
    watchUp: '5900.00',
    watchDown: '5875.00'
  }, {
    time: '10:20:00 ET',
    mode: 'paper',
    regime: 'Positive_Gamma',
    mapGrade: 'B',
    trinity: 'agree',
    maps: [{
      sym: 'ES',
      king: '5925.00',
      ceiling: '5925.00',
      floor: '5850.00',
      spot: '5879.50'
    }],
    setups: [{
      grade: 'Pass',
      key: 'MNQ rug long 20700 tap 3',
      reasons: ['tap_count', 'min_reward_risk', 'trinity_agreement']
    }],
    orders: null,
    position: 'flat',
    watchUp: '5900.00',
    watchDown: '5866.00'
  }, {
    kind: 'premarket',
    time: '09:20:00 ET',
    mode: 'paper',
    regime: 'Whipsaw',
    mapGrade: 'C',
    trinity: null,
    maps: [{
      sym: 'ES',
      king: '5925.00',
      ceiling: '5925.00',
      floor: '5850.00',
      spot: '5871.00'
    }, {
      sym: 'NQ',
      king: '20810.00',
      ceiling: '20900.00',
      floor: null,
      spot: '20702.25'
    }]
  }];
  const ablation = [['Primary win rate', '44.7%', '41.5%', '−3.2'], ['Expectancy', '+0.31 R', '+0.22 R', '−0.09'], ['Trades', '38', '41', '+3'], ['Max drawdown', '−4.20 R', '−5.00 R', '−0.80'], ['Combine pass', '0.1840', '0.1510', '−0.0330']].map((a, i) => ({
    id: i,
    m: a[0],
    b: a[1],
    v: a[2],
    d: a[3]
  }));
  const frontier = [[2.0, .36, 71, -.02], [2.5, .39, 57, -.04], [3.0, .45, 38, .31], [3.5, .47, 26, .29], [4.0, .5, 18, .22]].map((f, i) => ({
    id: i,
    rr: f[0].toFixed(1),
    wr: (f[1] * 100).toFixed(1),
    tr: f[2],
    ex: f[3],
    pareto: [2, 3].includes(i)
  }));
  const wf = [['W1', '2025-10-06 → 2026-01-02', 'rr 3.0', '+0.28', '+0.14'], ['W2', '2025-11-05 → 2026-02-02', 'rr 3.0', '+0.33', '+0.21'], ['W3', '2025-12-05 → 2026-03-04', 'rr 3.5', '+0.25', '−0.06'], ['W4', '2026-01-05 → 2026-04-03', 'rr 3.0', '+0.35', '+0.27'], ['W5', '2026-02-04 → 2026-05-04', 'no selection', '—', '—'], ['W6', '2026-03-06 → 2026-06-03', 'rr 3.0', '+0.30', '+0.19']].map((w, i) => ({
    id: i,
    w: w[0],
    span: w[1],
    pick: w[2],
    is: w[3],
    oos: w[4]
  }));
  const mk = (start, end, n, vol, seed) => {
    let q = seed;
    const rr = () => (q = q * 16807 % 2147483647) / 2147483647;
    const out = [];
    let p = start;
    const tick = .25;
    for (let i = 0; i < n; i++) {
      const o = p;
      const drift = (end - p) / (n - i);
      p = Math.round((p + drift + (rr() - .5) * vol) / tick) * tick;
      if (i === n - 1) p = end;
      const h = Math.max(o, p) + Math.round(rr() * vol * .5 / tick) * tick,
        l = Math.min(o, p) - Math.round(rr() * vol * .5 / tick) * tick;
      out.push({
        o,
        h,
        l,
        c: p
      });
    }
    return out;
  };
  const bars = {
    ES: mk(5871.5, 5884.25, 66, 5, 3),
    NQ: mk(20702.25, 20774.5, 66, 22, 5)
  };
  const times = bars.ES.map((_, i) => {
    const m = 30 + i;
    const hh = 9 + Math.floor(m / 60),
      mm = m % 60;
    return mm % 15 === 0 ? String(hh).padStart(2, '0') + ':' + String(mm).padStart(2, '0') : '';
  });
  cards[0].trade.bars = bars.ES.slice(-40);
  window.FSE_DATA = {
    bars,
    times,
    runs,
    levels,
    spot,
    funnel,
    trades,
    shadow,
    log,
    cards,
    ablation,
    frontier,
    wf
  };
})();
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/console/data.js", error: String((e && e.message) || e) }); }

__ds_ns.Button = __ds_scope.Button;

__ds_ns.IconButton = __ds_scope.IconButton;

__ds_ns.Icon = __ds_scope.Icon;

__ds_ns.Kbd = __ds_scope.Kbd;

__ds_ns.PoweredBy = __ds_scope.PoweredBy;

__ds_ns.StatusDot = __ds_scope.StatusDot;

__ds_ns.Wordmark = __ds_scope.Wordmark;

__ds_ns.Badge = __ds_scope.Badge;

__ds_ns.CandleChart = __ds_scope.CandleChart;

__ds_ns.DataTable = __ds_scope.DataTable;

__ds_ns.KeyValue = __ds_scope.KeyValue;

__ds_ns.LevelLadder = __ds_scope.LevelLadder;

__ds_ns.MeterBar = __ds_scope.MeterBar;

__ds_ns.Sparkline = __ds_scope.Sparkline;

__ds_ns.Stat = __ds_scope.Stat;

__ds_ns.FindingCard = __ds_scope.FindingCard;

__ds_ns.Checkbox = __ds_scope.Checkbox;

__ds_ns.Input = __ds_scope.Input;

__ds_ns.Segmented = __ds_scope.Segmented;

__ds_ns.Select = __ds_scope.Select;

__ds_ns.Switch = __ds_scope.Switch;

__ds_ns.Breadcrumbs = __ds_scope.Breadcrumbs;

__ds_ns.SideNav = __ds_scope.SideNav;

__ds_ns.Tabs = __ds_scope.Tabs;

__ds_ns.Banner = __ds_scope.Banner;

__ds_ns.CodeBlock = __ds_scope.CodeBlock;

__ds_ns.Dialog = __ds_scope.Dialog;

__ds_ns.Panel = __ds_scope.Panel;

__ds_ns.Toast = __ds_scope.Toast;

__ds_ns.Tooltip = __ds_scope.Tooltip;

})();
