import type { MapLevel } from '../data/LevelLadder';

export interface FindingCardData {
  kind?: 'decision' | 'premarket';
  /** New York time, e.g. "10:35:00 ET". */
  time: string;
  mode?: 'paper' | 'practice' | 'combine';
  regime?: string | null;
  mapGrade?: string | null;
  trinity?: string | null;
  maps?: Array<{ sym: string; king?: string | null; ceiling?: string | null; floor?: string | null; spot?: string | null }>;
  /** King flip since the previous card, e.g. "5905 → 5925". */
  flip?: string;
  setups?: Array<{ grade: 'A_Plus' | 'Alert_2R' | 'Pass'; key: string; reasons?: string[]; why?: string[] }>;
  notListed?: number;
  orders?: React.ReactNode;
  position?: React.ReactNode;
  watchUp?: string | null;
  watchDown?: string | null;
  /** Premarket plain-English summary (compact card). */
  summary?: string | null;
  /** Narrator (or fallback) prose — why enter / skip / pass / result. */
  whyPassed?: string | null;
  /** The primary setup's plan. When present on a decision card, the card renders the rich layout. */
  trade?: FindingTrade;
  /** GEX LevelLadder rows for the Map panel (heatmap magnitude by node). */
  ladder?: MapLevel[];
  /** Spot price for the ladder dashed row. */
  ladderSpot?: number;
}
export interface FindingTrade {
  instrument: string; name?: string; direction: 'long' | 'short';
  grade: 'A_Plus' | 'Alert_2R' | 'Pass'; pattern: string; node: string; tap: number;
  spot: number; dayPct?: number | null; rangePct?: number | null;
  /** Tick size and $ per tick, e.g. 0.25 / 1.25 for MES. */
  tick?: number; tickValue?: number; qty: number;
  /** Position-weighted reward:risk. */
  rr: number; gates?: string | null;
  entry: number; entryTag?: string; stop: number;
  targets: Array<{ label: string; price: number; qty: number; tag?: string }>;
  /** Map nodes; air pockets take a `to` price and render as a band. Off-scale levels list under the chart. */
  levels?: Array<{ kind: 'king' | 'ceiling' | 'floor' | 'gatekeeper' | 'wall' | 'air'; price: number; to?: number; label?: string }>;
  plan: { if: string; out: string; tgt: string };
  context?: Array<{ icon: string; text: string }>;
  odds?: { label: string; items: Array<{ pct: number; label: string }> } | null;
  action?: string; actionLabel?: string;
  /** Recent 1-min OHLC bars ending at spot. Empty when the bar store has no session bars. */
  bars?: Array<{ o: number; h: number; l: number; c: number }>;
}
/**
 * @startingPoint section="Domain" subtitle="Finding_Card notification as rendered in the feed" viewport="1100x1100"
 */
export interface FindingCardProps {
  card: FindingCardData;
  className?: string;
}
export declare function FindingCard(props: FindingCardProps): JSX.Element;
