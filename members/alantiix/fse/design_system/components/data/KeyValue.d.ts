export interface KeyValueProps {
  /** [label, value] pairs. */
  items: Array<[React.ReactNode, React.ReactNode]>;
  /** Number of label/value column pairs. */
  columns?: number;
  className?: string;
}
export declare function KeyValue(props: KeyValueProps): JSX.Element;