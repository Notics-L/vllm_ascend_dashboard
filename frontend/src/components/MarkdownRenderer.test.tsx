import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import MarkdownRenderer from './MarkdownRenderer'

it('hides Issue tracking comments while preserving headings, lists, and links', () => {
  const content = `<!-- vllm-ascend-daily:123 -->
## Schedule Daily Tests
[Run #123](https://github.com/example/repo/actions/runs/123)

## UT
<!-- daily-failure:secret-marker -->
- [cpu test](https://github.com/example/repo/actions/runs/123/job/456)
<!-- daily-section:UT:end -->`
  const html = renderToStaticMarkup(<MarkdownRenderer content={content} skipHtml />)
  expect(html).toContain('Schedule Daily Tests')
  expect(html).toMatch(/<ul[ >]/)
  expect(html).toContain('https://github.com/example/repo/actions/runs/123/job/456')
  expect(html).not.toContain('vllm-ascend-daily')
  expect(html).not.toContain('daily-failure')
  expect(html).not.toContain('daily-section')
})

it('preserves literal comment examples in code and never renders executable HTML', () => {
  const html = renderToStaticMarkup(<MarkdownRenderer content={'`<!-- literal -->`\n\n<script>alert(1)</script>'} skipHtml />)
  expect(html).toContain('&lt;!-- literal --&gt;')
  expect(html).not.toContain('<script>')
})
