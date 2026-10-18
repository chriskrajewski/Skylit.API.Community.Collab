export interface TabsProps {
  items: Array<{ value: string; label: React.ReactNode; count?: number }>;
  value: string;
  onChange?: (value: string) => void;
  className?: string;
}
export declare function Tabs(props: TabsProps): JSX.Element;