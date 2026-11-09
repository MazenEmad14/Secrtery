import assert from 'node:assert/strict';
import { test } from 'node:test';

import { actionKind, actionSummary, statusLabel } from './actionLog.js';

test('an email row says who it went to and the subject, nothing else', () => {
  const row = { action_type: 'send_email', summary: { to: 'ahmed@example.com', subject: 'Running late' } };
  assert.equal(actionKind(row.action_type), 'Email');
  assert.equal(actionSummary(row), 'to ahmed@example.com "Running late"');
});

test('rows for the other actions are described from their own fields', () => {
  assert.equal(actionSummary({ action_type: 'create_calendar_event', summary: { title: 'Review', date: '2026-12-01', time: '14:00' } }), 'Review 2026-12-01 14:00');
  assert.equal(actionSummary({ action_type: 'save_reminder', summary: { task: 'Call Sara' } }), 'Call Sara');
  assert.equal(actionSummary({ action_type: 'drive_save', summary: { name: 'Notes' } }), 'Notes');
  assert.equal(actionSummary({ action_type: 'something_new', summary: {} }), '');
  assert.equal(actionKind('something_new'), 'Action');
});

test('every status the server can send has a label, and an unconfirmed action is flagged as needing a look', () => {
  for (const status of ['done', 'failed', 'rejected', 'approved', 'awaiting_approval', 'expired', 'unconfirmed']) {
    assert.ok(statusLabel(status).text.length > 0, status);
  }
  assert.equal(statusLabel('unconfirmed').tone, 'warn');
  assert.equal(statusLabel('a-new-status').text, 'a-new-status');
});
