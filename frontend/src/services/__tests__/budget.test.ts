/**
 * Unit tests for budget service helpers — formatSats, triggerLabel, txTypeLabel
 */
import { describe, it, expect } from 'vitest';
import { formatSats, triggerLabel, txTypeLabel } from '@/services/budget';

describe('formatSats', () => {
  it('formats small amounts as sats', () => {
    expect(formatSats(500)).toBe('500 sats');
  });

  it('formats thousands with locale comma', () => {
    const result = formatSats(50_000);
    expect(result).toContain('50');
    expect(result).toContain('sats');
  });

  it('formats >= 100M sats as BTC', () => {
    expect(formatSats(100_000_000)).toBe('1.00000000 BTC');
    expect(formatSats(250_000_000)).toBe('2.50000000 BTC');
  });

  it('formats exactly 1 BTC', () => {
    expect(formatSats(100_000_000)).toContain('BTC');
  });

  it('formats zero', () => {
    expect(formatSats(0)).toBe('0 sats');
  });
});

describe('triggerLabel', () => {
  it('maps no_budget', () => {
    expect(triggerLabel('no_budget')).toBe('No Budget Set');
  });

  it('maps over_budget', () => {
    expect(triggerLabel('over_budget')).toBe('Over Budget');
  });

  it('maps global_limit', () => {
    expect(triggerLabel('global_limit')).toBe('Exceeds Global Limit');
  });

  it('maps manual_review', () => {
    expect(triggerLabel('manual_review')).toBe('Manual Review');
  });

  it('returns raw string for unknown trigger', () => {
    // @ts-expect-error testing unknown input
    expect(triggerLabel('some_future_trigger')).toBe('some_future_trigger');
  });
});

describe('txTypeLabel', () => {
  it('maps lightning_send', () => {
    expect(txTypeLabel('lightning_send')).toContain('Lightning Send');
  });

  it('maps lightning_receive', () => {
    expect(txTypeLabel('lightning_receive')).toContain('Lightning Receive');
  });

  it('maps onchain_send', () => {
    expect(txTypeLabel('onchain_send')).toContain('On-chain Send');
  });

  it('maps onchain_receive', () => {
    expect(txTypeLabel('onchain_receive')).toContain('On-chain Receive');
  });

  it('returns raw string for unknown type', () => {
    // @ts-expect-error testing unknown input
    expect(txTypeLabel('unknown_type')).toBe('unknown_type');
  });
});
