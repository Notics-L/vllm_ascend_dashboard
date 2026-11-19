import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button, ConfigProvider, Modal, Space, Switch, Tag, Tooltip, Typography, message, theme } from 'antd'
import { CheckCircleOutlined, DeleteOutlined, EditOutlined, SettingOutlined, StopOutlined } from '@ant-design/icons'
import { isAxiosError } from 'axios'
import { useDailyQueryError } from '../shared/useDailyQueryError'
import { useDailyTabOpening } from '../shared/useDailyTabOpening'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import type { SelectProps } from 'antd'
import { filterDailyCases } from './dailyCaseFilters'
import type { DailyCaseFilters } from './dailyCaseFilters'
import { useDailyCaseMutations, useDailyCases } from '../../../hooks/useDailyPipeline'
import type { DailyCase } from '../../../services/dailyPipeline'
import DailyCaseTag from './DailyCaseTag'
import { dailyTableRefresh } from '../shared/dailyTableRefresh'
import './DailyCases.css'

type TagRenderProps = Parameters<NonNullable<SelectProps['tagRender']>>[0]
type TagOption = Parameters<NonNullable<SelectProps['optionRender']>>[0]

function DailyCaseText({ value }: { value: string }) {
  const { token } = theme.useToken()
  return <Typography.Text style={{ color: token.colorText }}>{value}</Typography.Text>
}

interface DailyCaseStatusControls {
  pendingId: string | null
  disabled: boolean
  onChange: (record: DailyCase, checked: boolean) => void
}

