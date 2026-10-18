export interface StatusDotProps {
  /** live = pulsing accent (engine running on live data) · paper = hollow ring · ok = bull · warning (stale feed) · idle · halted = alarm. */
  status?: 'live' | 'paper' | 'ok' | 'warning' | 'idle' | 'halted';
  label?: React.ReactNode;
  className?: string;
}
export declare function StatusDot(props: StatusDotProps): JSX.Element;