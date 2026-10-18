export interface SelectProps extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  label?: React.ReactNode;
  hint?: React.ReactNode;
  /** Strings or {value,label} pairs. */
  options: Array<string | { value: string; label: string }>;
  size?: 'sm' | 'md';
}
export declare function Select(props: SelectProps): JSX.Element;