export interface MeterBarProps {
  label: React.ReactNode;
  value: number;
  max?: number;
  /** Right-hand text; defaults to value. */
  display?: React.ReactNode;
  /** ink (default) · accent (the row that matters) · muted (disabled gate). */
  tone?: 'ink' | 'accent' | 'bull' | 'bear' | 'warning' | 'muted';
  labelWidth?: number | string;
  className?: string;
}
export declare function MeterBar(props: MeterBarProps): JSX.Element;