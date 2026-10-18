import { describe, expect, it } from 'vitest';
import { DASH, MINUS, money } from '../lib/format';

describe('money (API decimal strings, no float)', () => {
  it('signs, groups and keeps the cents sent', () => {
    expect(money('8.88')).toBe('+$8.88');
    expect(money('-120.50')).toBe(`${MINUS}$120.50`);
    expect(money('50090.40')).toBe('+$50,090.40');
    expect(money('1234567890123456.07')).toBe('+$1,234,567,890,123,456.07');
    expect(money('0.00')).toBe('$0.00');
    expect(money('-0.00')).toBe('$0.00');
    expect(money('7')).toBe('+$7.00');
  });

  it('drops the plus sign for balances', () => {
    expect(money('50090.40', { signed: false })).toBe('$50,090.40');
    expect(money('-5.00', { signed: false })).toBe(`${MINUS}$5.00`);
  });

  it('shows missing values and passes unparseable text through', () => {
    expect(money(null)).toBe(DASH);
    expect(money(undefined, { missing: 'n/a' })).toBe('n/a');
    expect(money('abc')).toBe('abc');
  });
});
