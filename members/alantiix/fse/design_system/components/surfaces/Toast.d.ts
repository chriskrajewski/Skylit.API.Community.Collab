export interface ToastProps {
  /** info · accent (new A+) · ok (bull, filled / closed in profit) · warning (2R alerts, stale map, fallback to Paper) · danger (bracket rejected, kill switch). */
  tone?: 'info' | 'accent' | 'ok' | 'warning' | 'danger';
  title: React.ReactNode;
  message?: React.ReactNode;
  icon?: string;
  action?: React.ReactNode;
  onClose?: () => void;
}
export declare function Toast(props: ToastProps): JSX.Element;