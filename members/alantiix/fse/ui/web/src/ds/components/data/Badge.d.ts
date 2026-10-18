export interface BadgeProps {
  variant?: 'neutral' | 'outline' | 'accent' | 'accent-outline' | 'accent-soft' | 'bull' | 'bear' | 'warning' | 'warning-outline' | 'info' | 'danger' | 'danger-solid';
  /** Shortcut for setup Grades: A_Plus (solid accent), Alert_2R (warning outline), Pass (neutral). */
  grade?: 'A_Plus' | 'Alert_2R' | 'Pass';
  /** Shortcut for Order_Modes: paper (outline), practice (warning soft), combine (alarm outline). */
  mode?: 'paper' | 'practice' | 'combine';
  /** Trade direction: long (bull soft), short (bear soft). */
  side?: 'long' | 'short';
  dot?: boolean;
  children?: React.ReactNode;
  className?: string;
}
export declare function Badge(props: BadgeProps): JSX.Element;