import { Alert, Button, Drawer, List, Spin, Typography } from 'antd'
import { useDailyIssueDetail } from '../../../hooks/useDailyPipeline'
import MarkdownRenderer from '../../../components/MarkdownRenderer'
import { formatCollectionTime } from '../collection/collectionDisplay'
import './DailyIssueDrawer.css'

interface DailyIssueDrawerProps {
  number: number
  open: boolean
  onClose: () => void
}

function DailyIssueDrawer({ number, open, onClose }: DailyIssueDrawerProps) {
  const { data, isLoading, isFetching, isError, refetch } = useDailyIssueDetail(number, open)
  return <Drawer rootClassName="daily-issue-drawer" title={data ? `#${data.number}-${data.state === 'open' ? 'open' : 'close'}` : `#${number}`}
    placement="right" width={760} styles={{ wrapper: { maxWidth: '100vw' } }} open={open} onClose={onClose} destroyOnHidden>
    {isLoading ? <Spin size="small" /> : isError ? <Alert type="error" showIcon message="Issue 加载失败" action={
      <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
    } /> : data && <>
      <Typography.Text className="daily-issue-title">{data.title}</Typography.Text>
      <MarkdownRenderer content={data.body} />
      <div className="daily-issue-comments">
        <Typography.Text>评论（{data.comments.length}）</Typography.Text>
        <List dataSource={data.comments} renderItem={(comment) => <List.Item key={comment.id}>
          <article className="daily-issue-comment">
            <div className="daily-issue-comment-meta">
              <span>{comment.author}</span>
              <time dateTime={comment.createdAt}>{formatCollectionTime(comment.createdAt)}</time>
            </div>
            <MarkdownRenderer content={comment.body} />
          </article>
        </List.Item>} />
      </div>
    </>}
  </Drawer>
}

export default DailyIssueDrawer
