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
