export interface PoweredByProps {
  /** Lead-in text. Default "Data powered by". */
  label?: string;
  /** Trailing text after the name. Default "API". Pass "" to omit. */
  suffix?: string;
  /** Optional path to an official Skylit logo file, shown unmodified before the name. Not used by default (type only). */
  logoSrc?: string;
  href?: string;
  /** sm 11px (status bar, footers) · md 13px (splash, reports). */
  size?: 'sm' | 'md';
  className?: string;
}
export declare function PoweredBy(props: PoweredByProps): JSX.Element;