import api from './api'

export interface NotificationContact {
  id: number
  user_id: number | null
  name: string
  email: string | null
  phone: string | null
  phone_verified_at: string | null
  enabled: boolean
  group_ids: number[]
  created_at: string | null
  updated_at: string | null
}

export interface ContactPayload {
  name: string
  email?: string | null
  phone?: string | null
  user_id?: number | null
  enabled?: boolean
  group_ids?: number[]
}

export interface NotificationGroup {
  id: number
  name: string
  description: string | null
  enabled: boolean
  contact_ids: number[]
  contacts: Array<{ id: number; name: string; email: string | null }>
  created_at: string | null
}

export interface GroupPayload {
  name: string
  description?: string | null
  enabled?: boolean
  contact_ids?: number[]
}

export interface NotificationDelivery {
  id: number
  event_id: number
  source: string
  event_type: string
  severity: string
  title: string
  summary: string | null
  channel: string
  recipient: string
  attempt: number
  status: string
  provider: string | null
  error: string | null
  sent_at: string | null
  delivered_at: string | null
  created_at: string | null
}

export interface InboxItem {
  id: number
  event_id: number
  channel: 'sms' | 'voice'
  recipient: string
  content: string
  title: string
  severity: string
  status: string
  provider: string | null
  provider_message_id: string | null
  error: string | null
  created_at: string | null
  sent_at: string | null
  segments?: number
  answered?: boolean
  duration_seconds?: number | null
}

export interface PagedResult<T> {
  total: number
  page: number
  page_size: number
  items: T[]
}

export const listContacts = async (): Promise<NotificationContact[]> => {
  const { data } = await api.get<{ items: NotificationContact[] }>('/notifications/contacts')
  return data.items
}

export const createContact = async (payload: ContactPayload): Promise<NotificationContact> => {
  const { data } = await api.post<NotificationContact>('/notifications/contacts', payload)
  return data
}

export const updateContact = async (id: number, payload: Partial<ContactPayload>): Promise<NotificationContact> => {
  const { data } = await api.put<NotificationContact>(`/notifications/contacts/${id}`, payload)
  return data
}

export const deleteContact = async (id: number): Promise<void> => {
  await api.delete(`/notifications/contacts/${id}`)
}

export const listGroups = async (): Promise<NotificationGroup[]> => {
  const { data } = await api.get<{ items: NotificationGroup[] }>('/notifications/groups')
  return data.items
}

export const createGroup = async (payload: GroupPayload): Promise<NotificationGroup> => {
  const { data } = await api.post<NotificationGroup>('/notifications/groups', payload)
  return data
}

export const updateGroup = async (id: number, payload: Partial<GroupPayload>): Promise<NotificationGroup> => {
  const { data } = await api.put<NotificationGroup>(`/notifications/groups/${id}`, payload)
  return data
}

export const deleteGroup = async (id: number): Promise<void> => {
  await api.delete(`/notifications/groups/${id}`)
}

export const listDeliveries = async (params?: {
  status?: string
  channel?: string
  source?: string
  page?: number
  page_size?: number
}): Promise<PagedResult<NotificationDelivery>> => {
  const { data } = await api.get<PagedResult<NotificationDelivery>>('/notifications/deliveries', { params })
  return data
}

export const retryDelivery = async (id: number): Promise<{ success: boolean }> => {
  const { data } = await api.post<{ success: boolean }>(`/notifications/deliveries/${id}/retry`)
  return data
}

export const listInbox = async (params?: {
  channel?: 'sms' | 'voice'
  page?: number
  page_size?: number
}): Promise<PagedResult<InboxItem>> => {
  const { data } = await api.get<PagedResult<InboxItem>>('/notifications/inbox', { params })
  return data
}

export interface SmsProvider {
  id: string
  name: string
  provider: string
  access_key_id: string
  access_key_secret?: string
  access_key_secret_set?: boolean
  sign_name: string
  template_code: string
  template_param_key: string
  region_id: string
}

export interface SmsConfig {
  enabled: boolean
  provider: string
  access_key_id: string
  access_key_secret_set: boolean
  sign_name: string
  template_code: string
  template_param_key: string
  region_id: string
  fallback_enabled: boolean
  apply_to: 'critical' | 'all'
  test_phones: string[]
  active_provider_id: string
  providers: SmsProvider[]
}

