import assert from 'node:assert/strict';
import { test } from 'node:test';

import { urlBase64ToUint8Array } from './pushKey.js';

test('a VAPID public key (base64url, no padding) becomes the raw bytes PushManager expects', () => {
  // 65 bytes starting with 0x04 is an uncompressed P-256 point, which is what the server publishes.
  const raw = Uint8Array.from([4, ...Array.from({ length: 64 }, (_, i) => (i * 7 + 3) % 256)]);
  const encoded = Buffer.from(raw).toString('base64url');
  assert.deepEqual(Array.from(urlBase64ToUint8Array(encoded)), Array.from(raw));
});

test('lengths that need 0, 1 and 2 padding characters all decode', () => {
  for (const text of ['abc', 'abcd', 'abcde']) {
    assert.equal(Buffer.from(urlBase64ToUint8Array(Buffer.from(text).toString('base64url'))).toString(), text);
  }
});
