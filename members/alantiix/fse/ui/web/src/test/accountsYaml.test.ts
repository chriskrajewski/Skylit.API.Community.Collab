import { describe, expect, it } from 'vitest';
import { parseDocument } from 'yaml';
import {
  NEXUS_DEFAULT, PRESET_RULES, accountEntry, formFromEntry, presetDiff, removeAccount, resolveRules, setLeaf, writeAccount,
} from '../lib/accountsYaml';

const YAML = `# runner config
version: 1
plugins:
  - id: plugin_a   # the first plugin
    module: research.plugin_a.plugin:PluginA
    account: topstep_a
  - id: plugin_b
    module: research.plugin_b.plugin:PluginB
    account: topstep_a
  - id: plugin_d
    module: research.plugin_c.plugin:PluginC
    account: other
accounts:
  - id: topstep_a
    preset: topstep_50k_combine
  - id: other   # kept
    preset: none
`;

describe('removeAccount', () => {
  it('clears every plugin that named it and leaves the rest of the text as it was', () => {
    const doc = parseDocument(YAML);
    expect(removeAccount(doc, 'topstep_a')).toEqual(['plugin_a', 'plugin_b']);
    expect(doc.toString()).toBe(`# runner config
version: 1
plugins:
  - id: plugin_a # the first plugin
    module: research.plugin_a.plugin:PluginA
  - id: plugin_b
    module: research.plugin_b.plugin:PluginB
  - id: plugin_d
    module: research.plugin_c.plugin:PluginC
    account: other
accounts:
  - id: other # kept
    preset: none
`);
  });

  it('drops the accounts key with its last account, and returns [] for an unused one', () => {
    const doc = parseDocument('version: 1\nplugins:\n  - id: p\n    module: m\naccounts:\n  - id: a\n    preset: none\n');
    expect(removeAccount(doc, 'a')).toEqual([]);
    expect(doc.toString()).toBe('version: 1\nplugins:\n  - id: p\n    module: m\n');
  });
});

describe('preset-diff writer', () => {
  it('writes only the fields that differ from the preset', () => {
    const base = PRESET_RULES.topstep_50k_combine;
    let rules = setLeaf({}, ['starting_balance', 'value'], '100000.00');
    rules = setLeaf(rules, ['maximum_loss_limit', 'enabled'], true); // same as the preset
    rules = setLeaf(rules, ['daily_loss_limit', 'value'], '1000'); // the same amount, other text
    rules = setLeaf(rules, ['position_cap', 'micro_equivalents'], 30);
    expect(presetDiff(base, resolveRules(base, rules))).toEqual({
      starting_balance: { value: '100000.00' },
      position_cap: { micro_equivalents: 30 },
    });
    expect(accountEntry({ id: 'a', preset: 'topstep_50k_combine', rules, nexus: { ...NEXUS_DEFAULT } })).toEqual({
      id: 'a', preset: 'topstep_50k_combine',
      rules: { starting_balance: { value: '100000.00' }, position_cap: { micro_equivalents: 30 } },
    });
  });

  it('diffs against the chosen preset, and writes nexus keys only when they differ', () => {
    const rules = setLeaf({}, ['daily_loss_limit', 'enabled'], false);
    expect(accountEntry({ id: 'a', preset: 'none', rules, nexus: { ...NEXUS_DEFAULT } })).toEqual({ id: 'a', preset: 'none' });
    const nexus = { enabled: true, account: 'env:SKYLIT_NEXUS_ACCT', test_only: false };
    expect(accountEntry({ id: 'a', preset: 'none', rules: {}, nexus })).toEqual({
      id: 'a', preset: 'none', nexus: { enabled: true, account: 'env:SKYLIT_NEXUS_ACCT' },
    });
  });

  it('setLeaf removes a leaf and prunes empty parents', () => {
    const r = setLeaf({ a: { b: 1 }, c: 2 }, ['a', 'b'], undefined);
    expect(r).toEqual({ c: 2 });
  });

  it('writes an edited account back and carries a renamed id to its plugins', () => {
    const doc = parseDocument(YAML);
    const form = formFromEntry({ id: 'topstep_a', preset: 'topstep_50k_combine' });
    writeAccount(doc, 0, { ...form, id: 'combine_a', rules: { starting_balance: { value: '25000.00' } } });
    const data = doc.toJSON();
    expect(data.accounts[0]).toEqual({ id: 'combine_a', preset: 'topstep_50k_combine', rules: { starting_balance: { value: '25000.00' } } });
    expect(data.plugins.map((p: { account?: string }) => p.account)).toEqual(['combine_a', 'combine_a', 'other']);
    writeAccount(doc, null, formFromEntry({ id: 'third', preset: 'none' }));
    expect(doc.toJSON().accounts.map((a: { id: string }) => a.id)).toEqual(['combine_a', 'other', 'third']);
  });

  it('creates the accounts list when the YAML has none', () => {
    const doc = parseDocument('version: 1\nplugins:\n  - id: p\n    module: m\n');
    writeAccount(doc, null, formFromEntry({ id: 'a', preset: 'topstep_50k_combine' }));
    expect(doc.toJSON().accounts).toEqual([{ id: 'a', preset: 'topstep_50k_combine' }]);
  });
});
