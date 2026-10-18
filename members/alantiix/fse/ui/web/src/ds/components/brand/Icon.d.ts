export interface IconProps {
  /** Lucide icon name, kebab-case (e.g. "activity", "flask-conical", "git-branch"). */
  name: string;
  /** Pixel size. 14 is the default in dense UI; 12 inside sm controls; 16 for nav. */
  size?: number;
  className?: string;
  style?: React.CSSProperties;
  /** Accessible label; omit for decorative icons. */
  title?: string;
}
export declare function Icon(props: IconProps): JSX.Element;