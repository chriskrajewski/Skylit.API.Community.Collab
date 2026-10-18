// The Lucide icons the console uses, bundled at build time (lucide-static, pinned
// to the design system's 0.469.0). Add a name to ICON_NAMES to use a new icon;
// a unit test fails when a component asks for a name that is not here.
export const ICON_NAMES = [
  // design-system components
  'check', 'chevrons-up-down', 'x', 'triangle-alert', 'info', 'octagon-x', 'zap', 'circle-check',
  'octagon-alert', 'crown', 'shield', 'ruler', 'sunrise', 'radar',
  // console
  'activity', 'arrow-left', 'arrow-right-left', 'bell', 'chart-candlestick', 'chart-line',
  'chevron-right', 'circle-play', 'circle-stop', 'clock', 'copy', 'database', 'download',
  'eye', 'file-code', 'file-text', 'filter', 'flask-conical', 'folder', 'git-compare',
  'hard-drive', 'history', 'inbox', 'key-round', 'layers', 'list', 'list-checks', 'log-out',
  'menu', 'moon', 'play', 'plus', 'puzzle', 'radio', 'refresh-cw', 'rotate-ccw', 'save', 'scroll-text',
  'search', 'server', 'shield-check', 'sliders-horizontal', 'square', 'sun', 'terminal',
  'trash-2', 'undo-2', 'upload', 'workflow', 'crosshair', 'gauge',
];

// The glob lists the same names literally (Vite needs a static pattern), so only
// these icons are bundled.
const RAW = import.meta.glob(
  '/node_modules/lucide-static/icons/{activity,arrow-left,arrow-right-left,bell,chart-candlestick,chart-line,check,chevron-right,chevrons-up-down,circle-check,circle-play,circle-stop,clock,copy,crosshair,crown,database,download,eye,file-code,file-text,filter,flask-conical,folder,gauge,git-compare,hard-drive,history,inbox,info,key-round,layers,list,list-checks,log-out,menu,moon,octagon-alert,octagon-x,play,plus,puzzle,radar,radio,refresh-cw,rotate-ccw,ruler,save,scroll-text,search,server,shield,shield-check,sliders-horizontal,square,sun,sunrise,terminal,trash-2,triangle-alert,undo-2,upload,workflow,x,zap}.svg',
  {
  query: '?raw',
  import: 'default',
  eager: true,
});

function clean(text) {
  return text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/width="24"|height="24"/g, '')
    .replace('<svg', '<svg width="100%" height="100%"');
}

const SVGS = {};
for (const name of ICON_NAMES) {
  const raw = RAW['/node_modules/lucide-static/icons/' + name + '.svg'];
  if (typeof raw === 'string') SVGS[name] = clean(raw);
}

export function iconSvg(name) {
  return SVGS[name] || '';
}

export function hasIcon(name) {
  return name in SVGS;
}
