import { describe, expect, it } from 'vitest';
import {
  cardRowToFindingCardData,
  decisionCardWhy,
  passWhyFallback,
  toFindingCardData,
  toFindingCardDataFromDecision,
} from '../lib/findingCard';
import type { CardRow, DecisionCard } from '../lib/types';

const S = 1e9;
const NOW = 1_791_800_000 * S;

function decisionRow(over: Partial<DecisionCard> = {}): CardRow {
  const card: DecisionCard = {
    id: 'a1',
    title: 'Long MNQ at the open',
    as_of_ns: NOW,
    action: 'enter',
    instrument: 'MNQ',
    symbol: null,
    side: 'long',
    qty: 1,
    entry: 20000.25,
    stop: 19990,
    targets: [20010],
    reason: 'above the EMA',
    ref: null,
    tag: 'a1',
    result: null,
    fields: [['EMA', '21000.5']],
    ...over,
  };
  return {
    plugin: 'plugin_e',
    t_ns: NOW,
    format: 'decision',
    kind: 'entry',
    card,
    result: null,
    raw: { t_ns: NOW, plugin: 'plugin_e', card },
  };
}

describe('toFindingCardData', () => {
  it('maps engine trade into rich FindingCardData and Pass why from narration', () => {
    const data = toFindingCardData({
      narration: 'Taps were exhausted at this node.',
      card: {
        kind: 'decision',
        decision_time_ny: '2026-10-14T10:35:00-04:00',
        order_mode: 'paper',
        regime: 'Positive_Gamma',
        map_grade: 'B',
        trinity: { SPX: 'above', SPY: 'above', QQQ: 'above' },
        map: [{ symbol: 'ES', metric: 'gamma', king: { level: 5900 }, ceiling: { level: 5925 }, floor: { level: 5850 } }],
        spot: { ES: 5884.25 },
        setups: [{ grade: 'Pass', key: 'MES gatekeeper_fade short 5910 tap 1', why: ['Already used the allowed taps at this node'] }],
        ladder: [
          { kind: 'king', level: 5900, strike: 5905, value: 1400, label: 'King' },
          { kind: 'floor', level: 5850, strike: 5855, value: 610, label: 'Floor' },
        ],
        trade: {
          instrument: 'MES', direction: 'short', grade: 'Pass', pattern: 'gatekeeper_fade',
          node: 'ES 5910 gatekeeper', tap: 1, spot: 5884.25, qty: 5, rr: 2.4,
          entry: 5910.25, stop: 5914.5, targets: [{ label: 'TP1', price: 5900, qty: 3 }],
          plan: { if: 'IF', out: 'OUT', tgt: 'TGT' }, bars: [],
        },
      },
    });
    expect(data.time).toBe('10:35:00 ET');
    expect(data.trinity).toBe('agree');
    expect(data.trade?.grade).toBe('Pass');
    expect(data.whyPassed).toMatch(/Taps were exhausted/);
    expect(data.maps?.[0]?.king).toBe('5900.00');
    expect(data.ladder?.length).toBe(2);
    expect(data.ladder?.[0]?.value).toBe(1400);
  });

  it('falls back to setups.why when Narrator is off', () => {
    const card = {
      setups: [{
        grade: 'Pass',
        key: 'x',
        why: ['Already used the allowed taps at this node', 'Reward:risk is below the minimum gate'],
      }],
    };
    expect(passWhyFallback(card)).toMatch(/Passed — Already used the allowed taps/);
    const data = toFindingCardData({ card: { ...card, kind: 'decision', decision_time_ny: '2026-10-14T10:00:00-04:00' } });
    expect(data.whyPassed).toMatch(/Passed —/);
  });

  it('narrates A+ setups when Narrator is off', () => {
    const data = toFindingCardData({
      card: {
        kind: 'decision',
        decision_time_ny: '2026-10-14T10:00:00-04:00',
        setups: [{ grade: 'A_Plus', key: 'MES gatekeeper_fade short 5910', why: ['Cleared midpoint with trinity agree'] }],
        trade: {
          instrument: 'MES', direction: 'short', grade: 'A_Plus', pattern: 'gatekeeper_fade',
          node: 'ES 5910', tap: 1, spot: 5884, qty: 5, rr: 2.4,
          entry: 5910, stop: 5914, targets: [{ label: 'TP1', price: 5900, qty: 3 }],
          plan: { if: 'IF', out: 'OUT', tgt: 'TGT' }, bars: [],
        },
      },
    });
    expect(data.whyPassed).toMatch(/Cleared midpoint/);
  });
});

