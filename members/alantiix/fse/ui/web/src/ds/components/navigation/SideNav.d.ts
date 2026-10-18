export interface SideNavItem { value: string; label: React.ReactNode; icon?: string; meta?: React.ReactNode; }
export interface SideNavProps {
  sections: Array<{ title?: string; items: SideNavItem[] }>;
  value: string;
  onChange?: (value: string) => void;
  className?: string;
}
export declare function SideNav(props: SideNavProps): JSX.Element;