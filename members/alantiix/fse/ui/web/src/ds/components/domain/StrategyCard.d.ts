export type StrategyGrade = 'PASS' | 'FAIL' | 'DRAFT';

export interface StrategySource {
  url: string;
  author: string;
  post_id: string;
  text: string;
}

export interface StrategyProbability {
  label: string;
  pct: number | null;
}

export interface StrategyCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface StrategyEquity {
  trades: number | null;
  net: string | null;
  expectancy_r: string | null;
}

export interface StrategyCardData {
  id?: string;
  name?: string;
  hypothesis?: string;
  grade?: StrategyGrade;
  source?: StrategySource;
  rules?: string[];
  probabilities?: StrategyProbability[];
  judge?: StrategyCheck[];
  equity?: StrategyEquity;
  side?: string;
  symbols?: string[];
  timeframe?: string;
  spec_hash?: string;
  design_hash?: string;
}

export interface StrategyCardProps {
  card: StrategyCardData;
  className?: string;
}

export interface BacktestCardProps {
  card: StrategyCardData;
  className?: string;
}

export declare function StrategyCard(props: StrategyCardProps): JSX.Element;
export declare function BacktestCard(props: BacktestCardProps): JSX.Element;