describe('toFindingCardDataFromDecision', () => {
  it('builds a rich trade from an enter decision card', () => {
    const data = toFindingCardDataFromDecision(decisionRow());
    expect(data.trade?.instrument).toBe('MNQ');
    expect(data.trade?.direction).toBe('long');
    expect(data.trade?.grade).toBe('A_Plus');
    expect(data.trade?.entry).toBe(20000.25);
    expect(data.trade?.stop).toBe(19990);
    expect(data.trade?.targets[0]?.price).toBe(20010);
    expect(data.trade?.tickValue).toBe(0.5);
    expect(data.trade?.plan.if).toMatch(/EMA/);
    expect(data.summary).toBeUndefined();
    expect(data.whyPassed).toMatch(/Entered/);
  });

  it('uses MNQ tick value so stop P&L is not MES-priced', () => {
    // 438 ticks * $0.50 * 5 = $1,095 (not 438 * $1.25 * 5 = $2,737.50).
    const data = toFindingCardDataFromDecision(
      decisionRow({
        qty: 5,
        entry: 30131.25,
        stop: 30021.75,
        targets: [30165.75],
      }),
    );
    expect(data.trade?.tickValue).toBe(0.5);
    const ticks = Math.round(Math.abs(30131.25 - 30021.75) / 0.25);
    expect(ticks).toBe(438);
    expect(ticks * (data.trade?.tickValue ?? 0) * 5).toBe(1095);
  });

  it('builds rich Pass chrome for unpriced skip with why', () => {
    const data = toFindingCardDataFromDecision(
      decisionRow({
        action: 'skip',
        side: null,
        qty: null,
        entry: null,
        stop: null,
        targets: [],
        title: 'Slot 09:45 skipped',
        reason: 'late_bar (1200 ms)',
        fields: [['slot', '09:45'], ['spot', '20100.5']],
        spot: 20100.5,
      }),
    );
    expect(data.trade).toBeDefined();
    expect(data.trade?.grade).toBe('Pass');
    expect(data.trade?.actionLabel).toBe('Skip');
    expect(data.trade?.action).toMatch(/SKIP/);
    expect(data.trade?.spot).toBe(20100.5);
    expect(data.whyPassed).toMatch(/Skipped/);
    expect(data.whyPassed).toMatch(/late bar/);
    expect(data.summary).toBeUndefined();
  });

  it('attaches bars and dayPct when the decision card carries them', () => {
    const bars = Array.from({ length: 40 }, (_, i) => ({
      o: 20000 + i, h: 20001 + i, l: 19999 + i, c: 20000.5 + i,
    }));
    const data = toFindingCardDataFromDecision(
      decisionRow({ bars, dayPct: -0.12, rangePct: 0.45, gates: '12/15' }),
    );
    expect(data.trade?.bars?.length).toBe(40);
    expect(data.trade?.dayPct).toBe(-0.12);
    expect(data.trade?.rangePct).toBe(0.45);
    expect(data.trade?.gates).toBe('12/15');
  });

  it('builds rich Pass chrome for info cards with narration fallback', () => {
    const data = toFindingCardDataFromDecision(
      decisionRow({
        action: 'info',
        side: null,
        qty: null,
        entry: null,
        stop: null,
        targets: [],
        title: 'Order_Mode combine in use',
        reason: 'orders go to the account named by COMBINE_ACCOUNT_ID',
      }),
    );
    expect(data.trade?.grade).toBe('Pass');
    expect(data.trade?.actionLabel).toBe('Info');
    expect(data.whyPassed).toMatch(/orders go to the account/);
  });

  it('decisionCardWhy prefers narration when present', () => {
    expect(decisionCardWhy({
      ...decisionRow().card!,
      action: 'skip',
      narration: 'Price never cleared the open shuffle window.',
    })).toBe('Price never cleared the open shuffle window.');
  });

  it('cardRowToFindingCardData routes decision vs finding_card', () => {
    expect(cardRowToFindingCardData(decisionRow()).trade?.instrument).toBe('MNQ');
    const finding: CardRow = {
      plugin: 'playbook',
      t_ns: NOW,
      format: 'finding_card',
      title: 'Finding_Card',
      source: 'x/cards.jsonl',
      raw: {
        card: {
          kind: 'decision',
          decision_time_ny: '2026-10-14T10:00:00-04:00',
          setups: [{ grade: 'Pass', key: 'x', why: ['thin R'] }],
        },
      },
    };
    expect(cardRowToFindingCardData(finding).whyPassed).toMatch(/Passed —/);
  });
});
