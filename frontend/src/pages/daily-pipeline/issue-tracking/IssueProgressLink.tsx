import { Link } from 'react-router-dom'

function IssueProgressLink({ issueNumber }: { issueNumber: number }) {
  return <Link className="daily-collection-history-link" to={`/daily-pipeline/issue-tracking/issues/${issueNumber}/progress`}>查看</Link>
}

export default IssueProgressLink
