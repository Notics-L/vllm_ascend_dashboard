import { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Empty,
  Input,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { DownloadOutlined, GithubOutlined } from '@ant-design/icons'
import { useCoverageSyncStatus, useE2ECoverage } from '../../hooks/useTestBoard'
import type { E2ETestItem } from '../../services/testBoard'

const { Text } = Typography

const DIM_ORDER = ['arch', 'feature', 'parallel', 'deploy', 'hardware', 'quantization', 'graph_mode'] as const
type DimKey = typeof DIM_ORDER[number]

const DIM_LABEL: Record<DimKey, string> = {
  arch: '架构',
  feature: '特性',
  parallel: '并行',
  deploy: '部署',
  hardware: '硬件',
  quantization: '量化',
  graph_mode: '图模式',
}

const DIM_COLOR: Record<DimKey, string> = {
  arch: 'blue',
  feature: 'magenta',
  parallel: 'green',
  deploy: 'gold',
  hardware: 'purple',
  quantization: 'red',
  graph_mode: 'cyan',
}

const DIM_PRESETS: Record<string, DimKey[]> = {
  '架构 × 量化 × 图模式': ['arch', 'quantization', 'graph_mode'],
  '架构 × 特性 × 硬件': ['arch', 'feature', 'hardware'],
  '特性 × 并行 × 图模式': ['feature', 'parallel', 'graph_mode'],
  '全部维度': [...DIM_ORDER],
}

interface ComboRow {
  key: string
  combo: string[]
  count: number
}

function computeCombos(tests: E2ETestItem[], dimensions: DimKey[]): ComboRow[] {
  if (!dimensions.length) return []
  const rows = new Map<string, ComboRow>()
  for (const test of tests) {
    const valueSets = dimensions.map((dimension) => test.coverage[dimension] ?? [])
    if (valueSets.some((values) => !values.length)) continue

    let combinations: string[][] = [[]]
    for (const values of valueSets) {
      combinations = combinations.flatMap((combination) => values.map((value) => [...combination, value]))
    }
    for (const combination of combinations) {
      const key = combination.join('\u0001')
      const row = rows.get(key) ?? { key, combo: combination, count: 0 }
      row.count += 1
      rows.set(key, row)
    }
  }
  return [...rows.values()].sort((a, b) => b.count - a.count)
}

function sourceUrl(commit: string | null | undefined, filepath: string) {
  if (!commit) return undefined
  return `https://github.com/vllm-project/vllm-ascend/blob/${commit}/tests/e2e/${filepath}`
}

export default function E2ECoverageTab() {
  const { data, isLoading, error } = useE2ECoverage()
  const { data: syncStatus } = useCoverageSyncStatus()
  const [search, setSearch] = useState('')
  const [card, setCard] = useState<string>()
  const [arch, setArch] = useState<string>()
  const [graphMode, setGraphMode] = useState<string>()
  const [showUnmarked, setShowUnmarked] = useState(false)
  const [explorerDims, setExplorerDims] = useState<DimKey[]>(['arch', 'quantization', 'graph_mode'])
  const [comboFilter, setComboFilter] = useState<Record<string, string> | null>(null)

  const tests = data?.tests ?? []
  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    return tests.filter((test) => {
      if (!showUnmarked && !test.is_marked) return false
      if (card && String(test.card_count) !== card) return false
      if (arch && !(test.coverage.arch ?? []).includes(arch)) return false
      if (graphMode && !(test.coverage.graph_mode ?? []).includes(graphMode)) return false
      if (!keyword) return true
      return [
        test.filepath,
        test.test_name,
        ...test.models,
        ...Object.values(test.coverage).flat(),
      ].some((value) => value.toLowerCase().includes(keyword))
    })
  }, [arch, card, graphMode, search, showUnmarked, tests])

  const explorerRows = useMemo(() => computeCombos(tests, explorerDims), [explorerDims, tests])
  const explorerFiltered = useMemo(() => {
    if (!comboFilter) return filtered
    return filtered.filter((test) => Object.entries(comboFilter).every(([dimension, value]) => {
      return (test.coverage[dimension] ?? []).includes(value)
    }))
  }, [comboFilter, filtered])

  const grouped = useMemo(() => {
    const cards = [...new Set(explorerFiltered.map((test) => test.card_count))].sort((a, b) => a - b)
    return cards.map((cardCount) => ({
      card: cardCount,
      items: explorerFiltered.filter((test) => test.card_count === cardCount),
    }))
  }, [explorerFiltered])

  const summary = data?.summary
  const archOptions = (data?.taxonomy?.arch ?? []).map((value) => ({ label: value, value }))
  const graphOptions = (data?.taxonomy?.graph_mode ?? []).map((value) => ({ label: value, value }))
  const repoCommit = data?.repo_commit
  const lastSync = syncStatus?.e2e?.updated_at || data?.updated_at

  const columns = [
    {
      title: '文件',
      dataIndex: 'filepath',
      key: 'filepath',
      width: 280,
      ellipsis: true,
      render: (filepath: string) => {
        const url = sourceUrl(repoCommit, filepath)
        return url ? (
          <Tooltip title="在 GitHub 查看">
            <Button type="link" size="small" icon={<GithubOutlined />} href={url} target="_blank">
              {filepath}
            </Button>
          </Tooltip>
        ) : filepath
      },
    },
    { title: '测试用例', dataIndex: 'test_name', key: 'test_name', width: 260, ellipsis: true },
    {
      title: '模型',
      dataIndex: 'models',
      key: 'models',
      width: 180,
      render: (models: string[]) => models.length ? (
        <Space size={2} wrap>{models.map((model) => <Tag key={model} color="green">{model}</Tag>)}</Space>
      ) : <Text type="secondary">-</Text>,
    },
    ...DIM_ORDER.map((dimension) => ({
      title: DIM_LABEL[dimension],
      key: dimension,
      width: 130,
      render: (_value: unknown, test: E2ETestItem) => {
        const values = test.coverage[dimension] ?? []
        return values.length ? <Space size={2} wrap>{values.map((value) => <Tag key={value} color={DIM_COLOR[dimension]}>{value}</Tag>)}</Space> : <Text type="secondary">-</Text>
      },
    })),
  ]

  const exportCsv = () => {
    const header = ['文件', '测试用例', '卡数', '模型', ...DIM_ORDER.map((dimension) => DIM_LABEL[dimension])]
    const rows = [header, ...filtered.map((test) => [
      test.filepath,
      test.test_name,
      test.card_count,
      test.models.join(';'),
      ...DIM_ORDER.map((dimension) => (test.coverage[dimension] ?? []).join(';')),
    ])]
    const csv = rows.map((row) => row.map((value) => `"${String(value).replace(/"/g, '""')}"`).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'e2e_coverage.csv'
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div>
      {error && <Alert type="error" showIcon message="E2E 覆盖率加载失败" description={String(error)} style={{ marginBottom: 16 }} />}

      <Row gutter={12} style={{ marginBottom: 16 }}>
        <Col span={4}><Card loading={isLoading}><Statistic title="E2E 测试总数" value={summary?.total_tests ?? 0} /></Card></Col>
        <Col span={4}><Card loading={isLoading}><Statistic title="已标记测试" value={summary?.marked_tests ?? 0} /></Card></Col>
        <Col span={4}><Card loading={isLoading}><Statistic title="标记覆盖率" value={Math.round((summary?.marked_ratio ?? 0) * 100)} suffix="%" valueStyle={{ color: '#1890ff' }} /></Card></Col>
        <Col span={4}><Card loading={isLoading}><Statistic title="1 卡" value={summary?.by_card?.['1'] ?? 0} /></Card></Col>
        <Col span={4}><Card loading={isLoading}><Statistic title="2 卡" value={summary?.by_card?.['2'] ?? 0} /></Card></Col>
        <Col span={4}><Card loading={isLoading}><Statistic title="4 卡" value={summary?.by_card?.['4'] ?? 0} /></Card></Col>
      </Row>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input.Search placeholder="搜索测试名、模型或覆盖标签" allowClear style={{ width: 280 }} value={search} onChange={(event) => setSearch(event.target.value)} />
          <Select placeholder="卡数" allowClear style={{ width: 110 }} value={card} onChange={setCard} options={[1, 2, 4].map((value) => ({ label: `${value} 卡`, value: String(value) }))} />
          <Select placeholder="架构" allowClear style={{ width: 150 }} value={arch} onChange={setArch} options={archOptions} />
          <Select placeholder="图模式" allowClear style={{ width: 160 }} value={graphMode} onChange={setGraphMode} options={graphOptions} />
          <Checkbox checked={showUnmarked} onChange={(event) => setShowUnmarked(event.target.checked)}>显示未标记</Checkbox>
          <Button icon={<DownloadOutlined />} onClick={exportCsv} disabled={!filtered.length}>导出 CSV</Button>
          <Text type="secondary">最近同步：{lastSync ? new Date(lastSync).toLocaleString() : '-'}</Text>
        </Space>
      </Card>

      <Card
        title="交叉覆盖探索"
        size="small"
        style={{ marginBottom: 16 }}
        extra={<Text type="secondary">已覆盖组合：{explorerRows.length}</Text>}
      >
        <Space wrap style={{ marginBottom: 12 }}>
          {DIM_ORDER.map((dimension) => (
            <Checkbox
              key={dimension}
              checked={explorerDims.includes(dimension)}
              onChange={(event) => {
                setComboFilter(null)
                setExplorerDims(event.target.checked ? [...explorerDims, dimension] : explorerDims.filter((item) => item !== dimension))
              }}
            >
              {DIM_LABEL[dimension]}
            </Checkbox>
          ))}
        </Space>
        <Space wrap style={{ marginBottom: 12 }}>
          {Object.entries(DIM_PRESETS).map(([label, dimensions]) => (
            <Button key={label} size="small" type={dimensions.length === explorerDims.length && dimensions.every((dimension) => explorerDims.includes(dimension)) ? 'primary' : 'default'} onClick={() => { setComboFilter(null); setExplorerDims(dimensions) }}>
              {label}
            </Button>
          ))}
          {comboFilter && <Tag color="blue" closable onClose={() => setComboFilter(null)}>组合筛选：{Object.entries(comboFilter).map(([dimension, value]) => `${DIM_LABEL[dimension as DimKey] ?? dimension}=${value}`).join(' & ')}</Tag>}
        </Space>
        {explorerRows.length ? (
          <Table<ComboRow>
            dataSource={explorerRows}
            rowKey="key"
            size="small"
            pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 个组合` }}
            scroll={{ x: 600 }}
            columns={[
              ...explorerDims.map((dimension, index) => ({
                title: DIM_LABEL[dimension],
                key: dimension,
                render: (_value: unknown, row: ComboRow) => <Tag color={DIM_COLOR[dimension]}>{row.combo[index]}</Tag>,
              })),
              {
                title: '测试数',
                dataIndex: 'count',
                key: 'count',
                render: (count: number, row: ComboRow) => (
                  <Button type="link" size="small" onClick={() => {
                    const filter: Record<string, string> = {}
                    explorerDims.forEach((dimension, index) => { filter[dimension] = row.combo[index] })
                    setComboFilter(filter)
                  }}>
                    {count}
                  </Button>
                ),
              },
            ]}
          />
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可用的覆盖组合" />}
      </Card>

      <Card title={`E2E 覆盖明细（${explorerFiltered.length}）`} size="small">
        {!data && !isLoading ? <Empty description="暂无 E2E 覆盖数据，请先同步覆盖率" /> : grouped.length ? grouped.map((group) => (
          <div key={group.card} style={{ marginBottom: 16 }}>
            <Text strong>{group.card} 卡测试（{group.items.length}）</Text>
            <Table<E2ETestItem> dataSource={group.items} rowKey={(row) => `${row.filepath}-${row.test_name}`} columns={columns} size="small" pagination={false} scroll={{ x: 1500 }} style={{ marginTop: 8 }} />
          </div>
        )) : <Empty description="没有匹配的 E2E 测试" />}
      </Card>
    </div>
  )
}
