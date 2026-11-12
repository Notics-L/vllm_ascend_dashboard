import { useState, useMemo, useRef } from 'react'
import {
  Card,
  Table,
  Tag,
  Select,
  Space,
  Typography,
  Button,
  Modal,
  Input,
  Form,
  message,
  Alert,
  Tooltip,
  Switch,
} from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  ReloadOutlined,
  DownloadOutlined,
  DeleteOutlined,
  InboxOutlined,
} from '@ant-design/icons'
import {
  useModelFoMasters,
  useModelFoMasterSuites,
  useCreateModelFoMaster,
  useUpdateModelFoMaster,
  useDeleteModelFoMaster,
  useImportModelFoMasters,
} from '../hooks/useCI'
import type { ModelFoMaster, ModelFoMasterImportResult } from '../services/ci'
import {
  downloadModelFoMasterTemplate,
  exportNightlyTestCaseRuns,
} from '../services/ci'

const { Text } = Typography

/** 从「姓名 工号」原文拆出 姓名 / 工号，便于前端展示与 tooltip。 */
function splitModelFo(modelFo: string) {
  const m = modelFo.trim().match(/^(.*?)\s*(\d{5,})\s*$/)
  if (!m) return { name: modelFo.trim(), code: '' }
  return { name: m[1], code: m[2] }
}

const NODE_TYPE_OPTIONS = [
  { label: '单机', value: '单机' },
  { label: '多机', value: '多机' },
  { label: 'PD分离', value: 'PD分离' },
]

/**
 * 《用例配置》tab：团队维护的「模型-FO 映射主库」。
 *
 * 六元组：Suite / 节点类型 / 测试内容（yaml/路径）/ 模型 / 覆盖机型 / 模型FO。
 * 主体表格即主库，支持在线编辑 + Excel 批量导入（三元组 upsert）+ 下拉筛选。
 * 原「Nightly 用例运行明细」导出保留为次级入口，明细数据仍来自 nightly 快照。
 */
