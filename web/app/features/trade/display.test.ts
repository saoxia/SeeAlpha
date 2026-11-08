import test from 'node:test';
import assert from 'node:assert/strict';
import { formatPrice, priceDigits, fundingCountdown } from './display.ts';

test('display precision follows tick decimals including small and exponent prices', () => {
  assert.equal(priceDigits('0.10'), 1);
  assert.equal(priceDigits('0.00001000'), 5);
  assert.equal(priceDigits('1e-8'), 8);
  assert.equal(priceDigits('10'), 0);
  assert.equal(priceDigits(undefined), 2);
  assert.equal(formatPrice('76000.70455072', 1), '76000.7');
  assert.equal(formatPrice('76024.47195652', 2), '76024.47');
  assert.equal(formatPrice('0.00001234', 8), '0.00001234');
  assert.equal(formatPrice('1', 3), '1.000');
  for (const value of [undefined, null, '', 'invalid']) assert.equal(formatPrice(value, 1), '—');
});

test('funding countdown uses the supplied settlement and never invents another interval', () => {
  const now = 1_700_000_000_000;
  assert.equal(fundingCountdown(now + (4*3600+29*60+56)*1000, now), '04:29:56');
  assert.equal(fundingCountdown(now+1, now), '00:00:01');
  assert.equal(fundingCountdown(now-1000, now), '00:00:00');
  assert.equal(fundingCountdown(undefined, now), '—');
});
