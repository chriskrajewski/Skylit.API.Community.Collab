export interface WordmarkProps {
  /** sm 15px (top bar) · md 20px · lg 32px · xl 64px (splash/cover). */
  size?: 'sm' | 'md' | 'lg' | 'xl';
  /** Show the "futures strategy engine" name after a hairline rule. Default true. */
  lockup?: boolean;
  /** Break the name onto two lines. */
  stacked?: boolean;
  className?: string;
}
export declare function Wordmark(props: WordmarkProps): JSX.Element;