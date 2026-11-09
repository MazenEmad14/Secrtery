/** How a row of the action log is shown in Settings. Kept apart from the component so it can be tested. */

const KINDS = {
  send_email: 'Email',
  create_calendar_event: 'Calendar event',
  save_reminder: 'Reminder',
  drive_save: 'Google Drive',
};

export const STATUS_LABELS = {
  done: { text: 'Done', tone: 'good' },
  failed: { text: 'Failed', tone: 'bad' },
  rejected: { text: 'Rejected by you', tone: 'muted' },
  approved: { text: 'Approved', tone: 'muted' },
  awaiting_approval: { text: 'Waiting for your approval', tone: 'warn' },
  expired: { text: 'Never approved', tone: 'muted' },
  unconfirmed: { text: 'Not confirmed: check it', tone: 'warn' },
};

export function actionKind(type) {
  return KINDS[type] || 'Action';
}

/** One line saying what the action was: who or what, never the body. */
export function actionSummary(row) {
  const s = row.summary || {};
  switch (row.action_type) {
    case 'send_email': return [s.to && `to ${s.to}`, s.subject && `"${s.subject}"`].filter(Boolean).join(' ');
    case 'create_calendar_event': return [s.title, s.date, s.time].filter(Boolean).join(' ');
    case 'save_reminder': return [s.task, s.date, s.time].filter(Boolean).join(' ');
    case 'drive_save': return s.name || '';
    default: return '';
  }
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || { text: status, tone: 'muted' };
}
