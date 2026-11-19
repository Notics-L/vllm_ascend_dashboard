import { useState } from 'react'
import { Button, Form, Input, Modal, Space, message } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import { isAxiosError } from 'axios'
import { useDailyNotificationRecipients, useDailyNotificationRecipientMutations } from '../../../hooks/useDailyPipeline'
import type { DailyNotificationRecipient, DailyNotificationRecipientInput } from '../../../types/dailyPipeline'
import { dailyTableRefresh } from '../shared/dailyTableRefresh'
import { useCurrentUser } from '../../../hooks/useCurrentUser'
import './NotificationRecipients.css'

export default function NotificationRecipients() {
  const query = useDailyNotificationRecipients()
  const mutations = useDailyNotificationRecipientMutations()
  const { data: user } = useCurrentUser()
  const editable = user?.role === 'admin' || user?.role === 'super_admin'
  const [open, setOpen] = useState(false)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [page, setPage] = useState(1)
  const [form] = Form.useForm<DailyNotificationRecipientInput>()
  const [modal, holder] = Modal.useModal()
  const busy = mutations.save.isPending || mutations.remove.isPending
  const errorText = (error: unknown) => {
    const detail = isAxiosError(error) ? error.response?.data?.detail : null
    return typeof detail === 'string' ? detail : '操作失败，请重试'
  }
  const add = () => {
    form.resetFields()
    setOpen(true)
  }
  const save = async (values: DailyNotificationRecipientInput) => {
    try {
      await mutations.save.mutateAsync({ values: { ...values, account: values.account?.trim() || undefined } })
      setOpen(false)
      form.resetFields()
      void message.success('已保存')
    } catch (error) { void message.error(errorText(error)) }
  }
  const remove = (ids: number[]) => modal.confirm({
    title: '确定删除？', okText: '删除', cancelText: '取消', okButtonProps: { danger: true },
    onOk: async () => {
      try { await mutations.remove.mutateAsync(ids); setSelectedIds((current) => current.filter((id) => !ids.includes(id))); setPage(1); void message.success('已删除') }
      catch (error) { void message.error(errorText(error)); throw error }
    },
  })
  const refresh = async () => {
    const result = await query.refetch()
    if (result.isError) void message.error('刷新失败，请稍后重试')
  }
  const filterOptions = (field: 'githubName' | 'name') => [...new Set((query.data ?? []).map((row) => row[field]))]
    .map((value) => ({ text: value, value }))
  const columns: ProColumns<DailyNotificationRecipient>[] = [
    { title: 'ID', dataIndex: 'id', width: 70, search: false },
    { title: 'GitHub 用户', dataIndex: 'githubName', width: 220, ellipsis: true, filters: filterOptions('githubName'), filterSearch: true, onFilter: (value, row) => row.githubName === value },
    { title: '姓名', dataIndex: 'name', width: 180, ellipsis: true, filters: filterOptions('name'), filterSearch: true, onFilter: (value, row) => row.name === value },
  ]
  return <div className="daily-notification-recipients">
    {holder}
    <ProTable<DailyNotificationRecipient> size="small" rowKey="id" columns={columns} dataSource={query.data ?? []}
      loading={query.isFetching || busy} search={false}
      onChange={(_pagination, _filters, _sorter, extra) => { if (extra.action === 'filter') setPage(1) }} cardProps={false} tableAlertRender={false}
      headerTitle={<Space className="daily-cases-actions" size={8}>
        <Button className="daily-trigger-refresh" size="small" icon={<PlusOutlined />} disabled={!editable || busy} onClick={add}>新增</Button>
        <Button danger size="small" icon={<DeleteOutlined />} disabled={!editable || busy || selectedIds.length === 0} onClick={() => remove(selectedIds)}>删除</Button>
      </Space>}
      rowSelection={{ columnWidth: 48, selectedRowKeys: selectedIds, preserveSelectedRowKeys: true,
        onChange: (keys) => setSelectedIds(keys.map(Number)), getCheckboxProps: () => ({ disabled: busy || !editable }) }}
      {...dailyTableRefresh(refresh, query.isFetching || busy, { current: page, onChange: setPage, defaultPageSize: 5, showSizeChanger: true, pageSizeOptions: ['5', '10', '20'] })}
      scroll={{ x: 'max-content', y: 280 }} />
    <Modal title="新增映射" open={open} width={480} okText="保存" cancelText="取消"
      confirmLoading={mutations.save.isPending} onOk={() => form.submit()} onCancel={() => { if (!mutations.save.isPending) setOpen(false) }} destroyOnHidden>
      <Form form={form} layout="horizontal" labelCol={{ flex: '112px' }} wrapperCol={{ flex: 'auto' }}
        labelAlign="left" requiredMark={false} colon size="small" disabled={mutations.save.isPending} onFinish={save}>
        <Form.Item name="githubName" label="GitHub 用户" rules={[{ required: true, whitespace: true, message: '请输入 GitHub 用户名' }, { pattern: /^[a-zA-Z0-9][a-zA-Z0-9-]*$/, message: 'GitHub 用户名格式无效' }]}>
            <Input maxLength={100} />
        </Form.Item>
        <Form.Item name="name" label="姓名" rules={[{ required: true, whitespace: true, message: '请输入姓名' }]}><Input maxLength={100} /></Form.Item>
        <Form.Item name="account" label="工号" rules={[{ required: true, whitespace: true, message: '请输入工号' }]}>
          <Input.Password maxLength={100} autoComplete="new-password"  />
        </Form.Item>
      </Form>
    </Modal>
  </div>
}
