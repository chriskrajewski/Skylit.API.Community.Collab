export interface Candle { o: number; h: number; l: number; c: number; }
export interface ChartLevel {
  price: number;
  label?: string;
  /** king = solid ice · ceiling = dashed bear · floor = dashed bull · gatekeeper = dashed fog · air = dotted · spot = solid mist with filled tag. */
  kind: 'king' | 'ceiling' | 'floor' | 'gatekeeper' | 'air' | 'spot';
}
/**
 * @startingPoint section="Data" subtitle="Candles with Heatseeker level overlays" viewport="700x320"
 */
export interface CandleChartProps {
  /** OHLC bars, oldest first. Up candles use the ice candle tokens; down = grey body, white wick/border. */
  candles: Candle[];
  /** Horizontal level lines with right-axis price tags. */
  levels?: ChartLevel[];
  height?: number;
  /** Right price axis (64px). Default true. */
  axis?: boolean;
  /** Optional per-candle time labels (use '' to skip). */
  times?: string[];
  className?: string;
}
export declare function CandleChart(props: CandleChartProps): JSX.Element;