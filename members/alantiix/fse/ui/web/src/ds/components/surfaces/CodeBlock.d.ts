export interface CodeBlockProps {
  /** Source text. YAML keys/values/comments are tinted unless plain. */
  code: string;
  /** 1-based line numbers to highlight (e.g. a changed config key). */
  highlight?: number[];
  numbers?: boolean;
  /** Disable YAML tinting (CLI output, JSON). */
  plain?: boolean;
  maxHeight?: number | string;
  className?: string;
}
export declare function CodeBlock(props: CodeBlockProps): JSX.Element;