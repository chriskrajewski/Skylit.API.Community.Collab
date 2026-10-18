/**
 * @startingPoint section="Actions" subtitle="Primary, secondary, ghost, danger buttons" viewport="700x260"
 */
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** primary = the one accent action per view · secondary (default) · ghost · danger = solid alarm fill (halt, flatten, live orders) · danger-outline (destructive but not urgent: clear, delete run). */
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-outline';
  /** sm 24px · md 28px · lg 34px. */
  size?: 'sm' | 'md' | 'lg';
  /** Lucide icon name before the label. */
  icon?: string;
  iconRight?: string;
  /** Shortcut hint shown after the label, e.g. "⌘↵". */
  kbd?: string;
  children?: React.ReactNode;
}
export declare function Button(props: ButtonProps): JSX.Element;