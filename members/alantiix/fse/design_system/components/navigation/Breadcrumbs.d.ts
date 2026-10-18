export interface BreadcrumbsProps {
  /** Path segments; last is the current location. */
  items: Array<{ label: React.ReactNode; href?: string; onClick?: () => void }>;
  className?: string;
}
export declare function Breadcrumbs(props: BreadcrumbsProps): JSX.Element;