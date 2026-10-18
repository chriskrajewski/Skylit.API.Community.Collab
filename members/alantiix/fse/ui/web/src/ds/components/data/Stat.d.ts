export interface StatProps {
  label: React.ReactNode;
  /** Pre-formatted value, e.g. "0.42", "+3.10", "not applicable". */
  value: React.ReactNode;
  unit?: React.ReactNode;
  sub?: React.ReactNode;
  /** pos = accent, neg = neutral ink, flat = tertiary. */
  tone?: 'pos' | 'neg' | 'flat';
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}
export declare function Stat(props: StatProps): JSX.Element;