export interface SmsConfigUpdate {
  enabled?: boolean
  provider?: string
  access_key_id?: string
  access_key_secret?: string
  sign_name?: string
  template_code?: string
  template_param_key?: string
  region_id?: string
  fallback_enabled?: boolean
  apply_to?: 'critical' | 'all'
  test_phones?: string[]
  active_provider_id?: string
  providers?: SmsProvider[]
}

export const getSmsConfig = async (): Promise<SmsConfig> => {
  const { data } = await api.get<SmsConfig>('/notifications/channels/sms')
  return data
}

export const updateSmsConfig = async (payload: SmsConfigUpdate): Promise<SmsConfig> => {
  const { data } = await api.put<SmsConfig>('/notifications/channels/sms', payload)
  return data
}

export const testSms = async (phones: string[], text?: string): Promise<{ success: boolean; error?: string; results?: Array<Record<string, unknown>> }> => {
  const { data } = await api.post('/notifications/channels/sms/test', { phones, text })
  return data
}

export interface VoiceProvider {
  id: string
  name: string
  provider: string
  access_key_id: string
  access_key_secret?: string
  access_key_secret_set?: boolean
  called_show_number: string
  tts_code: string
  tts_param_key: string
  region_id: string
}

export interface VoiceConfig {
  enabled: boolean
  provider: string
  access_key_id: string
  access_key_secret_set: boolean
  called_show_number: string
  tts_code: string
  tts_param_key: string
  region_id: string
  play_times: number
  fallback_enabled: boolean
  apply_to: 'critical' | 'all'
  test_phones: string[]
  ivr_enabled: boolean
  callback_token: string
  ivr_actions: Record<string, string>
  ivr_allowed_sources: string[]
  ivr_allowed_phones: string[]
  active_provider_id: string
  providers: VoiceProvider[]
}

export interface VoiceConfigUpdate {
  enabled?: boolean
  provider?: string
  access_key_id?: string
  access_key_secret?: string
  called_show_number?: string
  tts_code?: string
  tts_param_key?: string
  region_id?: string
  play_times?: number
  fallback_enabled?: boolean
  apply_to?: 'critical' | 'all'
  test_phones?: string[]
  ivr_enabled?: boolean
  callback_token?: string
  ivr_actions?: Record<string, string>
  ivr_allowed_sources?: string[]
  ivr_allowed_phones?: string[]
  active_provider_id?: string
  providers?: VoiceProvider[]
}

export interface IvrAction {
  id: number
  provider_call_id: string
  digit: string
  action: string
  stage: string
  status: string
  result: Record<string, unknown> | null
  event_title: string
  event_source: string
  created_at: string | null
  executed_at: string | null
}

export const getVoiceConfig = async (): Promise<VoiceConfig> => {
  const { data } = await api.get<VoiceConfig>('/notifications/channels/voice')
  return data
}

export const updateVoiceConfig = async (payload: VoiceConfigUpdate): Promise<VoiceConfig> => {
  const { data } = await api.put<VoiceConfig>('/notifications/channels/voice', payload)
  return data
}

export const testVoice = async (phones: string[], text?: string): Promise<{ success: boolean; error?: string; results?: Array<Record<string, unknown>> }> => {
  const { data } = await api.post('/notifications/channels/voice/test', { phones, text })
  return data
}


export const listIvrActions = async (): Promise<IvrAction[]> => {
  const { data } = await api.get<{ items: IvrAction[] }>('/notifications/actions')
  return data.items
}

export interface NotificationPolicy {
  quiet_hours_enabled: boolean
  quiet_hours_start: string
  quiet_hours_end: string
  cooldown_minutes: number
  storm_window_minutes: number
  storm_threshold: number
  escalation_enabled: boolean
  escalation_group_ids: number[]
  oncall_group_id: number | null
}

export interface NotificationMetrics {
  days: number
  events: number
  deliveries: number
  sent: number
  failed: number
  success_rate: number
  channels: Record<string, { total: number; sent: number; failed: number; pending: number; success_rate: number }>
  actions: Record<string, { total: number; success: number; failed: number }>
}

export const getPolicy = async (): Promise<NotificationPolicy> => {
  const { data } = await api.get<NotificationPolicy>('/notifications/policy')
  return data
}

export const updatePolicy = async (payload: Partial<NotificationPolicy>): Promise<NotificationPolicy> => {
  const { data } = await api.put<NotificationPolicy>('/notifications/policy', payload)
  return data
}

export const getMetrics = async (days = 7): Promise<NotificationMetrics> => {
  const { data } = await api.get<NotificationMetrics>('/notifications/metrics', { params: { days } })
  return data
}
