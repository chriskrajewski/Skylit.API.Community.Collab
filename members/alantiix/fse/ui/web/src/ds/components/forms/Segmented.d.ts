export interface SegmentedProps {
  options: Array<string | { value: string; label: string }>;
  value: string;
  onChange?: (value: string) => void;
  className?: string;
}
export declare function Segmented(props: SegmentedProps): JSX.Element;