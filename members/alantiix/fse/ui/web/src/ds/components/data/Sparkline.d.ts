export interface SparklineProps {
  /** Series, e.g. cumulative R per trade. A dashed zero baseline is drawn. */
  values: number[];
  width?: number;
  height?: number;
  /** accent = ice · bull · bear · auto = bull/bear by the last value's sign · ink · muted. */
  tone?: 'accent' | 'bull' | 'bear' | 'auto' | 'ink' | 'muted';
  baseline?: boolean;
  /** Faint area fill to the zero line. */
  fill?: boolean;
  /** Stretch to 100% of the container width (viewBox kept, strokes non-scaling). */
  responsive?: boolean;
  className?: string;
}
export declare function Sparkline(props: SparklineProps): JSX.Element | null;