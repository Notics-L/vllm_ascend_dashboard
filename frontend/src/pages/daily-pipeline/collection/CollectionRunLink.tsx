import { useState } from 'react'
import { Button, Drawer } from 'antd'
import CollectionDetail from './CollectionDetail'
import './Collection.css'

function CollectionRunLink({ runId }: { runId: number }) {
  const [open, setOpen] = useState(false)
  return <>
    <Button type="link" className="daily-collection-link daily-collection-history-link" onClick={() => setOpen(true)}>{runId}</Button>
    <Drawer rootClassName="daily-collection-run-drawer" title="采集详情" placement="right" width={960}
      styles={{ wrapper: { maxWidth: '100vw' } }} open={open} onClose={() => setOpen(false)} destroyOnHidden>
      {open && <CollectionDetail id={String(runId)} />}
    </Drawer>
  </>
}

export default CollectionRunLink
