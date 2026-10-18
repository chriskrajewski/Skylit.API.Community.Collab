import { useEffect, useRef } from 'react';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap, lineNumbers, highlightActiveLine } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { yaml } from '@codemirror/lang-yaml';
import { tags } from '@lezer/highlight';

// Themed only with design-system tokens.
const theme = EditorView.theme({
  '&': { backgroundColor: 'var(--surface-input)', color: 'var(--text-primary)' },
  '.cm-content': { caretColor: 'var(--accent)', fontFamily: 'var(--font-mono)' },
  '.cm-gutters': { backgroundColor: 'var(--surface-panel)', color: 'var(--text-disabled)', border: 'none' },
  '.cm-activeLine': { backgroundColor: 'var(--accent-wash)' },
  '.cm-activeLineGutter': { backgroundColor: 'var(--surface-hover)' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': { backgroundColor: 'var(--signal-wash-2)' },
  '.cm-scroller': { fontFamily: 'var(--font-mono)' },
});
const highlight = HighlightStyle.define([
  { tag: tags.propertyName, color: 'var(--text-accent)' },
  { tag: [tags.definition(tags.propertyName)], color: 'var(--text-accent)' },
  { tag: tags.comment, color: 'var(--text-tertiary)' },
  { tag: [tags.bool, tags.null], color: 'var(--color-warning)' },
  { tag: [tags.number], color: 'var(--color-bull)' },
  { tag: [tags.string], color: 'var(--text-primary)' },
]);

/** A YAML editor (CodeMirror 6). ``gotoLine`` scrolls to and selects that line. */
export function YamlEditor({ value, onChange, readOnly, gotoLine }: {
  value: string; onChange?: (v: string) => void; readOnly?: boolean; gotoLine?: number | null;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const change = useRef(onChange);
  change.current = onChange;

  useEffect(() => {
    if (!host.current) return;
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(), highlightActiveLine(), history(), keymap.of([...defaultKeymap, ...historyKeymap]),
          yaml(), syntaxHighlighting(highlight), theme,
          EditorState.readOnly.of(!!readOnly), EditorView.editable.of(!readOnly),
          EditorView.updateListener.of((u) => { if (u.docChanged) change.current?.(u.state.doc.toString()); }),
        ],
      }),
    });
    view.current = v;
    return () => v.destroy();
  }, [readOnly]);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
    }
  }, [value]);

  useEffect(() => {
    const v = view.current;
    if (!v || !gotoLine) return;
    const line = v.state.doc.line(Math.min(Math.max(1, gotoLine), v.state.doc.lines));
    v.dispatch({ selection: { anchor: line.from, head: line.to }, scrollIntoView: true });
    v.focus();
  }, [gotoLine]);

  return <div className="editor" ref={host} />;
}
