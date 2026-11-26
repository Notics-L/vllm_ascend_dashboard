import { useMemo, useState } from 'react'
import type { Key } from 'react'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Divider,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import { InboxOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons'
import { useQueryClient } from '@tanstack/react-query'
import {
  executeUsersImport,
  parseUsersImportFile,
  previewUsersImport,
} from '../services/users'
import type {
  UserImportParseResult,
  UserImportPreviewResult,
  UserImportRequest,
  UserImportRow,
} from '../services/users'

const { Dragger } = Upload
const { Text } = Typography

interface Props {
  open: boolean
  onClose: () => void
  onSuccess?: () => void
}

interface RowOverride {
  password?: string
  role?: string
}

const roleLabelMap: Record<string, string> = {
  user: '普通用户',
  admin: '管理员',
  super_admin: '超级管理员',
}

function ImportUsersModal({ open, onClose, onSuccess }: Props) {
  const queryClient = useQueryClient()
  const [fileName, setFileName] = useState('')
  const [parseResult, setParseResult] = useState<UserImportParseResult | null>(null)
  const [parsing, setParsing] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [importing, setImporting] = useState(false)
  const [usernameColumn, setUsernameColumn] = useState<string>()
  const [emailColumn, setEmailColumn] = useState<string>()
  const [defaultPassword, setDefaultPassword] = useState('123456')
  const [defaultRole, setDefaultRole] = useState('user')
  const [lowercase, setLowercase] = useState(true)
  const [removeSpaces, setRemoveSpaces] = useState(true)
  const [removeSpecial, setRemoveSpecial] = useState(true)
  const [overrideRole, setOverrideRole] = useState<string>()
  const [overridePassword, setOverridePassword] = useState('')
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([])
  const [overrides, setOverrides] = useState<Record<number, RowOverride>>({})
  const [preview, setPreview] = useState<UserImportPreviewResult | null>(null)
  const [previewSearch, setPreviewSearch] = useState('')

  const resetAll = () => {
    setFileName('')
    setParseResult(null)
    setParsing(false)
    setPreviewing(false)
    setImporting(false)
    setUsernameColumn(undefined)
    setEmailColumn(undefined)
    setDefaultPassword('123456')
    setDefaultRole('user')
    setLowercase(true)
    setRemoveSpaces(true)
    setRemoveSpecial(true)
    setOverrideRole(undefined)
    setOverridePassword('')
    setSelectedRowKeys([])
    setOverrides({})
    setPreview(null)
    setPreviewSearch('')
  }

  const handleClose = () => {
    resetAll()
    onClose()
  }

  const buildRows = (): UserImportRow[] => {
    if (!parseResult || !usernameColumn) return []
    return parseResult.rows.map((row, index) => {
      const rowNumber = index + 1
      return {
        row_number: rowNumber,
        username_raw: String(row[usernameColumn] ?? ''),
        email_raw: emailColumn ? String(row[emailColumn] ?? '') : '',
        password: overrides[rowNumber]?.password,
        role: overrides[rowNumber]?.role,
      }
    })
  }

  const buildPayload = (): UserImportRequest => ({
    rows: buildRows(),
    default_password: defaultPassword,
    default_role: defaultRole,
    username_rules: {
      lowercase,
      remove_spaces: removeSpaces,
      remove_special: removeSpecial,
    },
  })

  const validateBeforePreview = (): boolean => {
    if (!parseResult) {
      message.warning('请先选择并解析 Excel 文件')
      return false
    }
    if (!usernameColumn) {
      message.warning('请选择作为账号的列')
      return false
    }
    if (!defaultPassword || defaultPassword.length < 6) {
      message.warning('默认密码至少 6 位')
      return false
    }
    return true
  }

  const refreshPreview = async (nextOverrides = overrides) => {
    if (!validateBeforePreview()) return
    setPreviewing(true)
    try {
      const data = await previewUsersImport({
        ...buildPayload(),
        rows: (parseResult?.rows || []).map((row, index) => {
          const rowNumber = index + 1
          return {
            row_number: rowNumber,
            username_raw: String(row[usernameColumn!] ?? ''),
            email_raw: emailColumn ? String(row[emailColumn] ?? '') : '',
            password: nextOverrides[rowNumber]?.password,
            role: nextOverrides[rowNumber]?.role,
          }
        }),
      })
      setPreview(data)
    } catch (error: any) {
      message.error(error.response?.data?.detail || '预览失败')
    } finally {
      setPreviewing(false)
    }
  }

  const handleParse = async (file: File) => {
    setParsing(true)
    setPreview(null)
    try {
      const result = await parseUsersImportFile(file)
      setParseResult(result)
      setFileName(file.name)
      setOverrides({})
      setSelectedRowKeys([])
      setPreview(null)
      setPreviewSearch('')
      const guessUsername = result.columns.find((c) => /姓名|名称|name|账号|用户/i.test(c))
      const guessEmail = result.columns.find((c) => /邮箱|email|mail/i.test(c))
      setUsernameColumn(guessUsername)
      setEmailColumn(guessEmail)
      message.success(`已解析 ${result.total} 条数据`)
    } catch (error: any) {
      message.error(error.response?.data?.detail || '文件解析失败')
    } finally {
      setParsing(false)
    }
  }

  const applyOverrides = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择需要单独配置的行')
      return
    }
    const next = { ...overrides }
    selectedRowKeys.forEach((key) => {
      const rowNumber = Number(key)
      next[rowNumber] = {
        ...next[rowNumber],
        ...(overrideRole ? { role: overrideRole } : {}),
        ...(overridePassword.trim() ? { password: overridePassword.trim() } : {}),
      }
    })
    setOverrides(next)
    setOverridePassword('')
    setOverrideRole(undefined)
    await refreshPreview(next)
    message.success('已应用到选中行')
  }

  const handleExecute = async () => {
    if (!validateBeforePreview()) return
    if (!preview || preview.valid_count === 0) {
      message.warning('请先生成预览，且至少有一条有效数据')
      return
    }
    setImporting(true)
    try {
      const result = await executeUsersImport(buildPayload())
      queryClient.invalidateQueries({ queryKey: ['users'] })
      const summary = `导入完成：新增 ${result.created} 条，更新邮箱 ${result.updated} 条，跳过 ${result.skipped} 条，失败 ${result.failed} 条`
      if (result.failed > 0) {
        message.warning(summary)
      } else {
        message.success(summary)
      }
      onSuccess?.()
      handleClose()
    } catch (error: any) {
      message.error(error.response?.data?.detail || '导入失败')
    } finally {
      setImporting(false)
    }
  }

  const filteredPreviewRows = useMemo(() => {
    if (!preview) return []
    const keyword = previewSearch.trim().toLowerCase()
    if (!keyword) return preview.rows
    return preview.rows.filter(
      (row) =>
        row.username.toLowerCase().includes(keyword) ||
        row.source_username.toLowerCase().includes(keyword)
    )
  }, [preview, previewSearch])

  const previewColumns = useMemo(
    () => [
      { title: '序号', dataIndex: 'row_number', width: 70 },
      {
        title: '原始账号值',
        dataIndex: 'source_username',
        ellipsis: true,
      },
      {
        title: '最终账号',
        dataIndex: 'username',
        render: (value: string, record: UserImportPreviewResult['rows'][number]) => (
          <Space size={4}>
            <Text code>{value}</Text>
            {record.renamed && <Tag color="orange">重名已改名</Tag>}
          </Space>
        ),
      },
      {
        title: '邮箱',
        dataIndex: 'email',
        ellipsis: true,
        render: (value: string) => value || '-',
      },
      {
        title: '角色',
        dataIndex: 'role',
        width: 110,
        render: (value: string) => roleLabelMap[value] || value,
      },
      { title: '密码', dataIndex: 'password', width: 100, ellipsis: true },
      {
        title: '状态',
        dataIndex: 'status',
        width: 150,
        render: (value: string, record: UserImportPreviewResult['rows'][number]) => {
          if (value === 'invalid') {
            return <Tag color="red">跳过：{record.messages.join('；')}</Tag>
          }
          if (value === 'renamed') {
            return <Tag color="orange">{record.messages.join('；')}</Tag>
          }
          if (value === 'update') {
            return <Tag color="blue">{record.messages.join('；')}</Tag>
          }
          return <Tag color="green">可导入</Tag>
        },
      },
    ],
    []
  )

  const allColumns = parseResult?.columns || []

  return (
    <Modal
      title="批量导入用户"
      open={open}
      width={1080}
      onCancel={handleClose}
      footer={
        <Space>
          <Button onClick={handleClose}>取消</Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => refreshPreview()}
            loading={previewing}
            disabled={!parseResult}
          >
            生成预览
          </Button>
          <Button
            type="primary"
            icon={<UploadOutlined />}
            onClick={handleExecute}
            loading={importing}
            disabled={!preview || preview.valid_count === 0}
          >
            确认导入（{preview?.valid_count || 0} 条）
          </Button>
        </Space>
      }
    >
      <Dragger
        accept=".xlsx,.xlsm"
        multiple={false}
        showUploadList={false}
        beforeUpload={(file) => {
          handleParse(file as File)
          return Upload.LIST_IGNORE
        }}
        disabled={parsing}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">点击或拖拽 Excel 文件到这里</p>
        <p className="ant-upload-hint">
          支持 .xlsx 格式，默认读取第一个工作表。账号和密码为必填，邮箱为可选项。{fileName && `当前文件：${fileName}`}
        </p>
      </Dragger>

      {parseResult && (
        <>
          <Divider orientation="left">第一步：列映射与默认配置</Divider>
          <Card size="small">
            <Space wrap size="large">
              <div>
                <div style={{ marginBottom: 4 }}>账号所在列 *</div>
                <Select
                  style={{ width: 220 }}
                  placeholder="请选择账号列"
                  value={usernameColumn}
                  onChange={(value) => {
                    setUsernameColumn(value)
                    setPreview(null)
                  }}
                  options={allColumns.map((c) => ({ value: c, label: c }))}
                />
              </div>
              <div>
                <div style={{ marginBottom: 4 }}>邮箱所在列（可选）</div>
                <Select
                  allowClear
                  style={{ width: 220 }}
                  placeholder="不设置则邮箱留空"
                  value={emailColumn}
                  onChange={(value) => {
                    setEmailColumn(value)
                    setPreview(null)
                  }}
                  options={allColumns.map((c) => ({ value: c, label: c }))}
                />
              </div>
              <div>
                <div style={{ marginBottom: 4 }}>默认密码（至少 6 位）</div>
                <Input.Password
                  style={{ width: 200 }}
                  value={defaultPassword}
                  onChange={(e) => {
                    setDefaultPassword(e.target.value)
                    setPreview(null)
                  }}
                />
              </div>
              <div>
                <div style={{ marginBottom: 4 }}>默认角色</div>
                <Select
                  style={{ width: 160 }}
                  value={defaultRole}
                  onChange={(value) => {
                    setDefaultRole(value)
                    setPreview(null)
                  }}
                  options={[
                    { value: 'user', label: '普通用户' },
                    { value: 'admin', label: '管理员' },
                    { value: 'super_admin', label: '超级管理员' },
                  ]}
                />
              </div>
            </Space>
            <div style={{ marginTop: 12 }}>
              <div style={{ marginBottom: 4 }}>账号生成规则</div>
              <Checkbox
                checked={lowercase}
                onChange={(e) => {
                  setLowercase(e.target.checked)
                  setPreview(null)
                }}
              >
                转为小写
              </Checkbox>
              <Checkbox
                checked={removeSpaces}
                onChange={(e) => {
                  setRemoveSpaces(e.target.checked)
                  setPreview(null)
                }}
              >
                去除空格
              </Checkbox>
              <Checkbox
                checked={removeSpecial}
                onChange={(e) => {
                  setRemoveSpecial(e.target.checked)
                  setPreview(null)
                }}
              >
                去除特殊字符（保留字母、数字、下划线、短横线）
              </Checkbox>
            </div>
            <div style={{ marginTop: 8 }}>
              <Text type="secondary">
                重名处理：最终账号与已有用户或本批次重复时，会自动追加递增数字（如 cuiqing → cuiqing1）。
              </Text>
            </div>
          </Card>
        </>
      )}

      {preview && (
        <>
          <Divider orientation="left">第二步：单独配置选中行（可选）</Divider>
          <Card size="small">
            <Space wrap>
              <Text>已选 {selectedRowKeys.length} 行：</Text>
              <Select
                allowClear
                style={{ width: 160 }}
                placeholder="覆盖角色"
                value={overrideRole}
                onChange={setOverrideRole}
                options={[
                  { value: 'user', label: '普通用户' },
                  { value: 'admin', label: '管理员' },
                  { value: 'super_admin', label: '超级管理员' },
                ]}
              />
              <Input.Password
                style={{ width: 180 }}
                placeholder="覆盖密码（留空不改）"
                value={overridePassword}
                onChange={(e) => setOverridePassword(e.target.value)}
              />
              <Button onClick={applyOverrides} disabled={selectedRowKeys.length === 0}>
                应用到选中行
              </Button>
              <Button
                type="link"
                onClick={() => {
                  setOverrides({})
                  setSelectedRowKeys([])
                  refreshPreview({})
                }}
              >
                清除所有单独配置
              </Button>
            </Space>
          </Card>

          <Divider orientation="left">第三步：预览确认</Divider>
          <Alert
            style={{ marginBottom: 12 }}
            type={preview.invalid_count > 0 ? 'warning' : 'success'}
            showIcon
            message={`共 ${preview.total} 条，可处理 ${preview.valid_count} 条（其中更新邮箱 ${preview.updated_count} 条），重名改名 ${preview.renamed_count} 条，将跳过 ${preview.invalid_count} 条`}
          />
          <Input.Search
            allowClear
            placeholder="按最终账号或原始账号检索，便于勾选优选覆盖数据"
            value={previewSearch}
            onChange={(e) => setPreviewSearch(e.target.value)}
            style={{ width: 420, marginBottom: 12 }}
          />
          <Table
            size="small"
            rowKey="row_number"
            columns={previewColumns}
            dataSource={filteredPreviewRows}
            pagination={{ pageSize: 8, showSizeChanger: false }}
            rowSelection={{
              selectedRowKeys,
              onChange: (keys) => setSelectedRowKeys(keys),
            }}
            scroll={{ y: 380 }}
          />
        </>
      )}
    </Modal>
  )
}

export default ImportUsersModal
