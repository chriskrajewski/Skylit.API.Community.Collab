export interface DataTableColumn<T = any> {
  key: string;
  label: React.ReactNode;
  /** Numbers align right. */
  align?: 'left' | 'right';
  width?: number | string;
  /** Render in tertiary ink (ids, timestamps). */
  muted?: boolean;
  render?: (value: any, row: T) => React.ReactNode;
}
/**
 * @startingPoint section="Data" subtitle="Dense sortable-looking data table" viewport="700x320"
 */
export interface DataTableProps<T = any> {
  columns: DataTableColumn<T>[];
  rows: T[];
  rowKey?: string;
  /** 26px rows instead of 32px. */
  dense?: boolean;
  onRowClick?: (row: T) => void;
  selectedKey?: string | number;
  maxHeight?: number | string;
  className?: string;
}
export declare function DataTable(props: DataTableProps): JSX.Element;