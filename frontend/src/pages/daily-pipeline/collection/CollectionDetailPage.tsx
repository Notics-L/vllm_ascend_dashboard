import { useLocation, useNavigate, useParams } from 'react-router-dom'
import DailyCasePage from '../cases/DailyCasePage'
import CollectionDetail from './CollectionDetail'
import './Collection.css'

function CollectionDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const state = location.state as { returnTo?: string } | null
  const returnTo = state?.returnTo === '/daily-pipeline/issue-tracking'
    ? '/daily-pipeline/issue-tracking'
    : '/daily-pipeline/collection'
  return <DailyCasePage title="采集详情" onBack={() => navigate(returnTo, { replace: true })}>
    <CollectionDetail id={runId ?? null} />
  </DailyCasePage>
}

export default CollectionDetailPage
