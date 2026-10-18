export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Lucide icon name. */
  icon: string;
  /** Required accessible label; also the native tooltip. */
  label: string;
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-outline';
  size?: 'sm' | 'md' | 'lg';
}
export declare function IconButton(props: IconButtonProps): JSX.Element;