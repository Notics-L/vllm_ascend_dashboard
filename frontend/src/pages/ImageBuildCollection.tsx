import { useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Alert, Button, ConfigProvider, Descriptions, Modal, Skeleton, Space, Table, Tabs, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { isAxiosError } from 'axios'
import { RedoOutlined, ReloadOutlined } from '@ant-design/icons'
import DailyCasePage from './daily-pipeline/cases/DailyCasePage'
import CollectionDuration from './daily-pipeline/collection/CollectionDuration'
import { collectionGithubLink, executionResult, formatCollectionTime } from './daily-pipeline/collection/collectionDisplay'
import { useDailyRefreshFeedback } from './daily-pipeline/shared/useDailyRefreshFeedback'
import { useHasAdminRole } from '../hooks/useRoles'
import {
  getImageBuildCollectionRun,
  getImageBuildCollectionRuns,
  imageBuildCollectionQueryKey,
  refreshImageBuildCollectionRun,
  rerunFailedImageBuildJobs,
  type ImageBuildCollectionRun,
  type ImageBuildMatrixRow,
  type ImageBuildPublication,
  type ImageBuildPublishedImage,
  type ImageBuildStageResult,
} from '../services/imageBuild'
import './daily-pipeline/collection/Collection.css'

const imageBuildCollectionPath = '/image-build?tab=collection'
const collectionDetailPath = (runId: number) => `/image-build/collection/runs/${runId}`
const pageSize = 20
const listPollIntervalMs = 15000
const detailPollIntervalMs = 5000
const publicationLabels = {
  pending: '等待汇总',
  published: '已生成',
  skipped: '已跳过',
  unavailable: '未获取',
}

function collectionError(error: unknown, fallback: string) {
  if (isAxiosError<{ detail?: string }>(error) && typeof error.response?.data?.detail === 'string') {
    return error.response.data.detail
  }
  return fallback
}

function detailQueryKey(runId: number) {
  return [...imageBuildCollectionQueryKey, 'run', runId] as const
}

function useImageBuildRerun() {
  const queryClient = useQueryClient()
  const canManage = useHasAdminRole()
  const [rerunTarget, setRerunTarget] = useState<ImageBuildCollectionRun>()
  const [notifications, notificationHolder] = message.useMessage()
  const rerun = useMutation({
    mutationFn: rerunFailedImageBuildJobs,
    onSuccess: ({ status }) => {
      if (status === 'submitted') notifications.success('GitHub 已接收重跑请求，采集将继续跟踪')
      else notifications.warning('GitHub 尚未确认重跑，采集将继续检查原 Run')
      setRerunTarget(undefined)
    },
    onError: (error) => {
      notifications.error(collectionError(error, '重跑请求失败'))
      setRerunTarget(undefined)
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: imageBuildCollectionQueryKey }),
  })

  const rerunDialog = (
    <ConfigProvider wave={{ disabled: true }}>
      {notificationHolder}
      <Modal
        className="daily-cases-actions"
        open={Boolean(rerunTarget)}
        title="重跑"
        okText="重跑"
        cancelText="取消"
        okType="default"
        okButtonProps={{ size: 'small', color: 'primary', variant: 'solid' }}
        cancelButtonProps={{ size: 'small', disabled: rerun.isPending }}
        transitionName=""
        maskTransitionName=""
        confirmLoading={rerun.isPending}
        closable={!rerun.isPending}
        keyboard={!rerun.isPending}
        maskClosable={false}
        onOk={() => {
          if (canManage && rerunTarget?.canRerun && !rerun.isPending) rerun.mutate(rerunTarget.runId)
        }}
        onCancel={() => setRerunTarget(undefined)}
      >
        <p>Run {rerunTarget?.runId} 的失败 Job 和依赖它们的 Job 将进入下一次运行。</p>
      </Modal>
    </ConfigProvider>
  )

  return { canManage, isPending: rerun.isPending, setRerunTarget, rerunDialog }
}

function githubLink(url: string, label: string) {
  return <a className="daily-collection-history-link" href={url} target="_blank" rel="noopener noreferrer">{label}</a>
}

function shortValue(value: string, length: number) {
  return <Typography.Text code title={value}>{value.slice(0, length)}…</Typography.Text>
}

function stageResult(stage: ImageBuildStageResult) {
  if (stage.total === 0) return '—'
  return <Space size={6}>
    <span>{stage.succeeded}/{stage.total}</span>
    {executionResult(stage.result)}
  </Space>
}

