import { Empty, Tag, Tooltip, Transfer, Tree } from 'antd'
import type { NightlyCase } from '../../../types/dailyPipeline'
import DailyCaseTag from './DailyCaseTag'
import { buildTransferChangeTree, getDailySelectionFromPool, getNightlyPoolKeys } from './nightlyCaseTree'
import type { NightlyCaseTreeNode } from './nightlyCaseTree'

interface NightlyCaseTreeTransferProps {
  cases: NightlyCase[]
  value?: string[]
  onChange?: (keys: string[]) => void
  disabled?: boolean
}

interface NightlyTransferItem {
  key: string
  title: string
  disabled: boolean
  source: NightlyCase
}

function CaseTransferLabel({ item, showStatus }: { item: NightlyCase; showStatus: boolean }) {
  return (
    <span className="daily-case-leaf">
      <Tooltip title={item.name}>
        <span className="daily-case-transfer-name">{item.name}</span>
      </Tooltip>
      {item.tags.length > 0 && (
        <Tooltip title={item.tags.join('、')}>
          <span className="daily-case-transfer-tags" aria-label={`标签：${item.tags.join('、')}`}>
            {item.tags.map((tag) => <DailyCaseTag key={tag} label={tag} />)}
          </span>
        </Tooltip>
      )}
      {showStatus && (
        <Tag className="daily-case-transfer-status" color={item.status === '启用' ? 'success' : 'error'}>
          {item.status}
        </Tag>
      )}
    </span>
  )
}

function NightlyCaseTreeTransfer({ cases, value, onChange, disabled = false }: NightlyCaseTreeTransferProps) {
  // The right-hand Transfer target is the Nightly pool; Daily stays on the left.
  const dailySelection = value ?? cases.filter((item) => item.dailyCaseId).map((item) => item.id)
  const targetKeys = getNightlyPoolKeys(cases, dailySelection)
  const dataSource: NightlyTransferItem[] = cases.map((item) => ({
    key: item.id,
    title: `${item.name} ${item.hardware} ${item.testType} ${item.tags.join(' ')}`,
    disabled: false,
    source: item,
  }))

  return (
    <Transfer<NightlyTransferItem>
      className="daily-case-tree-transfer daily-cases-actions"
      dataSource={dataSource}
      targetKeys={targetKeys}
      onChange={(keys) => onChange?.(getDailySelectionFromPool(cases, keys.map(String)))}
      disabled={disabled}
      titles={['Daily 用例', 'Nightly 用例']}
      showSearch
      filterOption={(input, item) => item.title.toLowerCase().includes(input.trim().toLowerCase())}
      locale={{ itemUnit: '条', itemsUnit: '条', searchPlaceholder: '搜索用例、硬件、类型或标签' }}
      render={(item) => item.source.name}
    >
      {({ direction, filteredItems, selectedKeys, onItemSelectAll, disabled: listDisabled }) => {
        if (!filteredItems.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无匹配用例" />
        const visibleKeys = new Set(filteredItems.map((item) => item.key))
        return (
          <Tree<NightlyCaseTreeNode>
            key={`${direction}:${dailySelection.join('|')}`}
            className="daily-case-transfer-tree"
            blockNode
            checkable
            selectable={false}
            defaultExpandAll
            disabled={listDisabled}
            checkedKeys={selectedKeys.filter((key) => visibleKeys.has(String(key)))}
            treeData={buildTransferChangeTree(filteredItems.map((item) => item.source), direction === 'left' ? 'daily' : 'nightly')}
            titleRender={(node) => node.changeGroup ? (
              <span className={`daily-case-change-group daily-case-change-group-${node.changeGroup}`}>{node.title}</span>
            ) : node.source ? (
              <CaseTransferLabel item={node.source} showStatus={direction === 'left'} />
            ) : <span className="daily-case-transfer-name">{node.title}</span>}
            onCheck={(_, info) => onItemSelectAll(info.node.selectableKeys, info.checked)}
          />
        )
      }}
    </Transfer>
  )
}

export default NightlyCaseTreeTransfer