function NightlyTestCaseConfig() {
  const [suiteFilter, setSuiteFilter] = useState<string | undefined>(undefined)
  const [nodeTypeFilter, setNodeTypeFilter] = useState<string | undefined>(undefined)
  const [modelKeyword, setModelKeyword] = useState('')
  const [yamlKeyword, setYamlKeyword] = useState('')

  const [modalOpen, setModalOpen] = useState(false)
  const [editingRecord, setEditingRecord] = useState<ModelFoMaster | null>(null)
  const [form] = Form.useForm()

  // 文件选择（受控，避开 antd Upload 异步竞态）
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const importMutation = useImportModelFoMasters()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [importResult, setImportResult] = useState<ModelFoMasterImportResult | null>(null)

  // 模板下载
  const [downloadingTemplate, setDownloadingTemplate] = useState(false)

  // 运行明细次级导出
  const [exportingRuns, setExportingRuns] = useState(false)

  const { data: masters, isLoading, refetch, isFetching } = useModelFoMasters({
    only_enabled: false,
    limit: 2000,
  })

  const { data: suites = [] } = useModelFoMasterSuites()

  const createMutation = useCreateModelFoMaster()
  const updateMutation = useUpdateModelFoMaster()
  const deleteMutation = useDeleteModelFoMaster()

  const filtered = useMemo(() => {
    if (!masters) return []
    return masters.filter((m) => {
      if (suiteFilter && m.suite !== suiteFilter) return false
      if (nodeTypeFilter && m.node_type !== nodeTypeFilter) return false
      if (modelKeyword && !m.model_name.toLowerCase().includes(modelKeyword.toLowerCase())) return false
      if (yamlKeyword && !m.test_yaml.toLowerCase().includes(yamlKeyword.toLowerCase())) return false
      return true
    })
  }, [masters, suiteFilter, nodeTypeFilter, modelKeyword, yamlKeyword])

  const handleAdd = () => {
    setEditingRecord(null)
    form.resetFields()
    setModalOpen(true)
  }

  const handleEdit = (record: ModelFoMaster) => {
    setEditingRecord(record)
    form.setFieldsValue(record)
    setModalOpen(true)
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      if (editingRecord) {
        await updateMutation.mutateAsync({ id: editingRecord.id, data: values })
        message.success('已更新')
      } else {
        await createMutation.mutateAsync(values)
        message.success('已新增')
      }
      setModalOpen(false)
    } catch (error: any) {
      if (error?.response?.data?.detail) {
        message.error(error.response.data.detail)
      }
    }
  }

  const handleDelete = (record: ModelFoMaster) => {
    Modal.confirm({
      title: '确认删除该映射',
      content: `${record.suite} / ${record.test_yaml}（${record.model_fo}）`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteMutation.mutateAsync(record.id)
          message.success('已删除')
        } catch (error: any) {
          message.error(error?.response?.data?.detail || '删除失败')
        }
      },
    })
  }

  const handleTemplateDownload = async () => {
    setDownloadingTemplate(true)
    try {
      const blob = await downloadModelFoMasterTemplate()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'model_fo_master_template.xlsx'
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch {
      message.error('模板下载失败')
    } finally {
      setDownloadingTemplate(false)
    }
  }

  const handleImport = async () => {
    if (!selectedFile) {
      message.warning('请先选择要导入的 Excel 文件')
      return
    }
    try {
      const result = await importMutation.mutateAsync(selectedFile)
      setImportResult(result)
      if (result.errors.length === 0) {
        message.success(`导入完成：新增/更新 ${result.imported} 条`)
      } else {
        message.warning(`导入完成：${result.imported} 条成功，${result.errors.length} 行有误`)
      }
      // 清空已选文件，允许再次选择（同一文件再选也能触发 onChange）
      setSelectedFile(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
      refetch()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '导入失败')
    }
  }

  const handleExportRuns = async (format: 'json' | 'xlsx') => {
    setExportingRuns(true)
    try {
      const params = {
        format,
        scope: 'all' as const,
      }
      const blob = await exportNightlyTestCaseRuns(params)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `nightly_run_details_${new Date().toISOString().slice(0, 10)}.${format}`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      message.success('运行明细已导出')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '导出运行明细失败')
    } finally {
      setExportingRuns(false)
    }
  }

  const columns = [
    {
      title: 'Suite',
      dataIndex: 'suite',
      key: 'suite',
      width: 80,
      render: (text: string) => <Tag color="geekblue">{text}</Tag>,
    },
    {
      title: '节点类型',
      dataIndex: 'node_type',
      key: 'node_type',
      width: 100,
      render: (text: string) => <Tag color="cyan">{text}</Tag>,
    },
    {
      title: '测试内容（yaml/路径）',
      dataIndex: 'test_yaml',
      key: 'test_yaml',
      width: 260,
      ellipsis: true,
      render: (text: string) => <Text code style={{ fontSize: 12 }}>{text}</Text>,
    },
    {
      title: '模型',
      dataIndex: 'model_name',
      key: 'model_name',
      width: 220,
      ellipsis: true,
      render: (text: string) => <Text>{text}</Text>,
    },
    {
      title: '覆盖机型',
      dataIndex: 'coverage_hardware',
      key: 'coverage_hardware',
      width: 170,
      ellipsis: true,
      render: (text: string | null) => text || <Text type="secondary">-</Text>,
    },
    {
      title: '模型 FO',
      dataIndex: 'model_fo',
      key: 'model_fo',
      width: 130,
      render: (text: string | null) => {
        if (!text) return <Text type="secondary">-</Text>
        const { name, code } = splitModelFo(text)
        return (
          <Tooltip title={code ? `${name}（工号 ${code}）` : name}>
            <span>
              {name}
              {code && <Text type="secondary" style={{ marginLeft: 4, fontSize: 12 }}>{code}</Text>}
            </span>
          </Tooltip>
        )
      },
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 70,
      render: (enabled: boolean) =>
        enabled ? <Tag color="green">启用</Tag> : <Tag color="default">停用</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      fixed: 'right',
      render: (_: any, record: ModelFoMaster) => (
        <Space size={0}>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record)}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ]

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null
    setSelectedFile(file)
    setImportResult(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>模型-FO 映射主库</Typography.Title>
          <Text type="secondary">
            Suite / 节点类型 / 测试内容（yaml）/ 模型 / 覆盖机型 / 模型FO 六元组，CI 报告与 Job 详情按本表回退解析责任人
          </Text>
        </div>
        <Space wrap>
          <Button
            onClick={() => fileInputRef.current?.click()}
            icon={<InboxOutlined />}
            disabled={importMutation.isPending}
          >
            选择 Excel{selectedFile ? `（${selectedFile.name}）` : ''}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
          <Button
            onClick={handleImport}
            loading={importMutation.isPending}
            disabled={!selectedFile}
          >
            确认导入
          </Button>
          <Button
            onClick={handleTemplateDownload}
            loading={downloadingTemplate}
            icon={<DownloadOutlined />}
          >
            下载模板
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => refetch()}
            loading={isFetching}
          >
            刷新
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleAdd}
          >
            新增映射
          </Button>
        </Space>
      </div>

      {importResult && (
        <Alert
          type={importResult.errors.length === 0 ? 'success' : 'warning'}
          showIcon
          style={{ marginBottom: 12 }}
          message={
            importResult.errors.length === 0
              ? `导入完成：共处理 ${importResult.total_rows} 行，新增/更新 ${importResult.imported} 条`
              : `导入完成：新增/更新 ${importResult.imported} 条，${importResult.errors.length} 行被拒绝`
          }
          description={
            importResult.errors.length > 0 ? (
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {importResult.errors.slice(0, 6).map((e) => (
                  <li key={e.row}>第 {e.row} 行：{e.reason}</li>
                ))}
                {importResult.errors.length > 6 && (
                  <li>… 还有 {importResult.errors.length - 6} 行错误</li>
                )}
              </ul>
            ) : undefined
          }
          closable
          onClose={() => setImportResult(null)}
        />
      )}

      <Card
        style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, display: 'flex', flexDirection: 'column', padding: 0 } }}
      >
        <div style={{ padding: 12, borderBottom: '1px solid #f0f0f0' }}>
          <Space wrap>
            <Select
              allowClear
              placeholder="Suite"
              style={{ width: 160 }}
              value={suiteFilter}
              onChange={setSuiteFilter}
              options={suites.map((s) => ({ label: s, value: s }))}
            />
            <Select
              allowClear
              placeholder="节点类型"
              style={{ width: 140 }}
              value={nodeTypeFilter}
              onChange={setNodeTypeFilter}
              options={NODE_TYPE_OPTIONS}
            />
            <Input.Search
              allowClear
              placeholder="搜索模型名"
              style={{ width: 220 }}
              value={modelKeyword}
              onChange={(e) => setModelKeyword(e.target.value)}
            />
            <Input.Search
              allowClear
              placeholder="搜索 yaml"
              style={{ width: 240 }}
              value={yamlKeyword}
              onChange={(e) => setYamlKeyword(e.target.value)}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              共 {filtered.length} 条
            </Typography.Text>
          </Space>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          <Table
            columns={columns as any}
            dataSource={filtered}
            loading={isLoading}
            rowKey="id"
            pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
            scroll={{ x: 1280 }}
            size="middle"
            footer={() => (
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <Text type="secondary" style={{ marginRight: 'auto', alignSelf: 'center' }}>
                  Nightly 运行明细（历史快照）：
                </Text>
                <Button size="small" loading={exportingRuns} onClick={() => handleExportRuns('json')}>
                  导出 JSON
                </Button>
                <Button size="small" loading={exportingRuns} onClick={() => handleExportRuns('xlsx')}>
                  导出 Excel
                </Button>
              </div>
            )}
          />
        </div>
      </Card>

      <Modal
        title={editingRecord ? '编辑映射' : '新增映射'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={createMutation.isPending || updateMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={640}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }} initialValues={{ enabled: true }}>
          <Form.Item label="六元组映射，三元组（Suite / 节点类型 / 测试内容）联合唯一" style={{ marginBottom: 8 }}>
            <Text type="secondary">同一三元组只允许一条记录；模型与机型可重复出现在多个三元组下</Text>
          </Form.Item>
          <Space size={16} style={{ width: '100%' }} wrap>
            <Form.Item name="suite" label="Suite" rules={[{ required: true, message: '请输入 Suite' }]} style={{ width: 150 }}>
              <Input placeholder="如 A2 / A3" list="model-fo-suite-options" autoComplete="off" />
            </Form.Item>
            <datalist id="model-fo-suite-options">
              {suites.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
            <Form.Item name="node_type" label="节点类型" rules={[{ required: true, message: '请选择节点类型' }]} style={{ width: 150 }}>
              <Select options={NODE_TYPE_OPTIONS} placeholder="选择节点类型" />
            </Form.Item>
            <Form.Item name="enabled" label="启用" valuePropName="checked" style={{ width: 70 }}>
              <Switch size="small" />
            </Form.Item>
          </Space>
          <Form.Item name="test_yaml" label="测试内容（yaml/路径）" rules={[{ required: true, message: '请输入 yaml 路径' }]}>
            <Input placeholder="如 Qwen3-30B-A3B-BF16-A2.yaml" />
          </Form.Item>
          <Space size={16} style={{ width: '100%' }} wrap>
            <Form.Item name="model_name" label="模型" rules={[{ required: true, message: '请输入模型名' }]} style={{ width: 280 }}>
              <Input placeholder="如 qwen3-30b-a3b-bf16-a2-performance" />
            </Form.Item>
            <Form.Item name="coverage_hardware" label="覆盖机型" style={{ width: 280 }}>
              <Input placeholder="如 A2单机 linux-aarch64-a2b3-8" />
            </Form.Item>
          </Space>
          <Form.Item name="model_fo" label="模型 FO（可暂缺，后续手动补录）">
            <Input placeholder="如 张三 12345678（姓名 工号，空格分隔）" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default NightlyTestCaseConfig
