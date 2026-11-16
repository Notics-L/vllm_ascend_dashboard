import { Alert, Button, Form, Result, Space, Spin, message } from 'antd'
import { isAxiosError } from 'axios'
import { useNavigate, useParams } from 'react-router-dom'
import { useDailyCaseMutations, useDailyCases } from '../../../hooks/useDailyPipeline'
import type { DailyCaseUpdate } from '../../../services/dailyPipeline'
import DailyCaseFields from './DailyCaseFields'
import DailyCasePage from './DailyCasePage'

function DailyCaseEdit() {
  const { caseId } = useParams<{ caseId: string }>()
  const navigate = useNavigate()
  const { data: cases = [], isLoading, isFetching, isError, refetch } = useDailyCases()
  const { updateCase } = useDailyCaseMutations()
  const record = cases.find((item) => item.id === caseId)
  const labelOptions = [...new Set(cases.flatMap((item) => item.tags || []))]
    .map((value) => ({ label: value, value }))
  const returnToCases = () => navigate('/daily-pipeline/cases', { replace: true })

  const handleSave = async (values: DailyCaseUpdate) => {
    if (!record) return
    try {
      await updateCase.mutateAsync({ id: record.id, values: { tags: values.tags || [], status: values.status } })
      message.success('用例已更新')
      returnToCases()
    } catch (error) {
      message.error(isAxiosError<{ detail?: string }>(error)
        ? error.response?.data?.detail || '保存失败，请重试'
        : '保存失败，请重试')
    }
  }

  return (
    <DailyCasePage title="编辑 Daily 用例" saving={updateCase.isPending} onBack={returnToCases}>
      {isLoading ? <Spin /> : isError ? (
        <Alert type="error" showIcon message="Daily 用例加载失败" action={
          <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
        } />
      ) : !record ? (
        <Result status="404" title="用例不存在" subTitle="该用例可能已被移出 Daily 清单。" />
      ) : (
        <Form<DailyCaseUpdate>
          key={record.id}
          className="daily-case-config-form daily-case-edit-form"
          layout="horizontal"
          labelCol={{ flex: '128px' }}
          wrapperCol={{ flex: 'auto' }}
          labelAlign="left"
          colon
          requiredMark={false}
          initialValues={{ tags: record.tags || [], status: record.status }}
          onFinish={handleSave}
          disabled={updateCase.isPending}
        >
          <DailyCaseFields record={record} labelOptions={labelOptions} />
          <Form.Item>
            <Space className="daily-cases-actions" size={8}>
              <Button type="primary" htmlType="submit" loading={updateCase.isPending}>保存</Button>
              <Button onClick={returnToCases} disabled={updateCase.isPending}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      )}
    </DailyCasePage>
  )
}

export default DailyCaseEdit
