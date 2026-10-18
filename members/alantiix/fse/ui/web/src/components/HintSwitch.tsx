import type { ChangeEvent, ReactNode } from 'react';
import { Switch } from '../ds/index.js';

/** A Switch with its explanation shown under it (not only as a hover tooltip). */
export function HintSwitch({ label, hint, checked, onChange, disabled }: {
  label: ReactNode; hint?: ReactNode; checked: boolean;
  onChange: (e: ChangeEvent<HTMLInputElement>) => void; disabled?: boolean;
}) {
  return (
    <div className="hint-switch">
      <Switch label={label} checked={checked} onChange={onChange} disabled={disabled} />
      {hint ? <div className="small muted">{hint}</div> : null}
    </div>
  );
}

type Schema = Record<string, any>;

/** The description of ``field`` in the ``$defs[model]`` of a pydantic JSON schema. */
export function describeField(schema: Schema | undefined, model: string, field: string): string | undefined {
  const node = model ? schema?.$defs?.[model] : schema;
  const d = node?.properties?.[field]?.description;
  return typeof d === 'string' ? d : undefined;
}
