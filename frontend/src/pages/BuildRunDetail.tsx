import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import {
  Button,
  Card,
  Collapse,
  Descriptions,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import {
  BuildGroup,
  BuildRun,
  fmtDuration,
  getRunDetail,
  getStatusColor,
} from '../services/releaseManagement'

const { Title, Text } = Typography

function StatusTag({ status }: { status: string }) {
  const labelMap: Record<string, string> = {
    success: '成功',
    failure: '失败',
    cancelled: '已取消',
    skipped: '已跳过',
    completed: '已完成',
    in_progress: '进行中',
    running: '运行中',
  }
  return (
    <Tag color={getStatusColor(status)}>
      {labelMap[status.toLowerCase()] || status}
    </Tag>
  )
}

/* ------------------------------------------------------------------ */
/*  Run header – compact timing cards                                */
/* ------------------------------------------------------------------ */

function RunHeader({ run }: { run: BuildRun }) {
  const cards = [
    { label: '排队时间', value: fmtDuration(run.wait_duration) },
    { label: '执行时间', value: fmtDuration(run.exec_duration) },
    { label: '总耗时', value: fmtDuration(run.wall_duration) },
    { label: 'Job 数', value: run.num_jobs },
  ]

  return (
    <Card bordered={false} style={{ marginBottom: 24 }}>
      <Descriptions
        column={{ xs: 1, sm: 2, md: 4, lg: 4 }}
        bordered
        size="small"
        style={{ marginBottom: 16 }}
      >
        <Descriptions.Item label="状态">
          <StatusTag status={run.status} />
        </Descriptions.Item>
        <Descriptions.Item label="分支">{run.branch}</Descriptions.Item>
        <Descriptions.Item label="事件">{run.event}</Descriptions.Item>
        <Descriptions.Item label="Commit">{run.commit_sha?.slice(0, 8)}</Descriptions.Item>
        <Descriptions.Item label="创建时间">
          {run.created_at ? new Date(run.created_at).toLocaleString('zh-CN') : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="开始时间">
          {run.started_at ? new Date(run.started_at).toLocaleString('zh-CN') : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="结束时间">
          {run.completed_at ? new Date(run.completed_at).toLocaleString('zh-CN') : '-'}
        </Descriptions.Item>
      </Descriptions>

      {/* Timing cards */}
      <Space size="middle" style={{ width: '100%', justifyContent: 'space-around' }}>
        {cards.map((c) => (
          <Card
            key={c.label}
            bordered={false}
            style={{
              flex: 1,
              textAlign: 'center',
              borderRadius: 8,
              backgroundColor: '#fafafa',
            }}
          >
            <div style={{ fontSize: 22, fontWeight: 700, color: '#1890ff' }}>
              {c.value}
            </div>
            <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 4 }}>
              {c.label}
            </div>
          </Card>
        ))}
      </Space>
    </Card>
  )
}

/* ------------------------------------------------------------------ */
/*  Step table inside each expanded Job panel                        */
/* ------------------------------------------------------------------ */

const stepColumns = [
  {
    title: '步骤名称',
    dataIndex: 'name',
    key: 'name',
    ellipsis: true,
  },
  {
    title: '编号',
    dataIndex: 'number',
    key: 'number',
    width: 80,
    align: 'center',
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 100,
    render: (s: string) => <StatusTag status={s} />,
  },
  {
    title: '耗时',
    dataIndex: 'duration',
    key: 'duration',
    width: 100,
    render: (v: number) => fmtDuration(v),
  },
]

/* ------------------------------------------------------------------ */
/*  Main page component                                              */
/* ------------------------------------------------------------------ */

export default function BuildRunDetail() {
  const { runId: runIdParam } = useParams<{ runId: string }>()

  /* Support both URL param and query string */
  const [searchParams] = useSearchParams()
  const runIdQuery = searchParams.get('runId')
  const runId = runIdParam
    ? parseInt(runIdParam, 10)
    : runIdQuery
      ? parseInt(runIdQuery, 10)
      : null

  const navigate = useNavigate()

  const [loading, setLoading] = useState(false)
  const [run, setRun] = useState<BuildRun | null>(null)
  const [groups, setGroups] = useState<BuildGroup[]>([])

  useEffect(() => {
    if (!runId) return
    setLoading(true)
    getRunDetail(runId)
      .then((data) => {
        setRun(data.run)
        setGroups(data.groups)
      })
      .catch(() => {
        // Handled by antd message inside api layer
      })
      .finally(() => setLoading(false))
  }, [runId])

  /* Invalid ID guard */
  if (!runId) {
    return (
      <div style={{ padding: 24 }}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Text type="secondary">无效的 Run ID</Text>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/release-management')}>
            返回
          </Button>
        </Space>
      </div>
    )
  }

  /* Collapse items – one per BuildGroup */
  const collapseItems = groups.map((g) => ({
    key: `${g.id}`,
    label: (
      <Space>
        <Text strong>{g.group_name}</Text>
        {g.sub_job_name && <Text type="secondary">·</Text>}
        {g.sub_job_name && <Text>{g.sub_job_name}</Text>}
        <Tag color="blue">{g.phase}</Tag>
        <StatusTag status={g.status} />
        <Text type="secondary">{fmtDuration(g.total_duration)}</Text>
        {g.architecture && (
          <Tag>{g.architecture}</Tag>
        )}
        {g.target && <Text type="secondary">{g.target}</Text>}
      </Space>
    ),
    children: (
      <Table
        rowKey="number"
        size="small"
        dataSource={g.steps}
        columns={stepColumns as any}
        pagination={false}
        scroll={{ y: 320 }}
      />
    ),
  }))

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: 24 }}>
      {/* Back + Title */}
      <div style={{ marginBottom: 24 }}>
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate('/release-management')}
        >
          返回
        </Button>
        <Title level={4} style={{ marginTop: 8 }}>
          构建运行详情 #{runId}
        </Title>
      </div>

      <Spin spinning={loading}>
        {run && <RunHeader run={run} />}

        {/* Conclusion summary */}
        {run?.conclusion_summary && Object.keys(run.conclusion_summary).length > 0 && (
          <Card bordered={false} title="结果汇总" style={{ marginBottom: 24 }}>
            <Space wrap>
              {Object.entries(run.conclusion_summary).map(([key, val]) => (
                <Tag key={key} color={getStatusColor(key)}>
                  {key}: {val}
                </Tag>
              ))}
            </Space>
          </Card>
        )}

        {/* Jobs Collapse */}
        {groups.length > 0 ? (
          <Card bordered={false} title={`Jobs (${groups.length})`}>
            <Collapse
              items={collapseItems}
              defaultActiveKey={[collapseItems[0]?.key]}
            />
          </Card>
        ) : (
          !loading && (
            <Card bordered={false}>
              <Text type="secondary">暂无 Job 数据</Text>
            </Card>
          )
        )}
      </Spin>
    </div>
  )
}
