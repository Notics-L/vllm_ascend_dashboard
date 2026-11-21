import { useState } from 'react'
import { Button } from 'antd'
import type { DailyCollectionRun } from '../../../types/dailyPipeline'
import DailyIssueDrawer from '../shared/DailyIssueDrawer'

function CollectionIssueLink({ issue, showState = true }: { issue: DailyCollectionRun['issue']; showState?: boolean }) {
  const [open, setOpen] = useState(false)
  if (!issue) return <>—</>
  return <>
    <Button type="link" className="daily-collection-link" onClick={() => setOpen(true)}>
      #{issue.number}{showState && `-${issue.state === 'open' ? 'open' : 'close'}`}
    </Button>
    <DailyIssueDrawer number={issue.number} open={open} onClose={() => setOpen(false)} />
  </>
}

export default CollectionIssueLink
