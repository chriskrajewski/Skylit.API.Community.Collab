export interface BannerProps {
  /** info (ice) · warning (operator action needed) · alarm (solid red, hatched — runner halted, kill switch tripped). */
  tone?: 'info' | 'warning' | 'alarm';
  title?: React.ReactNode;
  children?: React.ReactNode;
  /** Buttons on the right. */
  actions?: React.ReactNode;
  /** Full-bleed under the top bar: no radius, bottom border only. */
  flush?: boolean;
  icon?: string;
  className?: string;
}
export declare function Banner(props: BannerProps): JSX.Element;