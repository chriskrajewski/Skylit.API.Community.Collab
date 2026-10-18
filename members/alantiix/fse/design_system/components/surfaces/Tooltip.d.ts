export interface TooltipProps {
  content: React.ReactNode;
  side?: 'top' | 'bottom';
  children: React.ReactNode;
}
export declare function Tooltip(props: TooltipProps): JSX.Element;