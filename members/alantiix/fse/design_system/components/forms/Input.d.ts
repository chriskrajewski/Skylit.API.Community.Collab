export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size' | 'prefix'> {
  label?: React.ReactNode;
  hint?: React.ReactNode;
  /** Error message; border + hint in --ds-validation-error (bear). */
  error?: React.ReactNode;
  /** Warning message (e.g. OPERATOR: required); border + hint in --ds-validation-warning. */
  warning?: React.ReactNode;
  /** Leading Lucide icon (e.g. "search"). */
  icon?: string;
  /** Inline unit before the value, e.g. "$". */
  prefix?: React.ReactNode;
  /** Inline unit after the value, e.g. "R", "pts", "min". */
  suffix?: React.ReactNode;
  size?: 'sm' | 'md';
}
export declare function Input(props: InputProps): JSX.Element;