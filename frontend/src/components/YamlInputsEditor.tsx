import { Input } from 'antd'

interface YamlInputsEditorProps {
  value?: string
  onChange?: (value: string) => void
}

export default function YamlInputsEditor({
  value = '',
  onChange,
}: YamlInputsEditorProps) {
  return (
    <Input.TextArea
      aria-label="Inputs YAML"
      value={value}
      onChange={(event) => onChange?.(event.target.value)}
      autoSize={{ minRows: 3, maxRows: 10 }}
      placeholder={'参数名:\n  default: 示例值'}
      style={{ fontFamily: 'monospace' }}
    />
  )
}