const matrixColumns: ColumnsType<ImageBuildMatrixRow> = [
  { title: '芯片', dataIndex: 'chip', width: 100 },
  { title: '操作系统', dataIndex: 'os', width: 135 },
  { title: 'Base', dataIndex: 'base', width: 140, render: stageResult },
  { title: 'Nightly', dataIndex: 'nightly', width: 150, render: stageResult },
  { title: '组合耗时', key: 'duration', render: (_, row) => <CollectionDuration run={row} /> },
  { title: '链接', key: 'links', width: 135, render: (_, row) => (
    <Space size={12}>
      {row.base.jobUrl && githubLink(row.base.jobUrl, 'Base')}
      {row.nightly.jobUrl && githubLink(row.nightly.jobUrl, 'Nightly')}
    </Space>
  ) },
]

export function ImageBuildCollection() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const { canManage, isPending, setRerunTarget, rerunDialog } = useImageBuildRerun()
  const runs = useQuery({
    queryKey: [...imageBuildCollectionQueryKey, 'list', page],
    queryFn: ({ signal }) => getImageBuildCollectionRuns(page, pageSize, signal),
    placeholderData: keepPreviousData,
    refetchInterval: listPollIntervalMs,
  })

  const columns: ColumnsType<ImageBuildCollectionRun> = [
    { title: 'Run ID', dataIndex: 'runId', width: 135 },
    { title: 'Workflow 分支', dataIndex: 'workflowBranch', width: 128 },
    { title: '芯片', key: 'chips', width: 160, render: (_, run) => run.chips.join(' / ') || '—' },
    { title: '构建结果', dataIndex: 'result', width: 94, render: executionResult },
    { title: '发布汇总', key: 'publication', width: 94, render: (_, run) => (
      <Tag color={run.publicationStatus === 'published' ? 'green' : 'default'}>
        {publicationLabels[run.publicationStatus]}
      </Tag>
    ) },
    { title: '开始时间', dataIndex: 'startedAt', width: 155, render: formatCollectionTime },
    { title: '耗时', key: 'duration', width: 98, render: (_, run) => <CollectionDuration run={run} /> },
    { title: '链接', dataIndex: 'githubUrl', width: 76, render: collectionGithubLink },
    { title: '查看', key: 'details', width: 85, render: (_, run) => (
      <Link className="daily-collection-link" to={collectionDetailPath(run.runId)}>详情</Link>
    ) },
    { title: '重跑', key: 'rerun', width: 85, render: (_, run) => (
      <Button
        type="link"
        className="daily-collection-link daily-collection-history-link"
        disabled={!canManage || !run.canRerun || isPending}
        onClick={() => setRerunTarget(run)}
      >
        重跑
      </Button>
    ) },
  ]

  return (
    <>
      {runs.isError && <Alert
        type="error"
        showIcon
        message={collectionError(runs.error, '构建采集记录加载失败')}
        action={<Button size="small" onClick={() => void runs.refetch()}>重试</Button>}
      />}
      <Table
        className="daily-collection-table"
        size="small"
        rowKey="runId"
        columns={columns}
        dataSource={runs.data?.items ?? []}
        loading={runs.isPending}
        pagination={{ current: page, pageSize, total: runs.data?.total, onChange: setPage, showSizeChanger: false, hideOnSinglePage: true }}
        scroll={{ x: 1110 }}
        onRow={(run) => ({
          onDoubleClick: (event) => {
            if (!(event.target instanceof Element) || event.target.closest('a, button')) return
            navigate(collectionDetailPath(run.runId))
          },
        })}
      />
      {rerunDialog}
    </>
  )
}

const publicationColumns: ColumnsType<ImageBuildPublishedImage> = [
  { title: '芯片 / 系统', key: 'target', width: 145, render: (_, image) => `${image.chip} / ${image.os}` },
  { title: '类型', dataIndex: 'layer', width: 78 },
  { title: '发布仓库', dataIndex: 'repositories', width: 290, render: (_, { repositories }) => (
    <Space direction="vertical" size={0} style={{ width: '100%' }}>
      {repositories.map((repository) => (
        <Typography.Text key={repository} ellipsis={{ tooltip: repository }} style={{ maxWidth: '100%' }}>
          {repository}
        </Typography.Text>
      ))}
    </Space>
  ) },
  { title: 'Tag', dataIndex: 'tag', width: 310, ellipsis: true },
  { title: 'Digest', dataIndex: 'digest', width: 152, render: (digest: string) => shortValue(digest, 15) },
  { title: '源码 SHA', dataIndex: 'commits', width: 190, render: (_, { commits }) => (
    commits.arm64 && commits.arm64 === commits.amd64
      ? shortValue(commits.arm64, 12)
      : <Space direction="vertical" size={2}>
        {commits.arm64 && <span>arm64 {shortValue(commits.arm64, 12)}</span>}
        {commits.amd64 && <span>amd64 {shortValue(commits.amd64, 12)}</span>}
        {!commits.arm64 && !commits.amd64 && '—'}
      </Space>
  ) },
]

