import { describe, expect, it } from 'vitest'
import { formatWorkflowActionDetail } from './workflowActions'

describe('formatWorkflowActionDetail', () => {
  it('formats FastAPI validation error arrays as text', () => {
    expect(
      formatWorkflowActionDetail([
        { loc: ['body', 'ref'], msg: 'Field required', type: 'missing' },
        {
          loc: ['body', 'cron_expression'],
          msg: 'Invalid cron',
          type: 'value_error',
        },
      ]),
    ).toBe('body.ref: Field required\nbody.cron_expression: Invalid cron')
  })

  it('keeps strings and serializes object details', () => {
    expect(formatWorkflowActionDetail('plain error')).toBe('plain error')
    expect(formatWorkflowActionDetail({ message: 'failed' })).toBe(
      '{"message":"failed"}',
    )
  })
})
