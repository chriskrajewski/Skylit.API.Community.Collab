export interface DialogProps {
  open?: boolean;
  title: React.ReactNode;
  children?: React.ReactNode;
  /** Buttons, right-aligned. */
  footer?: React.ReactNode;
  onClose?: () => void;
  /** danger = solid alarm strip + red ring + red title, for halt / live-order confirmations. */
  tone?: 'default' | 'danger';
  /** Text of the solid alarm strip on danger dialogs. Default "Affects live state". */
  alarmLabel?: string;
  /** Render in-flow instead of fixed (for previews). */
  inline?: boolean;
  width?: number;
}
export declare function Dialog(props: DialogProps): JSX.Element | null;