function PublicationInfo({ publication }: { publication: ImageBuildPublication }) {
  return <Table
    size="small"
    rowKey={(image) => `${image.chip}/${image.os}/${image.layer}/${image.tag}`}
    title={() => <Space size={12}>
      <Typography.Text type="secondary">源码分支 {publication.sourceBranch}</Typography.Text>
      {githubLink(publication.reportUrl, 'build-info.json（ZIP）')}
    </Space>}
    dataSource={publication.images}
    columns={publicationColumns}
    pagination={false}
    scroll={{ x: 1165 }}
  />
}

export function ImageBuildCollectionDetail() {
  const params = useParams<{ runId: string }>()
  const runId = Number(params.runId)
  const validRunId = Number.isSafeInteger(runId) && runId > 0
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { canManage, isPending, setRerunTarget, rerunDialog } = useImageBuildRerun()
  const [notifications, notificationHolder] = message.useMessage()
  const goBack = () => navigate(imageBuildCollectionPath, { replace: true })
  const detail = useQuery({
    queryKey: detailQueryKey(runId),
    queryFn: ({ signal }) => getImageBuildCollectionRun(runId, signal),
    enabled: validRunId,
    refetchInterval: (query) => query.state.data?.collecting ? detailPollIntervalMs : false,
  })
  const run = detail.data
  const refresh = useDailyRefreshFeedback(async () => {
    if (!validRunId) return
    try {
      const latest = await refreshImageBuildCollectionRun(runId)
      queryClient.setQueryData(detailQueryKey(runId), latest)
      void queryClient.invalidateQueries({ queryKey: [...imageBuildCollectionQueryKey, 'list'] })
    } catch (error) {
      notifications.error(collectionError(error, '构建采集刷新失败'))
    }
  })

  if (!run) {
    return <DailyCasePage title="采集详情" onBack={goBack}>
      {validRunId && detail.isPending ? <Skeleton active /> : <Alert
        type={validRunId ? 'error' : 'info'}
        showIcon
        message={validRunId ? collectionError(detail.error, '构建采集详情加载失败') : '未找到该 Run 的采集记录'}
        action={validRunId && <Button size="small" onClick={() => void detail.refetch()}>重试</Button>}
      />}
    </DailyCasePage>
  }

  const { publication } = run

  return (
    <DailyCasePage title="采集详情" onBack={goBack}>
      {notificationHolder}
      {rerunDialog}
      <section className="daily-collection-detail">
        <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
          { key: 'run', label: 'Run ID', children: run.runId },
          { key: 'workflow', label: 'Workflow', children: run.workflow },
          { key: 'branch', label: 'Workflow 分支', children: run.workflowBranch },
          { key: 'sha', label: 'Workflow SHA', children: run.workflowSha ? shortValue(run.workflowSha, 12) : '—' },
          { key: 'start', label: '开始时间', children: formatCollectionTime(run.startedAt) },
          { key: 'end', label: '结束时间', children: formatCollectionTime(run.endedAt) },
          { key: 'duration', label: '总耗时', children: <CollectionDuration run={run} /> },
          { key: 'actor', label: '触发人', children: run.actor },
          { key: 'status', label: '构建结果', children: <Space size={8}>
            {executionResult(run.result)}
            {run.runAttempt != null && run.runAttempt > 1 && <Typography.Text type="secondary">第 {run.runAttempt} 次运行</Typography.Text>}
          </Space> },
          { key: 'github', label: '链接', children: collectionGithubLink(run.githubUrl) },
        ]} />
        {run.collectionError && <Alert type="warning" showIcon message={run.collectionError} />}
        <Tabs className="daily-stable-tabs" size="small" animated={false} items={[
          { key: 'matrix', label: '构建矩阵', children: <Table
            size="small" rowKey="key" dataSource={run.matrix} columns={matrixColumns} pagination={false}
            scroll={{ x: 725 }}
          /> },
          ...(publication
            ? [{ key: 'images', label: '发布信息', children: <PublicationInfo publication={publication} /> }]
            : []),
        ]} />
        <Space className="daily-cases-actions" size={8} style={{ marginTop: 12 }}>
          <Button size="small" icon={<ReloadOutlined />} loading={refresh.pending} onClick={refresh.run}>刷新</Button>
          {canManage && (run.result === '失败' || (run.runAttempt ?? 0) > 1) && <Button
            size="small"
            icon={<RedoOutlined />}
            disabled={!run.canRerun || isPending}
            onClick={() => setRerunTarget(run)}
          >
            重跑
          </Button>}
        </Space>
      </section>
    </DailyCasePage>
  )
}
