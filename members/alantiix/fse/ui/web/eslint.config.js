// ESLint flat config. The design-system adherence rules come from
// design_system/_adherence.oxlintrc.json (the single source), applied with two changes:
//
// 1. The per-component prop whitelists also allow the HTML attributes each
//    component's .d.ts accepts (Button extends ButtonHTMLAttributes, Input extends
//    InputHTMLAttributes, ...): onClick, disabled, checked, value, aria-*, and so on.
//    The DS selectors list declared props only, so `<Button onClick>` would warn
//    although the component forwards it.
// 2. The import patterns get a `**/` prefix so they match relative imports
//    (`../ds/components/...`), not only bare `components/...` specifiers.
//
// oxlint does not implement `no-restricted-syntax`, so ESLint runs these rules.
import { readFileSync } from 'node:fs';
import tseslint from 'typescript-eslint';
import globals from 'globals';

const adherence = JSON.parse(
  readFileSync(new URL('../../design_system/_adherence.oxlintrc.json', import.meta.url), 'utf8'),
);

const HTML_ATTRS = [
  'onClick', 'onChange', 'onKeyDown', 'onSubmit', 'onBlur', 'onFocus', 'onInput',
  'disabled', 'checked', 'defaultChecked', 'value', 'defaultValue', 'type', 'name', 'id',
  'placeholder', 'autoFocus', 'required', 'min', 'max', 'step', 'title', 'role', 'tabIndex',
  'readOnly', 'accept', 'multiple', 'autoComplete', 'spellCheck', 'maxLength', 'inputMode',
  'form', 'aria-label', 'aria-describedby', 'aria-invalid', 'aria-pressed', 'data-testid',
];

const [level, ...selectors] = adherence.rules['no-restricted-syntax'];
const patched = selectors.map((s) => ({
  ...s,
  selector: s.selector.replace('[name!=/^(?:', `[name!=/^(?:${HTML_ATTRS.join('|')}|`),
}));
const [importLevel, importOpts] = adherence.rules['no-restricted-imports'];
const importPatterns = importOpts.patterns.map((p) => ({
  ...p,
  group: p.group.map((g) => `**/${g}`),
}));

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'src/ds/**'] },
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: { globals: { ...globals.browser } },
    rules: {
      'no-restricted-syntax': [level === 'warn' ? 'error' : level, ...patched],
      'no-restricted-imports': [importLevel === 'warn' ? 'error' : importLevel,
        { patterns: importPatterns }],
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'src/test/**'],
    rules: { 'no-restricted-syntax': 'off', 'no-restricted-imports': 'off' },
  },
);
