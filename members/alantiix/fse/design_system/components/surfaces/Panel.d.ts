/**
 * @startingPoint section="Surfaces" subtitle="Hairline panel with header actions" viewport="700x300"
 */
export interface PanelProps {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: string;
  actions?: React.ReactNode;
  footer?: React.ReactNode;
  /** Remove body padding (for tables, ladders). */
  flush?: boolean;
  children?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}
export declare function Panel(props: PanelProps): JSX.Element;