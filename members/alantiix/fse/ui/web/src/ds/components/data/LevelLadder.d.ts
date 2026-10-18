export interface MapLevel {
  kind: 'king' | 'ceiling' | 'floor' | 'gatekeeper' | 'air';
  /** Converted futures level in points (ES/NQ). */
  level: number;
  /** Source option strike (SPX/QQQ/NDXP). */
  strike?: number | string;
  /** Node magnitude (abs GEX/VEX), drives bar length. */
  value?: number;
  label?: string;
}
/**
 * @startingPoint section="Data" subtitle="Heatseeker map levels ladder with spot" viewport="700x320"
 */
export interface LevelLadderProps {
  levels: MapLevel[];
  /** Futures price; a dashed spot row is inserted in sort order. */
  spot?: number;
  spotLabel?: string;
  className?: string;
}
export declare function LevelLadder(props: LevelLadderProps): JSX.Element;