function getDailyCaseColumns(cases: DailyCase[], statusControls: DailyCaseStatusControls): ProColumns<DailyCase>[] {
  const optionsFor = (field: 'hardware' | 'testType' | 'status') => (
    [...new Set(cases.map((item) => item[field]))].map((value) => ({ label: value, value }))
  )

  return [
    {
      title: '用例名称',
      dataIndex: 'name',
      key: 'name',
      formItemProps: { className: 'daily-cases-filter-name' },
      width: 300,
      valueType: 'text',
      fieldProps: { placeholder: '请输入用例名称', allowClear: true, style: { width: '100%' } },
      render: (_, record) => <DailyCaseText value={record.name} />,
    },
    {
      title: '硬件类型',
      dataIndex: 'hardware',
      key: 'hardware',
      formItemProps: { className: 'daily-cases-filter-hardware' },
      width: 130,
      valueType: 'select',
      fieldProps: { mode: 'multiple', options: optionsFor('hardware'), placeholder: '全部硬件', allowClear: true, style: { width: '100%' } },
      render: (_, record) => <Tag color="blue">{record.hardware}</Tag>,
    },
    {
      title: '测试类型',
      dataIndex: 'testType',
      key: 'testType',
      formItemProps: { className: 'daily-cases-filter-type' },
      width: 120,
      valueType: 'select',
      fieldProps: { mode: 'multiple', options: optionsFor('testType'), placeholder: '全部类型', allowClear: true, style: { width: '100%' } },
      render: (_, record) => <DailyCaseText value={record.testType} />,
    },
    {
      title: '标签',
      dataIndex: 'tags',
      key: 'tags',
      formItemProps: { className: 'daily-cases-filter-tags' },
      width: 200,
      valueType: 'select',
      fieldProps: {
        mode: 'multiple',
        options: [...new Set(cases.flatMap((item) => item.tags || []))]
          .map((value) => ({ label: value, value })),
        placeholder: '全部标签',
        tagRender: (props: TagRenderProps) => <DailyCaseTag label={String(props.value)} closable={props.closable} onClose={props.onClose} />,
        optionRender: (option: TagOption) => <DailyCaseTag label={String(option.data.value)} />,
        allowClear: true,
        style: { width: '100%' },
      },
      render: (_, record) => (
        record.tags?.length ? (
          <Space size={4} wrap>
            {record.tags.map((tag) => <DailyCaseTag key={tag} label={tag} />)}
          </Space>
        ) : <Typography.Text type="secondary">—</Typography.Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      formItemProps: { className: 'daily-cases-filter-status' },
      width: 170,
      valueType: 'select',
      fieldProps: { mode: 'multiple', options: optionsFor('status'), placeholder: '全部状态', allowClear: true, style: { width: '100%' } },
      render: (_, record) => (
        <Space size={6}>
        <Switch
          className="daily-case-table-status"
          checked={record.status === '启用'}
          size="small"
          loading={statusControls.pendingId === record.id}
          disabled={statusControls.disabled}
          onChange={(checked) => statusControls.onChange(record, checked)}
          aria-label={`${record.name}的启用状态`}
        />
        {record.autoDisabled && <Tooltip title="可手动启用；新的失败将再次禁用">
          {record.processingFailureId ? <Link to={'/daily-pipeline/issue-tracking/cases/' + record.processingFailureId + '/progress'}><Tag color="orange" style={{ cursor: 'pointer', marginInlineEnd: 0 }}>故障禁用</Tag></Link> : <Tag color="orange">故障禁用</Tag>}
        </Tooltip>}
        </Space>
      ),
    },
    {
      title: '成功次数',
      dataIndex: 'successCount',
      width: 100,
      search: false,
      render: (_, record) => record.successCount ?? 0,
    },
    {
      title: '失败次数',
      dataIndex: 'failureCount',
      width: 100,
      search: false,
      render: (_, record) => record.failureCount ?? 0,
    },
    {
      title: '执行时间',
      dataIndex: 'executionTimes',
      key: 'executionTimes',
      width: 260,
      search: false,
      render: (_, record) => (
        record.executionTimes.length ? (
          <Space size={4} wrap>
            <Tag>{record.executionTimes[0]}</Tag>
            {record.executionTimes.length > 1 && (
              <Tooltip title={<>{record.executionTimes.map((time) => <div key={time}>{time}</div>)}</>}>
                <Tag>+{record.executionTimes.length - 1}</Tag>
              </Tooltip>
            )}
          </Space>
        ) : <Typography.Text type="secondary">未配置</Typography.Text>
      ),
    },
  ]
}

function getMutationError(error: unknown, fallback: string) {
  return isAxiosError<{ detail?: string }>(error) ? error.response?.data?.detail || fallback : fallback
}

function DailyPipelineCases() {
  const navigate = useNavigate()
  const casesQuery = useDailyCases(true, { staleTime: 0 })
  const opening = useDailyTabOpening(true)
  useDailyQueryError(casesQuery, 'Daily 用例加载失败')
  const { data: cases = [], isFetching, refetch } = casesQuery
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([])
  const [pendingStatusId, setPendingStatusId] = useState<string | null>(null)
  const [filters, setFilters] = useState<DailyCaseFilters>({})
  const [searchCollapsed, setSearchCollapsed] = useState(true)
  const [currentPage, setCurrentPage] = useState(1)
  const filteredCases = useMemo(() => filterDailyCases(cases, filters), [cases, filters])
  const { updateCase, updateStatuses, deleteCases } = useDailyCaseMutations()
  const [modal, contextHolder] = Modal.useModal()
  const busy = deleteCases.isPending || updateCase.isPending || updateStatuses.isPending

  const handleStatusChange = async (record: DailyCase, checked: boolean) => {
    if (busy) return
    setPendingStatusId(record.id)
    try {
      await updateCase.mutateAsync({ id: record.id, values: { status: checked ? '启用' : '禁用' } })
      message.success(checked ? '用例已启用' : '用例已禁用')
    } catch (error) {
      message.error(getMutationError(error, '状态更新失败，请重试'))
    } finally {
      setPendingStatusId(null)
    }
  }

  const columns = getDailyCaseColumns(cases, {
    pendingId: pendingStatusId,
    disabled: busy,
    onChange: (record, checked) => { void handleStatusChange(record, checked) },
  })

  const handleBatchStatusChange = async (status: DailyCase['status']) => {
    const ids = [...selectedRowKeys]
    if (!ids.length || busy) return
    try {
      const result = await updateStatuses.mutateAsync({ ids, status })
      message.success(`已${status} ${result.updated} 条用例`)
    } catch (error) {
      message.error(getMutationError(error, `批量${status}失败，请重试`))
    }
  }

  const handleConfigure = () => {
    navigate('/daily-pipeline/cases/new')
  }

  const handleEdit = () => {
    const record = cases.find((item) => item.id === selectedRowKeys[0])
    if (record) navigate(`/daily-pipeline/cases/${encodeURIComponent(record.id)}/edit`)
  }

  const handleDelete = () => {
    const ids = [...selectedRowKeys]
    modal.confirm({
      title: '确定删除吗？',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          const result = await deleteCases.mutateAsync(ids)
          setSelectedRowKeys([])
          setCurrentPage(1)
          message.success(`已从 Daily 移出 ${result.deleted} 条用例`)
        } catch (error) {
          message.error(getMutationError(error, '删除失败，请重试'))
          throw error
        }
      },
    })
  }


  return (
    <Space
      className="daily-cases-page"
      direction="vertical"
      size={16}
      style={{ width: '100%' }}
    >
      <div className="daily-cases-toolbar">
        <Typography.Text strong>Daily 用例</Typography.Text>
        <ConfigProvider theme={{
          token: { colorPrimary: '#1677ff', borderRadius: 4, controlHeight: 28, fontSize: 13 },
          components: { Button: { fontWeight: 400, primaryShadow: 'none' } },
        }}>
          <Space className="daily-cases-actions" size={8} wrap>
            <Button color="primary" variant="outlined" icon={<SettingOutlined />} onClick={handleConfigure} disabled={busy}>配置</Button>
            <Button color="cyan" variant="outlined" icon={<EditOutlined />} onClick={handleEdit} disabled={selectedRowKeys.length !== 1 || busy}>编辑</Button>
            <Button color="danger" variant="outlined" icon={<DeleteOutlined />} onClick={handleDelete} disabled={!selectedRowKeys.length || busy} loading={deleteCases.isPending}>删除</Button>
            <Button color="green" variant="outlined" icon={<CheckCircleOutlined />} onClick={() => void handleBatchStatusChange('启用')} disabled={!selectedRowKeys.length || busy} loading={updateStatuses.isPending && updateStatuses.variables?.status === '启用'}>启用</Button>
            <Button color="orange" variant="outlined" icon={<StopOutlined />} onClick={() => void handleBatchStatusChange('禁用')} disabled={!selectedRowKeys.length || busy} loading={updateStatuses.isPending && updateStatuses.variables?.status === '禁用'}>禁用</Button>
          </Space>
        </ConfigProvider>
      </div>
      {contextHolder}
      <ConfigProvider theme={{
        token: {
          colorPrimary: '#1677ff', borderRadius: 6, controlHeight: 32, fontSize: 13,
        },
        components: {
          Button: { fontWeight: 400, primaryShadow: 'none' },
          Select: { optionFontSize: 13, optionSelectedFontWeight: 400 },
        },
      }}>
        <ProTable<DailyCase, DailyCaseFilters>
          className="daily-cases-pro-table"
          tableClassName="daily-cases-table"
          headerTitle={false}
          toolBarRender={false}
          {...dailyTableRefresh(refetch, opening || isFetching || busy, {
            current: currentPage,
            onChange: (page) => setCurrentPage(page),
            defaultPageSize: 10,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50],
            showTotal: (total, range) => `第 ${range[0]}–${range[1]} 条，共 ${total} 条`,
            position: ['bottomRight'],
            responsive: true,
          })}
          tableAlertRender={false}
          search={{
            labelWidth: 'auto',
            collapsed: searchCollapsed,
            onCollapse: setSearchCollapsed,
            submitterColSpanProps: { span: 24, className: 'daily-cases-filter-actions' },
            span: { xs: 24, sm: 12, md: 8, lg: 6, xl: 4, xxl: 4 },
            searchText: '查询',
            resetText: '重置',
          }}
          onSubmit={(values) => {
            setFilters(values)
            setCurrentPage(1)
          }}
          onReset={() => {
            setFilters({})
            setCurrentPage(1)
          }}
          rowKey="id"
          columns={columns}
          dataSource={filteredCases}
          rowSelection={{
            type: 'checkbox',
            columnWidth: 48,
            fixed: true,
            selectedRowKeys,
            preserveSelectedRowKeys: true,
            onChange: (keys) => setSelectedRowKeys(keys.map(String)),
          }}

          scroll={{ x: 1380, y: 'var(--daily-case-body-height)' }}
        />
      </ConfigProvider>
    </Space>
  )
}

export default DailyPipelineCases
